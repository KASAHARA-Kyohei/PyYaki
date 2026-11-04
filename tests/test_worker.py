import contextlib
import os
import select
import struct
import subprocess
import sys
import time

import pytest

from pyyaki import __version__
from pyyaki._json import decode
from pyyaki.errors import FrameworkError
from pyyaki.worker import read_frame, write_frame

PHASES = [
    "worker_entered",
    "config_loaded",
    "app_import_started",
    "app_imported",
    "startup_hooks_started",
    "startup_hooks_completed",
]
pytestmark = pytest.mark.skipif(
    os.name == "nt", reason="POSIX pipe harness; Windows host tested separately"
)


@pytest.fixture
def project(tmp_path):
    root = tmp_path / "鯛焼き space"
    (root / "ui").mkdir(parents=True)
    (root / "ui/index.html").write_text("<h1>Native worker test</h1>")
    (root / "pyproject.toml").write_text('[tool.pyyaki]\nentry="app:app"\n')
    (root / "app.py").write_text("""import asyncio
import os
from pathlib import Path
from pyyaki import App, AppError, Context
app = App()
@app.on_startup
async def startup():
    await asyncio.sleep(0.02)
    Path("lifecycle.txt").write_text("started")
@app.on_shutdown
async def shutdown():
    Path("lifecycle.txt").write_text("stopped")
@app.command("visit")
async def visit(*, ctx: Context) -> dict[str, str]:
    ctx.state["visits"] = ctx.state.get("visits", 0) + 1
    await ctx.emit("visited", {"count": ctx.state["visits"]})
    await ctx.emit("visited", {"count": ctx.state["visits"]})
    return {"session": ctx.session_id, "mode": ctx.mode}
@app.command("echo")
async def echo(name: str) -> str:
    print("Python print stays outside RPC", flush=True)
    os.write(1, b"native-stdout-log" * 10000)
    return name
@app.command("wait")
async def wait() -> str:
    await asyncio.sleep(10)
    return "done"
@app.command("fail")
async def fail() -> None:
    raise AppError("APP_NATIVE", "Expected native error")
""")
    return root


def receive(fd, timeout=5):
    deadline = time.monotonic() + timeout

    def exact(size):
        data = bytearray()
        while len(data) < size:
            assert select.select([fd], [], [], max(0, deadline - time.monotonic()))[0], (
                "worker timeout"
            )
            block = os.read(fd, size - len(data))
            if not block:
                raise EOFError("worker closed pipe")
            data.extend(block)
        return bytes(data)

    size = struct.unpack("<I", exact(4))[0]
    assert 0 < size <= 1_048_576
    return decode(exact(size).decode())


@contextlib.contextmanager
def worker(project, tmp_path, mode="windows"):
    child_read, write = os.pipe()
    read, child_write = os.pipe()
    with (tmp_path / "worker.log").open("w+b") as log:
        process = subprocess.Popen(
            [
                sys.executable,
                "-I",
                "-m",
                "pyyaki.worker",
                "--mode",
                mode,
                "--project",
                str(project),
                "--read-handle",
                str(child_read),
                "--write-handle",
                str(child_write),
            ],
            pass_fds=(child_read, child_write),
            cwd=project,
            stdin=subprocess.DEVNULL,
            stdout=log,
            stderr=log,
        )
        os.close(child_read)
        os.close(child_write)
        try:
            yield process, read, write, log
        finally:
            os.close(write)
            try:
                process.wait(timeout=3)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait(timeout=3)
            os.close(read)


def initialize(read, write, mode="windows", request_id_mode=None):
    write_frame(
        write, {"v": 1, "type": "worker_hello", "nonce": "a" * 64, "framework_version": __version__}
    )
    notices, welcome = receive_startup(read)
    assert [notice["phase"] for notice in notices] == PHASES
    assert notices[-1]["worker_elapsed_ms"] - notices[-2]["worker_elapsed_ms"] >= 15
    assert welcome == {
        "v": 1,
        "type": "worker_welcome",
        "nonce": "a" * 64,
        "framework_version": __version__,
    }
    hello = {"v": 1, "type": "hello", "client_version": __version__}
    if request_id_mode is not None:
        hello["request_id_mode"] = request_id_mode
    dispatch(write, hello)
    welcome = receive(read)
    assert welcome["message"]["mode"] == mode
    assert welcome["message"]["capabilities"] == ["rpc", "events"]
    assert welcome["message"]["limits"]["request_id_mode"] == (request_id_mode or "legacy")


def receive_startup(read):
    notices = []
    while (message := receive(read))["type"] == "worker_progress":
        assert set(message) == {
            "v",
            "type",
            "nonce",
            "framework_version",
            "phase",
            "worker_elapsed_ms",
        }
        assert message["nonce"] == "a" * 64
        assert message["framework_version"] == __version__
        assert message["phase"] == PHASES[len(notices)]
        assert message["worker_elapsed_ms"] >= (notices[-1]["worker_elapsed_ms"] if notices else 0)
        notices.append(message)
    return notices, message


def dispatch(write, message, session="host-issued-session"):
    write_frame(write, {"v": 1, "type": "dispatch", "session_id": session, "message": message})


def call(write, request_id, method, params=None, timeout=30000):
    dispatch(
        write,
        {
            "v": 1,
            "type": "call",
            "id": request_id,
            "method": method,
            "params": params or {},
            "timeout_ms": timeout,
        },
    )


@pytest.mark.parametrize("mode", ["windows", "macos"])
def test_native_rpc_concurrency_errors_cancel_and_logs(project, tmp_path, mode):
    with worker(project, tmp_path, mode) as (process, read, write, log):
        initialize(read, write, mode)
        call(write, "slow", "wait")
        call(write, "fast", "echo", {"name": "鯛焼き🐟"})
        reply = receive(read)["message"]
        assert reply["id"] == "fast" and reply["result"] == "鯛焼き🐟"
        dispatch(write, {"v": 1, "type": "cancel", "id": "slow"})
        assert receive(read)["message"]["error"]["code"] == "CANCELLED"
        for identifier, method, params, code, timeout in [
            ("bad", "echo", {"name": 1}, "VALIDATION_ERROR", 30000),
            ("fail", "fail", {}, "APP_NATIVE", 30000),
            ("missing", "missing", {}, "METHOD_NOT_FOUND", 30000),
            ("timeout", "wait", {}, "TIMEOUT", 20),
        ]:
            call(write, identifier, method, params, timeout)
            assert receive(read)["message"]["error"]["code"] == code
        write_frame(write, {"v": 1, "type": "shutdown"})
        assert process.wait(timeout=3) == 0
        log.seek(0)
        assert b"native-stdout-log" in log.read()


def test_wrong_worker_version_rejected_before_import(project, tmp_path):
    (project / "app.py").write_text('raise RuntimeError("APP_SHOULD_NOT_IMPORT")')
    with worker(project, tmp_path) as (process, _, write, log):
        write_frame(
            write, {"v": 1, "type": "worker_hello", "nonce": "a" * 64, "framework_version": "wrong"}
        )
        assert process.wait(timeout=3) == 1
        log.seek(0)
        assert b"APP_SHOULD_NOT_IMPORT" not in log.read()


@pytest.mark.parametrize("mode", ["windows", "macos"])
def test_native_context_events_and_lifecycle(project, tmp_path, mode):
    with worker(project, tmp_path, mode) as (process, read, write, _):
        initialize(read, write, mode)
        assert (project / "lifecycle.txt").read_text() == "started"
        for count in (1, 2):
            call(write, str(count), "visit")
            messages = [receive(read)["message"] for _ in range(3)]
            events = [m for m in messages if m["type"] == "event"]
            assert [event["seq"] for event in events] == [count * 2 - 1, count * 2]
            assert all(event["payload"] == {"count": count} for event in events)
            reply = next(m for m in messages if m["type"] == "result")
            assert reply["result"] == {"mode": mode, "session": "host-issued-session"}
        write_frame(write, {"v": 1, "type": "shutdown"})
        assert process.wait(timeout=3) == 0
        assert (project / "lifecycle.txt").read_text() == "stopped"


def test_startup_failure_never_sends_worker_welcome_and_runs_cleanup(project, tmp_path):
    with (project / "app.py").open("a") as file:
        file.write(
            "\n@app.on_startup\nasync def fail_startup():\n"
            '    raise RuntimeError("startup failed")\n'
        )
    with worker(project, tmp_path) as (process, read, write, log):
        write_frame(
            write,
            {"v": 1, "type": "worker_hello", "nonce": "a" * 64, "framework_version": __version__},
        )
        notices, failure = receive_startup(read)
        assert [notice["phase"] for notice in notices] == PHASES[:-1]
        assert failure["type"] == "worker_failed"
        assert failure["phase"] == "startup_hooks_started"
        assert failure["error_type"] == "RuntimeError"
        assert failure["message"] == "startup failed"
        assert process.wait(timeout=3) == 1
        assert (project / "lifecycle.txt").read_text() == "stopped"
        log.seek(0)
        assert b"startup failed" in log.read()


def test_shutdown_before_sdk_hello_still_runs_cleanup(project, tmp_path):
    with worker(project, tmp_path) as (process, read, write, _):
        write_frame(
            write,
            {"v": 1, "type": "worker_hello", "nonce": "a" * 64, "framework_version": __version__},
        )
        assert receive_startup(read)[1]["type"] == "worker_welcome"
        write_frame(write, {"v": 1, "type": "shutdown"})
        assert process.wait(timeout=3) == 0
        assert (project / "lifecycle.txt").read_text() == "stopped"


def test_native_session_switch_is_rejected(project, tmp_path):
    with worker(project, tmp_path) as (process, read, write, _):
        initialize(read, write)
        dispatch(write, {"v": 1, "type": "cancel", "id": "x"}, session="forged")
        assert process.wait(timeout=3) == 1


@pytest.mark.parametrize("mode", ["windows", "macos"])
def test_native_sequence_ids_continue_and_reject_replay(project, tmp_path, mode):
    with worker(project, tmp_path, mode) as (process, read, write, _):
        initialize(read, write, mode, request_id_mode="sequence")
        for index in range(1, 4101):
            call(write, str(index), "missing")
            reply = receive(read)["message"]
            assert reply["id"] == str(index)
            assert reply["error"]["code"] == "METHOD_NOT_FOUND"
        call(write, "1", "missing")
        assert receive(read)["type"] == "worker_failed"
        assert process.wait(timeout=3) == 1


def test_import_failure_is_bounded_and_traceback_is_saved(project, tmp_path):
    message = "原因😀<script>" * 200
    (project / "app.py").write_text(f"raise ValueError({message!r})", encoding="utf-8")
    with worker(project, tmp_path) as (process, read, write, log):
        write_frame(
            write,
            {"v": 1, "type": "worker_hello", "nonce": "a" * 64, "framework_version": __version__},
        )
        notices, failure = receive_startup(read)
        assert [notice["phase"] for notice in notices] == PHASES[:3]
        assert failure["phase"] == "app_import_started"
        assert failure["error_type"] == "ValueError"
        assert failure["message"] == message[:1024]
        assert failure["nonce"] == "a" * 64
        assert process.wait(timeout=3) == 1
        log.seek(0)
        # The original trace is written before the host is notified and may stop the child.
        assert message.encode() in log.read()


@pytest.mark.parametrize("size", [0, 1_048_577, 0xFFFFFFFF])
def test_frame_length_checked_before_body_read(size):
    read, write = os.pipe()
    try:
        os.write(write, struct.pack("<I", size))
        with pytest.raises(FrameworkError, match="Invalid native frame size"):
            read_frame(read)
    finally:
        os.close(read)
        os.close(write)
