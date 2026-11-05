"""Maintainer-only real WebView2 fault/boundary checks; projects stay in test-results."""

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
from datetime import UTC, datetime
from pathlib import Path

from windows_handles import file_identity, identity_in_child

ROOT = Path(__file__).resolve().parents[1]
CASES = (
    "ui-json",
    "ui-type",
    "oversize",
    "worker-crash",
    "pipe-close",
    "import-timeout",
    "worker-frame-zero",
    "worker-frame-large",
    "worker-frame-json",
    "assets",
    "navigation",
    "invalid-proxy",
    "handle-inheritance",
    "user-actions",
    "worker-grandchild",
    "host-kill-tree",
)


class Processes:
    """Observe only descendants discovered while our Popen host is alive."""

    class Entry(ctypes.Structure):
        _fields_ = [
            ("size", w.DWORD),
            ("usage", w.DWORD),
            ("pid", w.DWORD),
            ("heap", ctypes.c_size_t),
            ("module", w.DWORD),
            ("threads", w.DWORD),
            ("ppid", w.DWORD),
            ("priority", w.LONG),
            ("flags", w.DWORD),
            ("name", w.WCHAR * 260),
        ]

    def __init__(self):
        self.kernel = ctypes.WinDLL("kernel32", use_last_error=True)
        k = self.kernel
        k.CreateToolhelp32Snapshot.argtypes = [w.DWORD, w.DWORD]
        k.CreateToolhelp32Snapshot.restype = w.HANDLE
        for name in ("Process32FirstW", "Process32NextW"):
            getattr(k, name).argtypes = [w.HANDLE, ctypes.POINTER(self.Entry)]
            getattr(k, name).restype = w.BOOL
        k.OpenProcess.argtypes = [w.DWORD, w.BOOL, w.DWORD]
        k.OpenProcess.restype = w.HANDLE
        k.WaitForSingleObject.argtypes = [w.HANDLE, w.DWORD]
        k.WaitForSingleObject.restype = w.DWORD
        k.CloseHandle.argtypes = [w.HANDLE]
        self.known = {}
        self.handles = {}

    def sample(self, host):
        snapshot = self.kernel.CreateToolhelp32Snapshot(2, 0)
        if snapshot == ctypes.c_void_p(-1).value:
            raise ctypes.WinError(ctypes.get_last_error())
        try:
            entry = self.Entry()
            entry.size = ctypes.sizeof(entry)
            table = {}
            more = self.kernel.Process32FirstW(snapshot, ctypes.byref(entry))
            while more:
                table[entry.pid] = {"ppid": entry.ppid, "name": entry.name}
                more = self.kernel.Process32NextW(snapshot, ctypes.byref(entry))
        finally:
            self.kernel.CloseHandle(snapshot)
        owned = {host}
        while True:
            extra = {pid for pid, row in table.items() if row["ppid"] in owned}
            if extra <= owned:
                break
            owned |= extra
        for pid in owned & table.keys():
            if pid not in self.known:
                self.known[pid] = table[pid]
                handle = self.kernel.OpenProcess(0x100000, False, pid)
                if handle:
                    self.handles[pid] = handle

    def active(self):
        active = []
        for pid, handle in self.handles.items():
            status = self.kernel.WaitForSingleObject(handle, 0)
            if status == 0xFFFFFFFF:
                raise ctypes.WinError(ctypes.get_last_error())
            if status == 258:
                active.append(pid)
            elif status != 0:
                raise RuntimeError(f"Unexpected process wait result for {pid}: {status}")
        return active

    def close(self):
        for handle in self.handles.values():
            self.kernel.CloseHandle(handle)


def read_markers(path, prefix):
    return [
        json.loads(line[len(prefix) :])
        for line in path.read_text(encoding="utf-8", errors="strict").splitlines(keepends=True)
        if line.startswith(prefix) and line.endswith("\n")
    ]


def network_sample(folder, processes):
    # These numeric PIDs were discovered from our own host's process tree.
    pids = ",".join(str(pid) for pid in processes.known)
    command = (
        f"$ownedPids=@({pids}); "
        "$ErrorActionPreference='Stop'; "
        "$liveBefore=@(Get-Process -Id $ownedPids -ErrorAction SilentlyContinue | "
        "Select-Object -ExpandProperty Id); "
        "$wv=@(Get-CimInstance Win32_Process | "
        "Where-Object {$_.ProcessId -in $ownedPids -and $_.Name -eq 'msedgewebview2.exe'} | "
        "Select-Object ProcessId,CommandLine); "
        "$tcp=@(Get-NetTCPConnection -ErrorAction Stop | "
        "Where-Object {$_.State -eq 'Listen' -and $_.OwningProcess -in $ownedPids} | "
        "Select-Object OwningProcess,LocalAddress,LocalPort); "
        "$udp=@(Get-NetUDPEndpoint -ErrorAction Stop | "
        "Where-Object {$_.OwningProcess -in $ownedPids} | "
        "Select-Object OwningProcess,LocalAddress,LocalPort); "
        "$liveAfter=@(Get-Process -Id $ownedPids -ErrorAction SilentlyContinue | "
        "Select-Object -ExpandProperty Id); "
        "@{tcp_listeners=$tcp;udp_endpoints=$udp;webview_processes=$wv; "
        "live_before=$liveBefore;live_after=$liveAfter} | ConvertTo-Json -Depth 4"
    )
    with (folder / "network.json").open("wb") as log:
        return subprocess.Popen(
            ["powershell.exe", "-NoProfile", "-Command", command],
            stdout=log,
            stderr=subprocess.STDOUT,
            creationflags=subprocess.CREATE_NO_WINDOW,
        )


def run_case(binary, output, scenario, worker_python):
    folder = output / scenario
    project = folder / "project"
    folder.mkdir()
    shutil.copytree(ROOT / "tests/fixtures/windows-boundary", project)
    shutil.copy2(ROOT / "tests/windows_handles.py", project / "win_handles.py")
    probe = {"scenario": scenario}
    sentinel = None
    startup_options = {}
    sentinel_identity = None
    if scenario == "handle-inheritance":
        import msvcrt

        sentinel = (project / "sentinel.bin").open("xb")
        sentinel.write(b"owned-inheritable-sentinel")
        sentinel.flush()
        sentinel_handle = msvcrt.get_osfhandle(sentinel.fileno())
        os.set_handle_inheritable(sentinel_handle, True)
        sentinel_identity = file_identity(sentinel_handle)
        if "error" in sentinel_identity:
            raise RuntimeError(f"Sentinel identity unavailable: {sentinel_identity}")
        probe.update(sentinel_handle=sentinel_handle, sentinel_identity=sentinel_identity)
        startup = subprocess.STARTUPINFO()
        startup.lpAttributeList = {"handle_list": [sentinel_handle]}
        startup_options["startupinfo"] = startup
    (project / "probe.json").write_text(json.dumps(probe), encoding="utf-8")
    (project / "pyproject.toml").write_text('[tool.pyyaki]\nentry="app:app"\n', encoding="utf-8")
    (project / "ui/config.js").write_text(
        f"export const scenario={json.dumps(scenario)};", encoding="utf-8"
    )
    (project / "ui/other.html").write_text(
        '<h1 id="should-not-load">Wrong page</h1>', encoding="utf-8"
    )
    # Existing, syntactically valid CSS avoids false success from MIME or missing files.
    (project / "private.css").write_text("body {color:red}", encoding="utf-8")
    (project / "ui/.secret.css").write_text("body {color:red}", encoding="utf-8")
    (project / "ui/private").mkdir()
    (project / "ui/private/secret.css").write_text("body {color:red}", encoding="utf-8")
    (project / "ui/_pyyaki").mkdir()
    (project / "ui/_pyyaki/unknown.css").write_text("body {color:red}", encoding="utf-8")
    command = [
        str(binary),
        "run",
        "--project",
        str(project),
        "--python",
        str(worker_python),
        "--diagnostics",
    ]
    env = os.environ.copy()
    if scenario == "invalid-proxy":
        # Child-only diagnostic setting; never edit the machine/browser proxy configuration.
        env["WEBVIEW2_ADDITIONAL_BROWSER_ARGUMENTS"] = (
            "--proxy-server=http://127.0.0.1:9 --proxy-bypass-list=<-loopback>"
        )
    base = Path(os.environ["LOCALAPPDATA"]) / "PyYaki/prototype"
    before = set(base.iterdir()) if base.exists() else set()
    observer = Processes()
    timed_out = False
    network = None
    killed_owned_host = False
    with (folder / "launcher.log").open("wb") as log:
        host = subprocess.Popen(
            command,
            stdout=log,
            stderr=log,
            env=env,
            creationflags=subprocess.CREATE_NO_WINDOW,
            **startup_options,
        )
        (folder / "launch.json").write_text(
            json.dumps(
                {
                    "pid": host.pid,
                    "command": command,
                    "proxy_argument": env.get("WEBVIEW2_ADDITIONAL_BROWSER_ARGUMENTS"),
                },
                indent=2,
            ),
            encoding="utf-8",
        )
        deadline = time.monotonic() + (180 if scenario == "user-actions" else 35)
        try:
            sentinel_in_host = None
            if sentinel is not None:
                os.set_handle_inheritable(sentinel_handle, False)
                sentinel_in_host = identity_in_child(host, sentinel_handle)
            while host.poll() is None:
                observer.sample(host.pid)
                if scenario == "host-kill-tree" and not killed_owned_host:
                    markers = read_markers(folder / "launcher.log", "pyyaki_diagnostic=")
                    spawned = [m["data"] for m in markers if m["phase"] == "fault_ready"]
                    if spawned and spawned[-1]["pid"] in observer.handles:
                        child = spawned[-1]
                        worker_pid = child["worker_pid"]
                        if (
                            worker_pid not in observer.known
                            or observer.known[child["pid"]]["ppid"] != worker_pid
                            or observer.known[worker_pid]["name"] != "python.exe"
                        ):
                            raise RuntimeError("Child identity is outside our host's observed tree")
                        # Only our root Popen handle; the Job Object owns descendant cleanup.
                        host.kill()
                        killed_owned_host = True
                if scenario == "invalid-proxy" and network is None:
                    markers = read_markers(folder / "launcher.log", "pyyaki_diagnostic=")
                    if any(marker["phase"] == "ready" for marker in markers):
                        network = network_sample(folder, observer)
                if time.monotonic() > deadline:
                    timed_out = True
                    host.kill()  # Only the Popen child handle created by this case.
                time.sleep(0.1)
            host.wait(timeout=5)
            drain = time.monotonic() + 10
            while observer.active() and time.monotonic() < drain:
                time.sleep(0.1)
            active = observer.active()
            if network is not None:
                try:
                    network.wait(timeout=15)
                except subprocess.TimeoutExpired:
                    network.kill()
                    network.wait(timeout=5)
            matches = []
            for instance in set(base.iterdir()) - before:
                path = instance / "host.log"
                if path.is_file():
                    markers = read_markers(path, "pyyaki_startup=")
                    if markers and markers[0]["data"] == {"pid": host.pid}:
                        matches.append((instance, markers))
            if len(matches) != 1:
                raise RuntimeError(f"Cannot identify own host log: pid={host.pid}")
            instance, stages = matches[0]
            for name in ("host.log", "worker.log"):
                if (instance / name).exists():
                    shutil.copy2(instance / name, folder / name)
            diagnostics = read_markers(folder / "launcher.log", "pyyaki_diagnostic=")
            results = [m["data"] for m in diagnostics if m["phase"] == "result"]
            missing = [pid for pid in observer.known if pid not in observer.handles]
            record = {
                "scenario": scenario,
                "host_pid": host.pid,
                "exit_code": host.returncode,
                "watchdog": timed_out,
                "killed_owned_host": killed_owned_host,
                "instance": str(instance),
                "stages": stages,
                "diagnostics": diagnostics,
                "remaining_observed_pids": active,
                "observed_processes": observer.known,
                "missing_process_handles": missing,
                "ok": not timed_out
                and host.returncode == 0
                and bool(results)
                and results[-1].get("ok") is True
                and not active
                and not missing,
            }
            if scenario == "host-kill-tree":
                record["ok"] = (
                    killed_owned_host
                    and not timed_out
                    and host.returncode != 0
                    and not active
                    and not missing
                )
            if scenario in ("worker-grandchild", "host-kill-tree"):
                spawned = [m["data"] for m in diagnostics if m["phase"] == "fault_ready"]
                record["spawned_child"] = spawned[-1] if spawned else None
                record["ok"] &= bool(
                    spawned
                    and spawned[-1]["pid"] in observer.handles
                    and observer.known[spawned[-1]["pid"]]["ppid"] == spawned[-1]["worker_pid"]
                )
            if sentinel is not None:
                record["sentinel_parent_identity"] = sentinel_identity
                record["sentinel_host_identity"] = sentinel_in_host
                record["ok"] &= sentinel_in_host == sentinel_identity
            if scenario == "user-actions":
                phases = [stage["phase"] for stage in stages]
                record["ok"] &= all(
                    phase in phases
                    for phase in (
                        "new_window_rejected",
                        "download_rejected",
                    )
                )
            if scenario == "navigation":
                record["ok"] &= any(s["phase"] == "navigation_rejected" for s in stages)
            if scenario == "oversize":
                record["ok"] &= any(
                    s["phase"] == "native_ipc_rejected"
                    and s["data"] == {"reason": "message_too_large"}
                    for s in stages
                )
            if network is not None:
                record["network_exit_code"] = network.returncode
                sample = json.loads((folder / "network.json").read_text(encoding="utf-8-sig"))
                record["network_sample_valid"] = (
                    network.returncode == 0
                    and host.pid in sample["live_before"]
                    and host.pid in sample["live_after"]
                    and any(
                        "--proxy-server=http://127.0.0.1:9" in row["CommandLine"]
                        and "--proxy-bypass-list=<-loopback>" in row["CommandLine"]
                        for row in sample["webview_processes"]
                    )
                )
                record["ok"] &= record["network_sample_valid"]
            (folder / "result.json").write_text(
                json.dumps(record, ensure_ascii=False, indent=2), encoding="utf-8"
            )
            return record
        finally:
            if host.poll() is None:
                host.kill()
                host.wait(timeout=5)
            if network is not None and network.poll() is None:
                network.kill()
                network.wait(timeout=5)
            observer.close()
            if sentinel is not None:
                sentinel.close()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--binary", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--case", choices=CASES, action="append")
    parser.add_argument("--python", type=Path, help="Explicit worker Python; defaults to this venv")
    args = parser.parse_args()
    if sys.platform != "win32":
        parser.error("Requires Windows/WebView2")
    binary = args.binary.resolve(strict=True)
    worker_python = (args.python or Path(sys.executable)).resolve(strict=True)
    output = args.output.resolve()
    output.mkdir(parents=True, exist_ok=False)
    report = {
        "created_at": datetime.now(UTC).isoformat(),
        "source_commit": subprocess.check_output(
            ["git", "rev-parse", "HEAD"], cwd=ROOT, text=True
        ).strip(),
        "working_tree_dirty": bool(
            subprocess.check_output(["git", "status", "--porcelain"], cwd=ROOT, text=True)
        ),
        "probe_sha256": hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
        "fixture_sha256": {
            str(path.relative_to(ROOT)): hashlib.sha256(path.read_bytes()).hexdigest()
            for path in sorted((ROOT / "tests/fixtures/windows-boundary").rglob("*"))
            if path.is_file() and "__pycache__" not in path.parts
        },
        "binary_sha256": hashlib.sha256(binary.read_bytes()).hexdigest(),
        "python": sys.version,
        "worker_python": str(worker_python),
        "support_sha256": hashlib.sha256(
            (ROOT / "tests/windows_handles.py").read_bytes()
        ).hexdigest(),
        "runs": [],
    }
    try:
        # The click case needs explicit selection and a human/Computer Use input session.
        for scenario in args.case or [case for case in CASES if case != "user-actions"]:
            record = run_case(binary, output, scenario, worker_python)
            report["runs"].append(record)
            print(
                f"{scenario}: ok={record['ok']} exit={record['exit_code']} "
                f"watchdog={record['watchdog']}",
                flush=True,
            )
    finally:
        (output / "report.json").write_text(
            json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8"
        )
    return 0 if all(record["ok"] for record in report["runs"]) else 1


if __name__ == "__main__":
    raise SystemExit(main())
