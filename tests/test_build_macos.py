import errno
import hashlib
import json
import platform
import plistlib
import sys
import zipfile
from pathlib import Path

import pytest

import pyyaki.build_macos as builder
from pyyaki import __version__
from pyyaki.build_macos import build, check_locked_inputs, check_tree, collect


def test_asset_selection_excludes_secrets_and_development_files(tmp_path):
    (tmp_path / "app.py").write_text("# app")
    (tmp_path / "ui").mkdir()
    (tmp_path / "ui/index.html").write_text("<h1>Hello</h1>")
    for name in (".env", "secret.key", "private.pem"):
        (tmp_path / "ui" / name).write_text("not for distribution")
    assert collect(tmp_path, ["app.py", "ui/**/*"]) == [Path("app.py"), Path("ui/index.html")]
    with pytest.raises(ValueError, match="relative"):
        collect(tmp_path, ["../outside.py"])
    with pytest.raises(ValueError, match="no allowed files"):
        collect(tmp_path, ["ui/.env"])


def test_external_assets_and_nonrelocatable_runtime_links_are_rejected(tmp_path, symlink):
    root = tmp_path / "project"
    root.mkdir()
    (tmp_path / "secret.py").write_text("private")
    symlink(root / "app.py", tmp_path / "secret.py")
    with pytest.raises(ValueError, match="escapes"):
        collect(root, ["app.py"])
    with pytest.raises(ValueError, match="Non-relocatable"):
        check_tree(root)
    (root / "app.py").unlink()
    (root / "real.py").write_text("allowed")
    symlink(root / "alias.py", "real.py")
    check_tree(root)
    with pytest.raises(ValueError, match="regular files"):
        collect(root, ["alias.py"])


def test_existing_app_is_never_overwritten(tmp_path, monkeypatch):
    monkeypatch.setattr("pyyaki.build_macos.sys.platform", "darwin")
    (tmp_path / "ui").mkdir()
    (tmp_path / "ui/index.html").write_text("hello")
    (tmp_path / "pyproject.toml").write_text(
        '[tool.pyyaki]\nname="Example"\napp-id="com.example.test"\nversion="0.1.0"\n'
    )
    output = tmp_path / "Example.app"
    output.mkdir()
    (output / "owned.txt").write_text("keep")
    with pytest.raises(ValueError, match="new .app"):
        build(tmp_path, tmp_path, tmp_path, tmp_path, tmp_path, tmp_path, output)
    assert (output / "owned.txt").read_text() == "keep"


def test_macos_cross_build_is_rejected(tmp_path, monkeypatch):
    monkeypatch.setattr("pyyaki.build_macos.sys.platform", "win32")
    with pytest.raises(ValueError, match="requires macOS"):
        build(tmp_path, tmp_path, tmp_path, tmp_path, tmp_path, tmp_path)


def test_offline_inputs_reject_remote_sources_and_pip_options(tmp_path):
    wheelhouse = tmp_path / "wheels"
    wheelhouse.mkdir()
    (wheelhouse / "example.whl").write_bytes(b"test")
    lock = tmp_path / "requirements.lock"
    for text in (
        "--find-links https://example.invalid",
        "example @ https://example.invalid/x.whl",
        "example==1.0",
        "",
    ):
        lock.write_text(text)
        with pytest.raises(ValueError):
            check_locked_inputs(lock, wheelhouse)
    lock.write_text("example==1.0 --hash=sha256:" + "a" * 64)
    check_locked_inputs(lock, wheelhouse)
    (wheelhouse / "index.html").write_text("remote links")
    with pytest.raises(ValueError, match="regular .whl"):
        check_locked_inputs(lock, wheelhouse)


@pytest.fixture
def prepared_build(tmp_path, monkeypatch):
    """Exercise staging/publication with stubbed external Python and signing tools."""
    actual_platform = sys.platform
    monkeypatch.setattr(builder.sys, "platform", "darwin")
    project = tmp_path / "project"
    (project / "ui").mkdir(parents=True)
    (project / "ui/index.html").write_text("hello")
    (project / "app.py").write_text("# app")
    (project / "pyproject.toml").write_text(
        '[tool.pyyaki]\nname="Example"\napp-id="com.example.test"\n'
    )
    host = tmp_path / "host"
    host.write_bytes(b"stub host, not Mach-O")
    runtime = tmp_path / "runtime"
    (runtime / "bin").mkdir(parents=True)
    (runtime / "bin/python3").write_bytes(b"stub Python")
    wheelhouse = tmp_path / "wheels"
    wheelhouse.mkdir()
    (wheelhouse / "dependency.whl").write_bytes(b"stub dependency")
    wheel = tmp_path / "framework.whl"
    with zipfile.ZipFile(wheel, "w") as archive:
        archive.writestr(
            f"pyyaki-{__version__}.dist-info/METADATA", f"Name: pyyaki\nVersion: {__version__}\n"
        )
    lock = tmp_path / "requirements.lock"
    lock.write_text("dependency==1.0 --hash=sha256:" + "a" * 64)
    output = tmp_path / "output/Example.app"
    calls = []

    def run(arguments, **kwargs):
        calls.append(arguments)
        if arguments == [str(host), "--version"]:
            return f"PyYaki native host {__version__}"
        return ""

    monkeypatch.setattr(builder, "run", run)
    monkeypatch.setattr(
        builder,
        "probe",
        lambda path: {
            "version": [3, 14, 3],
            "implementation": "cpython",
            "gil_disabled": False,
            "machine": platform.machine(),
            "prefix": str(path.parent.parent),
        },
    )
    if actual_platform != "darwin":

        def stub_rename(source, destination):
            if destination.exists() or destination.is_symlink():
                raise FileExistsError(destination)
            source.rename(destination)

        monkeypatch.setattr(builder, "rename_exclusive", stub_rename)
    return dict(
        project=project,
        host=host,
        runtime=runtime,
        wheelhouse=wheelhouse,
        framework_wheel=wheel,
        requirements=lock,
        output=output,
    ), calls


@pytest.mark.parametrize("phase", ["dependencies", "framework", "import", "sign", "verify"])
def test_failed_external_step_removes_all_staging(prepared_build, monkeypatch, phase):
    inputs, calls = prepared_build
    original = builder.run

    def fail(arguments, **kwargs):
        is_pip = "pip" in arguments
        if (
            (phase == "dependencies" and is_pip and "--no-deps" not in arguments)
            or (phase == "framework" and is_pip and "--no-deps" in arguments)
            or (phase == "import" and "-c" in arguments)
            or (
                phase == "sign" and "codesign" in Path(arguments[0]).name and "--force" in arguments
            )
            or (phase == "verify" and "--verify" in arguments)
        ):
            if is_pip:
                (Path(arguments[arguments.index("--target") + 1]) / "partial.txt").write_text(
                    "partial"
                )
            raise ValueError(f"injected {phase} failure")
        return original(arguments, **kwargs)

    monkeypatch.setattr(builder, "run", fail)
    with pytest.raises(ValueError, match=f"injected {phase}"):
        build(**inputs)
    assert calls
    assert list(inputs["output"].parent.iterdir()) == []
    assert inputs["host"].read_bytes() == b"stub host, not Mach-O"
    assert (inputs["runtime"] / "bin/python3").read_bytes() == b"stub Python"


@pytest.mark.parametrize("phase", ["report_write", "report_publish", "app_publish", "interrupt"])
def test_failed_publication_leaves_no_outputs(prepared_build, monkeypatch, phase):
    inputs, _ = prepared_build
    if phase == "report_write":
        original = Path.write_text

        def write(path, *args, **kwargs):
            if path.name == "build-report.json":
                raise OSError(errno.ENOSPC, "injected disk full")
            return original(path, *args, **kwargs)

        monkeypatch.setattr(Path, "write_text", write)
    elif phase == "report_publish":

        def link(*args, **kwargs):
            raise PermissionError("injected report publication failure")

        monkeypatch.setattr(builder.os, "link", link)
    else:

        def rename(*args):
            if phase == "interrupt":
                raise KeyboardInterrupt
            raise PermissionError("injected app publication failure")

        monkeypatch.setattr(builder, "rename_exclusive", rename)
    with pytest.raises(KeyboardInterrupt if phase == "interrupt" else OSError):
        build(**inputs)
    assert list(inputs["output"].parent.iterdir()) == []


def test_late_output_collision_preserves_other_output(prepared_build, monkeypatch):
    inputs, _ = prepared_build
    original = builder.run

    def run(arguments, **kwargs):
        if "--verify" in arguments:
            inputs["output"].mkdir()
            (inputs["output"] / "owned.txt").write_text("keep")
        return original(arguments, **kwargs)

    monkeypatch.setattr(builder, "run", run)
    with pytest.raises(FileExistsError):
        build(**inputs)
    assert (inputs["output"] / "owned.txt").read_text() == "keep"
    assert list(inputs["output"].parent.iterdir()) == [inputs["output"]]


def test_complete_report_and_app_are_published_together(prepared_build):
    inputs, _ = prepared_build
    assert build(**inputs) == inputs["output"]
    report = inputs["output"].with_suffix(".build.json")
    metadata = json.loads(report.read_text())
    assert metadata["app_id"] == "com.example.test"
    contents = inputs["output"] / "Contents"
    plist = plistlib.loads((contents / "Info.plist").read_bytes())
    icon = contents / "Resources" / plist["CFBundleIconFile"]
    source = Path(builder.__file__).parent / "static/pyyaki.icns"
    assert icon.read_bytes() == source.read_bytes()
    assert metadata["files"][icon.relative_to(inputs["output"]).as_posix()]["sha256"] == (
        hashlib.sha256(icon.read_bytes()).hexdigest()
    )
    assert sorted(p.name for p in inputs["output"].parent.iterdir()) == [
        "Example.app",
        "Example.build.json",
    ]


def test_missing_input_creates_no_staging(prepared_build):
    inputs, _ = prepared_build
    inputs["framework_wheel"].unlink()
    with pytest.raises(FileNotFoundError):
        build(**inputs)
    assert not inputs["output"].parent.exists()


def test_partial_runtime_copy_is_removed(prepared_build, monkeypatch):
    inputs, _ = prepared_build

    def copytree(source, destination, **kwargs):
        destination.mkdir()
        (destination / "partial.txt").write_text("partial")
        raise OSError("injected copy failure")

    monkeypatch.setattr(builder.shutil, "copytree", copytree)
    with pytest.raises(OSError, match="copy failure"):
        build(**inputs)
    assert list(inputs["output"].parent.iterdir()) == []


def test_failed_runtime_relocation_is_removed(prepared_build, monkeypatch):
    inputs, _ = prepared_build
    original = builder.probe

    def probe(path):
        info = original(path)
        if path != inputs["runtime"] / "bin/python3":
            info["prefix"] = "not-the-staged-runtime"
        return info

    monkeypatch.setattr(builder, "probe", probe)
    with pytest.raises(ValueError, match="cannot relocate"):
        build(**inputs)
    assert list(inputs["output"].parent.iterdir()) == []


def test_failed_publication_does_not_remove_replaced_report(prepared_build, monkeypatch):
    inputs, _ = prepared_build
    report = inputs["output"].with_suffix(".build.json")

    def rename(*args):
        report.unlink()
        report.write_text("other publisher's report")
        raise PermissionError("injected rename failure")

    monkeypatch.setattr(builder, "rename_exclusive", rename)
    with pytest.raises(PermissionError):
        build(**inputs)
    assert report.read_text() == "other publisher's report"
    assert list(inputs["output"].parent.iterdir()) == [report]


@pytest.mark.skipif(sys.platform != "darwin", reason="Darwin renamex_np integration")
def test_exclusive_rename_rejects_even_empty_existing_directory(tmp_path):
    source, destination = tmp_path / "source", tmp_path / "destination"
    source.mkdir()
    destination.mkdir()
    with pytest.raises(FileExistsError):
        builder.rename_exclusive(source, destination)
    assert source.is_dir() and destination.is_dir()


def test_existing_broken_report_link_is_rejected(tmp_path, monkeypatch, symlink):
    monkeypatch.setattr(builder.sys, "platform", "darwin")
    (tmp_path / "ui").mkdir()
    (tmp_path / "ui/index.html").write_text("hello")
    (tmp_path / "pyproject.toml").write_text('[tool.pyyaki]\napp-id="com.example.test"\n')
    output = tmp_path / "Example.app"
    report = output.with_suffix(".build.json")
    symlink(report, tmp_path / "missing.json")
    with pytest.raises(ValueError, match="new .app"):
        build(tmp_path, tmp_path, tmp_path, tmp_path, tmp_path, tmp_path, output)
    assert report.is_symlink()
    assert not (tmp_path / "missing.json").exists()
