"""Offline, same-architecture .app prototype from explicit, prebuilt inputs."""

import ctypes
import hashlib
import json
import os
import platform
import plistlib
import re
import shutil
import subprocess
import sys
import tempfile
import tomllib
import zipfile
from email.parser import Parser
from pathlib import Path

from . import __version__
from .config import load_config

EXCLUDED = {".git", ".venv", "__pycache__", "build", "dist", "test-results", ".pytest_cache"}


def rename_exclusive(source: Path, destination: Path) -> None:
    # Darwin <sys/stdio.h>: renamex_np(..., RENAME_EXCL=0x4). Unlike rename(),
    # this also rejects an empty directory or symlink created after our last check.
    library = ctypes.CDLL(None, use_errno=True)
    rename = library.renamex_np
    rename.argtypes = (ctypes.c_char_p, ctypes.c_char_p, ctypes.c_uint)
    rename.restype = ctypes.c_int
    if rename(os.fsencode(source), os.fsencode(destination), 0x4):
        error = ctypes.get_errno()
        raise OSError(error, os.strerror(error), str(destination))


def publish(bundle: Path, output: Path, report: Path, manifest: dict) -> None:
    staged_report = bundle.parent / "build-report.json"
    staged_report.write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
    # Same-volume hard link publishes the complete report without following or replacing
    # an existing path. Keep its inode in staging until the app has been published too.
    os.link(staged_report, report)
    try:
        rename_exclusive(bundle, output)
    except BaseException:
        try:
            owned = staged_report.stat()
            current = report.lstat()
            if (owned.st_dev, owned.st_ino) == (current.st_dev, current.st_ino):
                report.unlink()
        except FileNotFoundError:
            pass
        raise


def digest(path: Path) -> str:
    with path.open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def run(arguments, *, cwd=None):
    result = subprocess.run(arguments, cwd=cwd, text=True, capture_output=True, timeout=300)
    if result.returncode:
        raise ValueError(f"Command failed: {Path(arguments[0]).name}\n{result.stderr[-4000:]}")
    return result.stdout


def check_tree(root: Path):
    for path in root.rglob("*"):
        if path.is_symlink():
            target = os.readlink(path)
            if Path(target).is_absolute() or not path.resolve().is_relative_to(root.resolve()):
                raise ValueError(f"Non-relocatable symlink: {path.relative_to(root)}")
        elif not path.is_file() and not path.is_dir():
            raise ValueError("Only regular files, directories and internal symlinks are supported")


def collect(project: Path, patterns: list[str]) -> list[Path]:
    files = set()
    for pattern in patterns:
        if (
            not isinstance(pattern, str)
            or Path(pattern).is_absolute()
            or ".." in Path(pattern).parts
        ):
            raise ValueError("include must contain project-relative glob patterns")
        matched = False
        for path in project.glob(pattern):
            if path.is_dir():
                continue
            relative = path.relative_to(project)
            if (
                any(part in EXCLUDED or part.startswith(".venv") for part in relative.parts)
                or any(part.startswith(".") for part in relative.parts)
                or path.suffix in {".pem", ".key", ".pyc"}
            ):
                continue
            if not path.resolve().is_relative_to(project):
                raise ValueError("Application asset escapes project")
            if path.is_symlink():
                raise ValueError("Application includes must use regular files, not symlinks")
            if path.is_file():
                files.add(relative)
                matched = True
        if not matched:
            raise ValueError(f"include matched no allowed files: {pattern}")
    return sorted(files)


def check_locked_inputs(requirements: Path, wheelhouse: Path):
    records = requirements.read_text().replace("\\\n", " ").splitlines()
    count = 0
    for record in records:
        record = record.partition("#")[0].strip()
        if not record:
            continue
        if not re.fullmatch(
            r"[A-Za-z0-9][A-Za-z0-9_.-]*==[0-9][A-Za-z0-9.!+_-]*"
            r"(?:\s+--hash=sha256:[a-fA-F0-9]{64})+",
            record,
        ):
            raise ValueError("Requirements must contain only name==version and SHA-256 hashes")
        count += 1
    if not count:
        raise ValueError("Requirements lock is empty")
    if not wheelhouse.is_dir() or not any(wheelhouse.iterdir()):
        raise ValueError("Provide a nonempty, local wheelhouse")
    if any(p.is_symlink() or not p.is_file() or p.suffix != ".whl" for p in wheelhouse.iterdir()):
        raise ValueError("Wheelhouse must contain only regular .whl files")


def probe(python: Path) -> dict:
    return json.loads(
        run(
            [
                str(python),
                "-I",
                "-B",
                "-S",
                "-c",
                "import sys,sysconfig,platform,json;print(json.dumps(dict("
                "version=list(sys.version_info[:3]),implementation=sys.implementation.name,"
                "gil_disabled=bool(sysconfig.get_config_var('Py_GIL_DISABLED')),"
                "machine=platform.machine(),prefix=sys.base_prefix)))",
            ]
        )
    )


def build(
    project: Path,
    host: Path,
    runtime: Path,
    wheelhouse: Path,
    framework_wheel: Path,
    requirements: Path,
    output: Path | None = None,
) -> Path:
    if sys.platform != "darwin":
        raise ValueError("build macos requires macOS; cross-building is not supported")
    config = load_config(project)
    project = config.root
    settings = tomllib.loads((project / "pyproject.toml").read_text())["tool"]["pyyaki"]
    name, app_id = settings.get("name", "PyYaki"), settings.get("app-id")
    version = settings.get("version", "0.1.0")
    if (
        not isinstance(name, str)
        or not name.strip()
        or len(name.encode("utf-8")) > 100
        or name in {".", ".."}
        or any(c in name for c in "/\\\0")
    ):
        raise ValueError("name must be a nonempty application filename")
    if not isinstance(app_id, str) or not re.fullmatch(
        r"[A-Za-z0-9-]+(?:\.[A-Za-z0-9-]+)+", app_id
    ):
        raise ValueError("app-id must be a reverse-DNS identifier")
    if not isinstance(version, str) or not re.fullmatch(r"\d+\.\d+\.\d+", version):
        raise ValueError("version must use three numeric components")
    output = Path(os.path.abspath(output or project / "dist" / f"{name}.app"))
    report = output.with_suffix(".build.json")
    if (
        output.suffix != ".app"
        or output.exists()
        or report.exists()
        or output.is_symlink()
        or report.is_symlink()
    ):
        raise ValueError("Output must be a new .app path with no existing build report")
    host, runtime, wheelhouse, framework_wheel, requirements = (
        path.resolve(strict=True)
        for path in (host, runtime, wheelhouse, framework_wheel, requirements)
    )
    check_locked_inputs(requirements, wheelhouse)
    if run([str(host), "--version"]).strip() != f"PyYaki native host {__version__}":
        raise ValueError("Prebuilt host version does not match this framework")
    with zipfile.ZipFile(framework_wheel) as wheel:
        metadata = Parser().parsestr(
            wheel.read(f"pyyaki-{__version__}.dist-info/METADATA").decode()
        )
        if metadata["Name"] != "pyyaki" or metadata["Version"] != __version__:
            raise ValueError("Framework wheel version mismatch")
    check_tree(runtime)
    interpreter = runtime / "bin/python3"
    info = probe(interpreter)
    if (
        info["version"][:2] != [3, 14]
        or info["implementation"] != "cpython"
        or info["gil_disabled"]
        or info["machine"] != platform.machine()
        or Path(info["prefix"]).resolve() != runtime
    ):
        raise ValueError("Use a standalone CPython 3.14 GIL runtime for this Mac's architecture")
    macos = settings.get("build", {}).get("macos", {})
    module = Path(*config.entry.split(":")[0].split("."))
    entry_file = module.with_suffix(".py")
    if not (project / entry_file).is_file():
        entry_file = module / "__init__.py"
    patterns = macos.get(
        "include", [entry_file.as_posix(), f"{config.ui_dir.relative_to(project).as_posix()}/**/*"]
    )
    if not isinstance(patterns, list) or not patterns:
        raise ValueError("build.macos.include must be a nonempty array")
    assets = collect(project, patterns)
    if (config.ui_dir / config.ui_entry).relative_to(project) not in assets:
        raise ValueError("include must contain the configured UI entry")
    output.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix=".pyyaki-", dir=output.parent) as temporary:
        bundle = Path(temporary) / output.name
        contents = bundle / "Contents"
        resources = contents / "Resources"
        app = resources / "app"
        app.mkdir(parents=True)
        (contents / "MacOS").mkdir()
        shutil.copy2(host, contents / "MacOS/PyYaki")
        shutil.copytree(
            runtime,
            resources / "python",
            symlinks=True,
            ignore=shutil.ignore_patterns("__pycache__", "*.pyc"),
        )
        python = resources / "python/bin/python3"
        if probe(python)["prefix"] != str(resources / "python"):
            raise ValueError("Python cannot relocate into the app bundle")
        site = resources / "python/lib/python3.14/site-packages"
        site.mkdir(parents=True, exist_ok=True)
        run(
            [
                sys.executable,
                "-I",
                "-B",
                "-m",
                "pip",
                "--isolated",
                "--disable-pip-version-check",
                "install",
                "--no-index",
                "--find-links",
                str(wheelhouse),
                "--only-binary=:all:",
                "--require-hashes",
                "--no-compile",
                "--target",
                str(site),
                "-r",
                str(requirements),
            ]
        )
        run(
            [
                sys.executable,
                "-I",
                "-m",
                "pip",
                "--isolated",
                "--disable-pip-version-check",
                "install",
                "--no-index",
                "--no-deps",
                "--no-compile",
                "--target",
                str(site),
                str(framework_wheel),
            ]
        )
        if list(site.rglob("*.pth")) or any(site.rglob("sitecustomize.py")):
            raise ValueError(".pth and sitecustomize dependencies are not supported")
        for relative in assets:
            if relative == Path("pyproject.toml"):
                raise ValueError("pyproject.toml is generated; do not include it")
            target = app / relative
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(project / relative, target)

        # Preserve only runtime settings, not unrelated tool configuration or credentials.
        def quote(value):
            return json.dumps(value, ensure_ascii=False)

        text = (
            f"[tool.pyyaki]\nentry={quote(config.entry)}\n"
            f"ui-dir={quote(config.ui_dir.relative_to(project).as_posix())}\n"
            f"ui-entry={quote(config.ui_entry)}\n[tool.pyyaki.window]\n"
        )
        window = settings.get("window", {})
        if not isinstance(window, dict) or not isinstance(window.get("title", name), str):
            raise ValueError("Invalid window configuration")
        for key, default, minimum in (("width", 960, 320), ("height", 720, 240)):
            value = window.get(key, default)
            if type(value) is not int or not minimum <= value <= 4096:
                raise ValueError(f"Invalid window {key}")
        for key, value in {
            "title": window.get("title", name),
            "width": window.get("width", 960),
            "height": window.get("height", 720),
        }.items():
            text += f"{key}={quote(value)}\n"
        (app / "pyproject.toml").write_text(text)
        (resources / "pyyaki-bundle.json").write_text(
            json.dumps({"format": 1, "framework_version": __version__})
        )
        with (contents / "Info.plist").open("wb") as stream:
            plistlib.dump(
                {
                    "CFBundleIdentifier": app_id,
                    "CFBundleName": name,
                    "CFBundleExecutable": "PyYaki",
                    "CFBundlePackageType": "APPL",
                    "CFBundleShortVersionString": version,
                    "CFBundleVersion": version,
                    "NSHighResolutionCapable": True,
                },
                stream,
            )
        run(
            [
                str(python),
                "-I",
                "-B",
                "-c",
                "import sys,importlib;from pathlib import Path;from pyyaki import App;"
                "from pyyaki.config import load_config;c=load_config(Path(sys.argv[1]));"
                "sys.path.insert(0,str(c.root));m,n=c.entry.split(':');"
                "assert isinstance(getattr(importlib.import_module(m),n),App)",
                str(app),
            ],
            cwd=app,
        )
        check_tree(bundle)
        machos = []
        for path in bundle.rglob("*"):
            if not path.is_file() or path.is_symlink():
                continue
            with path.open("rb") as stream:
                magic = stream.read(4)
            if magic not in {
                b"\xcf\xfa\xed\xfe",
                b"\xfe\xed\xfa\xcf",
                b"\xca\xfe\xba\xbe",
                b"\xbe\xba\xfe\xca",
            }:
                continue
            if platform.machine() not in run(["/usr/bin/lipo", "-archs", str(path)]).split():
                raise ValueError(f"Mach-O architecture mismatch: {path.relative_to(bundle)}")
            for line in run(["/usr/bin/otool", "-L", str(path)]).splitlines()[1:]:
                dependency = line.strip().split(" (", 1)[0]
                if dependency.startswith("/") and not dependency.startswith(
                    ("/usr/lib/", "/System/Library/")
                ):
                    raise ValueError(f"External library in bundle: {dependency}")
            machos.append(path)
        for path in machos:
            run(["/usr/bin/codesign", "--force", "--sign", "-", str(path)])
        run(["/usr/bin/codesign", "--force", "--sign", "-", str(bundle)])
        run(["/usr/bin/codesign", "--verify", "--deep", "--strict", str(bundle)])
        manifest = {
            "format": 1,
            "framework_version": __version__,
            "app_id": app_id,
            "python": info,
            "signing": "ad-hoc; not notarized",
            "files": {},
            "inputs": {
                "host_sha256": digest(host),
                "framework_wheel_sha256": digest(framework_wheel),
                "requirements_sha256": digest(requirements),
                "wheels": {path.name: digest(path) for path in sorted(wheelhouse.glob("*.whl"))},
            },
        }
        for path in sorted(bundle.rglob("*")):
            relative = path.relative_to(bundle).as_posix()
            if path.is_symlink():
                manifest["files"][relative] = {"symlink": os.readlink(path)}
            elif path.is_file():
                manifest["files"][relative] = {"size": path.stat().st_size, "sha256": digest(path)}
        publish(bundle, output, report, manifest)
    return output
