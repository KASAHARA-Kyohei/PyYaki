"""Development-only file polling and ownership of the reloadable ASGI child."""

import asyncio
import contextlib
import hashlib
import importlib
import multiprocessing
import os
import signal
import sys
import tempfile
import threading
import traceback
import uuid
from pathlib import Path

import uvicorn

from . import App
from .config import load_config

INTERVAL = 0.25
IGNORED = {"__pycache__", "node_modules", "venv", "env", "test-results", "build", "dist"}


def snapshot(root: Path, *, python_only: bool = False) -> dict:
    """Observe local regular files, excluding caches, hidden paths and symlinks."""
    result = {}
    for directory, dirs, names in os.walk(root, followlinks=False):
        base = Path(directory)
        dirs[:] = [
            name
            for name in dirs
            if not name.startswith(".") and name not in IGNORED and not (base / name).is_symlink()
        ]
        for name in names:
            if name.startswith(".") or name.endswith(("~", ".swp", ".tmp")):
                continue
            path = base / name
            if python_only and not (path.suffix == ".py" or path == root / "pyproject.toml"):
                continue
            try:
                if path.is_symlink() or not path.is_file():
                    continue
                stat = path.stat()
                # Windows st_ctime is creation time. Preserving mtime and size can
                # otherwise hide a source edit even though the contents changed.
                digest = (
                    hashlib.sha256(path.read_bytes()).digest()
                    if os.name == "nt" and (path.suffix == ".py" or path == root / "pyproject.toml")
                    else None
                )
                result[path] = (
                    stat.st_mtime_ns,
                    stat.st_ctime_ns,
                    stat.st_size,
                    stat.st_ino,
                    digest,
                )
            except FileNotFoundError:
                # Editors may replace or remove a file during the scan.
                continue
    return result


def serve(root: Path, listener, *, reload: bool, stop=None, ready=None) -> int:
    """Run one generation; the supervisor alone owns the reserved listener."""
    if os.name == "nt":
        for stream in (sys.stdout, sys.stderr):
            if hasattr(stream, "reconfigure"):
                stream.reconfigure(encoding="utf-8", errors="backslashreplace", line_buffering=True)
    entry = "(configuration)"
    try:
        config = load_config(root)
        entry = config.entry
        sys.path.insert(0, str(root))
        if reload:
            # Timestamp-based pyc validation can reuse same-size edits within one second.
            # This unique, unwritten cache namespace always reads application source.
            sys.pycache_prefix = str(Path(tempfile.gettempdir()) / f"pyyaki-dev-{uuid.uuid4().hex}")
            sys.dont_write_bytecode = True
        module, name = config.entry.split(":")
        app = getattr(importlib.import_module(module), name)
        if not isinstance(app, App):
            raise ValueError("entry must refer to a pyyaki.App")
    except KeyboardInterrupt:
        return 130
    except Exception as exc:
        print(
            f"pyyaki: cannot load application {entry!r} in {root / 'pyproject.toml'}: {exc}",
            file=sys.stderr,
        )
        traceback.print_exc()
        return 1
    from .web import create_asgi

    server = uvicorn.Server(
        uvicorn.Config(
            create_asgi(app, config, dev_reload=reload),
            host="127.0.0.1",
            port=listener.getsockname()[1],
            proxy_headers=False,
            ws="websockets-sansio",
            ws_max_size=1_048_576,
            timeout_graceful_shutdown=2,
        )
    )

    async def run():
        async def control():
            while not stop.is_set():
                if server.started:
                    ready.set()
                await asyncio.sleep(0.05)
            server.should_exit = True

        task = asyncio.create_task(control()) if stop is not None else None
        try:
            await server.serve(sockets=[listener])
        finally:
            if task is not None:
                task.cancel()
                with contextlib.suppress(asyncio.CancelledError):
                    await task

    try:
        # A shared Windows listener cannot be attached to each generation's IOCP.
        # Use the socket-only selector loop for reload, without changing the global
        # policy or the native worker's Proactor loop. No-reload keeps the default.
        factory = asyncio.SelectorEventLoop if os.name == "nt" and reload else None
        asyncio.run(run(), loop_factory=factory)
    except KeyboardInterrupt:
        return 130
    return 0 if server.started else 1


def _child(root, listener, stop, ready):
    raise SystemExit(serve(root, listener, reload=True, stop=stop, ready=ready))


def supervise(root: Path, listener) -> int:
    """Spawn fresh interpreters and stop only child processes created here."""
    context = multiprocessing.get_context("spawn")
    exiting = threading.Event()
    exit_code = 0

    def interrupted(sig, frame):
        nonlocal exit_code
        exit_code = 128 + sig
        exiting.set()

    previous = {sig: signal.signal(sig, interrupted) for sig in (signal.SIGINT, signal.SIGTERM)}
    process = None
    stop = None

    def shutdown():
        if process is None or process.pid is None:
            return
        stop.set()
        process.join(5)
        if process.is_alive():
            print("pyyaki: child shutdown timed out; terminating owned server", file=sys.stderr)
            process.terminate()
            process.join(1)
        if process.is_alive():
            process.kill()
            process.join()
        process.close()

    try:
        files = snapshot(root, python_only=True)
        ever_ready = False
        while not exiting.is_set():
            stop, ready = context.Event(), context.Event()
            process = context.Process(target=_child, args=(root, listener, stop, ready))
            process.start()
            reported_exit = False
            while not exiting.wait(INTERVAL):
                ever_ready = ever_ready or ready.is_set()
                if not process.is_alive() and not ever_ready:
                    return process.exitcode or 1
                if not process.is_alive() and not reported_exit:
                    print(
                        "pyyaki: server exited; waiting for a Python/configuration change",
                        file=sys.stderr,
                    )
                    reported_exit = True
                current = snapshot(root, python_only=True)
                if current != files:
                    files = current
                    print("PyYaki: Python/configuration changed; restarting server", flush=True)
                    break
            shutdown()
            process = None
        return exit_code
    finally:
        shutdown()
        for sig, handler in previous.items():
            signal.signal(sig, handler)
