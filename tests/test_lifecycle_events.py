import asyncio
import json

import pytest
from fastapi.testclient import TestClient

from pyyaki import App, Context, FrameworkError
from pyyaki._rpc import Connection
from pyyaki.config import Config
from pyyaki.web import create_asgi


def test_context_injection_and_strict_signature():
    app = App()

    @app.command("count")
    async def count(value: int = 1, *, ctx: Context) -> int:
        ctx.state["count"] = ctx.state.get("count", 0) + value
        return ctx.state["count"]

    async def run():
        connection = Connection(app, None, "one", "web")
        assert await app.dispatch("count", {}, ctx=connection.context) == 1
        assert await app.dispatch("count", {"value": 2}, ctx=connection.context) == 3
        with pytest.raises(FrameworkError) as error:
            await app.dispatch("count", {})
        assert error.value.code == "NOT_READY"
        with pytest.raises(FrameworkError) as error:
            await app.dispatch("count", {"ctx": {}}, ctx=connection.context)
        assert error.value.code == "VALIDATION_ERROR"

    asyncio.run(run())

    async def positional(ctx: Context) -> None:
        pass

    async def renamed(*, context: Context) -> None:
        pass

    async def optional(*, ctx: Context = None) -> None:
        pass

    async def wrong_type(*, ctx: str) -> None:
        pass

    for function in (positional, renamed, optional, wrong_type):
        with pytest.raises(TypeError):
            app.command("bad")(function)


def test_event_validation_snapshot_queue_bound_and_closed_context():
    async def run():
        connection = Connection(App(), None, "one", "macos")
        ctx = connection.context
        for name, payload in [
            ("pyyaki.secret", {}),
            ("Bad", {}),
            ("ok", object()),
            ("ok", float("nan")),
            ("ok", {"x": "a" * 1_048_576}),
        ]:
            with pytest.raises(FrameworkError) as error:
                await ctx.emit(name, payload)
            assert error.value.code == "SERIALIZATION_ERROR"
        payload = {"items": ["before"]}
        await ctx.emit("changed", payload)
        payload["items"].append("after")
        assert json.loads(connection.events.get_nowait())["payload"] == {"items": ["before"]}
        for _ in range(64):
            await ctx.emit("changed", None)
        with pytest.raises(FrameworkError) as error:
            await ctx.emit("changed", None)
        assert error.value.code == "BUSY"
        connection.events.get_nowait()
        await ctx.emit("changed", None)
        assert [json.loads(connection.events.get_nowait())["seq"] for _ in range(64)] == list(
            range(3, 67)
        )
        ctx.state["private"] = "discard"
        ctx._close()
        assert ctx.state == {}
        with pytest.raises(FrameworkError) as error:
            await ctx.emit("changed", None)
        assert error.value.code == "DISCONNECTED"

    asyncio.run(run())


def test_event_writer_preserves_order_and_disconnect_cleans_up():
    class Socket:
        def __init__(self):
            self.messages = []
            self.sent = asyncio.Event()
            self.closed = asyncio.Event()

        async def send_text(self, text):
            self.messages.append(json.loads(text))
            if len(self.messages) == 3:
                self.sent.set()

        async def receive_text(self):
            await self.closed.wait()
            raise EOFError

    async def run():
        socket = Socket()
        connection = Connection(App(), socket, "one", "windows")
        task = asyncio.create_task(connection.run())
        await asyncio.sleep(0)
        for index in range(3):
            await connection.context.emit("progress", index)
        await asyncio.wait_for(socket.sent.wait(), 1)
        assert [m["seq"] for m in socket.messages] == [1, 2, 3]
        assert [m["payload"] for m in socket.messages] == [0, 1, 2]
        socket.closed.set()
        with pytest.raises(EOFError):
            await task
        assert connection.closed
        assert not connection.tasks
        with pytest.raises(FrameworkError) as error:
            await connection.context.emit("progress", 4)
        assert error.value.code == "DISCONNECTED"

    asyncio.run(run())


def test_event_writer_failure_terminates_connection():
    class Socket:
        async def receive_text(self):
            await asyncio.Future()

        async def send_text(self, text):
            raise BrokenPipeError

    async def run():
        connection = Connection(App(), Socket(), "one", "web")
        task = asyncio.create_task(connection.run())
        await connection.context.emit("progress", 1)
        with pytest.raises(asyncio.CancelledError):
            await asyncio.wait_for(task, 1)
        assert connection.closed
        assert connection.events.empty()

    asyncio.run(run())


def test_disconnect_closes_context_before_command_cleanup():
    async def run():
        app = App()
        started, closed = asyncio.Event(), asyncio.Event()
        messages = []
        cleanup_codes = []

        @app.command("wait")
        async def wait(*, ctx: Context) -> None:
            started.set()
            try:
                await asyncio.Future()
            finally:
                ctx.state["late"] = "must be discarded"
                try:
                    await ctx.emit("late", None)
                except FrameworkError as error:
                    cleanup_codes.append(error.code)

        class Socket:
            first = True

            async def receive_text(self):
                if self.first:
                    self.first = False
                    return json.dumps(
                        {
                            "v": 1,
                            "type": "call",
                            "id": "1",
                            "method": "wait",
                            "params": {},
                            "timeout_ms": 30000,
                        }
                    )
                await closed.wait()
                raise EOFError

            async def send_text(self, text):
                messages.append(text)

        connection = Connection(app, Socket(), "one", "web")
        task = asyncio.create_task(connection.run())
        await asyncio.wait_for(started.wait(), 1)
        closed.set()
        with pytest.raises(EOFError):
            await task
        assert connection.context.state == {}
        assert cleanup_codes == ["DISCONNECTED"]
        assert messages == []

    asyncio.run(run())


def test_lifespan_order_cleanup_on_failure_and_registration(caplog):
    app = App()
    calls = []

    @app.on_startup
    async def first():
        calls.append("first")

    @app.on_startup
    async def second():
        calls.append("second")

    @app.on_shutdown
    async def cleanup():
        calls.append("cleanup")

    @app.on_shutdown
    async def failing_cleanup():
        calls.append("failing_cleanup")
        raise RuntimeError("cleanup failed")

    async def run():
        async with app._lifespan():
            assert calls == ["first", "second"]
            with pytest.raises(RuntimeError, match="active lifespan"):
                async with app._lifespan():
                    pass
        assert calls == ["first", "second", "failing_cleanup", "cleanup"]

        @app.on_startup
        async def fail():
            raise RuntimeError("startup failed")

        with pytest.raises(RuntimeError, match="startup failed"):
            async with app._lifespan():
                pytest.fail("Failed startup must not reach ready")
        assert calls[-2:] == ["failing_cleanup", "cleanup"]

    asyncio.run(run())
    assert "Shutdown hook failed" in caplog.text
    for register in (app.on_startup, app.on_shutdown):
        with pytest.raises(TypeError):
            register(lambda: None)

        async def invalid(value=1):
            pass

        with pytest.raises(TypeError):
            register(invalid)


def test_shutdown_hook_has_cooperative_deadline(caplog):
    app = App()
    cancelled = []

    @app.on_shutdown
    async def slow():
        try:
            await asyncio.sleep(60)
        finally:
            cancelled.append(True)

    async def run():
        async with app._lifespan():
            pass

    asyncio.run(run())
    assert cancelled == [True]
    assert "2 second deadline" in caplog.text


def test_asgi_construction_is_lazy_and_startup_failure_runs_cleanup(tmp_path):
    app = App()
    calls = []

    @app.on_startup
    async def fail():
        calls.append("startup")
        raise RuntimeError("cannot start")

    @app.on_shutdown
    async def cleanup():
        calls.append("shutdown")

    server = create_asgi(app, Config(tmp_path, "app:app", tmp_path, "index.html"))
    assert calls == []
    with pytest.raises(RuntimeError, match="cannot start"), TestClient(server):
        pytest.fail("Startup must fail before serving")
    assert calls == ["startup", "shutdown"]
