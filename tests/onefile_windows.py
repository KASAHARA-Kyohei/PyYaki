"""Maintainer-only real Windows onefile smoke. Launch/stop only owned process trees."""

import argparse
import ctypes
import hashlib
import json
import os
import shutil
import subprocess
import sys
import time
from ctypes import wintypes as w
from pathlib import Path

from diagnostics_windows import Processes, read_markers


def markers(path, prefix):
    return read_markers(path, prefix) if path.exists() else []


def owned_records(instances, pid, owned_pids):
    records = []
    for instance in sorted(instances):
        launch = markers(instance / "launcher.log", "pyyaki_onefile=")
        host = markers(instance / "host.log", "pyyaki_startup=")
        if launch and launch[0]["data"].get("pid") == pid:
            records.append({"instance": str(instance), "launcher": launch, "host": [], "smoke": []})
        elif host and host[0]["data"].get("pid") in owned_pids:
            records.append(
                {
                    "instance": str(instance),
                    "launcher": [],
                    "host": host,
                    "smoke": markers(instance / "host.log", "native_smoke="),
                }
            )
    return records


def phase_order(phases, *names):
    return all(name in phases for name in names) and [phases.index(n) for n in names] == sorted(
        phases.index(n) for n in names
    )


def run_case(binary, output, name, local, *, stop=None, smoke=True, expected_error=None):
    with binary.open("rb") as stream:
        binary_sha256 = hashlib.file_digest(stream, "sha256").hexdigest()
    folder = output / name
    folder.mkdir()
    local.mkdir(parents=True, exist_ok=True)
    env = os.environ.copy()
    env.update(
        LOCALAPPDATA=str(local),
        PYTHONHOME=str(folder / "invalid-home"),
        PYTHONPATH=str(folder / "invalid-path"),
        WEBVIEW2_BROWSER_EXECUTABLE_FOLDER=str(folder / "invalid-webview"),
        WEBVIEW2_ADDITIONAL_BROWSER_ARGUMENTS="--pyyaki-invalid-sentinel",
    )
    observer = Processes()
    k = observer.kernel
    k.QueryFullProcessImageNameW.argtypes = [w.HANDLE, w.DWORD, w.LPWSTR, ctypes.POINTER(w.DWORD)]
    k.QueryFullProcessImageNameW.restype = w.BOOL
    u = ctypes.WinDLL("user32", use_last_error=True)
    callback = ctypes.WINFUNCTYPE(w.BOOL, w.HWND, w.LPARAM)
    u.EnumWindows.argtypes = [callback, w.LPARAM]
    u.GetWindowThreadProcessId.argtypes = [w.HWND, ctypes.POINTER(w.DWORD)]
    u.PostMessageW.argtypes = [w.HWND, w.UINT, w.WPARAM, w.LPARAM]
    images = {}
    before = set((local / "PyYaki/prototype").glob("*"))
    command = [str(binary), *(["--smoke-test"] if smoke else [])]
    timed_out = False
    action = None
    started = time.monotonic()
    observed = {}
    with (folder / "launcher-stdio.log").open("wb") as log:
        child = subprocess.Popen(
            command,
            stdout=log,
            stderr=log,
            env=env,
            cwd=folder,
            creationflags=subprocess.CREATE_NO_WINDOW,
        )
        process_created_seconds = time.monotonic() - started
        try:
            while child.poll() is None:
                observer.sample(child.pid)
                for pid in observer.known:
                    if pid in images:
                        continue
                    handle = k.OpenProcess(0x1000, False, pid)
                    if handle:
                        try:
                            size = w.DWORD(32768)
                            text = ctypes.create_unicode_buffer(size.value)
                            if k.QueryFullProcessImageNameW(handle, 0, text, ctypes.byref(size)):
                                images[pid] = text.value
                        finally:
                            k.CloseHandle(handle)
                instances = set((local / "PyYaki/prototype").glob("*")) - before
                live_records = owned_records(instances, child.pid, observer.known)
                launch = [p for record in live_records for p in record["launcher"]]
                host = [p for record in live_records for p in record["host"]]
                for kind, phases in (("launcher", launch), ("host", host)):
                    for phase in phases:
                        observed.setdefault(f"{kind}:{phase['phase']}", time.monotonic() - started)
                close_pid = None
                if action is None:
                    if stop == "cancel" and any(
                        p["phase"] == "preparation_started" for p in launch
                    ):
                        close_pid = child.pid
                    elif expected_error and any(p["phase"] == "preparation_failed" for p in launch):
                        close_pid = child.pid
                    elif stop in {"close", "kill"} and any(
                        p["phase"] == "sdk_welcome_posted" for p in host
                    ):
                        if stop == "kill":
                            child.kill()
                            action = "killed_owned_launcher_after_welcome"
                        else:
                            close_pid = next(
                                p["data"]["pid"]
                                for p in launch
                                if p["phase"] == "gui_child_started"
                            )
                    elif stop == "interrupt" and any(
                        p["phase"] == "preparation_started" for p in launch
                    ):
                        child.kill()
                        action = "killed_owned_launcher_during_preparation"
                if close_pid:
                    sent = []

                    @callback
                    def close(hwnd, _, target_pid=close_pid, results=sent):
                        pid = w.DWORD()
                        u.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
                        if pid.value == target_pid:
                            results.append(bool(u.PostMessageW(hwnd, 0x10, 0, 0)))
                        return True

                    u.EnumWindows(close, 0)
                    if any(sent):
                        action = f"posted_close_to_owned_pid_{close_pid}"
                if time.monotonic() - started > 120:
                    timed_out = True
                    child.kill()
                time.sleep(0.05)
            child.wait(5)
            until = time.monotonic() + 10
            while observer.active() and time.monotonic() < until:
                time.sleep(0.1)
            records = owned_records(
                set((local / "PyYaki/prototype").glob("*")) - before,
                child.pid,
                observer.known,
            )
            for record in records:
                instance = Path(record["instance"])
                saved = folder / instance.name
                saved.mkdir()
                for name_ in ("launcher.log", "host.log", "worker.log"):
                    if (instance / name_).exists():
                        shutil.copy2(instance / name_, saved / name_)
            launch = [p for record in records for p in record["launcher"]]
            hosts = [p for record in records for p in record["host"]]
            smokes = [p for record in records for p in record["smoke"]]
            remain = observer.active()
            missing = sorted(observer.known.keys() - observer.handles.keys())
            errors = [p["data"]["error"] for p in launch if p["phase"] == "preparation_failed"]
            prepared = [p for p in launch if p["phase"] == "preparation_completed"]
            ordered = [p["phase"] for p in launch]
            common = (
                not timed_out and not remain and not missing and "launcher_native_draw" in ordered
            )
            common &= phase_order(ordered, "launcher_native_draw", "preparation_started")
            if expected_error:
                ok = (
                    common
                    and child.returncode == 1
                    and any(expected_error in error for error in errors)
                    and not prepared
                    and not hosts
                )
            elif stop in {"cancel", "interrupt"}:
                ok = common and action is not None and not prepared and not hosts
            else:
                ok = (
                    common
                    and bool(prepared)
                    and not errors
                    and any(p["phase"] == "sdk_welcome_posted" for p in hosts)
                )
                ok &= action is not None if stop else bool(smokes and smokes[-1]["ok"])
                ok &= child.returncode == (1 if stop == "kill" else 0)
                ok &= phase_order(
                    ordered, "preparation_started", "preparation_completed", "gui_child_started"
                )
                if name == "cached":
                    ok &= bool(prepared and prepared[-1]["data"]["reused"])
                cache = prepared[-1]["data"]["cache"] if prepared else ""
                runtime = str(Path(cache) / "runtime").lower()
                worker_images = [
                    image
                    for pid, image in images.items()
                    if observer.known[pid]["name"].lower() in {"python.exe", "msedgewebview2.exe"}
                ]
                ok &= bool(worker_images) and all(
                    image.removeprefix("\\\\?\\").lower().startswith(runtime + "\\")
                    for image in worker_images
                )
            record = {
                "name": name,
                "command": command,
                "binary_sha256": binary_sha256,
                "host_pid": child.pid,
                "exit_code": child.returncode,
                "elapsed_seconds": time.monotonic() - started,
                "process_created_seconds": process_created_seconds,
                "first_observed_seconds": observed,
                "watchdog": timed_out,
                "action": action,
                "processes": observer.known,
                "process_images": images,
                "remaining": remain,
                "missing_handles": missing,
                "records": records,
                "ok": bool(ok),
            }
            (folder / "result.json").write_text(
                json.dumps(record, ensure_ascii=False, indent=2), encoding="utf-8"
            )
            return record
        finally:
            if child.poll() is None:
                child.kill()
                child.wait(5)
            observer.close()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--binary", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--local", type=Path)
    parser.add_argument(
        "--case",
        choices=["smoke", "cached", "close", "kill", "cancel", "interrupt", "corrupt"],
        default="smoke",
    )
    parser.add_argument("--expected-error")
    args = parser.parse_args()
    if sys.platform != "win32":
        parser.error("Windows only")
    if args.case == "corrupt" and not args.expected_error:
        parser.error("corrupt requires --expected-error")
    output = args.output.resolve()
    output.mkdir(exist_ok=False)
    local = (args.local or output / "local").resolve()
    result = run_case(
        args.binary.resolve(),
        output,
        args.case,
        local,
        stop=args.case if args.case in {"close", "kill", "cancel", "interrupt"} else None,
        smoke=args.case in {"smoke", "cached", "corrupt"},
        expected_error=args.expected_error,
    )
    print(
        json.dumps(
            {
                key: result[key]
                for key in (
                    "name",
                    "host_pid",
                    "exit_code",
                    "elapsed_seconds",
                    "watchdog",
                    "remaining",
                    "ok",
                )
            }
        ),
        flush=True,
    )
    return 0 if result["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
