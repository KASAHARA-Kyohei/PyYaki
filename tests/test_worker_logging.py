"""Real worker subprocess/log tests on both Windows handles and POSIX fds."""

import contextlib
import json
import os
import queue
import subprocess
import sys
import threading
from pathlib import Path

import pytest

from pyyaki import __version__
from pyyaki.worker import read_frame, write_frame

BOOTSTRAP = (Path(__file__).resolve().parents[1] / "native/worker_bootstrap.py").read_text(
    encoding="utf-8"
)


@contextlib.contextmanager
def worker(project, log_path):
    child_read, write = os.pipe()
    read, child_write = os.pipe()
    options = {}
    if os.name == "nt":
        import msvcrt

        handles = [msvcrt.get_osfhandle(fd) for fd in (child_read, child_write)]
        for handle in handles:
            os.set_handle_inheritable(handle, True)
        startup = subprocess.STARTUPINFO()
        startup.lpAttributeList = {"handle_list": handles}
        options.update(startupinfo=startup, creationflags=subprocess.CREATE_NO_WINDOW)
    else:
        handles = [child_read, child_write]
        options["pass_fds"] = (child_read, child_write)
    received = queue.Queue()

    def reader():
        try:
            while True:
                received.put(read_frame(read))
        except (EOFError, OSError) as error:
            received.put(error)

    def receive():
        message = received.get(timeout=10)
        if isinstance(message, Exception):
            raise message
        return message

    with log_path.open("w+b") as log:
        try:
            process = subprocess.Popen(
                [
                    sys.executable,
                    "-I",
                    "-c",
                    BOOTSTRAP,
                    "--project",
                    str(project),
                    "--read-handle",
                    str(handles[0]),
                    "--write-handle",
                    str(handles[1]),
                ],
                cwd=project,
                stdin=subprocess.DEVNULL,
                stdout=log,
                stderr=log,
                **options,
            )
        finally:
            os.close(child_read)
            os.close(child_write)
        thread = threading.Thread(target=reader, daemon=True)
        thread.start()
        try:
            yield process, receive, write
        finally:
            os.close(write)
            try:
                process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait(timeout=5)
            thread.join(timeout=2)
            os.close(read)


@pytest.mark.parametrize("scenario", ["normal", "import", "hooks"])
def test_worker_text_logs_are_utf8_without_changing_file_defaults(tmp_path, scenario):
    project = tmp_path / "鯛焼き space"
    (project / "ui").mkdir(parents=True)
    (project / "ui/index.html").write_text("<h1>Log probe</h1>", encoding="utf-8")
    (project / "pyproject.toml").write_text('[tool.pyyaki]\nentry="app:app"\n', encoding="utf-8")
    source = r"""
import io, json, logging, sys
from pathlib import Path
print("標準出力 😀 \ud800")
sys.stderr.write("標準エラー 😀 \udcff\n")
logging.warning("日本語ログ 😀")
with io.TextIOWrapper(io.BytesIO()) as file:
    state = {"file_encoding": file.encoding, "utf8_mode": sys.flags.utf8_mode,
             "isolated": sys.flags.isolated, "stdout_encoding": sys.stdout.encoding,
             "stderr_encoding": sys.stderr.encoding, "stdout_errors": sys.stdout.errors,
             "stderr_errors": sys.stderr.errors}
Path("stdio.json").write_text(json.dumps(state), encoding="utf-8")
if SCENARIO == "import":
    raise ValueError("import失敗 😀")
from pyyaki import App
app = App()
@app.on_startup
async def startup():
    print("起動フック 😀")
    if SCENARIO == "hooks":
        raise RuntimeError("起動失敗 😀")
@app.command("echo")
async def echo(value: str) -> str:
    print("大量出力 😀\n" * 10000)
    return value
"""
    (project / "app.py").write_text(f"SCENARIO = {scenario!r}\n" + source, encoding="utf-8")
    log_path = tmp_path / "worker.log"
    with worker(project, log_path) as (process, receive, write):
        write_frame(
            write,
            {"v": 1, "type": "worker_hello", "nonce": "a" * 64, "framework_version": __version__},
        )
        while (message := receive())["type"] == "worker_progress":
            pass
        if scenario == "normal":
            assert message["type"] == "worker_welcome"

            def dispatch(payload):
                write_frame(
                    write,
                    {"v": 1, "type": "dispatch", "session_id": "logs", "message": payload},
                )

            dispatch({"v": 1, "type": "hello", "client_version": __version__})
            assert receive()["message"]["type"] == "welcome"
            dispatch(
                {
                    "v": 1,
                    "type": "call",
                    "id": "echo",
                    "method": "echo",
                    "params": {"value": "応答 😀"},
                    "timeout_ms": 30000,
                }
            )
            assert receive()["message"]["result"] == "応答 😀"
            write_frame(write, {"v": 1, "type": "shutdown"})
            assert process.wait(timeout=5) == 0
        else:
            assert message["type"] == "worker_failed"
            assert message["phase"] == (
                "app_import_started" if scenario == "import" else "startup_hooks_started"
            )
            assert message["message"] == (
                "import失敗 😀" if scenario == "import" else "起動失敗 😀"
            )
            assert process.wait(timeout=5) == 1
    text = log_path.read_bytes().decode("utf-8", errors="strict")
    assert "標準出力 😀 \\ud800" in text
    assert "標準エラー 😀 \\udcff" in text
    assert "WARNING:root:日本語ログ 😀" in text
    if scenario == "normal":
        assert text.count("大量出力 😀") == 10000
    else:
        assert "Traceback (most recent call last):" in text
        assert (
            "ValueError: import失敗 😀" if scenario == "import" else "RuntimeError: 起動失敗 😀"
        ) in text
    state = json.loads((project / "stdio.json").read_text(encoding="utf-8"))
    assert state["stdout_encoding"] == state["stderr_encoding"] == "utf-8"
    assert state["stdout_errors"] == state["stderr_errors"] == "backslashreplace"
    assert state["isolated"] == 1
    # Compare a fresh isolated interpreter, rather than this pytest process's locale/flags.
    baseline = json.loads(
        subprocess.check_output(
            [
                sys.executable,
                "-I",
                "-c",
                "import io,json,sys; f=io.TextIOWrapper(io.BytesIO()); "
                "print(json.dumps({'file_encoding':f.encoding,'utf8_mode':sys.flags.utf8_mode}))",
            ]
        )
    )
    assert state["file_encoding"] == baseline["file_encoding"]
    assert state["utf8_mode"] == baseline["utf8_mode"]


def test_bootstrap_reports_package_import_failure_in_utf8():
    # Fail before worker.py can configure anything, even with CP932 streams.
    fault = """
import importlib.abc, sys
sys.stdout.reconfigure(encoding="cp932", errors="strict")
sys.stderr.reconfigure(encoding="cp932", errors="strict")
class MissingPackage(importlib.abc.MetaPathFinder):
    def find_spec(self, fullname, path, target=None):
        if fullname == "pyyaki":
            raise ImportError("依存パッケージ失敗 😀")
sys.meta_path.insert(0, MissingPackage())
"""
    result = subprocess.run(
        [sys.executable, "-I", "-c", fault + BOOTSTRAP],
        capture_output=True,
        timeout=10,
    )
    assert result.returncode == 1
    assert "ImportError: 依存パッケージ失敗 😀" in result.stderr.decode("utf-8", errors="strict")
