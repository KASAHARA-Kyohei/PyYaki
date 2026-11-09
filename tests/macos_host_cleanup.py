"""Own-process-only host death probes; preserve pre-cleanup evidence and raw logs."""

import argparse
import hashlib
import json
import math
import os
import shutil
import signal
import subprocess
import sys
import time
import traceback
from pathlib import Path

from startup_probe import ROOT, records, wait_owned

SCENARIOS = (
    "import",
    "hooks",
    "rpc-gil",
    "idle",
    "stopped",
    "worker-crash",
    "guard-crash",
    "normal",
)
BASE = Path.home() / "Library/Application Support/PyYaki/prototype"

# Supplement GUI probes when macOS suppresses rAF. This owner exercises the real private
# guard and worker protocol, but its markers do NOT establish GUI startup or rendering.
OWNER = """import contextlib,json,os,signal,struct,subprocess,sys,time
from pathlib import Path
from pyyaki import __version__
from pyyaki.worker import read_frame,write_frame,read_exact
binary,python,project,base,scenario = sys.argv[1:]
folder=Path(base)/"owner";folder.mkdir(parents=True)
log=(folder/"host.log").open("w")
def mark(phase,data=None):
    log.write("pyyaki_startup="+json.dumps(dict(phase=phase,data=data,headless_owner=True))+"\\n");log.flush()
mark("host_started",dict(pid=os.getpid()))
r1,w1=os.pipe();r2,w2=os.pipe();life_r,life_w=os.pipe();status_r,status_w=os.pipe()
workerlog=(folder/"worker.log").open("wb")
guard=subprocess.Popen([binary,"--__pyyaki-worker-guard",str(r1),str(w2),str(life_r),str(status_w),python,project],
    cwd=project,pass_fds=(r1,w2,life_r,status_w),process_group=0,stdout=workerlog,stderr=workerlog)
for fd in (r1,w2,life_r,status_w):os.close(fd)
stopping=False
def stop(sig,frame):
    global stopping
    stopping=True
    with contextlib.suppress(OSError):write_frame(w1,dict(v=1,type="shutdown"))
signal.signal(signal.SIGTERM,stop)
try:
    worker=struct.unpack("<I",read_exact(status_r,4))[0]
    mark("python_spawned",dict(worker_pid=worker,guard_pid=guard.pid,process_group=guard.pid))
    write_frame(w1,dict(v=1,type="worker_hello",nonce="a"*64,framework_version=__version__))
    while read_frame(r2)["type"] != "worker_welcome":pass
    def dispatch(message):
        write_frame(w1,dict(v=1,type="dispatch",session_id="cleanup",message=message))
    dispatch(dict(v=1,type="hello",client_version=__version__))
    assert read_frame(r2)["message"]["type"] == "welcome"
    mark("sdk_welcome_posted")
    if scenario == "rpc-gil":
        dispatch(dict(v=1,type="call",id="block",method="block_gil",params={},timeout_ms=120000))
    while True:read_frame(r2)
except (EOFError,BrokenPipeError):
    if not stopping:
        mark("backend_failed")
        while not stopping:time.sleep(0.02)
        raise SystemExit(1)
finally:
    # This Popen child remains unreaped: its PID still reserves the private group ID.
    try:os.killpg(guard.pid,signal.SIGKILL)
    except OSError as error:print("owner_group_cleanup_errno="+str(error.errno),file=sys.stderr)
    guard.wait(timeout=5)
    for fd in (w1,r2,life_w,status_r):os.close(fd)
"""


def save(path, value):
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding="utf-8")


def table():
    text = subprocess.check_output(
        ["ps", "-axo", "pid=,ppid=,pgid=,stat=,lstart=,command="], text=True
    )
    result = {}
    for line in text.splitlines():
        parts = line.split(maxsplit=9)
        if len(parts) == 10:
            result[int(parts[0])] = {
                "ppid": int(parts[1]),
                "pgid": int(parts[2]),
                "state": parts[3],
                "started": " ".join(parts[4:9]),
                "command": parts[9],
            }
    return result


def same_process(first, current):
    return bool(
        current
        and first["started"] == current["started"]
        and (first["command"] == current["command"] or current["state"].startswith("Z"))
        and first["pgid"] == current["pgid"]
    )


def read_trace(path):
    try:
        return records(path)
    except FileNotFoundError, json.JSONDecodeError:
        return []  # A log line may be observed before its write completes.


def run_case(binary, python, folder, scenario, timeout, headless=False):
    folder.mkdir()
    project = folder / "日本語 project"
    shutil.copytree(ROOT / "examples/hello", project, ignore=shutil.ignore_patterns("__pycache__"))
    child_script = project / "owned_child.py"
    child_script.write_text("""import json,os,time
from pathlib import Path
Path("child.json").write_text(json.dumps(dict(pid=os.getpid(),ppid=os.getppid(),pgid=os.getpgrp())))
time.sleep(120)
""")
    instrument = """import asyncio,ctypes,json,os,subprocess,sys,time,threading
from pathlib import Path
def mark(phase):
    Path("phase.json").write_text(json.dumps(dict(phase=phase,pid=os.getpid(),ppid=os.getppid(),pgid=os.getpgrp())))
child = subprocess.Popen([sys.executable,"-I",str(Path("owned_child.py").absolute())],
    stdin=subprocess.DEVNULL,stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL)
mark("import")
if SCENARIO == "rpc-gil":
    def heartbeat():
        tick = 0
        while True:
            tick += 1
            Path("heartbeat.txt").write_text(str(tick))
            time.sleep(0.03)
    threading.Thread(target=heartbeat,daemon=True).start()
if SCENARIO == "import": time.sleep(120)
"""
    source = (project / "app.py").read_text()
    suffix = """
@app.on_startup
async def cleanup_startup():
    mark("hooks")
    if SCENARIO == "hooks": await asyncio.sleep(120)
@app.on_shutdown
async def cleanup_shutdown():
    Path("shutdown.json").write_text(json.dumps(dict(pid=os.getpid())))
@app.command("block_gil")
async def block_gil() -> None:
    await asyncio.sleep(0.2)
    mark("rpc-gil")
    ctypes.PyDLL(None).sleep(120)
"""
    (project / "app.py").write_text(f"SCENARIO={scenario!r}\n" + instrument + source + suffix)
    js = project / "ui/main.js"
    js.write_text(
        js.read_text()
        + """
if (SCENARIO === "rpc-gil") pyyaki.invoke("block_gil").catch(() => {});
""".replace("SCENARIO", json.dumps(scenario))
    )
    base = folder / "owner-instances" if headless else BASE
    before = set(base.iterdir()) if base.exists() else set()
    result = {"scenario": scenario, "ok": False, "owned": {}, "observer_cleanup": []}
    instance = None
    with (folder / "launcher.log").open("wb") as log:
        if headless:
            owner = folder / "owner.py"
            owner.write_text(OWNER)
            command = [
                str(python),
                "-I",
                str(owner),
                str(binary),
                str(python),
                str(project),
                str(base),
                scenario,
            ]
        else:
            command = [
                str(binary),
                "run",
                "--project",
                str(project),
                "--python",
                str(python),
                "--diagnostics",
            ]
        process = subprocess.Popen(
            command,
            cwd=ROOT,
            stdout=log,
            stderr=log,
        )
        result["host_pid"] = process.pid
        try:
            deadline = time.monotonic() + 20
            while True:
                assert process.poll() is None, "host exited before fault injection"
                for candidate in (set(base.iterdir()) - before) if base.exists() else []:
                    trace = read_trace(candidate / "host.log")
                    if trace and trace[0].get("data") == {"pid": process.pid}:
                        instance = candidate
                try:
                    phase = json.loads((project / "phase.json").read_text())
                    child = json.loads((project / "child.json").read_text())
                except FileNotFoundError, json.JSONDecodeError:
                    phase = child = None
                trace = read_trace(instance / "host.log") if instance else []
                ready = any(row["phase"] == "sdk_welcome_posted" for row in trace)
                target = scenario if scenario in ("import", "hooks", "rpc-gil") else "hooks"
                if (
                    phase
                    and child
                    and phase["phase"] == target
                    and (ready or scenario in ("import", "hooks"))
                ):
                    break
                assert time.monotonic() < deadline, "probe did not reach requested phase"
                time.sleep(0.03)
            snapshot = table()
            worker = phase["pid"]
            assert snapshot[child["pid"]]["ppid"] == worker
            assert snapshot[child["pid"]]["pgid"] == snapshot[worker]["pgid"]
            parent = snapshot[worker]["ppid"]
            assert parent == process.pid or snapshot[parent]["ppid"] == process.pid
            if parent != process.pid:
                assert "--__pyyaki-worker-guard" in snapshot[parent]["command"]
                assert snapshot[parent]["pgid"] == parent == snapshot[worker]["pgid"]
                assert any(
                    row["phase"] == "python_spawned"
                    and row.get("data", {}).get("guard_pid") == parent
                    and row["data"].get("worker_pid") == worker
                    for row in trace
                )
            owned = {worker, child["pid"]}
            if parent != process.pid:
                owned.add(parent)
            for pid in owned:
                result["owned"][str(pid)] = snapshot[pid]
            save(
                folder / "before-fault.json",
                {"phase": phase, "child": child, "owned": result["owned"], "trace": trace},
            )
            if scenario == "rpc-gil":
                heartbeat = project / "heartbeat.txt"
                tick = heartbeat.read_text()
                assert int(tick) >= 2
                time.sleep(0.2)
                assert heartbeat.read_text() == tick, "GIL heartbeat did not stop"
                result["blocked_heartbeat_tick"] = int(tick)
            if scenario == "stopped":
                os.kill(worker, signal.SIGSTOP)
                deadline = time.monotonic() + 2
                while not table()[worker]["state"].startswith("T"):
                    assert time.monotonic() < deadline, "worker did not stop"
                    time.sleep(0.01)
                result["worker_stopped_before_fault"] = True
            if scenario == "worker-crash":
                os.kill(worker, signal.SIGKILL)
            elif scenario == "guard-crash":
                assert parent != process.pid, "this host has no guard"
                os.kill(parent, signal.SIGKILL)
            elif scenario == "normal":
                process.terminate()
            else:
                process.kill()
            if scenario in ("worker-crash", "guard-crash"):
                deadline = time.monotonic() + 12
                while not any(
                    row["phase"] == "backend_failed" for row in read_trace(instance / "host.log")
                ):
                    assert time.monotonic() < deadline, "worker death not detected"
                    time.sleep(0.03)
                process.terminate()
            assert not wait_owned(process, 5), "host did not exit"
            result["host_exit_code"] = process.returncode
            deadline = time.monotonic() + timeout
            while True:
                snapshot = table()
                remaining = {
                    pid: snapshot[int(pid)]
                    for pid in result["owned"]
                    if int(pid) in snapshot
                    and same_process(result["owned"][pid], snapshot[int(pid)])
                }
                if not remaining or time.monotonic() >= deadline:
                    break
                time.sleep(0.05)
            result["remaining_before_observer_cleanup"] = remaining
            result["shutdown_hook"] = (project / "shutdown.json").exists()
            expected = (
                0
                if scenario == "normal"
                else 1
                if scenario in ("worker-crash", "guard-crash")
                else -9
            )
            result["ok"] = (
                not remaining
                and process.returncode == expected
                and (scenario != "normal" or result["shutdown_hook"])
            )
            save(folder / "observed.json", result)
        except BaseException:
            result["error"] = traceback.format_exc()
        finally:
            wait_owned(process, 1)
            # Retain ownership snapshots even if setup failed before injection.
            for pid, original in result["owned"].items():
                current = table().get(int(pid))
                if same_process(original, current) and not current["state"].startswith("Z"):
                    try:
                        os.kill(int(pid), signal.SIGKILL)
                    except ProcessLookupError:
                        continue
                    result["observer_cleanup"].append(int(pid))
            deadline = time.monotonic() + 3
            while True:
                current = table()
                after = [
                    pid
                    for pid, original in result["owned"].items()
                    if same_process(original, current.get(int(pid)))
                ]
                if not after or time.monotonic() >= deadline:
                    break
                time.sleep(0.05)
            result["remaining_after_observer_cleanup"] = after
            result["ok"] &= not after and not result["observer_cleanup"]
            if instance:
                for name in ("host.log", "worker.log"):
                    source = instance / name
                    if source.is_file():
                        shutil.copy2(source, folder / name)
            save(folder / "result.json", result)
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--binary", type=Path, required=True)
    parser.add_argument("--python", type=Path, default=ROOT / ".venv/bin/python")
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--scenario", choices=SCENARIOS, action="append")
    parser.add_argument("--fullscreen-control", type=Path)
    parser.add_argument("--headless-owner", action="store_true")
    parser.add_argument("--cleanup-timeout", type=float, default=3)
    args = parser.parse_args()
    if sys.platform != "darwin":
        parser.error("requires macOS")
    if not math.isfinite(args.cleanup_timeout) or not 0 < args.cleanup_timeout <= 30:
        parser.error("cleanup timeout must be in (0, 30]")
    output = args.output.resolve()
    if not output.is_relative_to(ROOT / "test-results"):
        parser.error("output must be new and under test-results")
    output.mkdir(parents=True, exist_ok=False)
    scenarios = args.scenario or list(SCENARIOS)
    if args.headless_owner:
        if args.fullscreen_control or "guard-crash" in (args.scenario or []):
            parser.error(
                "headless owner excludes fullscreen control and native guard-crash detection"
            )
        scenarios = [name for name in scenarios if name != "guard-crash"]
    if len(set(scenarios)) != len(scenarios):
        parser.error("scenarios must be unique")
    report = {
        "planned": scenarios,
        "cases": [],
        "status": "running",
        "binary_sha256": hashlib.sha256(args.binary.read_bytes()).hexdigest(),
        "commit": subprocess.check_output(
            ["git", "rev-parse", "HEAD"], cwd=ROOT, text=True
        ).strip(),
        "dirty": bool(subprocess.check_output(["git", "status", "--porcelain"], cwd=ROOT)),
        "mode": "headless-private-guard" if args.headless_owner else "native-gui-host",
    }
    try:
        for scenario in scenarios:

            def action(selected=scenario):
                return run_case(
                    args.binary.resolve(),
                    args.python.absolute(),
                    output / selected,
                    selected,
                    args.cleanup_timeout,
                    args.headless_owner,
                )

            if args.fullscreen_control:
                from macos_regression import fullscreen_case

                control = fullscreen_case(
                    args.fullscreen_control.resolve(), output / f"{scenario}-control", action
                )
                row = control["startup"]
                row["space_control_ok"] = control["ok"]
                row["ok"] &= control["ok"]
            else:
                row = action()
            report["cases"].append(row)
            print(
                scenario,
                row["ok"],
                "remaining",
                list(row.get("remaining_before_observer_cleanup", {})),
                flush=True,
            )
            save(output / "report.json", report)
        report["status"] = "passed" if all(row["ok"] for row in report["cases"]) else "failed"
    except BaseException:
        report["status"] = "failed"
        raise
    finally:
        report["not_completed"] = scenarios[len(report["cases"]) :]
        save(output / "report.json", report)
    return 0 if report["status"] == "passed" else 1


if __name__ == "__main__":
    raise SystemExit(main())
