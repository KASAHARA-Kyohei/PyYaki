"""Owned hidden console for actual CTRL_C_EVENT tests; never signal the user's console."""

import ctypes
import json
import os
import signal
import subprocess
import sys
import time
from ctypes import wintypes as w
from pathlib import Path

from diagnostics_windows import Processes

ROOT = Path(__file__).resolve().parents[1]


class ConsoleRun:
    def __init__(self, command, cwd, folder, env=None, log_path=None):
        self.folder = folder.resolve()
        self.folder.mkdir(parents=True, exist_ok=False)
        (self.folder / "command.json").write_text(
            json.dumps(
                {
                    "command": list(map(str, command)),
                    "cwd": str(cwd),
                    "log_path": str(log_path) if log_path else None,
                }
            ),
            encoding="utf-8",
        )
        self.log = (self.folder / "console.log").open("wb")
        startup = subprocess.STARTUPINFO()
        startup.dwFlags = subprocess.STARTF_USESHOWWINDOW
        startup.wShowWindow = 0
        self.process = subprocess.Popen(
            [sys.executable, "-X", "utf8", str(Path(__file__).resolve()), str(self.folder)],
            cwd=ROOT,
            env=env,
            stdout=self.log,
            stderr=self.log,
            startupinfo=startup,
            creationflags=subprocess.CREATE_NEW_CONSOLE,
        )

    def interrupt(self):
        (self.folder / "interrupt").touch()

    def wait(self, timeout=40):
        try:
            code = self.process.wait(timeout)
        except subprocess.TimeoutExpired:
            (self.folder / "kill-owned-child").touch()
            self.process.wait(10)
            raise
        finally:
            if self.process.poll() is not None:
                self.log.close()
        result = json.loads((self.folder / "result.json").read_text(encoding="utf-8"))
        assert result["remaining"] == [], result
        return code, result


def helper(folder):
    if not folder.is_relative_to(ROOT / "test-results"):
        raise ValueError("console fixture must be under test-results")
    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    handler_type = ctypes.WINFUNCTYPE(w.BOOL, w.DWORD)

    @handler_type
    def handler(event):
        return event in (0, 1)  # The helper stays alive to reap only its own child.

    kernel.SetConsoleCtrlHandler.argtypes = [handler_type, w.BOOL]
    kernel.GenerateConsoleCtrlEvent.argtypes = [w.DWORD, w.DWORD]
    assert kernel.SetConsoleCtrlHandler(handler, True)
    spec = json.loads((folder / "command.json").read_text(encoding="utf-8"))
    observer = Processes()
    result = {"helper_pid": os.getpid(), "command": spec, "ctrl_c_sent": False}
    try:
        log_path = Path(spec["log_path"]).resolve() if spec["log_path"] else folder / "launcher.log"
        if not log_path.is_relative_to(ROOT / "test-results"):
            raise ValueError("log path must be under test-results")
        with log_path.open("ab") as log:
            child = subprocess.Popen(spec["command"], cwd=spec["cwd"], stdout=log, stderr=log)
            result["pid"] = child.pid
            (folder / "started.json").write_text(json.dumps(result), encoding="utf-8")
            deadline = time.monotonic() + 150
            while child.poll() is None:
                observer.sample(child.pid)
                if (folder / "interrupt").exists() and not result["ctrl_c_sent"]:
                    assert kernel.GenerateConsoleCtrlEvent(0, 0), ctypes.get_last_error()
                    result["ctrl_c_sent"] = True
                if (folder / "kill-owned-child").exists() or time.monotonic() > deadline:
                    result["killed_by_harness"] = True
                    child.kill()
                time.sleep(0.03)
            child.wait()
            result["exit_code"] = child.returncode
        deadline = time.monotonic() + 8
        while observer.active() and time.monotonic() < deadline:
            time.sleep(0.05)
        result["observed"] = observer.known
        result["remaining"] = observer.active()
        (folder / "result.json").write_text(json.dumps(result, indent=2), encoding="utf-8")
        return child.returncode
    finally:
        observer.close()


if __name__ == "__main__":
    signal.signal(signal.SIGINT, signal.SIG_IGN)
    raise SystemExit(helper(Path(sys.argv[1]).resolve()))
