"""Transport-independent RPC session for web and native adapters."""

import asyncio
import contextlib
from typing import Literal, Protocol

from ._json import decode, encode
from .app import NAME, App
from .context import Context
from .errors import FrameworkError

MAX_INFLIGHT = 16
MAX_CALLS = 4096
MAX_EVENTS = 64


class Transport(Protocol):
    async def send_text(self, text: str) -> None: ...
    async def receive_text(self) -> str: ...


def envelope(message: dict, fields: set[str]) -> None:
    if set(message) != fields or type(message.get("v")) is not int or message["v"] != 1:
        raise FrameworkError("BAD_REQUEST", "Invalid protocol envelope")


class Connection:
    def __init__(
        self,
        app: App,
        socket: Transport,
        session_id: str,
        mode: Literal["web", "windows", "macos"],
    ):
        self.app = app
        self.socket = socket
        self.tasks: dict[str, asyncio.Task] = {}
        self.seen: set[str] = set()
        self.send_lock = asyncio.Lock()
        self.context = Context(session_id, mode, self.emit)
        self.events: asyncio.Queue[str] = asyncio.Queue(MAX_EVENTS)
        self.sequence = 0
        self.closed = False

    async def emit(self, name: str, payload: object) -> None:
        if self.closed:
            raise FrameworkError("DISCONNECTED", "Connection closed")
        if not isinstance(name, str) or not NAME.fullmatch(name) or name.startswith("pyyaki"):
            raise FrameworkError("SERIALIZATION_ERROR", "Invalid or reserved event name")
        # Encode before enqueueing to validate and snapshot mutable payloads.
        text = encode(
            {"v": 1, "type": "event", "name": name, "payload": payload, "seq": self.sequence + 1}
        )
        try:
            self.events.put_nowait(text)
        except asyncio.QueueFull:
            raise FrameworkError("BUSY", "Event queue is full") from None
        self.sequence += 1

    async def send_events(self) -> None:
        while True:
            await self.send_text(await self.events.get())

    async def send(self, message: dict) -> None:
        await self.send_text(encode(message))

    async def send_text(self, text: str) -> None:
        if self.closed:
            raise FrameworkError("DISCONNECTED", "Connection closed")
        async with asyncio.timeout(2):
            async with self.send_lock:
                await self.socket.send_text(text)

    async def error(self, request_id: str, error: FrameworkError) -> None:
        try:
            await self.send({"v": 1, "type": "error", "id": request_id, "error": error.payload()})
        except FrameworkError:
            # Invalid AppError details must not mask the original transport contract.
            await self.send(
                {
                    "v": 1,
                    "type": "error",
                    "id": request_id,
                    "error": {"code": "SERIALIZATION_ERROR", "message": "Invalid error payload"},
                }
            )

    async def call(self, message: dict) -> None:
        request_id = message["id"]
        terminal = False
        try:
            async with asyncio.timeout(message["timeout_ms"] / 1000):
                result = await self.app.dispatch(
                    message["method"], message["params"], ctx=self.context
                )
            reply = {"v": 1, "type": "result", "id": request_id, "result": result}
            encode(reply)
            terminal = True
            await self.send(reply)
        except TimeoutError:
            if terminal:
                raise
            await self.error(request_id, FrameworkError("TIMEOUT", "Command timed out"))
        except asyncio.CancelledError:
            if not terminal and not self.closed:
                with contextlib.suppress(Exception):
                    await self.error(request_id, FrameworkError("CANCELLED", "Command cancelled"))
            raise
        except FrameworkError as exc:
            if terminal:
                raise
            await self.error(request_id, exc)
        finally:
            self.tasks.pop(request_id, None)

    async def receive(self) -> None:
        while True:
            message = decode(await self.socket.receive_text())
            kind = message.get("type")
            if kind == "cancel":
                envelope(message, {"v", "type", "id"})
                if not isinstance(message["id"], str):
                    raise FrameworkError("BAD_REQUEST", "Invalid cancellation ID")
                task = self.tasks.get(message["id"])
                if task is not None:
                    task.cancel()
                continue
            envelope(message, {"v", "type", "id", "method", "params", "timeout_ms"})
            request_id = message["id"]
            if (
                kind != "call"
                or not isinstance(request_id, str)
                or not 1 <= len(request_id) <= 128
                or not request_id.isascii()
                or request_id in self.seen
                or len(self.seen) >= MAX_CALLS
            ):
                raise FrameworkError("BAD_REQUEST", "Invalid, duplicate, or exhausted request ID")
            self.seen.add(request_id)
            if (
                type(message["timeout_ms"]) is not int
                or not 1 <= message["timeout_ms"] <= 120_000
                or not isinstance(message["method"], str)
            ):
                await self.error(request_id, FrameworkError("BAD_REQUEST", "Invalid call options"))
            elif len(self.tasks) >= MAX_INFLIGHT:
                await self.error(request_id, FrameworkError("BUSY", "Too many concurrent commands"))
            else:
                task = asyncio.create_task(self.call(message))
                self.tasks[request_id] = task
                # Retrieve exceptions promptly. A failed writer terminates the connection.
                task.add_done_callback(lambda task, key=request_id: self.finished(key, task))

    def finished(self, request_id: str, task: asyncio.Task) -> None:
        if self.tasks.get(request_id) is task:
            self.tasks.pop(request_id)
        if not task.cancelled() and task.exception() is not None and not self.closed:
            # Cancelling the owning receive loop makes cleanup run for every pending call.
            self.owner.cancel()

    async def run(self) -> None:
        self.owner = asyncio.current_task()
        writer = asyncio.create_task(self.send_events())
        writer.add_done_callback(lambda task: self.finished("", task))
        try:
            await self.receive()
        finally:
            self.closed = True
            self.context._close()
            tasks = [*self.tasks.values(), writer]
            for task in tasks:
                task.cancel()
            if tasks:
                await asyncio.gather(*tasks, return_exceptions=True)
            # Command finally blocks may have modified state during cancellation.
            self.context.state.clear()
            while not self.events.empty():
                self.events.get_nowait()
