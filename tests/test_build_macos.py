from pathlib import Path

import pytest

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


def test_external_assets_and_nonrelocatable_runtime_links_are_rejected(tmp_path):
    root = tmp_path / "project"
    root.mkdir()
    (tmp_path / "secret.py").write_text("private")
    (root / "app.py").symlink_to(tmp_path / "secret.py")
    with pytest.raises(ValueError, match="escapes"):
        collect(root, ["app.py"])
    with pytest.raises(ValueError, match="Non-relocatable"):
        check_tree(root)
    (root / "app.py").unlink()
    (root / "real.py").write_text("allowed")
    (root / "alias.py").symlink_to("real.py")
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
