"""Maintainer-only assembly of a Windows native CLI wheel from explicit local assets."""

import argparse
import configparser
import csv
import hashlib
import io
import json
import os
import platform
import stat
import struct
import subprocess
import sys
import tempfile
import zipfile
from pathlib import Path

from . import __version__
from .build_macos_wheel import DIST, read_wheel, record_hash

NATIVE = "pyyaki/_native/windows"
SCRIPT = f"pyyaki-{__version__}.data/scripts/pyyaki.exe"


def pe_subsystem(data: bytes) -> int:
    if len(data) < 64 or data[:2] != b"MZ":
        raise ValueError("expected Windows x64 PE32+ executable")
    offset = struct.unpack_from("<I", data, 60)[0]
    if (
        offset > 1024 * 1024
        or offset + 94 > len(data)
        or data[offset : offset + 4] != b"PE\0\0"
        or struct.unpack_from("<H", data, offset + 4)[0] != 0x8664
        or struct.unpack_from("<H", data, offset + 24)[0] != 0x20B
    ):
        raise ValueError("expected Windows x64 PE32+ executable")
    return struct.unpack_from("<H", data, offset + 92)[0]


def binary(path, subsystem, version):
    if path.is_symlink() or not path.is_file() or path.stat().st_size > 128 * 1024 * 1024:
        raise ValueError("native binary must be a bounded regular file")
    data = path.read_bytes()
    if pe_subsystem(data) != subsystem:
        raise ValueError("native binary has the wrong PE subsystem")
    result = subprocess.run(
        [str(path.resolve()), "--version"], check=True, capture_output=True, timeout=30
    )
    if result.stdout.decode("utf-8").strip() != version or path.read_bytes() != data:
        raise ValueError("native binary version mismatch or changed during verification")
    return data


def build(wheel: Path, host: Path, cli: Path, output: Path) -> Path:
    if sys.platform != "win32" or platform.machine().upper() != "AMD64":
        raise ValueError("Windows native wheels must be assembled on Windows x64")
    digest = hashlib.sha256(wheel.read_bytes()).hexdigest()
    contents = read_wheel(wheel)
    if any(name.startswith("pyyaki/_native/") for name in contents):
        raise ValueError("input must not already contain native assets")
    if hashlib.sha256(wheel.read_bytes()).hexdigest() != digest:
        raise ValueError("input wheel changed during verification")
    host_data = binary(host, 2, f"PyYaki native host {__version__}")
    cli_data = binary(cli, 3, f"PyYaki {__version__}")
    entry_name = f"{DIST}/entry_points.txt"
    entries = configparser.ConfigParser()
    entries.read_string(contents.get(entry_name, b"").decode())
    for section in ("console_scripts", "gui_scripts"):
        if entries.has_section(section):
            entries.remove_option(section, "pyyaki")
            if not entries[section]:
                entries.remove_section(section)
    text = io.StringIO()
    entries.write(text)
    if text.getvalue().strip():
        contents[entry_name] = text.getvalue().encode()
    else:
        contents.pop(entry_name, None)
    manifest = {
        "format": 1,
        "framework_version": __version__,
        "machine": "AMD64",
        "cli": {"size": len(cli_data), "sha256": hashlib.sha256(cli_data).hexdigest()},
        "host": {"size": len(host_data), "sha256": hashlib.sha256(host_data).hexdigest()},
    }
    contents[SCRIPT] = cli_data
    contents[f"{NATIVE}/pyyaki-host.exe"] = host_data
    contents[f"{NATIVE}/manifest.json"] = json.dumps(manifest, indent=2).encode()
    tag = "py3-none-win_amd64"
    contents[f"{DIST}/WHEEL"] = (
        f"Wheel-Version: 1.0\nGenerator: pyyaki.build_windows_wheel\n"
        f"Root-Is-Purelib: false\nTag: {tag}\n"
    ).encode()
    rows = [
        [name, record_hash(body), str(len(body))]
        for name, body in sorted(contents.items())
        if name != f"{DIST}/RECORD"
    ]
    rows.append([f"{DIST}/RECORD", "", ""])
    buffer = io.StringIO(newline="")
    csv.writer(buffer).writerows(rows)
    contents[f"{DIST}/RECORD"] = buffer.getvalue().encode()
    output.mkdir(parents=True, exist_ok=True)
    destination = output / f"pyyaki-{__version__}-{tag}.whl"
    report = destination.with_suffix(".build.json")
    if any(path.exists() or path.is_symlink() for path in (destination, report)):
        raise ValueError("output wheel or build report already exists")
    with tempfile.TemporaryDirectory(prefix=".pyyaki-wheel-", dir=output) as temporary:
        staged = Path(temporary) / destination.name
        with zipfile.ZipFile(staged, "w", compression=zipfile.ZIP_DEFLATED) as archive:
            for name, body in sorted(contents.items()):
                entry = zipfile.ZipInfo(name)
                entry.create_system = 3
                entry.external_attr = (stat.S_IFREG | (0o755 if name == SCRIPT else 0o644)) << 16
                archive.writestr(entry, body, compress_type=zipfile.ZIP_DEFLATED)
        details = {
            "format": 1,
            "tag": tag,
            "manifest": manifest,
            "input_wheel_sha256": digest,
            "wheel_sha256": hashlib.sha256(staged.read_bytes()).hexdigest(),
            "authenticode_verified": False,
        }
        staged_report = Path(temporary) / "report.json"
        staged_report.write_text(json.dumps(details, indent=2), encoding="utf-8")
        os.link(staged_report, report)
        try:
            os.link(staged, destination)
        except BaseException:
            if report.stat().st_ino == staged_report.stat().st_ino:
                report.unlink()
            raise
    return destination


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ("wheel", "host", "cli", "output"):
        parser.add_argument(f"--{name}", type=Path, required=True)
    args = parser.parse_args()
    try:
        result = build(args.wheel, args.host, args.cli, args.output)
    except (OSError, ValueError, KeyError, zipfile.BadZipFile, subprocess.SubprocessError) as exc:
        parser.exit(1, f"pyyaki: cannot assemble Windows native wheel: {exc}\n")
    print(result)


if __name__ == "__main__":
    main()
