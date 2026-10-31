"""Connection-local state and event delivery; never supplied by JSON callers."""

from collections.abc import Awaitable, Callable
from typing import Literal

from .errors import FrameworkError


class Context:
    def __init__(
        self,
        session_id: str,
        mode: Literal["web", "windows", "macos"],
        emit: Callable[[str, object], Awaitable[None]],
    ):
        self._session_id = session_id
        self._mode = mode
        self._state: dict = {}
        self._emit = emit
        self._closed = False

    @property
    def session_id(self) -> str:
        return self._session_id

    @property
    def mode(self) -> Literal["web", "windows", "macos"]:
        return self._mode

    @property
    def state(self) -> dict:
        return self._state

    async def emit(self, name: str, payload: object) -> None:
        if self._closed:
            raise FrameworkError("DISCONNECTED", "Connection closed")
        await self._emit(name, payload)

    def _close(self) -> None:
        self._closed = True
        self._emit = None
        self._state.clear()
