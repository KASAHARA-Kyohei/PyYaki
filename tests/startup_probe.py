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

from startup_checks import validate_startup

ROOT = Path(__file__).resolve().parents[1]
PREFIX = "pyyaki_startup="


def records(path):
    return [
        json.loads(line[len(PREFIX) :])
        for line in path.read_text(encoding="utf-8").splitlines()
        if line.startswith(PREFIX)
    ]


def wait_owned(process, watchdog):
    """Reap only this Popen child, including when the caller is interrupted."""
    expired = False
    try:
        try:
            process.wait(timeout=watchdog)
        except subprocess.TimeoutExpired:
            expired = True
    finally:
        if process.poll() is None:
            process.terminate()
            try:
                process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait(timeout=5)
    return expired


def observe(command, folder, base, watchdog, *, env=None):
    """Collect one owned host's logs for either development or packaged invocation."""
    before = set(base.iterdir()) if base.exists() else set()
    pending_error = None
    with (folder / "launcher.log").open("wb") as log:
        process = subprocess.Popen(command, stdout=log, stderr=log, cwd=ROOT, env=env)
        closed_by_harness = False
        try:
            closed_by_harness = wait_owned(process, watchdog)
        except BaseException as error:
            pending_error = error
        finally:
            (folder / "process.json").write_text(
                json.dumps(
                    {
                        "pid": process.pid,
                        "command": command,
                        "exit_code": process.returncode,
                        "closed_by_harness": closed_by_harness,
                        "interrupted": isinstance(pending_error, KeyboardInterrupt),
                    },
                    indent=2,
                ),
                encoding="utf-8",
            )
    matches = []
    for instance in (set(base.iterdir()) - before) if base.exists() else []:
        host_log = instance / "host.log"
        if not host_log.is_file():
            continue
        try:
            first_line = next(
                line
                for line in host_log.read_text(encoding="utf-8").splitlines()
                if line.startswith(PREFIX)
            )
            first = json.loads(first_line[len(PREFIX) :])
        except OSError, UnicodeError, ValueError, StopIteration:
            continue
        if not isinstance(first, dict):
            continue
        if first.get("phase") == "host_started" and first.get("data") == {"pid": process.pid}:
            matches.append(instance)
    if len(matches) != 1:
        if pending_error is not None:
            raise pending_error
        raise RuntimeError(
            f"Cannot identify own host log: pid={process.pid}, matches={len(matches)}"
        )
    instance = matches[0]
    for name in ("host.log", "worker.log"):
        source = instance / name
        if source.is_file():
            shutil.copy2(source, folder / name)
    if pending_error is not None:
        raise pending_error
    trace = records(folder / "host.log")
    host_text = (folder / "host.log").read_text(encoding="utf-8")
    smoke = [
        json.loads(line.removeprefix("native_smoke="))
        for line in host_text.splitlines()
        if line.startswith("native_smoke=")
    ]
    phases = [record["phase"] for record in trace]
    return {
        "pid": process.pid,
        "exit_code": process.returncode,
        "closed_by_harness": closed_by_harness,
        "instance": str(instance),
        "markers": trace,
        "smoke": smoke,
        "ok": not closed_by_harness
        and process.returncode == 0
        and bool(smoke)
        and smoke[-1].get("ok") is True
        and "sdk_welcome_posted" in phases,
    }


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
    command = [
        str(binary),
        "run",
        "--project",
        str(project),
        "--python",
        sys.executable,
        "--smoke-test",
    ]
    result = observe(command, folder, base, watchdog)
    result.update(scenario=scenario, index=index)
    result["validation_errors"] = validate_startup(result["markers"])
    result["ok"] &= not result["validation_errors"]
    return result


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
        "expected_runs": 3 * args.runs,
        "status": "running",
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
        report["status"] = (
            "passed"
            if (
                len(report["runs"]) == report["expected_runs"]
                and all(record["ok"] for record in report["runs"])
            )
            else "failed"
        )
    except KeyboardInterrupt:
        report.update(status="interrupted", error="KeyboardInterrupt")
    except Exception as error:
        report.update(status="error", error=f"{type(error).__name__}: {error}")
    finally:
        (output / "report.json").write_text(
            json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8"
        )
    return 130 if report["status"] == "interrupted" else (0 if report["status"] == "passed" else 1)


if __name__ == "__main__":
    raise SystemExit(main())
