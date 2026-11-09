import csv
import io
import json
import struct
import zipfile

import pytest
from test_build_macos_wheel import pure_wheel

from pyyaki import __version__
from pyyaki import build_windows_wheel as builder


def pe(subsystem=3):
    data = bytearray(256)
    data[:2] = b"MZ"
    struct.pack_into("<I", data, 60, 64)
    data[64:70] = b"PE\0\0\x64\x86"
    struct.pack_into("<H", data, 88, 0x20B)
    struct.pack_into("<H", data, 156, subsystem)
    return bytes(data)


@pytest.fixture
def inputs(tmp_path, monkeypatch):
    monkeypatch.setattr(builder.sys, "platform", "win32")
    monkeypatch.setattr(builder.platform, "machine", lambda: "AMD64")
    wheel = pure_wheel(tmp_path / "input.whl")
    host, cli = tmp_path / "host.exe", tmp_path / "cli.exe"
    host.write_bytes(pe(2))
    cli.write_bytes(pe())

    def run(arguments, **kwargs):
        from subprocess import CompletedProcess

        text = f"PyYaki {'native host ' if arguments[0] == str(host.resolve()) else ''}"
        return CompletedProcess(arguments, 0, f"{text}{__version__}\n".encode())

    monkeypatch.setattr(builder.subprocess, "run", run)
    return wheel, host, cli, tmp_path / "output"


def test_native_script_and_host_inventory_hashes_tag_and_no_overwrite(inputs):
    wheel, host, cli, output = inputs
    result = builder.build(*inputs)
    with zipfile.ZipFile(result) as archive:
        assert archive.read(builder.SCRIPT) == cli.read_bytes()
        assert archive.read(f"{builder.NATIVE}/pyyaki-host.exe") == host.read_bytes()
        manifest = json.loads(archive.read(f"{builder.NATIVE}/manifest.json"))
        assert manifest["framework_version"] == __version__
        assert "Tag: py3-none-win_amd64" in archive.read(f"{builder.DIST}/WHEEL").decode()
        rows = list(csv.reader(io.StringIO(archive.read(f"{builder.DIST}/RECORD").decode())))
        assert {r[0] for r in rows} == set(archive.namelist())
        for name, digest, size in rows:
            if name.endswith("/RECORD"):
                assert digest == size == ""
            else:
                assert digest == builder.record_hash(archive.read(name))
                assert size == str(len(archive.read(name)))
    before = result.read_bytes()
    with pytest.raises(ValueError, match="already exists"):
        builder.build(*inputs)
    assert result.read_bytes() == before


@pytest.mark.parametrize("bad", [b"", b"MZ", pe()[:100], pe().replace(b"\x64\x86", b"\x64\xaa")])
def test_malformed_or_wrong_cpu_pe_rejected(bad):
    with pytest.raises(ValueError):
        builder.pe_subsystem(bad)


def test_gui_cli_subsystem_cannot_be_swapped(inputs):
    inputs[1].write_bytes(pe(3))
    with pytest.raises(ValueError, match="subsystem"):
        builder.build(*inputs)


def test_version_mismatch_rejected(inputs, monkeypatch):
    from subprocess import CompletedProcess

    monkeypatch.setattr(
        builder.subprocess, "run", lambda *a, **kw: CompletedProcess(a, 0, b"PyYaki other")
    )
    with pytest.raises(ValueError, match="version mismatch"):
        builder.build(*inputs)


def test_console_entry_removed_without_deleting_other_entries(inputs, monkeypatch):
    read = builder.read_wheel

    def entries(path):
        contents = read(path)
        contents[f"{builder.DIST}/entry_points.txt"] = (
            b"[console_scripts]\npyyaki = pyyaki.cli:main\nother = other:main\n"
        )
        return contents

    monkeypatch.setattr(builder, "read_wheel", entries)
    with zipfile.ZipFile(builder.build(*inputs)) as archive:
        text = archive.read(f"{builder.DIST}/entry_points.txt").decode()
        assert "pyyaki =" not in text and "other = other:main" in text
