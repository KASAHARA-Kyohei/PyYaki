"""Command registration and dispatch, independent of the web transport."""

import asyncio
import inspect
import logging
import re
import types
import typing
import uuid
from contextlib import asynccontextmanager
from dataclasses import dataclass
from typing import get_args, get_origin, get_type_hints

from pydantic import TypeAdapter, ValidationError

from ._json import check_json
from .context import Context
from .errors import FrameworkError

logger = logging.getLogger(__name__)
NAME = re.compile(r"[a-z][a-z0-9_.-]{0,63}\Z")


def supported(annotation) -> bool:
    if annotation in (str, bool, int, float, type(None)):
        return True
    origin, args = get_origin(annotation), get_args(annotation)
    if origin is list:
        return len(args) == 1 and supported(args[0])
    if origin is dict:
        return len(args) == 2 and args[0] is str and supported(args[1])
    if origin in (typing.Union, types.UnionType):
        return len(args) == 2 and type(None) in args and all(supported(arg) for arg in args)
    return False


@dataclass(frozen=True)
class Command:
    function: typing.Callable
    signature: inspect.Signature
    arguments: dict[str, TypeAdapter]
    result: TypeAdapter
    context: bool


class App:
    def __init__(self):
        self._commands: dict[str, Command] = {}
        self._startup: list[typing.Callable] = []
        self._shutdown: list[typing.Callable] = []
        self._running = False

    def on_startup(self, function):
        self._register_hook(self._startup, function)
        return function

    def on_shutdown(self, function):
        self._register_hook(self._shutdown, function)
        return function

    @staticmethod
    def _register_hook(hooks, function):
        if not inspect.iscoroutinefunction(function) or inspect.signature(function).parameters:
            raise TypeError("Lifecycle hooks must be async def functions without parameters")
        hooks.append(function)

    @asynccontextmanager
    async def _lifespan(self):
        if self._running:
            raise RuntimeError("This App already has an active lifespan")
        self._running = True
        try:
            for hook in self._startup:
                await hook()
            yield
        finally:
            try:
                async with asyncio.timeout(2):
                    for hook in reversed(self._shutdown):
                        try:
                            await hook()
                        except Exception:
                            logger.exception("Shutdown hook failed")
            except TimeoutError:
                logger.error("Shutdown hooks exceeded the 2 second deadline")
            finally:
                self._running = False

    def command(self, name: str):
        if not isinstance(name, str) or not NAME.fullmatch(name) or name.startswith("pyyaki"):
            raise ValueError("Invalid or reserved command name")

        def register(function):
            if name in self._commands:
                raise ValueError(f"Duplicate command: {name}")
            if not inspect.iscoroutinefunction(function):
                raise TypeError("Commands must be async def functions")
            signature = inspect.signature(function)
            hints = get_type_hints(function)
            adapters = {}
            context = False
            for parameter in signature.parameters.values():
                annotation = hints.get(parameter.name)
                if parameter.name == "ctx" or annotation is Context:
                    if (
                        parameter.name != "ctx"
                        or annotation is not Context
                        or parameter.kind is not inspect.Parameter.KEYWORD_ONLY
                        or parameter.default is not inspect.Parameter.empty
                    ):
                        raise TypeError("Context must be declared as a required keyword-only ctx")
                    context = True
                    continue
                if parameter.kind not in (
                    inspect.Parameter.POSITIONAL_OR_KEYWORD,
                    inspect.Parameter.KEYWORD_ONLY,
                ):
                    raise TypeError("Commands only accept named parameters")
                if annotation is None or not supported(annotation):
                    raise TypeError(f"Unsupported or missing annotation: {parameter.name}")
                adapters[parameter.name] = TypeAdapter(annotation)
                if parameter.default is not inspect.Parameter.empty:
                    check_json(parameter.default)
                    adapters[parameter.name].validate_python(parameter.default, strict=True)
            annotation = hints.get("return")
            if annotation is None or not supported(annotation):
                raise TypeError("Commands need a supported return annotation")
            public_signature = signature.replace(
                parameters=[p for p in signature.parameters.values() if p.name != "ctx"]
            )
            self._commands[name] = Command(
                function, public_signature, adapters, TypeAdapter(annotation), context
            )
            return function

        return register

    async def dispatch(self, name: str, params: dict, *, ctx: Context | None = None) -> object:
        command = self._commands.get(name)
        if command is None:
            raise FrameworkError("METHOD_NOT_FOUND", "Unknown command")
        if type(params) is not dict:
            raise FrameworkError("VALIDATION_ERROR", "params must be an object")
        try:
            check_json(params, "VALIDATION_ERROR")
            arguments = command.signature.bind(**params)
            arguments.apply_defaults()
            for key, value in arguments.arguments.items():
                arguments.arguments[key] = command.arguments[key].validate_python(
                    value, strict=True
                )
        except (TypeError, ValidationError) as exc:
            raise FrameworkError("VALIDATION_ERROR", "Arguments do not match the command") from exc
        try:
            if command.context:
                if ctx is None:
                    raise FrameworkError("NOT_READY", "Command requires a connection Context")
                arguments.arguments["ctx"] = ctx
            result = await command.function(**arguments.arguments)
            check_json(result)
            try:
                command.result.validate_python(result, strict=True)
            except ValidationError as exc:
                raise FrameworkError("SERIALIZATION_ERROR", "Invalid command result") from exc
            return result
        except FrameworkError:
            raise
        except Exception:
            diagnostic_id = uuid.uuid4().hex
            logger.exception("Command failed (diagnostic_id=%s)", diagnostic_id)
            raise FrameworkError(
                "INTERNAL_ERROR", "Command failed", {"diagnostic_id": diagnostic_id}
            ) from None

    def asgi(self):
        # Construction does not open a port or execute application commands.
        from .config import load_config
        from .web import create_asgi

        return create_asgi(self, load_config())
