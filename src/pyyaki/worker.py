"""Private native worker: two inherited pipes, no sockets and no stdout framing."""

import argparse
import asyncio
import contextlib
import importlib
import os
import struct
import sys
import time
import traceback
from pathlib import Path

from . import App, __version__
from ._json import MAX_MESSAGE_BYTES, decode, encode
from ._rpc import MAX_INFLIGHT, Connection, envelope, negotiate_request_ids
from .config import load_config
from .errors import FrameworkError


def read_exact(fd: int, size: int) -> bytes:
    chunks = bytearray()
    while len(chunks) < size:
        block = os.read(fd, size - len(chunks))
        if not block:
            raise EOFError("Native host disconnected")
        chunks.extend(block)
    return bytes(chunks)


def read_frame(fd: int) -> dict:
    size = struct.unpack("<I", read_exact(fd, 4))[0]
    if not 0 < size <= MAX_MESSAGE_BYTES:
        raise FrameworkError("BAD_REQUEST", "Invalid native frame size")
    try:
        return decode(read_exact(fd, size).decode("utf-8"))
    except UnicodeError as exc:
        raise FrameworkError("BAD_REQUEST", "Invalid native frame encoding") from exc


def write_frame(fd: int, message: dict) -> None:
    body = encode(message).encode("utf-8")
    data = memoryview(struct.pack("<I", len(body)) + body)
    while data:
        count = os.write(fd, data)
        if not count:
            raise BrokenPipeError("Native host disconnected")
        data = data[count:]


class PipeTransport:
    def __init__(self, read_fd: int, write_fd: int):
        self.read_fd, self.write_fd = read_fd, write_fd
        self.session_id: str | None = None

    async def receive_text(self) -> str:
        message = await asyncio.to_thread(read_frame, self.read_fd)
        if message == {"v": 1, "type": "shutdown"}:
            raise EOFError("Native host requested shutdown")
        envelope(message, {"v", "type", "session_id", "message"})
        if (
            message["type"] != "dispatch"
            or not isinstance(message["session_id"], str)
            or not 1 <= len(message["session_id"]) <= 128
        ):
            raise FrameworkError("BAD_REQUEST", "Invalid native dispatch")
        if self.session_id is None:
            self.session_id = message["session_id"]
        elif message["session_id"] != self.session_id:
            raise FrameworkError("BAD_REQUEST", "Native session changed")
        return encode(message["message"])

    async def send_text(self, text: str) -> None:
        await asyncio.to_thread(
            write_frame,
            self.write_fd,
            {
                "v": 1,
                "type": "dispatch",
                "session_id": self.session_id,
                "message": decode(text),
            },
        )


class StartupReporter:
    """Bounded private startup notices; never sent to the public SDK."""

    def __init__(self, write_fd: int, nonce: str):
        self.write_fd = write_fd
        self.nonce = nonce
        self.started = time.perf_counter()
        self.phase = "worker_entered"

    async def send(self, kind: str, **fields) -> None:
        await asyncio.to_thread(
            write_frame,
            self.write_fd,
            {
                "v": 1,
                "type": kind,
                "nonce": self.nonce,
                "framework_version": __version__,
                "phase": self.phase,
                "worker_elapsed_ms": (time.perf_counter() - self.started) * 1000,
                **fields,
            },
        )

    async def mark(self, phase: str) -> None:
        self.phase = phase
        await self.send("worker_progress")

    async def failure(self, error: Exception) -> None:
        # Keep the original traceback in worker.log, even if __str__ itself fails.
        try:
            message = str(error)
        except Exception:
            message = "Exception message unavailable"
        message = message[:1024].encode("utf-8", errors="replace").decode("utf-8")
        error_type = type(error).__name__[:128].encode("utf-8", errors="replace").decode()
        with contextlib.suppress(Exception):
            await self.send("worker_failed", error_type=error_type, message=message)


class ReportedWorkerError(Exception):
    """The original traceback was saved before notifying the supervising host."""


async def run(read_fd: int, write_fd: int, root: Path, mode: str = "windows") -> None:
    hello = await asyncio.to_thread(read_frame, read_fd)
    envelope(hello, {"v", "type", "nonce", "framework_version"})
    if (
        hello["type"] != "worker_hello"
        or hello["framework_version"] != __version__
        or not isinstance(hello["nonce"], str)
        or len(hello["nonce"]) != 64
    ):
        raise FrameworkError("PROTOCOL_MISMATCH", "Invalid native host handshake")
    reporter = StartupReporter(write_fd, hello["nonce"])
    try:
        await reporter.mark("worker_entered")
        config = load_config(root)
        await reporter.mark("config_loaded")
        sys.path.insert(0, str(config.root))
        module, name = config.entry.split(":")
        await reporter.mark("app_import_started")
        app = getattr(importlib.import_module(module), name)
        if not isinstance(app, App):
            raise TypeError("entry must refer to a pyyaki.App")
        await reporter.mark("app_imported")
        await reporter.mark("startup_hooks_started")
        async with app._lifespan():
            await reporter.mark("startup_hooks_completed")
            reporter.phase = "connection"
            await serve(app, read_fd, write_fd, hello["nonce"], mode)
    except EOFError, BrokenPipeError:
        raise
    except Exception as error:
        traceback.print_exc()
        sys.stderr.flush()
        await reporter.failure(error)
        raise ReportedWorkerError from None


async def serve(app: App, read_fd: int, write_fd: int, nonce: str, mode: str) -> None:
    # All startup writes finish before Connection starts its serialized public writer.
    # Failure reporting happens only after Connection has joined its writer tasks.
    await asyncio.to_thread(
        write_frame,
        write_fd,
        {
            "v": 1,
            "type": "worker_welcome",
            "nonce": nonce,
            "framework_version": __version__,
        },
    )
    transport = PipeTransport(read_fd, write_fd)
    public_hello = decode(await transport.receive_text())
    request_id_mode = negotiate_request_ids(public_hello, {"v", "type", "client_version"})
    if public_hello["type"] != "hello" or public_hello["client_version"] != __version__:
        raise FrameworkError("PROTOCOL_MISMATCH", "Invalid SDK handshake")
    connection = Connection(
        app, transport, transport.session_id, mode, request_id_mode=request_id_mode
    )
    await connection.send(
        {
            "v": 1,
            "type": "welcome",
            "session_id": connection.context.session_id,
            "mode": mode,
            "capabilities": ["rpc", "events"],
            "framework_version": __version__,
            "limits": {
                "request_id_mode": request_id_mode,
                "max_message_bytes": MAX_MESSAGE_BYTES,
                "max_inflight": MAX_INFLIGHT,
                "default_timeout_ms": 30000,
                "max_timeout_ms": 120000,
            },
        }
    )
    await connection.run()


def main() -> int:
    parser = argparse.ArgumentParser(description="Private PyYaki native worker")
    parser.add_argument("--read-handle", type=int, required=True)
    parser.add_argument("--write-handle", type=int, required=True)
    parser.add_argument("--project", type=Path, required=True)
    parser.add_argument("--mode", choices=["windows", "macos"], default="windows")
    args = parser.parse_args()
    read_fd, write_fd = args.read_handle, args.write_handle
    if os.name == "nt":
        import msvcrt

        read_fd = msvcrt.open_osfhandle(read_fd, os.O_RDONLY | os.O_BINARY)
        write_fd = msvcrt.open_osfhandle(write_fd, os.O_WRONLY | os.O_BINARY)
    try:
        asyncio.run(run(read_fd, write_fd, args.project, args.mode))
        return 0
    except EOFError, BrokenPipeError:
        return 0
    except ReportedWorkerError:
        return 1
    except Exception:
        # Full application traces are retained in the host's private worker log.
        traceback.print_exc()
        return 1
    finally:
        os.close(read_fd)
        os.close(write_fd)


if __name__ == "__main__":
    raise SystemExit(main())
