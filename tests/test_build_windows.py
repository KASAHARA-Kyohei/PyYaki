import hashlib
import json
import struct

import pytest

from pyyaki.build_windows import MAGIC, assemble, build, regular_tree, unsigned_host, valid_path


def host(path):
    data = bytearray(512)
    data[:2] = b"MZ"
    struct.pack_into("<I", data, 60, 64)
    data[64:70] = b"PE\0\0\x64\x86"
    struct.pack_into("<H", data, 70, 1)
    struct.pack_into("<H", data, 84, 240)
    struct.pack_into("<H", data, 88, 0x20B)
    struct.pack_into("<II", data, 344, 128, 384)
    path.write_bytes(data)
    return path


def test_assembly_tracks_bytes_and_rejects_existing_outputs(tmp_path):
    template = host(tmp_path / "host.exe")
    payload = tmp_path / "payload"
    payload.mkdir()
    (payload / "file.dat").write_bytes(b"payload")
    output = tmp_path / "app.exe"
    index = assemble(template, payload, output, "com.example.app")
    data = output.read_bytes()
    magic, offset, size, sha = struct.unpack("<8sQQ32s8x", data[-64:])
    assert magic == MAGIC and len(data) % 8 == 0
    assert hashlib.sha256(data[offset : offset + size]).digest() == sha
    assert json.loads(data[offset : offset + size]) == index
    entry = index["files"][0]
    assert data[entry["offset"] : entry["offset"] + entry["size"]] == b"payload"
    with pytest.raises(FileExistsError):
        assemble(template, payload, output, "com.example.app")
    assert output.read_bytes() == data


@pytest.mark.parametrize(
    "path", ["../x", "C:/x", "x:y", "a\\b", "x.", "NUL.txt", "COM1", "a//x", "ui/鯛.html"]
)
def test_windows_namespace_paths_rejected(path):
    assert not valid_path(path)


def test_template_overlay_and_signature_rejected(tmp_path):
    template = host(tmp_path / "host.exe")
    unsigned_host(template)
    data = template.read_bytes()
    template.write_bytes(data + b"overlay")
    with pytest.raises(ValueError, match="overlay"):
        unsigned_host(template)
    signed = bytearray(data)
    struct.pack_into("<II", signed, 232, 512, 8)
    template.write_bytes(signed + bytes(8))
    with pytest.raises(ValueError, match="unsigned"):
        unsigned_host(template)


def test_tree_rejects_case_collisions_and_reparse_points(tmp_path, symlink):
    target = tmp_path / "target"
    target.mkdir()
    (target / "file").write_bytes(b"content")
    linked = tmp_path / "link"
    symlink(linked, target / "file")
    with pytest.raises(ValueError, match="Reparse"):
        regular_tree(tmp_path)


def test_cross_build_is_rejected(tmp_path, monkeypatch):
    monkeypatch.setattr("pyyaki.build_windows.sys.platform", "darwin")
    with pytest.raises(ValueError, match="requires Windows"):
        build(tmp_path, tmp_path, tmp_path, tmp_path, tmp_path, tmp_path, tmp_path)
