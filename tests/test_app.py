import asyncio

import pytest

from pyyaki import App, AppError, FrameworkError
from pyyaki._json import decode, encode


def test_named_arguments_defaults_and_unicode():
    app = App()

    @app.command("hello")
    async def hello(name: str, *, count: int = 2) -> list[str]:
        return [name] * count

    assert asyncio.run(app.dispatch("hello", {"name": "鯛焼き🐟"})) == ["鯛焼き🐟"] * 2


@pytest.mark.parametrize("params", [{}, {"count": "1"}, {"count": True}, {"count": 1, "extra": 2}])
def test_strict_arguments(params):
    app = App()

    @app.command("echo")
    async def echo(count: int) -> int:
        return count

    with pytest.raises(FrameworkError) as error:
        asyncio.run(app.dispatch("echo", params))
    assert error.value.code == "VALIDATION_ERROR"


@pytest.mark.parametrize("value", [2**53, float("nan"), float("inf"), object(), {1: "bad"}])
def test_invalid_result(value):
    app = App()

    @app.command("bad")
    async def bad() -> int:
        return value

    with pytest.raises(FrameworkError) as error:
        asyncio.run(app.dispatch("bad", {}))
    assert error.value.code == "SERIALIZATION_ERROR"


def test_errors_do_not_expose_exception_message():
    app = App()

    @app.command("crash")
    async def crash() -> str:
        raise RuntimeError("private-token-and-path")

    with pytest.raises(FrameworkError) as error:
        asyncio.run(app.dispatch("crash", {}))
    assert error.value.code == "INTERNAL_ERROR"
    assert "private-token" not in str(error.value.payload())
    assert error.value.details["diagnostic_id"]
    with pytest.raises(FrameworkError, match="Unknown command"):
        asyncio.run(app.dispatch("missing", {}))


def test_explicit_application_error():
    app = App()

    @app.command("fail")
    async def fail() -> None:
        raise AppError("APP_EXAMPLE", "Expected error", {"field": "name"})

    with pytest.raises(AppError) as error:
        asyncio.run(app.dispatch("fail", {}))
    assert error.value.payload()["details"] == {"field": "name"}
    with pytest.raises(ValueError):
        AppError("INTERNAL_ERROR", "Cannot impersonate framework errors")


def test_registration_rejects_unsupported_commands():
    app = App()

    async def valid(name: str) -> str:
        return name

    def sync(name: str) -> str:
        return name

    async def variadic(*args: str) -> str:
        return ""

    async def untyped(value) -> str:
        return str(value)

    async def custom(value: object) -> str:
        return str(value)

    for function in (sync, variadic, untyped, custom):
        with pytest.raises(TypeError):
            app.command("test")(function)
    for name in ("pyyaki.secret", "BadName", "../eval", ""):
        with pytest.raises(ValueError):
            app.command(name)(valid)
    app.command("test")(valid)
    with pytest.raises(ValueError):
        app.command("test")(valid)


@pytest.mark.parametrize("text", ['{"v":1,"v":2}', '{"x":NaN}', "[]", '{"x":9007199254740992}'])
def test_malformed_json(text):
    with pytest.raises(FrameworkError):
        decode(text)


def test_json_size_depth_cycles_and_round_trip():
    assert decode(encode({"name": "鯛焼き🐟"})) == {"name": "鯛焼き🐟"}
    with pytest.raises(FrameworkError):
        encode({"large": "a" * 1_048_576})
    circular = []
    circular.append(circular)
    with pytest.raises(FrameworkError):
        encode(circular)
    with pytest.raises(FrameworkError):
        decode('{"large":"' + "a" * 1_048_576 + '"}')
