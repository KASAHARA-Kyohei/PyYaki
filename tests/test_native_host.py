import hashlib
import json
import os
import struct
from pathlib import Path

import pytest

from pyyaki import __version__
from pyyaki import _native_host as native


def macho(cpu=0x100000C, version=0xB0000, library=None):
    commands = struct.pack("<6I", 0x32, 24, 1, version, 0, 0)
    count = 1
    if library:
        name = library.encode() + b"\0"
        commands += struct.pack("<6I", 0xC, 24 + len(name), 24, 0, 0, 0) + name
        count += 1
    return struct.pack("<8I", 0xFEEDFACF, cpu, 0, 2, count, len(commands), 0, 0) + commands


@pytest.mark.parametrize("cpu,name", [(0x100000C, "arm64"), (0x1000007, "x86_64")])
def test_macho_cpu_target_and_system_libraries(cpu, name):
    info = native.macho_info(macho(cpu, 0xB0000, "/usr/lib/libSystem.B.dylib"))
    assert info == {
        "machine": name,
        "minimum_macos": [11, 0, 0],
        "libraries": ["/usr/lib/libSystem.B.dylib"],
    }


@pytest.mark.parametrize(
    "data",
    [
        b"",
        b"\xca\xfe\xba\xbe" + b"x" * 80,
        macho()[:-1],
        macho(cpu=123),
        macho(library="@rpath/private.dylib"),
        macho(library="/usr/lib/../../private/unsafe.dylib"),
    ],
)
def test_macho_rejects_unsupported_or_nonrelocatable_hosts(data):
    with pytest.raises(ValueError):
        native.macho_info(data)


@pytest.fixture
def bundle(tmp_path, monkeypatch):
    monkeypatch.setattr(native, "HOST_DIR", tmp_path)
    monkeypatch.setattr(native.sys, "platform", "darwin")
    monkeypatch.setattr(native.platform, "machine", lambda: "arm64")
    monkeypatch.setattr(native.platform, "mac_ver", lambda: ("27.0.1", (), "arm64"))
    data = macho()
    host = tmp_path / "pyyaki-host"
    host.write_bytes(data)
    host.chmod(0o755)
    record = {
        "format": 1,
        "framework_version": __version__,
        "size": len(data),
        "sha256": hashlib.sha256(data).hexdigest(),
        **native.macho_info(data),
    }
    (tmp_path / "manifest.json").write_text(json.dumps(record))
    return host, record


def test_bundle_version_hash_cpu_os_and_missing_host(bundle, monkeypatch):
    host, record = bundle
    assert native.bundled_host() == host
    monkeypatch.setattr(native.platform, "machine", lambda: "x86_64")
    with pytest.raises(ValueError, match="CPU"):
        native.bundled_host()
    monkeypatch.setattr(native.platform, "machine", lambda: "arm64")
    monkeypatch.setattr(native.platform, "mac_ver", lambda: ("10.15.7", (), "arm64"))
    with pytest.raises(ValueError, match="requires macOS"):
        native.bundled_host()
    monkeypatch.setattr(native.platform, "mac_ver", lambda: ("27.0.1", (), "arm64"))
    manifest = host.parent / "manifest.json"
    for key, value in (
        ("framework_version", "9.9.9"),
        ("sha256", "0" * 64),
        ("size", 0),
        ("machine", "x86_64"),
        ("minimum_macos", [10, 0, 0]),
    ):
        manifest.write_text(json.dumps({**record, key: value}))
        with pytest.raises(ValueError, match="mismatch"):
            native.bundled_host()
    host.unlink()
    with pytest.raises(ValueError, match="no bundled macOS host"):
        native.bundled_host()


def test_windows_python_wrapper_is_rejected(monkeypatch):
    monkeypatch.setattr(native.sys, "platform", "win32")
    with pytest.raises(ValueError, match="before Python initialization"):
        native.bundled_host()


def test_launch_exec_preserves_venv_path_and_does_not_import_app(tmp_path, monkeypatch, symlink):
    project = tmp_path / "日本語 project"
    (project / "ui").mkdir(parents=True)
    (project / "ui/index.html").write_text("hello")
    (project / "pyproject.toml").write_text('[tool.pyyaki]\nentry="app:app"\n')
    marker = project / "imported"
    (project / "app.py").write_text(f"from pathlib import Path\nPath({str(marker)!r}).touch()")
    interpreter = tmp_path / "venv with space" / "bin/python"
    interpreter.parent.mkdir(parents=True)
    symlink(interpreter, Path(native.sys.executable))
    monkeypatch.setattr(native.sys, "executable", str(interpreter))
    monkeypatch.setattr(native, "bundled_host", lambda: tmp_path / "host")
    calls = []
    monkeypatch.setattr(os, "execv", lambda file, args: calls.append((file, args)))
    native.launch(project, None, smoke_test=True)
    assert calls == [
        (
            str(tmp_path / "host"),
            [
                str(tmp_path / "host"),
                "run",
                "--project",
                str(project),
                "--python",
                str(interpreter),
                "--smoke-test",
            ],
        )
    ]
    assert not marker.exists()
    with pytest.raises(ValueError, match="not executable"):
        native.launch(project, tmp_path / "missing-python")
