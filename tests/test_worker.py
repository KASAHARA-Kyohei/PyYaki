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
from pyyaki import App, AppError
app = App()
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
def worker(project, tmp_path):
    child_read, write = os.pipe()
    read, child_write = os.pipe()
    with (tmp_path / "worker.log").open("w+b") as log:
        process = subprocess.Popen(
            [
                sys.executable,
                "-I",
                "-m",
                "pyyaki.worker",
                "--project",
                str(project),
                "--read-handle",
                str(child_read),
                "--write-handle",
                str(child_write),
            ],
            pass_fds=(child_read, child_write),
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


def initialize(read, write):
    write_frame(
        write, {"v": 1, "type": "worker_hello", "nonce": "a" * 64, "framework_version": __version__}
    )
    assert receive(read) == {
        "v": 1,
        "type": "worker_welcome",
        "nonce": "a" * 64,
        "framework_version": __version__,
    }
    dispatch(write, {"v": 1, "type": "hello", "client_version": __version__})
    welcome = receive(read)
    assert welcome["message"]["mode"] == "windows"


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


def test_native_rpc_concurrency_errors_cancel_and_logs(project, tmp_path):
    with worker(project, tmp_path) as (process, read, write, log):
        initialize(read, write)
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


def test_native_session_switch_is_rejected(project, tmp_path):
    with worker(project, tmp_path) as (process, read, write, _):
        initialize(read, write)
        dispatch(write, {"v": 1, "type": "cancel", "id": "x"}, session="forged")
        assert process.wait(timeout=3) == 1


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
