"""Repeat native startup with import/hook delays and retain per-instance logs.

Maintainer tool for Windows/macOS; observes markers, not physical frame presentation.
"""

import argparse
import hashlib
import json
import os
import platform
import shutil
import subprocess
import sys
from datetime import UTC, datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PREFIX = "pyyaki_startup="


def records(path):
    return [
        json.loads(line[len(PREFIX) :])
        for line in path.read_text(encoding="utf-8").splitlines()
        if line.startswith(PREFIX)
    ]


def run_case(binary, output, base, scenario, index, delay, watchdog):
    folder = output / f"{scenario}-{index:02}"
    project = folder / "project"
    shutil.copytree(ROOT / "examples/hello", project, ignore=shutil.ignore_patterns("__pycache__"))
    app = project / "app.py"
    if scenario == "import":
        app.write_text(
            f"import time\ntime.sleep({delay!r})\n" + app.read_text(encoding="utf-8"),
            encoding="utf-8",
        )
    elif scenario == "hooks":
        with app.open("a", encoding="utf-8") as file:
            file.write(
                "\n@app.on_startup\nasync def probe_startup():\n"
                f"    await asyncio.sleep({delay!r})\n"
            )
    before = set(base.iterdir()) if base.exists() else set()
    command = [
        str(binary),
        "run",
        "--project",
        str(project),
        "--python",
        sys.executable,
        "--smoke-test",
    ]
    with (folder / "launcher.log").open("wb") as log:
        process = subprocess.Popen(command, stdout=log, stderr=log, cwd=ROOT)
        closed_by_harness = False
        try:
            process.wait(timeout=watchdog)
        except subprocess.TimeoutExpired:
            closed_by_harness = True
            # Only our own child is terminated. Windows Job Object owns its worker cleanup.
            process.terminate()
            try:
                process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait(timeout=5)
    matches = []
    for instance in (set(base.iterdir()) - before) if base.exists() else []:
        host_log = instance / "host.log"
        if host_log.is_file():
            trace = records(host_log)
            if (
                trace
                and trace[0]["phase"] == "host_started"
                and trace[0]["data"] == {"pid": process.pid}
            ):
                matches.append((instance, trace))
    if len(matches) != 1:
        raise RuntimeError(
            f"Cannot identify own host log: pid={process.pid}, matches={len(matches)}"
        )
    instance, trace = matches[0]
    for name in ("host.log", "worker.log"):
        source = instance / name
        if source.is_file():
            shutil.copy2(source, folder / name)
    host_text = (folder / "host.log").read_text(encoding="utf-8")
    smoke = [
        json.loads(line.removeprefix("native_smoke="))
        for line in host_text.splitlines()
        if line.startswith("native_smoke=")
    ]
    phases = [record["phase"] for record in trace]
    return {
        "scenario": scenario,
        "index": index,
        "pid": process.pid,
        "exit_code": process.returncode,
        "closed_by_harness": closed_by_harness,
        "instance": str(instance),
        "markers": trace,
        "ok": not closed_by_harness
        and process.returncode == 0
        and bool(smoke)
        and smoke[-1].get("ok") is True
        and "sdk_welcome_posted" in phases,
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--binary", type=Path, required=True)
    parser.add_argument("--runs", type=int, default=5)
    parser.add_argument("--delay", type=float, default=5)
    parser.add_argument("--watchdog", type=int, default=30)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    if sys.platform not in ("win32", "darwin"):
        parser.error("Requires Windows/WebView2 or macOS/WKWebView")
    if not 1 <= args.runs <= 100 or not 0 <= args.delay <= 15 or not 20 <= args.watchdog <= 120:
        parser.error("runs: 1..100, delay: 0..15 seconds, watchdog: 20..120 seconds")
    binary = args.binary.resolve(strict=True)
    base = (
        Path(os.environ["LOCALAPPDATA"])
        if sys.platform == "win32"
        else Path.home() / "Library/Application Support"
    ) / "PyYaki/prototype"
    output = (
        args.output
        or ROOT / "test-results" / ("startup-" + datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ"))
    ).absolute()
    output.mkdir(parents=True, exist_ok=False)
    report = {
        "created_at": datetime.now(UTC).isoformat(),
        "platform": platform.platform(),
        "python": sys.version,
        "python_executable": sys.executable,
        "binary": str(binary),
        "binary_sha256": hashlib.sha256(binary.read_bytes()).hexdigest(),
        "source_commit": subprocess.check_output(
            ["git", "rev-parse", "HEAD"], cwd=ROOT, text=True
        ).strip(),
        "working_tree_dirty": bool(
            subprocess.check_output(["git", "status", "--porcelain"], cwd=ROOT, text=True)
        ),
        "delay_seconds": args.delay,
        "note": "Marker times include scheduling/IPC, not physical frame presentation.",
        "runs": [],
    }
    print(f"Output: {output}", flush=True)
    try:
        for scenario in ("baseline", "import", "hooks"):
            for index in range(args.runs):
                record = run_case(binary, output, base, scenario, index, args.delay, args.watchdog)
                report["runs"].append(record)
                print(
                    f"{scenario}-{index:02}: ok={record['ok']} exit={record['exit_code']}",
                    flush=True,
                )
    finally:
        (output / "report.json").write_text(
            json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8"
        )
    return 0 if all(record["ok"] for record in report["runs"]) else 1


if __name__ == "__main__":
    raise SystemExit(main())
