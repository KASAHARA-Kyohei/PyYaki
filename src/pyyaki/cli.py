"""Loopback-only CLI for the initial web prototype."""

import argparse
import errno
import socket
import struct
import subprocess
import sys
import zipfile
from pathlib import Path

from . import __version__
from .config import load_config


def main() -> int:
    parser = argparse.ArgumentParser(prog="pyyaki")
    parser.add_argument("--version", action="version", version=f"PyYaki {__version__}")
    commands = parser.add_subparsers(dest="command", required=True)
    dev = commands.add_parser("dev", help="Start the local web prototype")
    dev.add_argument("--port", type=int, default=8000)
    dev.add_argument(
        "--no-reload", action="store_true", help="Disable automatic development reload"
    )
    run = commands.add_parser("run", help="Run the bundled macOS native host")
    run.add_argument("--project", type=Path, default=Path.cwd())
    run.add_argument("--python", type=Path)
    diagnostics = run.add_mutually_exclusive_group()
    diagnostics.add_argument("--diagnostics", action="store_true")
    diagnostics.add_argument("--smoke-test", action="store_true", help=argparse.SUPPRESS)
    build = commands.add_parser("build", help="Build a local application bundle")
    build.add_argument("target", nargs="?", choices=["windows", "macos"])
    build.add_argument("--project", type=Path, default=Path.cwd())
    build.add_argument("--host", type=Path)
    build.add_argument("--runtime", type=Path)
    build.add_argument("--webview2", type=Path)
    build.add_argument("--wheelhouse", type=Path)
    build.add_argument("--framework-wheel", type=Path)
    build.add_argument("--requirements", type=Path)
    build.add_argument("--output", type=Path)
    args = parser.parse_args()
    if args.command == "run":
        from ._native_host import launch

        try:
            launch(
                args.project, args.python, smoke_test=args.smoke_test, diagnostics=args.diagnostics
            )
        except (OSError, ValueError, TypeError, AttributeError) as exc:
            parser.exit(1, f"pyyaki: cannot run native application: {exc}\n")
        return 0
    if args.command == "build" and args.target == "windows":
        required = ("host", "runtime", "webview2", "wheelhouse", "framework_wheel", "requirements")
        if any(getattr(args, name) is None for name in required):
            parser.error(
                "build windows requires --host, --runtime, --webview2, --wheelhouse, "
                "--framework-wheel and --requirements"
            )
        from .build_windows import build as build_windows

        try:
            result = build_windows(
                args.project,
                args.host,
                args.runtime,
                args.webview2,
                args.wheelhouse,
                args.framework_wheel,
                args.requirements,
                args.output,
            )
        except (
            OSError,
            ValueError,
            TypeError,
            AttributeError,
            KeyError,
            zipfile.BadZipFile,
            subprocess.SubprocessError,
            struct.error,
        ) as exc:
            parser.exit(1, f"pyyaki: cannot build Windows exe: {exc}\n")
        print(f"Built local Windows onefile prototype (unsigned): {result}")
        return 0
    if args.command == "build" and args.target == "macos":
        required = ("host", "runtime", "wheelhouse", "framework_wheel", "requirements")
        if any(getattr(args, name) is None for name in required):
            parser.error(
                "build macos requires --host, --runtime, --wheelhouse, "
                "--framework-wheel and --requirements"
            )
        from .build_macos import build as build_macos

        try:
            result = build_macos(
                args.project,
                args.host,
                args.runtime,
                args.wheelhouse,
                args.framework_wheel,
                args.requirements,
                args.output,
            )
        except (
            OSError,
            ValueError,
            TypeError,
            AttributeError,
            KeyError,
            zipfile.BadZipFile,
            subprocess.SubprocessError,
        ) as exc:
            parser.exit(1, f"pyyaki: cannot build macOS app: {exc}\n")
        print(f"Built local macOS prototype (ad-hoc signed, not notarized): {result}")
        return 0
    if args.command != "dev":
        parser.error("build without a target is not implemented in this prototype")
    if not 1 <= args.port <= 65535:
        parser.error("port must be between 1 and 65535")
    config_path = Path.cwd() / "pyproject.toml"
    try:
        config = load_config()
    except FileNotFoundError:
        parser.exit(
            1,
            f"pyyaki: project configuration not found: {config_path}\n"
            "Run pyyaki dev from the project directory.\n",
        )
    except OSError as exc:
        parser.exit(1, f"pyyaki: cannot read project configuration {config_path}: {exc}\n")
    except (ValueError, TypeError, AttributeError) as exc:
        parser.exit(1, f"pyyaki: invalid project configuration in {config_path}: {exc}\n")

    try:
        # Reserve the actual listener before app import, avoiding a probe/bind race.
        listener = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    except OSError as exc:
        parser.exit(1, f"pyyaki: cannot create a local network socket: {exc}\n")

    with listener:
        try:
            # Windows SO_REUSEADDR permits a second bind to an active dev port.
            # Reserve it exclusively before sharing the socket with our child.
            option = socket.SO_EXCLUSIVEADDRUSE if sys.platform == "win32" else socket.SO_REUSEADDR
            listener.setsockopt(socket.SOL_SOCKET, option, 1)
            listener.bind(("127.0.0.1", args.port))
        except OSError as exc:
            if exc.errno == errno.EADDRINUSE:
                example_port = 8002 if args.port == 8001 else 8001
                parser.exit(
                    1,
                    f"pyyaki: port {args.port} is already in use. "
                    "Stop the other process or choose another port, "
                    f"for example: pyyaki dev --port {example_port}\n",
                )
            hint = (
                "The port may be occupied or restricted; choose another --port.\n"
                if sys.platform == "win32" and getattr(exc, "winerror", None) == 10013
                else ""
            )
            parser.exit(1, f"pyyaki: cannot listen on 127.0.0.1:{args.port}: {exc}\n{hint}")
        from ._dev import serve, supervise

        print(f"PyYaki prototype: http://127.0.0.1:{args.port}", flush=True)
        if args.no_reload:
            return serve(config.root, listener, reload=False)
        return supervise(config.root, listener)


if __name__ == "__main__":
    raise SystemExit(main())
