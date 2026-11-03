import asyncio
import threading
from contextlib import contextmanager

import pytest
from fastapi.testclient import TestClient
from starlette.websockets import WebSocketDisconnect

from pyyaki import App, AppError, Context, __version__
from pyyaki.config import Config
from pyyaki.web import COOKIE, Nonces, create_asgi

ORIGIN = "http://127.0.0.1:8000"
WS_URL = "ws://127.0.0.1:8000/_pyyaki/ws"


@pytest.fixture
def client(tmp_path):
    ui = tmp_path / "ui"
    ui.mkdir()
    (ui / "index.html").write_text("<!doctype html><h1>Hello</h1>")
    (ui / ".env").write_text("SECRET=hidden")
    (tmp_path / "private.html").write_text("hidden")
    app = App()
    started, cancelled = threading.Event(), threading.Event()
    contexts = []
    hooks = []

    @app.on_startup
    async def startup():
        hooks.append("startup")

    @app.on_shutdown
    async def shutdown():
        hooks.append("shutdown")

    @app.command("visit")
    async def visit(value: str, *, ctx: Context) -> dict[str, str]:
        assert hooks == ["startup"]
        contexts.append(ctx)
        ctx.state["visits"] = ctx.state.get("visits", 0) + 1
        await ctx.emit("visited", {"value": value, "visits": str(ctx.state["visits"])})
        return {"session": ctx.session_id, "mode": ctx.mode, "visits": str(ctx.state["visits"])}

    @app.command("echo")
    async def echo(value: str) -> str:
        return value

    @app.command("wait")
    async def wait() -> str:
        started.set()
        try:
            await asyncio.sleep(10)
            return "done"
        finally:
            cancelled.set()

    @app.command("fail")
    async def fail() -> None:
        raise AppError("APP_TEST", "Expected failure")

    @app.command("invalid_error")
    async def invalid_error() -> None:
        raise AppError("APP_TEST", "Invalid details", object())

    @app.command("too_large")
    async def too_large() -> str:
        return "a" * 1_048_576

    config = Config(tmp_path, "app:app", ui, "index.html")
    with TestClient(create_asgi(app, config), base_url=ORIGIN) as instance:
        instance.started = started
        instance.cancelled = cancelled
        instance.contexts = contexts
        instance.hooks = hooks
        yield instance
    assert hooks == ["startup", "shutdown"]


def nonce(client):
    response = client.get("/_pyyaki/session", headers={"origin": ORIGIN})
    assert response.status_code == 200
    assert "HttpOnly" in response.headers["set-cookie"]
    assert response.headers["cache-control"] == "no-store"
    return response.json()["nonce"]


@contextmanager
def connected(client):
    token = nonce(client)
    with client.websocket_connect(WS_URL, headers={"origin": ORIGIN}) as socket:
        socket.send_json({"v": 1, "type": "hello", "client_version": __version__, "nonce": token})
        welcome = socket.receive_json()
        assert welcome["capabilities"] == ["rpc", "events"]
        yield socket, welcome


def call(socket, request_id="one", method="echo", params=None, timeout=30000):
    socket.send_json(
        {
            "v": 1,
            "type": "call",
            "id": request_id,
            "method": method,
            "params": params if params is not None else {"value": "鯛焼き🐟"},
            "timeout_ms": timeout,
        }
    )


def test_rpc_errors_and_recovery(client):
    with connected(client) as (socket, _):
        call(socket)
        assert socket.receive_json()["result"] == "鯛焼き🐟"
        for index, (method, params, code) in enumerate(
            [
                ("echo", {"value": 1}, "VALIDATION_ERROR"),
                ("missing", {}, "METHOD_NOT_FOUND"),
                ("fail", {}, "APP_TEST"),
                ("invalid_error", {}, "SERIALIZATION_ERROR"),
                ("too_large", {}, "SERIALIZATION_ERROR"),
            ]
        ):
            call(socket, str(index), method, params)
            assert socket.receive_json()["error"]["code"] == code
        call(socket, "after-error")
        assert socket.receive_json()["result"] == "鯛焼き🐟"


def test_context_and_events_are_connection_local(client):
    def visit(socket, identifier, welcome, expected):
        call(socket, identifier, "visit", {"value": "鯛焼き🐟"})
        messages = [socket.receive_json(), socket.receive_json()]
        event = next(message for message in messages if message["type"] == "event")
        result = next(message for message in messages if message["type"] == "result")
        assert event == {
            "v": 1,
            "type": "event",
            "name": "visited",
            "seq": expected,
            "payload": {"value": "鯛焼き🐟", "visits": str(expected)},
        }
        assert result["result"] == {
            "session": welcome["session_id"],
            "mode": "web",
            "visits": str(expected),
        }

    with connected(client) as (first, one), connected(client) as (second, two):
        visit(first, "1", one, 1)
        visit(first, "2", one, 2)
        visit(second, "1", two, 1)
        # A forged ctx must not replace the host's connection context.
        call(first, "forged", "visit", {"value": "bad", "ctx": {"session_id": two["session_id"]}})
        assert first.receive_json()["error"]["code"] == "VALIDATION_ERROR"
    assert all(not ctx.state for ctx in client.contexts)
    with connected(client) as (fresh, welcome):
        visit(fresh, "1", welcome, 1)


def test_slow_command_does_not_block_other_calls(client):
    with connected(client) as (socket, _):
        call(socket, "slow", "wait", {})
        assert client.started.wait(1)
        call(socket, "fast")
        assert socket.receive_json()["id"] == "fast"
        socket.send_json({"v": 1, "type": "cancel", "id": "slow"})
        assert socket.receive_json()["error"]["code"] == "CANCELLED"
        assert client.cancelled.wait(1)


def test_timeout_and_disconnect_cancel_work(client):
    with connected(client) as (socket, _):
        call(socket, "timeout", "wait", {}, timeout=20)
        assert socket.receive_json()["error"]["code"] == "TIMEOUT"
        assert client.cancelled.wait(1)
        client.cancelled.clear()
        client.started.clear()
        call(socket, "disconnect", "wait", {})
        assert client.started.wait(1)
    assert client.cancelled.wait(1)


def test_inflight_limit(client):
    with connected(client) as (socket, _):
        for index in range(17):
            call(socket, str(index), "wait", {})
        response = socket.receive_json()
        assert response["id"] == "16"
        assert response["error"]["code"] == "BUSY"


def test_sessions_cannot_cancel_each_other(client):
    with connected(client) as (first, welcome1), connected(client) as (second, welcome2):
        assert welcome1["session_id"] != welcome2["session_id"]
        call(first, "same-id", "wait", {})
        assert client.started.wait(1)
        second.send_json({"v": 1, "type": "cancel", "id": "same-id"})
        call(second, "same-id")
        assert second.receive_json()["type"] == "result"
        assert not client.cancelled.is_set()
        first.send_json({"v": 1, "type": "cancel", "id": "same-id"})
        assert first.receive_json()["error"]["code"] == "CANCELLED"


@pytest.mark.parametrize(
    "message",
    [
        '{"v":1,"type":"hello"}',
        '{"v":1,"type":"call","id":[],"method":"echo","params":{},"timeout_ms":30000}',
        '{"v":1,"type":"cancel","id":1}',
        '{"v":true,"type":"cancel","id":"x"}',
        '{"v":1,"type":"cancel","id":"x","extra":0}',
        '{"v":1,"v":1}',
        '{"x":NaN}',
        '{"x":"' + "a" * 1_048_576 + '"}',
    ],
)
def test_malformed_protocol_is_closed(client, message):
    with connected(client) as (socket, _):
        socket.send_text(message)
        with pytest.raises(WebSocketDisconnect):
            socket.receive_json()


def test_duplicate_ids_close_connection(client):
    with connected(client) as (socket, _):
        call(socket)
        socket.receive_json()
        call(socket)
        with pytest.raises(WebSocketDisconnect):
            socket.receive_json()


def test_prototype_request_history_is_bounded(client, monkeypatch):
    monkeypatch.setattr("pyyaki._rpc.MAX_CALLS", 1)
    with connected(client) as (socket, _):
        call(socket)
        assert socket.receive_json()["type"] == "result"
        call(socket, "two")
        with pytest.raises(WebSocketDisconnect):
            socket.receive_json()


@pytest.mark.parametrize("timeout", [0, -1, 120001, True, "30"])
def test_bad_timeouts_return_error(client, timeout):
    with connected(client) as (socket, _):
        call(socket, timeout=timeout)
        assert socket.receive_json()["error"]["code"] == "BAD_REQUEST"


def test_session_origin_cookie_and_replay_checks(client):
    assert client.get("/_pyyaki/session").status_code == 403
    assert (
        client.get("/_pyyaki/session", headers={"origin": "https://evil.example"}).status_code
        == 403
    )
    assert client.get("/", headers={"host": "evil.example"}).status_code == 400
    token = nonce(client)
    with (
        pytest.raises(WebSocketDisconnect),
        client.websocket_connect(WS_URL, headers={"origin": "https://evil.example"}),
    ):
        pass
    for attempt in range(2):
        with client.websocket_connect(WS_URL, headers={"origin": ORIGIN}) as socket:
            socket.send_json(
                {"v": 1, "type": "hello", "client_version": __version__, "nonce": token}
            )
            if attempt == 0:
                assert socket.receive_json()["type"] == "welcome"
            else:
                with pytest.raises(WebSocketDisconnect):
                    socket.receive_json()
    client.cookies.clear()
    with (
        pytest.raises(WebSocketDisconnect),
        client.websocket_connect(WS_URL, headers={"origin": ORIGIN}),
    ):
        pass


def test_nonce_cannot_be_used_by_different_cookie(client):
    token = nonce(client)
    client.cookies.set(COOKIE, "different", domain="127.0.0.1", path="/")
    with client.websocket_connect(WS_URL, headers={"origin": ORIGIN}) as socket:
        socket.send_json({"v": 1, "type": "hello", "client_version": __version__, "nonce": token})
        with pytest.raises(WebSocketDisconnect):
            socket.receive_json()


def test_nonce_expiry_and_bounded_storage(monkeypatch):
    store = Nonces()
    for _ in range(300):
        store.issue("cookie", ORIGIN)
    assert len(store.pending) == 256
    token = store.issue("cookie", ORIGIN)
    monkeypatch.setattr("pyyaki.web.time.monotonic", lambda: float("inf"))
    assert not store.consume(token, "cookie", ORIGIN)


def test_assets_and_security_headers(client):
    response = client.get("/")
    assert response.status_code == 200
    assert "script-src 'self'" in response.headers["content-security-policy"]
    assert response.headers["x-content-type-options"] == "nosniff"
    assert client.get("/_pyyaki/sdk.js").status_code == 200
    for path in ["/.env", "/%2e%2e/private.html", "/_pyyaki/unknown", "/a%5cb"]:
        assert client.get(path).status_code == 404


def test_assets_reject_external_symlink(client, tmp_path, symlink):
    symlink(tmp_path / "ui/escape.html", tmp_path / "private.html")
    assert client.get("/escape.html").status_code == 404
