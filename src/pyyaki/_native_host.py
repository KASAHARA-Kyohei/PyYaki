"""Validate and launch the macOS wheel's prebuilt host; never search PATH for it."""

import hashlib
import json
import os
import platform
import struct
import sys
import sysconfig
from pathlib import Path

from . import __version__
from .config import load_config

HOST_DIR = Path(__file__).parent / "_native" / "macos"
CPUS = {0x100000C: "arm64", 0x1000007: "x86_64"}


def macho_info(data: bytes) -> dict:
    """Read a thin little-endian Mach-O executable's CPU and deployment target."""
    if len(data) < 32 or data[:4] != b"\xcf\xfa\xed\xfe":
        raise ValueError("host must be a thin 64-bit Mach-O executable")
    _, cpu, _, kind, count, length, _, _ = struct.unpack_from("<8I", data)
    if cpu not in CPUS or kind != 2 or count > 4096 or length > len(data) - 32:
        raise ValueError("unsupported or malformed Mach-O host")
    offset, minimum, libraries = 32, None, []
    end = offset + length
    for _ in range(count):
        if offset + 8 > end:
            raise ValueError("truncated Mach-O load command")
        command, size = struct.unpack_from("<II", data, offset)
        if size < 8 or offset + size > end:
            raise ValueError("invalid Mach-O load command size")
        if command in (0x32, 0x24):  # LC_BUILD_VERSION / LC_VERSION_MIN_MACOSX
            if size < (24 if command == 0x32 else 16):
                raise ValueError("truncated Mach-O deployment target")
            if command == 0x32 and struct.unpack_from("<I", data, offset + 8)[0] != 1:
                raise ValueError("host must target macOS")
            version = struct.unpack_from("<I", data, offset + (12 if command == 0x32 else 8))[0]
            target = [version >> 16, (version >> 8) & 255, version & 255]
            minimum = max(minimum or target, target)
        if command in (0xC, 0x80000018, 0x8000001F, 0x80000023):
            if size < 24:
                raise ValueError("truncated Mach-O library command")
            start = struct.unpack_from("<I", data, offset + 8)[0]
            if not 24 <= start < size:
                raise ValueError("invalid Mach-O library name")
            name = data[offset + start : offset + size].split(b"\0", 1)[0].decode("utf-8")
            if ".." in Path(name).parts or not name.startswith(("/usr/lib/", "/System/Library/")):
                raise ValueError(f"host links a non-system library: {name}")
            libraries.append(name)
        offset += size
    if offset != end or minimum is None or minimum[0] < 10:
        raise ValueError("host has no valid macOS deployment target")
    return {"machine": CPUS[cpu], "minimum_macos": minimum, "libraries": libraries}


def bundled_host() -> Path:
    if sys.platform != "darwin":
        raise ValueError(
            "Python CLI run supports macOS only; Windows requires a native CLI entry "
            "before Python initialization (see native/README.md)"
        )
    if (
        sys.implementation.name != "cpython"
        or sys.version_info[:2] != (3, 14)
        or sysconfig.get_config_var("Py_GIL_DISABLED")
    ):
        raise ValueError("run requires CPython 3.14 with the normal GIL")
    host, manifest = HOST_DIR / "pyyaki-host", HOST_DIR / "manifest.json"
    if not host.is_file() or not manifest.is_file():
        raise ValueError(
            "this installation has no bundled macOS host; install the matching macOS wheel. "
            "Source/editable installs can use the maintainer host (see native/README.md)"
        )
    if host.is_symlink() or manifest.is_symlink() or not os.access(host, os.X_OK):
        raise ValueError("bundled host must be an executable regular file with a regular manifest")
    record = json.loads(manifest.read_text(encoding="utf-8"))
    data = host.read_bytes()
    info = macho_info(data)
    if (
        not isinstance(record, dict)
        or record.get("format") != 1
        or record.get("framework_version") != __version__
        or record.get("size") != len(data)
        or record.get("sha256") != hashlib.sha256(data).hexdigest()
        or any(record.get(key) != info[key] for key in ("machine", "minimum_macos"))
    ):
        raise ValueError(
            "bundled host metadata, version or hash mismatch; reinstall the macOS wheel"
        )
    if info["machine"] != platform.machine():
        raise ValueError(f"bundled host CPU {info['machine']} does not match {platform.machine()}")
    version = tuple(map(int, platform.mac_ver()[0].split(".")))
    if (version + (0, 0))[:3] < tuple(info["minimum_macos"]):
        raise ValueError(f"bundled host requires macOS {'.'.join(map(str, info['minimum_macos']))}")
    return host


def launch(project: Path, python: Path | None, *, smoke_test=False, diagnostics=False) -> None:
    host = bundled_host()
    project = load_config(project).root
    # Resolving this symlink would lose the venv. Keep its absolute invocation path.
    interpreter = os.path.abspath(python if python is not None else sys.executable)
    if not Path(interpreter).is_file() or not os.access(interpreter, os.X_OK):
        raise ValueError(f"Python interpreter is not executable: {interpreter}")
    arguments = [str(host), "run", "--project", str(project), "--python", interpreter]
    if smoke_test:
        arguments.append("--smoke-test")
    if diagnostics:
        arguments.append("--diagnostics")
    # Retain the PID, main thread, terminal signals and the host's existing cleanup.
    os.execv(str(host), arguments)
