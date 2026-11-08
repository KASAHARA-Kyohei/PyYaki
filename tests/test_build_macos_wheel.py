import csv
import io
import json
import zipfile

import pytest
from test_native_host import macho

from pyyaki import __version__
from pyyaki import build_macos_wheel as builder


def pure_wheel(path, *, changes=None, record=True):
    contents = {
        "pyyaki/__init__.py": b"# package",
        f"{builder.DIST}/METADATA": f"Name: pyyaki\nVersion: {__version__}\n".encode(),
        f"{builder.DIST}/WHEEL": b"Wheel-Version: 1.0\nRoot-Is-Purelib: true\nTag: py3-none-any\n",
    }
    if record:
        buffer = io.StringIO(newline="")
        csv.writer(buffer).writerows(
            [
                *[
                    [name, builder.record_hash(body), str(len(body))]
                    for name, body in contents.items()
                ],
                [f"{builder.DIST}/RECORD", "", ""],
            ]
        )
        contents[f"{builder.DIST}/RECORD"] = buffer.getvalue().encode()
    contents.update(changes or {})
    with zipfile.ZipFile(path, "w") as archive:
        for name, data in contents.items():
            archive.writestr(name, data)
    return path


@pytest.fixture
def inputs(tmp_path, monkeypatch):
    monkeypatch.setattr(builder.sys, "platform", "darwin")
    monkeypatch.setattr(builder.platform, "machine", lambda: "arm64")
    host = tmp_path / "host"
    host.write_bytes(macho())
    host.chmod(0o755)
    wheel = pure_wheel(tmp_path / "input.whl")
    calls = []

    def command(args):
        calls.append(args)
        return f"PyYaki native host {__version__}\n" if args[-1] == "--version" else ""

    monkeypatch.setattr(builder, "command", command)
    return wheel, host, tmp_path / "output", calls


def test_build_platform_tag_executable_manifest_record_and_no_overwrite(inputs):
    wheel, host, output, calls = inputs
    result = builder.build(wheel, host, output)
    assert result.name == f"pyyaki-{__version__}-py3-none-macosx_11_0_arm64.whl"
    with zipfile.ZipFile(result) as archive:
        native = f"{builder.NATIVE}/pyyaki-host"
        assert archive.read(native) == host.read_bytes()
        assert (archive.getinfo(native).external_attr >> 16) & 0o111
        manifest = json.loads(archive.read(f"{builder.NATIVE}/manifest.json"))
        assert manifest["minimum_macos"] == [11, 0, 0]
        metadata = archive.read(f"{builder.DIST}/WHEEL").decode()
        assert "Root-Is-Purelib: false" in metadata
        assert "Tag: py3-none-macosx_11_0_arm64" in metadata
        rows = list(csv.reader(io.StringIO(archive.read(f"{builder.DIST}/RECORD").decode())))
        assert {row[0] for row in rows} == set(archive.namelist())
        for name, digest, size in rows:
            if name.endswith("/RECORD"):
                assert digest == size == ""
            else:
                assert digest == builder.record_hash(archive.read(name))
                assert size == str(len(archive.read(name)))
    assert calls[0][:3] == ["/usr/bin/codesign", "--verify", "--strict"]
    before = result.read_bytes()
    with pytest.raises(ValueError, match="already exists"):
        builder.build(wheel, host, output)
    assert result.read_bytes() == before


@pytest.mark.parametrize(
    "changes,match",
    [
        ({"../escape": b"bad"}, "pure PyYaki"),
        ({"pyyaki//ambiguous.py": b"bad"}, "pure PyYaki"),
        ({f"{builder.DIST}/RECORD.jws": b"stale signature"}, "pure PyYaki"),
        ({"pyyaki/__init__.py": b"tampered"}, "RECORD hash"),
        ({f"{builder.DIST}/METADATA": b"Name: pyyaki\nVersion: 9.9.9\n"}, "version mismatch"),
        ({f"{builder.DIST}/RECORD": b"\n"}, "invalid input"),
        ({f"{builder.NATIVE}/pyyaki-host": b"binary"}, "pure PyYaki"),
    ],
)
def test_bad_input_wheel_is_rejected_before_host_execution(inputs, changes, match):
    wheel, host, output, calls = inputs
    pure_wheel(wheel, changes=changes)
    with pytest.raises(ValueError, match=match):
        builder.build(wheel, host, output)
    assert calls == []


def test_host_version_and_codesign_failure_do_not_publish(inputs, monkeypatch):
    wheel, host, output, calls = inputs
    monkeypatch.setattr(builder, "command", lambda args: "wrong-version")
    with pytest.raises(ValueError, match="host version"):
        builder.build(wheel, host, output)
    assert not output.exists()

    def failed(args):
        raise ValueError("codesign failed")

    monkeypatch.setattr(builder, "command", failed)
    with pytest.raises(ValueError, match="codesign failed"):
        builder.build(wheel, host, output)
    assert not output.exists()


def test_publish_failure_removes_only_own_report(inputs, monkeypatch):
    wheel, host, output, calls = inputs
    link = builder.os.link

    def fail_wheel(source, destination):
        if str(destination).endswith(".whl"):
            raise OSError("injected publish failure")
        return link(source, destination)

    monkeypatch.setattr(builder.os, "link", fail_wheel)
    with pytest.raises(OSError, match="publish failure"):
        builder.build(wheel, host, output)
    assert not list(output.iterdir())
