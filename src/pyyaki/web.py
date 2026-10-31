"""Local ASGI adapter for the first prototype; no production authentication."""

import asyncio
import contextlib
import secrets
import time
from collections import OrderedDict
from importlib.resources import files

from fastapi import FastAPI, Request, WebSocket, WebSocketDisconnect
from fastapi.responses import FileResponse, JSONResponse, Response
from starlette.middleware.trustedhost import TrustedHostMiddleware

from . import __version__
from ._json import MAX_MESSAGE_BYTES, decode
from ._rpc import MAX_INFLIGHT, Connection, envelope
from .app import App
from .config import Config
from .errors import FrameworkError

COOKIE = "pyyaki_session"
SAFE_ASSETS = {
    ".html",
    ".css",
    ".js",
    ".mjs",
    ".json",
    ".png",
    ".jpg",
    ".jpeg",
    ".gif",
    ".svg",
    ".webp",
    ".ico",
    ".woff",
    ".woff2",
    ".ttf",
    ".otf",
}
CSP = (
    "default-src 'self'; script-src 'self'; style-src 'self'; img-src 'self' data:; "
    "font-src 'self'; object-src 'none'; frame-src 'none'; base-uri 'none'; "
    "form-action 'none'; frame-ancestors 'none'; "
)


class Nonces:
    def __init__(self):
        self.pending: OrderedDict[str, tuple[str, str, float]] = OrderedDict()

    def issue(self, cookie: str, origin: str) -> str:
        now = time.monotonic()
        for nonce, (_, _, expires) in list(self.pending.items()):
            if expires <= now:
                del self.pending[nonce]
        while len(self.pending) >= 256:
            self.pending.popitem(last=False)
        nonce = secrets.token_urlsafe(32)
        self.pending[nonce] = (cookie, origin, now + 30)
        return nonce

    def consume(self, nonce: str, cookie: str, origin: str) -> bool:
        record = self.pending.pop(nonce, None)
        return bool(
            record
            and record[2] > time.monotonic()
            and secrets.compare_digest(record[0], cookie)
            and record[1] == origin
        )


def expected_origin(connection) -> str:
    scheme = "https" if connection.url.scheme in ("https", "wss") else "http"
    return f"{scheme}://{connection.headers.get('host', '')}"


def create_asgi(app: App, config: Config) -> FastAPI:
    @contextlib.asynccontextmanager
    async def lifespan(server):
        async with app._lifespan():
            yield

    server = FastAPI(docs_url=None, redoc_url=None, openapi_url=None, lifespan=lifespan)
    nonces = Nonces()
    server.add_middleware(
        TrustedHostMiddleware, allowed_hosts=["127.0.0.1", "localhost"], www_redirect=False
    )

    @server.middleware("http")
    async def secure_headers(request: Request, call_next):
        response = await call_next(request)
        origin = expected_origin(request)
        ws_origin = origin.replace("http", "ws", 1)
        response.headers["Content-Security-Policy"] = CSP + f"connect-src 'self' {ws_origin};"
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["Referrer-Policy"] = "no-referrer"
        response.headers["Cache-Control"] = "no-store"
        return response

    @server.get("/_pyyaki/session")
    async def session(request: Request):
        origin = expected_origin(request)
        supplied = request.headers.get("origin")
        if (supplied is not None and supplied != origin) or request.headers.get(
            "sec-fetch-site", "same-origin" if supplied else ""
        ) != "same-origin":
            return Response(status_code=403)
        cookie = request.cookies.get(COOKIE) or secrets.token_urlsafe(32)
        response = JSONResponse({"nonce": nonces.issue(cookie, origin)})
        response.set_cookie(
            COOKIE, cookie, httponly=True, samesite="strict", secure=request.url.scheme == "https"
        )
        return response

    @server.get("/_pyyaki/sdk.js")
    async def sdk():
        return Response(
            files("pyyaki").joinpath("static/sdk.js").read_text(), media_type="text/javascript"
        )

    @server.websocket("/_pyyaki/ws")
    async def websocket(socket: WebSocket):
        origin = expected_origin(socket)
        if socket.headers.get("origin") != origin or not socket.cookies.get(COOKIE):
            await socket.close(code=1008)
            return
        await socket.accept()
        try:
            async with asyncio.timeout(10):
                hello = decode(await socket.receive_text())
            envelope(hello, {"v", "type", "client_version", "nonce"})
            if hello["type"] != "hello" or hello["client_version"] != __version__:
                await socket.close(code=1002)
                return
            if not isinstance(hello["nonce"], str) or not nonces.consume(
                hello["nonce"], socket.cookies[COOKIE], origin
            ):
                await socket.close(code=1008)
                return
            connection = Connection(app, socket, secrets.token_urlsafe(24), "web")
            await connection.send(
                {
                    "v": 1,
                    "type": "welcome",
                    "session_id": connection.context.session_id,
                    "mode": "web",
                    "capabilities": ["rpc", "events"],
                    "framework_version": __version__,
                    "limits": {
                        "max_message_bytes": MAX_MESSAGE_BYTES,
                        "max_inflight": MAX_INFLIGHT,
                        "default_timeout_ms": 30_000,
                        "max_timeout_ms": 120_000,
                    },
                }
            )
            await connection.run()
        except WebSocketDisconnect:
            pass
        except FrameworkError, TimeoutError, KeyError:
            with contextlib.suppress(RuntimeError, WebSocketDisconnect):
                await socket.close(code=1002)
        except asyncio.CancelledError:
            with contextlib.suppress(RuntimeError, WebSocketDisconnect):
                await socket.close(code=1011)
            raise

    @server.get("/{path:path}")
    async def asset(path: str):
        if path.startswith("_pyyaki/") or any(c in path for c in ("\\", ":", "\x00")):
            return Response(status_code=404)
        if any(part.startswith(".") for part in path.split("/")):
            return Response(status_code=404)
        target = (config.ui_dir / (path or config.ui_entry)).resolve()
        if (
            not target.is_relative_to(config.ui_dir)
            or not target.is_file()
            or target.suffix.lower() not in SAFE_ASSETS
        ):
            return Response(status_code=404)
        return FileResponse(target)

    return server
