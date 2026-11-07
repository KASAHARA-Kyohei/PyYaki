"""Offline Windows x64 onefile prototype, using explicit prebuilt local inputs.

Format 1 deliberately stores uncompressed blobs. Compression and signing remain
separate validation gates. Building imports the supplied app: use trusted inputs.
"""

import hashlib
import json
import os
import re
import shutil
import struct
import sys
import tempfile
import tomllib
import zipfile
from email.parser import Parser
from pathlib import Path

from . import __version__
from .build_macos import check_locked_inputs, collect, digest, probe, run
from .config import load_config

MAGIC = b"PYYAKI01"
LIMIT = 2 * 1024**3
FILE_LIMIT = 512 * 1024**2
INDEX_LIMIT = 8 * 1024**2


def valid_path(path: str) -> bool:
    if not path or len(path) > 220 or not path.isascii():
        return False
    for part in path.split("/"):
        base = part.split(".")[0].upper()
        if (
            not part
            or part in {".", ".."}
            or part.endswith((".", " "))
            or any(ord(ch) < 32 or ch in '\\:<>"|?*' for ch in part)
            or base in {"CON", "PRN", "AUX", "NUL", "CONIN$", "CONOUT$"}
            or re.fullmatch(r"(?:COM|LPT)[1-9]", base)
        ):
            return False
    return True


def regular_tree(root: Path) -> list[Path]:
    """Reject ALL reparse points, including junctions. Never traverse them."""
    result = []
    pending = [root]
    names = set()
    while pending:
        path = pending.pop()
        if path.is_symlink() or getattr(path.lstat(), "st_file_attributes", 0) & 0x400:
            raise ValueError(f"Reparse points are not supported: {path}")
        if path != root:
            name = path.relative_to(root).as_posix()
            if not valid_path(name) or name.lower() in names:
                raise ValueError(f"Unsafe, unsupported or colliding Windows path: {name}")
            names.add(name.lower())
        if path.is_dir():
            pending.extend(path.iterdir())
        elif path.is_file():
            if path.stat().st_size > FILE_LIMIT:
                raise ValueError("Onefile per-file limit is 512 MiB")
            result.append(path)
        else:
            raise ValueError("Only regular files and directories are supported")
        if len(names) > 20_000 or len(result) > 10_000:
            raise ValueError("Onefile file/directory limit exceeded")
    return sorted(result)


def unsigned_host(path: Path) -> None:
    with path.open("rb") as stream:
        if stream.read(2) != b"MZ":
            raise ValueError("Host must be an unsigned Windows x64 PE32+ template")
        stream.seek(60)
        pe = struct.unpack("<I", stream.read(4))[0]
        if pe > 1024**2:
            raise ValueError("Invalid PE offset")
        stream.seek(pe)
        if stream.read(6) != b"PE\0\0\x64\x86":
            raise ValueError("Host must be a Windows x64 PE template")
        sections = struct.unpack("<H", stream.read(2))[0]
        stream.seek(pe + 20)
        optional = struct.unpack("<H", stream.read(2))[0]
        stream.seek(pe + 24)
        if stream.read(2) != b"\x0b\x02" or optional < 152 or not 1 <= sections <= 96:
            raise ValueError("Host must use PE32+")
        stream.seek(pe + 24 + 144)
        if stream.read(8) != bytes(8):
            raise ValueError("Use an unsigned template; sign only after assembly")
        end = pe + 24 + optional + sections * 40
        for n in range(sections):
            stream.seek(pe + 24 + optional + n * 40 + 16)
            size, offset = struct.unpack("<II", stream.read(8))
            end = max(end, size + offset)
        if end != path.stat().st_size:
            raise ValueError("Host template must have no existing overlay")


def assemble(host: Path, payload: Path, output: Path, app_id: str) -> dict:
    unsigned_host(host)
    if not re.fullmatch(r"[A-Za-z0-9-]+(?:\.[A-Za-z0-9-]+)+", app_id) or len(app_id) > 120:
        raise ValueError("app-id must be a reverse-DNS identifier, at most 120 ASCII characters")
    files = regular_tree(payload)
    index = {
        "format": 1,
        "framework_version": __version__,
        "app_id": app_id,
        "host_size": host.stat().st_size,
        "files": [],
    }
    if sum(path.stat().st_size for path in files) + index["host_size"] + INDEX_LIMIT + 64 > LIMIT:
        raise ValueError("Onefile prototype limit is 2 GiB")
    with output.open("xb") as stream:
        with host.open("rb") as source:
            shutil.copyfileobj(source, stream, 1024**2)
        for path in files:
            offset = stream.tell()
            expected_size = path.stat().st_size
            if expected_size > FILE_LIMIT or offset + expected_size + INDEX_LIMIT + 64 > LIMIT:
                raise ValueError("Input changed while building or exceeded package size limits")
            hasher = hashlib.sha256()
            with path.open("rb") as source:
                remaining = expected_size
                while remaining:
                    block = source.read(min(remaining, 1024**2))
                    if not block:
                        raise ValueError("Input shrank while building")
                    stream.write(block)
                    hasher.update(block)
                    remaining -= len(block)
                if source.read(1):
                    raise ValueError("Input grew while building")
            size = stream.tell() - offset
            if size != path.stat().st_size:
                raise ValueError("Input changed while building or exceeded per-file limit")
            index["files"].append(
                {
                    "path": path.relative_to(payload).as_posix(),
                    "offset": offset,
                    "size": size,
                    "sha256": hasher.hexdigest(),
                }
            )
        offset = stream.tell()
        data = json.dumps(index, ensure_ascii=True, separators=(",", ":")).encode("utf-8")
        # Index whitespace pads the footer to an 8-byte boundary, before a future certificate.
        data += b" " * (-(offset + len(data)) % 8)
        if not data or len(data) > INDEX_LIMIT:
            raise ValueError("Onefile index limit exceeded")
        stream.write(data)
        stream.write(
            struct.pack("<8sQQ32s8x", MAGIC, offset, len(data), hashlib.sha256(data).digest())
        )
        stream.flush()
        os.fsync(stream.fileno())
    return index


def build(project, host, runtime, webview2, wheelhouse, framework_wheel, requirements, output=None):
    if sys.platform != "win32" or sys.maxsize <= 2**32:
        raise ValueError("build windows requires Windows x64 Python; cross-building is unsupported")
    config = load_config(project)
    project = config.root
    settings = tomllib.loads((project / "pyproject.toml").read_text(encoding="utf-8"))["tool"][
        "pyyaki"
    ]
    name, app_id = settings.get("name", "PyYaki"), settings.get("app-id", "")
    if not isinstance(name, str) or not valid_path(name) or "/" in name:
        raise ValueError("name must be a safe ASCII Windows filename in this prototype")
    output = Path(os.path.abspath(output or project / "dist" / f"{name}.exe"))
    report = output.with_suffix(".build.json")
    if output.suffix.lower() != ".exe" or any(os.path.lexists(p) for p in (output, report)):
        raise ValueError("Output must be a new .exe path with no existing build report")
    host, runtime, webview2, wheelhouse, framework_wheel, requirements = (
        path.resolve(strict=True)
        for path in (host, runtime, webview2, wheelhouse, framework_wheel, requirements)
    )
    check_locked_inputs(requirements, wheelhouse)
    unsigned_host(host)
    if run([str(host), "--version"]).strip() != f"PyYaki native host {__version__}":
        raise ValueError("Prebuilt host and framework version mismatch")
    if run([str(host), "--onefile-format"]).strip() != MAGIC.decode():
        raise ValueError("Host does not support onefile format 1")
    with zipfile.ZipFile(framework_wheel) as wheel:
        metadata = Parser().parsestr(
            wheel.read(f"pyyaki-{__version__}.dist-info/METADATA").decode()
        )
        if metadata["Name"] != "pyyaki" or metadata["Version"] != __version__:
            raise ValueError("Framework wheel version mismatch")
    regular_tree(runtime)
    regular_tree(webview2)
    unsigned_python = runtime / "python.exe"
    info = probe(unsigned_python)
    if (
        info["version"][:2] != [3, 14]
        or info["implementation"] != "cpython"
        or info["gil_disabled"]
        or info["machine"].lower() != "amd64"
    ):
        raise ValueError("Use a CPython 3.14 x64 normal GIL embeddable runtime")
    if not (webview2 / "msedgewebview2.exe").is_file():
        raise ValueError("Provide a local x64 Fixed Version WebView2 directory")
    module = Path(*config.entry.split(":")[0].split("."))
    entry = module.with_suffix(".py")
    if not (project / entry).is_file():
        entry = module / "__init__.py"
    patterns = (
        settings.get("build", {})
        .get("windows", {})
        .get("include", [entry.as_posix(), f"{config.ui_dir.relative_to(project).as_posix()}/**/*"])
    )
    if not isinstance(patterns, list) or not patterns:
        raise ValueError("build.windows.include must be a nonempty array")
    assets = collect(project, patterns)
    if (config.ui_dir / config.ui_entry).relative_to(project) not in assets or entry not in assets:
        raise ValueError("include must contain application and UI entries")
    output.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix=".pyyaki-", dir=output.parent) as temporary:
        staging = Path(temporary)
        payload = staging / "payload"
        shutil.copytree(
            runtime,
            payload / "runtime/python",
            ignore=shutil.ignore_patterns("__pycache__", "*.pyc"),
        )
        shutil.copytree(webview2, payload / "runtime/webview2")
        packages = payload / "packages"
        pip = [
            sys.executable,
            "-I",
            "-B",
            "-m",
            "pip",
            "--isolated",
            "--disable-pip-version-check",
            "install",
            "--no-index",
            "--no-compile",
            "--target",
            str(packages),
        ]
        run(
            [
                *pip,
                "--only-binary=:all:",
                "--require-hashes",
                "--find-links",
                str(wheelhouse),
                "-r",
                str(requirements),
            ]
        )
        run([*pip, "--no-deps", str(framework_wheel)])
        if list(packages.rglob("*.pth")) or list(packages.rglob("sitecustomize.py")):
            raise ValueError(".pth and sitecustomize dependencies are unsupported")
        (payload / "runtime/python/python314._pth").write_text(
            "python314.zip\n.\n../../packages\n", encoding="utf-8"
        )
        app = payload / "app"
        for relative in assets:
            if relative == Path("pyproject.toml"):
                raise ValueError("pyproject.toml is generated; do not include it")
            target = app / relative
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(project / relative, target)
        window = settings.get("window", {})
        title = window.get("title", name)
        if not isinstance(title, str):
            raise ValueError("Invalid window title")
        for key, default, minimum in (("width", 960, 320), ("height", 720, 240)):
            if (
                type(window.get(key, default)) is not int
                or not minimum <= window.get(key, default) <= 4096
            ):
                raise ValueError(f"Invalid window {key}")
        values = {
            "entry": config.entry,
            "ui-dir": config.ui_dir.relative_to(project).as_posix(),
            "ui-entry": config.ui_entry,
        }
        text = "[tool.pyyaki]\n" + "".join(
            f"{k}={json.dumps(v, ensure_ascii=False)}\n" for k, v in values.items()
        )
        text += "[tool.pyyaki.window]\n" + "".join(
            f"{k}={json.dumps(v, ensure_ascii=False)}\n"
            for k, v in {
                "title": title,
                "width": window.get("width", 960),
                "height": window.get("height", 720),
            }.items()
        )
        (app / "pyproject.toml").write_text(text, encoding="utf-8")
        run(
            [
                str(payload / "runtime/python/python.exe"),
                "-I",
                "-B",
                "-c",
                "import sys,importlib;from pathlib import Path;from pyyaki import App;"
                "from pyyaki.config import load_config;c=load_config(Path(sys.argv[1]));"
                "sys.path.insert(0,str(c.root));m,n=c.entry.split(':');"
                "assert isinstance(getattr(importlib.import_module(m),n),App)",
                str(app),
            ],
            cwd=staging,
        )
        staged_exe = staging / "app.exe"
        index = assemble(host, payload, staged_exe, app_id)
        manifest = {
            "format": 1,
            "framework_version": __version__,
            "app_id": app_id,
            "compression": "none (prototype)",
            "signing": "unsigned",
            "python": info,
            "host_sha256": digest(host),
            "framework_wheel_sha256": digest(framework_wheel),
            "requirements_sha256": digest(requirements),
            "wheels": {p.name: digest(p) for p in sorted(wheelhouse.glob("*.whl"))},
            "index": index,
            "exe_sha256": digest(staged_exe),
            "exe_bytes": staged_exe.stat().st_size,
        }
        staged_report = staging / "build.json"
        staged_report.write_text(
            json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        # Publish complete files without replacement. Keep the report inode for safe rollback.
        os.link(staged_report, report)
        try:
            os.link(staged_exe, output)
        except BaseException:
            if report.stat().st_ino == staged_report.stat().st_ino:
                report.unlink()
            raise
    return output
