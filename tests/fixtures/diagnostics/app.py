"""Maintainer-only measurement app; never part of the public SDK or hello app."""

import asyncio
import json
import os
from pathlib import Path

from pyyaki import App, Context

app = App()
root = Path(__file__).parent
config = json.loads((root / "probe.json").read_text())


@app.on_startup
async def startup():
    await asyncio.sleep(config.get("startup_delay_seconds", 0))


@app.on_shutdown
async def shutdown():
    (root / "shutdown.json").write_text(json.dumps({"entered": True}))
    if config["scenario"] == "shutdown":
        await asyncio.sleep(60)
    (root / "shutdown.json").write_text(json.dumps({"entered": True, "completed": True}))


@app.command("probe.echo")
async def echo(value: str, emit: bool = False, *, ctx: Context) -> str:
    ctx.state["echo_calls"] = ctx.state.get("echo_calls", 0) + 1
    if emit:
        await ctx.emit("tick", {"value": value})
    return value


@app.command("probe.delay")
async def delay(milliseconds: int = 1000, *, ctx: Context) -> None:
    ctx.state["active"] = ctx.state.get("active", 0) + 1
    try:
        await asyncio.sleep(milliseconds / 1000)
    finally:
        if "active" in ctx.state:
            ctx.state["active"] -= 1


@app.command("probe.info")
async def info(*, ctx: Context) -> dict[str, int]:
    return {
        "pid": os.getpid(),
        "active": ctx.state.get("active", 0),
        "asyncio_tasks": len(asyncio.all_tasks()),
        "fds": len(os.listdir("/dev/fd")),
        "echo_calls": ctx.state.get("echo_calls", 0),
    }
