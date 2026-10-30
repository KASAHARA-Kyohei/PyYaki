"""Loopback-only CLI for the initial web prototype."""

import argparse
import importlib
import socket
import sys

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
    build = commands.add_parser("build", help="Windows packaging (not implemented)")
    build.add_argument("target", nargs="?", choices=["windows"])
    args = parser.parse_args()
    if args.command != "dev":
        parser.error("Windows run/build are not implemented in this prototype")
    if not 1 <= args.port <= 65535:
        parser.error("port must be between 1 and 65535")
    try:
        config = load_config()
        # Reserve the actual listener before app import, avoiding a probe/bind race.
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as listener:
            listener.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            listener.bind(("127.0.0.1", args.port))
            sys.path.insert(0, str(config.root))
            module, name = config.entry.split(":")
            app = getattr(importlib.import_module(module), name)
            if not isinstance(app, App):
                raise ValueError("entry must refer to a pyyaki.App")
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
    except OSError as exc:
        parser.exit(1, f"pyyaki: cannot open project or listen on port {args.port}: {exc}\n")
    except (ValueError, TypeError, ImportError, AttributeError) as exc:
        parser.exit(1, f"pyyaki: {exc}\n")


if __name__ == "__main__":
    raise SystemExit(main())
