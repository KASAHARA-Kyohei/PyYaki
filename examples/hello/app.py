"""Small commands for checking the first web prototype."""

import asyncio

from pyyaki import App, AppError, Context

app = App()


@app.command("greet")
async def greet(name: str) -> dict[str, str]:
    return {"message": f"こんにちは、{name}さん。PyYakiへようこそ。"}


@app.command("wait")
async def wait(seconds: int = 3, *, ctx: Context) -> dict[str, str]:
    if not 1 <= seconds <= 10:
        raise AppError("APP_RANGE", "待ち時間は1〜10秒で指定してください。")
    ctx.state["waits"] = ctx.state.get("waits", 0) + 1
    await ctx.emit("progress", {"completed": 0, "total": seconds})
    for completed in range(1, seconds + 1):
        await asyncio.sleep(1)
        await ctx.emit("progress", {"completed": completed, "total": seconds})
    return {"message": f"{seconds}秒の処理が完了しました。待機中もUIは操作できます。"}


@app.command("fail")
async def fail() -> None:
    raise AppError("APP_DEMO", "これは動作確認用のエラーです。接続は維持されています。")
