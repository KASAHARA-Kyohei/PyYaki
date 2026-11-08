"""Maintainer-only assembly of a macOS wheel from an explicit pure wheel and host."""

import argparse
import base64
import csv
import hashlib
import io
import json
import os
import platform
import stat
import subprocess
import sys
import tempfile
import zipfile
from email.parser import BytesParser
from pathlib import Path, PurePosixPath

from . import __version__
from ._native_host import macho_info

DIST = f"pyyaki-{__version__}.dist-info"
NATIVE = "pyyaki/_native/macos"


def record_hash(data):
    return "sha256=" + base64.urlsafe_b64encode(hashlib.sha256(data).digest()).rstrip(b"=").decode()


def read_wheel(path: Path) -> dict[str, bytes]:
    with zipfile.ZipFile(path) as archive:
        infos = archive.infolist()
        names = [item.filename for item in infos]
        if len(names) != len(set(names)) or sum(i.file_size for i in infos) > 128 * 1024 * 1024:
            raise ValueError("duplicate or oversized wheel contents")
        for item in infos:
            name = PurePosixPath(item.filename)
            if (
                not name.parts
                or name.as_posix() != item.filename
                or name.is_absolute()
                or ".." in name.parts
                or "\\" in item.filename
                or name.parts[0] not in ("pyyaki", DIST)
                or item.is_dir()
                or stat.S_ISLNK(item.external_attr >> 16)
                or item.filename.startswith(NATIVE + "/")
                or item.filename in (f"{DIST}/RECORD.jws", f"{DIST}/RECORD.p7s")
            ):
                raise ValueError("expected a pure PyYaki wheel with regular package/metadata files")
        contents = {name: archive.read(name) for name in names}
    metadata = BytesParser().parsebytes(contents[f"{DIST}/METADATA"])
    wheel = BytesParser().parsebytes(contents[f"{DIST}/WHEEL"])
    if metadata["Name"] != "pyyaki" or metadata["Version"] != __version__:
        raise ValueError("framework wheel version mismatch")
    if wheel["Root-Is-Purelib"] != "true" or wheel.get_all("Tag") != ["py3-none-any"]:
        raise ValueError("input must be the setuptools py3-none-any wheel")
    records = list(csv.reader(io.StringIO(contents[f"{DIST}/RECORD"].decode())))
    if any(len(row) != 3 for row in records):
        raise ValueError("invalid input wheel RECORD")
    if len(records) != len(contents) or {row[0] for row in records} != set(contents):
        raise ValueError("input wheel RECORD inventory mismatch")
    for row in records:
        name, digest, size = row
        if name == f"{DIST}/RECORD":
            if digest or size:
                raise ValueError("invalid input wheel RECORD self entry")
        elif digest != record_hash(contents[name]) or size != str(len(contents[name])):
            raise ValueError(f"input wheel RECORD hash/size mismatch: {name}")
    return contents


def command(arguments):
    return subprocess.run(arguments, check=True, text=True, capture_output=True, timeout=30).stdout


def build(wheel: Path, host: Path, output: Path) -> Path:
    if sys.platform != "darwin":
        raise ValueError(
            "macOS host wheels must be assembled on macOS; cross-building is unsupported"
        )
    input_digest = hashlib.sha256(wheel.read_bytes()).hexdigest()
    contents = read_wheel(wheel)
    if hashlib.sha256(wheel.read_bytes()).hexdigest() != input_digest:
        raise ValueError("input wheel changed during verification")
    if host.is_symlink() or not host.is_file() or not os.access(host, os.X_OK):
        raise ValueError("host must be an executable regular file")
    data = host.read_bytes()
    info = macho_info(data)
    if info["machine"] != platform.machine():
        raise ValueError("host CPU must match this Mac")
    command(["/usr/bin/codesign", "--verify", "--strict", str(host)])
    if command([str(host.resolve()), "--version"]).strip() != f"PyYaki native host {__version__}":
        raise ValueError("host version does not match the framework wheel")
    if host.read_bytes() != data:
        raise ValueError("host changed during verification")
    major, minor, _ = info["minimum_macos"]
    # Modern macOS compatibility tags use major_0; round a minor requirement upward safely.
    if major >= 11 and minor:
        major, minor = major + 1, 0
    tag = f"py3-none-macosx_{major}_{minor}_{info['machine']}"
    output.mkdir(parents=True, exist_ok=True)
    destination = output / f"pyyaki-{__version__}-{tag}.whl"
    report = destination.with_suffix(".build.json")
    if any(path.exists() or path.is_symlink() for path in (destination, report)):
        raise ValueError("output wheel or build report already exists")
    manifest = {
        "format": 1,
        "framework_version": __version__,
        **info,
        "size": len(data),
        "sha256": hashlib.sha256(data).hexdigest(),
    }
    contents[f"{NATIVE}/pyyaki-host"] = data
    contents[f"{NATIVE}/manifest.json"] = json.dumps(manifest, indent=2).encode()
    contents[f"{DIST}/WHEEL"] = (
        f"Wheel-Version: 1.0\nGenerator: pyyaki.build_macos_wheel\n"
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
    with tempfile.TemporaryDirectory(prefix=".pyyaki-wheel-", dir=output) as temporary:
        staged = Path(temporary) / destination.name
        with zipfile.ZipFile(staged, "w", compression=zipfile.ZIP_DEFLATED) as archive:
            for name, body in sorted(contents.items()):
                entry = zipfile.ZipInfo(name)
                entry.create_system = 3
                mode = 0o755 if name == f"{NATIVE}/pyyaki-host" else 0o644
                entry.external_attr = (stat.S_IFREG | mode) << 16
                archive.writestr(entry, body, compress_type=zipfile.ZIP_DEFLATED)
        details = {
            "format": 1,
            "tag": tag,
            "host": manifest,
            "codesign_verified": True,
            "input_wheel_sha256": input_digest,
            "wheel_sha256": hashlib.sha256(staged.read_bytes()).hexdigest(),
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
    parser.add_argument("--wheel", type=Path, required=True)
    parser.add_argument("--host", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    try:
        result = build(args.wheel, args.host, args.output)
    except subprocess.CalledProcessError as exc:
        parser.exit(1, f"pyyaki: host verification failed: {exc}\n{exc.stderr}\n")
    except (OSError, ValueError, KeyError, zipfile.BadZipFile, subprocess.SubprocessError) as exc:
        parser.exit(1, f"pyyaki: cannot assemble macOS host wheel: {exc}\n")
    print(result)


if __name__ == "__main__":
    main()
