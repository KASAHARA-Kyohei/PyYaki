"""Loopback-only CLI for the initial web prototype."""

import argparse
import errno
import importlib
import socket
import subprocess
import sys
import zipfile
from pathlib import Path

import uvicorn

from . import App, __version__
from .config import load_config


def main() -> int:
    parser = argparse.ArgumentParser(prog="pyyaki")
    parser.add_argument("--version", action="version", version=f"PyYaki {__version__}")
    commands = parser.add_subparsers(dest="command", required=True)
    dev = commands.add_parser("dev", help="Start the local web prototype")
    dev.add_argument("--port", type=int, default=8000)
    commands.add_parser("run", help="Windows GUI (not implemented)")
    build = commands.add_parser("build", help="Build a local application bundle")
    build.add_argument("target", nargs="?", choices=["windows", "macos"])
    build.add_argument("--project", type=Path, default=Path.cwd())
    build.add_argument("--host", type=Path)
    build.add_argument("--runtime", type=Path)
    build.add_argument("--wheelhouse", type=Path)
    build.add_argument("--framework-wheel", type=Path)
    build.add_argument("--requirements", type=Path)
    build.add_argument("--output", type=Path)
    args = parser.parse_args()
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
        parser.error("Windows run/build are not implemented in this prototype")
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
            listener.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
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
            parser.exit(1, f"pyyaki: cannot listen on 127.0.0.1:{args.port}: {exc}\n")
        try:
            sys.path.insert(0, str(config.root))
            module, name = config.entry.split(":")
            app = getattr(importlib.import_module(module), name)
            if not isinstance(app, App):
                raise ValueError("entry must refer to a pyyaki.App")
        except (OSError, ValueError, TypeError, ImportError, AttributeError) as exc:
            parser.exit(
                1,
                f"pyyaki: cannot load application {config.entry!r}: {exc}\n"
                f"Check the entry in {config_path} and the application dependencies.\n",
            )
        from .web import create_asgi

        server = uvicorn.Server(
            uvicorn.Config(
                create_asgi(app, config),
                host="127.0.0.1",
                port=args.port,
                proxy_headers=False,
                ws="websockets-sansio",
                ws_max_size=1_048_576,
                timeout_graceful_shutdown=2,
            )
        )
        print(f"PyYaki prototype: http://127.0.0.1:{args.port}", flush=True)
        try:
            server.run(sockets=[listener])
        except KeyboardInterrupt:
            # Uvicorn re-raises SIGINT after its graceful shutdown completes.
            return 130
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
