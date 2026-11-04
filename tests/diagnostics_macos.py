"""Real WKWebView measurements and fault injection; no third-party Python dependencies."""

import argparse
import hashlib
import json
import math
import os
import platform
import queue
import shutil
import signal
import subprocess
import sys
import threading
import time
from datetime import UTC, datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PREFIX = "pyyaki_diagnostic="


def distribution(values):
    if not values:
        return None
    ordered = sorted(values)
    return {
        "n": len(values),
        "p50": ordered[math.ceil(len(values) * 0.50) - 1],
        "p95": ordered[math.ceil(len(values) * 0.95) - 1],
        "max": ordered[-1],
    }


def processes():
    result = subprocess.run(
        ["ps", "-axo", "pid=,ppid=,rss=,pcpu=,stat="], capture_output=True, text=True, check=True
    )
    return {
        int(parts[0]): {
            "ppid": int(parts[1]),
            "rss_kib": int(parts[2]),
            "cpu_percent": float(parts[3]),
            "state": parts[4],
        }
        for line in result.stdout.splitlines()
        if len(parts := line.split()) == 5
    }


def sample_processes(host):
    table = processes()
    selected = {host}
    while True:
        children = {pid for pid, row in table.items() if row["ppid"] in selected}
        if children <= selected:
            break
        selected |= children
    return {str(pid): row for pid, row in table.items() if pid in selected}


def run_case(
    binary,
    output,
    scenario,
    index=0,
    duration=0,
    rpc_samples=100,
    delay=0,
    prevent_idle_sleep=False,
):
    project = output / f"{scenario}-{index:02}"
    shutil.copytree(
        ROOT / "tests/fixtures/diagnostics", project, ignore=shutil.ignore_patterns("__pycache__")
    )
    config = {
        "scenario": scenario,
        "duration_seconds": duration,
        "rpc_samples": rpc_samples,
        "startup_delay_seconds": delay,
    }
    (project / "probe.json").write_text(json.dumps(config))
    (project / "ui/config.js").write_text(f"export default {json.dumps(config)};\n")
    (project / "pyproject.toml").write_text('[tool.pyyaki]\nentry="app:app"\n')
    command = [
        str(binary),
        "run",
        "--project",
        str(project),
        "--python",
        str(ROOT / ".venv/bin/python"),
        "--diagnostics",
    ]
    markers = queue.Queue()
    started = time.perf_counter()
    wall_started = time.time()
    process = subprocess.Popen(
        command, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE, text=True
    )
    sleep_guard = None

    def reader():
        with (project / "host-stderr.log").open("w") as log:
            for line in process.stderr:
                observed = time.perf_counter() - started
                log.write(line)
                log.flush()
                if line.startswith(PREFIX):
                    marker = json.loads(line[len(PREFIX) :])
                    marker["observer_elapsed_ms"] = observed * 1000
                    markers.put(marker)

    thread = threading.Thread(target=reader, daemon=True)
    thread.start()
    record = {"scenario": scenario, "config": config, "markers": [], "memory": []}
    worker_pid = None
    stopped = False
    shutdown_at = None
    result = None
    next_sample = 0
    deadline = started + duration + 35 + delay
    try:
        if prevent_idle_sleep:
            sleep_guard = subprocess.Popen(
                ["/usr/bin/caffeinate", "-i", "-w", str(process.pid)],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
        while process.poll() is None or thread.is_alive() or not markers.empty():
            now = time.perf_counter()
            gap = time.time() - wall_started - (now - started)
            if abs(gap) > 5:
                raise RuntimeError(
                    f"{scenario}: wall/monotonic clock gap {gap:.1f}s; "
                    "continuous interval invalid (sleep or clock adjustment)"
                )
            if now > deadline:
                raise TimeoutError(f"{scenario}: diagnostic watchdog expired")
            if now >= next_sample and process.poll() is None:
                record["memory"].append(
                    {"elapsed_seconds": now - started, "processes": sample_processes(process.pid)}
                )
                next_sample = now + 10
            try:
                marker = markers.get(timeout=0.05)
            except queue.Empty:
                continue
            record["markers"].append(marker)
            phase = marker["phase"]
            if phase == "sample":
                (project / "progress.json").write_text(
                    json.dumps(
                        {
                            "sample": marker["data"],
                            "memory": record["memory"][-1] if record["memory"] else None,
                        },
                        indent=2,
                    )
                )
                if marker["data"]["batches"] % 60 == 0:
                    print(
                        f"  {scenario}: {marker['data']['elapsed_ms'] / 1000:.0f}s, "
                        f"echo={marker['data']['echo_calls']}, "
                        f"tasks={marker['data']['asyncio_tasks']}, fd={marker['data']['fds']}",
                        flush=True,
                    )
            if phase == "interactive":
                record["memory"].append(
                    {
                        "elapsed_seconds": time.perf_counter() - started,
                        "processes": sample_processes(process.pid),
                    }
                )
                next_sample = time.perf_counter() + 10
            if phase == "fault_ready":
                worker_pid = marker["data"]["pid"]
                table = processes()
                if table.get(worker_pid, {}).get("ppid") != process.pid:
                    raise RuntimeError("Refusing fault injection into an unrelated process")
                if scenario == "freeze":
                    os.kill(worker_pid, signal.SIGSTOP)
                    stopped = True
                elif scenario == "crash":
                    os.kill(worker_pid, signal.SIGKILL)
                elif scenario == "shutdown":
                    shutdown_at = time.perf_counter()
                    process.terminate()
            elif phase == "paused_timeout" and stopped:
                os.kill(worker_pid, signal.SIGCONT)
                stopped = False
            elif phase == "result":
                result = marker["data"]
                if result.get("resources"):
                    worker_pid = result["resources"][0]["pid"]
                shutdown_at = time.perf_counter()
        process.wait(timeout=5)
        thread.join(timeout=2)
        record["exit_code"] = process.returncode
        record["total_seconds"] = time.perf_counter() - started
        record["shutdown_ms"] = (
            (time.perf_counter() - shutdown_at) * 1000 if shutdown_at is not None else None
        )
        record["result"] = result
        shutdown_file = project / "shutdown.json"
        record["shutdown_hook"] = (
            json.loads(shutdown_file.read_text()) if shutdown_file.exists() else None
        )
        after = processes()
        known = {int(pid) for sample in record["memory"] for pid in sample["processes"]}
        if worker_pid is not None:
            known.add(worker_pid)
        record["remaining_pids"] = sorted(pid for pid in known if pid in after)
        record["ok"] = (
            process.returncode == 0
            and not record["remaining_pids"]
            and (scenario == "shutdown" or bool(result and result.get("ok")))
        )
        if scenario == "shutdown":
            record["ok"] &= bool(record["shutdown_hook"]) and record["shutdown_ms"] < 3000
        for name in ("rpc_ms", "soak_rpc_ms", "timer_lag_ms"):
            if result and name in result:
                record[name] = distribution(result[name])
        return record
    finally:
        if process.poll() is None:
            process.terminate()
            try:
                process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait(timeout=3)
        if sleep_guard is not None:
            try:
                sleep_guard.wait(timeout=2)
            except subprocess.TimeoutExpired:
                sleep_guard.terminate()
                sleep_guard.wait(timeout=2)
        # Host Drop owns worker process-group cleanup, including a SIGSTOP'ed worker.
        thread.join(timeout=2)
        process.stderr.close()
        record["wall_elapsed_seconds"] = time.time() - wall_started
        record["clock_gap_seconds"] = record["wall_elapsed_seconds"] - (
            time.perf_counter() - started
        )
        record["prevent_idle_sleep"] = prevent_idle_sleep
        (project / "observation.json").write_text(json.dumps(record, indent=2))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--binary", type=Path, default=ROOT / "native/target/release/pyyaki")
    parser.add_argument("--output", type=Path)
    parser.add_argument("--duration", type=int, default=300)
    parser.add_argument("--startup-runs", type=int, default=10)
    parser.add_argument(
        "--prevent-idle-sleep",
        action="store_true",
        help="Hold a temporary caffeinate -i assertion for each owned host",
    )
    args = parser.parse_args()
    if sys.platform != "darwin":
        parser.error("This harness requires macOS/WKWebView")
    if not 0 <= args.duration <= 3600 or not 1 <= args.startup_runs <= 100:
        parser.error("duration must be 0..3600; startup-runs must be 1..100")
    binary = args.binary.resolve(strict=True)
    output = args.output or ROOT / "test-results" / (
        "diagnostics-" + datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    )
    output = output.absolute()
    output.mkdir(parents=True, exist_ok=False)
    records = []
    report = {
        "created_at": datetime.now(UTC).isoformat(),
        "os": platform.platform(),
        "macos": platform.mac_ver()[0],
        "cpu": subprocess.check_output(
            ["sysctl", "-n", "machdep.cpu.brand_string"], text=True
        ).strip(),
        "physical_memory_bytes": int(
            subprocess.check_output(["sysctl", "-n", "hw.memsize"], text=True)
        ),
        "python": sys.version,
        "binary": str(binary),
        "binary_sha256": hashlib.sha256(binary.read_bytes()).hexdigest(),
        "source_commit": subprocess.check_output(
            ["git", "rev-parse", "HEAD"], cwd=ROOT, text=True
        ).strip(),
        "working_tree_dirty": bool(
            subprocess.check_output(["git", "status", "--porcelain"], cwd=ROOT, text=True)
        ),
        "notes": [
            "Fresh WebView profile on every launch; OS caches were not cleared.",
            "Observer markers include IPC/stderr delivery and are not physical frame presentation.",
            "RSS covers host and discovered descendants, not unattributed WebKit XPC processes.",
            "No Windows performance or long-term stability guarantee.",
            "Wall/monotonic gaps over 5 seconds invalidate the continuous interval.",
        ],
        "prevent_idle_sleep": args.prevent_idle_sleep,
        "runs": records,
    }
    print(f"Output: {output}", flush=True)
    try:
        cases = [("baseline", index, 0, 100, 0) for index in range(args.startup_runs)]
        cases += [
            ("delayed", 0, 0, 0, 5),
            ("freeze", 0, 0, 0, 0),
            ("crash", 0, 0, 0, 0),
            ("shutdown", 0, 0, 0, 0),
            ("soak", 0, args.duration, 100, 0),
        ]
        for scenario, index, duration, samples, delay in cases:
            print(f"Starting {scenario}-{index:02} ({duration}s)", flush=True)
            try:
                record = run_case(
                    binary,
                    output,
                    scenario,
                    index,
                    duration,
                    samples,
                    delay,
                    prevent_idle_sleep=args.prevent_idle_sleep,
                )
            except Exception as error:
                records.append({"scenario": scenario, "ok": False, "harness_error": str(error)})
                raise
            records.append(record)
            (output / "report.json").write_text(json.dumps(report, indent=2))
            print(f"  ok={record['ok']} exit={record['exit_code']}", flush=True)
            if not record["ok"]:
                raise RuntimeError(f"{scenario} failed; see report and host-stderr.log")
    finally:
        (output / "report.json").write_text(json.dumps(report, indent=2))


if __name__ == "__main__":
    main()
