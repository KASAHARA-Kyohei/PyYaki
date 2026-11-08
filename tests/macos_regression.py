"""Mac maintainer regression: own windows, retained evidence, no physical-display claim.

Use --build to build the host/fixture, --checks for source checks, and an optional
--bundle/--manifest pair to verify and relocate an existing trusted build.
"""

import argparse
import hashlib
import json
import os
import platform
import shutil
import subprocess
import sys
import sysconfig
import time
from datetime import UTC, datetime
from pathlib import Path

from macos_bundle_checks import verify_manifest
from startup_checks import validate_startup
from startup_probe import ROOT, observe, run_case, wait_owned


def save(path, value):
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding="utf-8")


def digest(path):
    with path.open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def completed(report, expected):
    return (
        expected > 0
        and len(report["planned_cases"]) == expected
        and len(set(report["planned_cases"])) == expected
        and len(report["cases"]) == expected
        and sorted(row["name"] for row in report["cases"]) == sorted(report["planned_cases"])
        and all(row.get("ok") is True for row in report["cases"])
        and all(row["exit_code"] == 0 for row in report["commands"])
    )


def command_check(output, label, command):
    with (output / f"{label}.log").open("xb") as log:
        process = subprocess.Popen(command, cwd=ROOT, stdout=log, stderr=subprocess.STDOUT)
        expired = False
        try:
            expired = wait_owned(process, 600)
        finally:
            result = {
                "name": label,
                "command": command,
                "pid": process.pid,
                "exit_code": 124 if expired else process.returncode,
                "watchdog": expired,
            }
            save(output / f"{label}.json", result)
    return result


def ready_control(process, folder, timeout=25):
    deadline = time.monotonic() + timeout
    path = folder / "ready.json"
    while time.monotonic() < deadline:
        if path.is_file():
            try:
                data = json.loads(path.read_text(encoding="utf-8"))
            except json.JSONDecodeError:
                pass  # create_new + write_all can be observed before the last byte is written.
            else:
                if (
                    data.get("pid") != process.pid
                    or type(data.get("stable_ms")) is not int
                    or data["stable_ms"] < 2000
                    or not all(
                        data.get("state", {}).get(key) is True
                        for key in (
                            "fullscreen",
                            "active_space",
                            "unoccluded",
                            "application_active",
                        )
                    )
                ):
                    raise ValueError("Invalid fullscreen readiness evidence")
                if process.poll() is not None:
                    raise RuntimeError("Fullscreen control exited before the test")
                return data
        if process.poll() is not None:
            raise RuntimeError("Fullscreen control did not become ready")
        time.sleep(0.1)
    raise TimeoutError("Fullscreen control readiness watchdog")


def fullscreen_case(control, folder, action):
    folder.mkdir(parents=True, exist_ok=False)
    state_folder = folder / "control"
    with (folder / "control.log").open("xb") as log:
        process = subprocess.Popen(
            [str(control), str(state_folder)], cwd=ROOT, stdout=log, stderr=subprocess.STDOUT
        )
        result = {"control_pid": process.pid, "ok": False}
        try:
            result["before_child"] = ready_control(process, state_folder)
            result["startup"] = action()
        except BaseException as error:
            result["error"] = f"{type(error).__name__}: {error}"
            raise
        finally:
            try:
                if process.poll() is None:
                    # Publish the complete request without replacing an existing file.
                    staged = state_folder / "finish-staged.json"
                    try:
                        with staged.open("x", encoding="utf-8") as stream:
                            json.dump({"controller_pid": process.pid}, stream)
                        os.link(staged, state_folder / "finish.json")
                        staged.unlink()
                    except OSError as error:
                        result["cleanup_error"] = f"{type(error).__name__}: {error}"
            finally:
                expired = wait_owned(process, 10)
                result.update(control_exit_code=process.returncode, control_watchdog=expired)
                control_report = state_folder / "report.json"
                if control_report.is_file():
                    try:
                        result["control"] = json.loads(control_report.read_text(encoding="utf-8"))
                    except (OSError, ValueError) as error:
                        result["control_error"] = f"{type(error).__name__}: {error}"
                result["ok"] = (
                    process.returncode == 0
                    and not expired
                    and result.get("startup", {}).get("ok") is True
                    and result.get("control", {}).get("pid") == process.pid
                    and result.get("control", {}).get("ok") is True
                    and result.get("control", {}).get("status") == "completed"
                    and result.get("control", {}).get("ready") is True
                    and result.get("control", {}).get("state", {}).get("fullscreen") is False
                    and "cleanup_error" not in result
                )
                save(folder / "result.json", result)
    return result


def bundle_case(bundle, folder, base, watchdog):
    folder.mkdir(parents=True, exist_ok=False)
    environment = os.environ.copy()
    environment.update(
        PATH="/usr/bin:/bin:/usr/sbin:/sbin",
        PYTHONHOME="/nonexistent/pyyaki-regression-home",
        PYTHONPATH="/nonexistent/pyyaki-regression-path",
    )
    result = observe(
        [str(bundle / "Contents/MacOS/PyYaki"), "--smoke-test"],
        folder,
        base,
        watchdog,
        env=environment,
    )
    result["validation_errors"] = validate_startup(result["markers"], require_space_restore=True)
    result["ok"] &= not result["validation_errors"]
    save(folder / "result.json", result)
    return result


def verify_bundle(bundle, manifest, output, label, commands):
    result = verify_manifest(bundle, manifest)
    check = command_check(
        output,
        label,
        ["/usr/bin/codesign", "--verify", "--deep", "--strict", "--verbose=2", str(bundle)],
    )
    commands.append(check)
    result["ok"] &= check["exit_code"] == 0
    save(output / f"{label}.json", result)
    return result


def parse_args():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--binary", type=Path, default=ROOT / "native/target/release/pyyaki")
    parser.add_argument(
        "--fullscreen-control",
        type=Path,
        default=ROOT / "native/target/release/examples/macos_space_control",
    )
    parser.add_argument("--runs", type=int, default=3)
    parser.add_argument("--delay", type=float, default=5)
    parser.add_argument("--fullscreen-runs", type=int, default=3)
    parser.add_argument("--watchdog", type=int, default=30)
    parser.add_argument("--build", action="store_true")
    parser.add_argument("--checks", action="store_true")
    parser.add_argument("--bundle", type=Path)
    parser.add_argument("--manifest", type=Path)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    if sys.platform != "darwin":
        parser.error("Requires macOS/WKWebView")
    if (
        sys.implementation.name != "cpython"
        or sys.version_info[:2] != (3, 14)
        or sysconfig.get_config_var("Py_GIL_DISABLED")
    ):
        parser.error("Requires CPython 3.14 with the regular GIL")
    if not (
        1 <= args.runs <= 30
        and 0 <= args.delay <= 5
        and 0 <= args.fullscreen_runs <= 10
        and 20 <= args.watchdog <= 60
    ):
        parser.error("runs 1..30, delay 0..5, fullscreen-runs 0..10, watchdog 20..60")
    if bool(args.bundle) != bool(args.manifest):
        parser.error("Specify both --bundle and --manifest")
    if args.build and (
        args.binary.absolute() != ROOT / "native/target/release/pyyaki"
        or args.fullscreen_control.absolute()
        != ROOT / "native/target/release/examples/macos_space_control"
    ):
        parser.error("--build requires the default host and control paths")
    return args


def main():
    args = parse_args()
    output = (
        args.output
        or ROOT
        / "test-results"
        / ("macos-regression-" + datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ"))
    ).absolute()
    output = output.resolve()
    if not output.is_relative_to((ROOT / "test-results").resolve()):
        raise ValueError("Output must be a new directory under test-results")
    if args.bundle and output.is_relative_to(args.bundle.resolve(strict=True)):
        raise ValueError("Output must not be inside the input bundle")
    output.mkdir(parents=True, exist_ok=False)
    report = {
        "created_at": datetime.now(UTC).isoformat(),
        "platform": platform.platform(),
        "python": sys.version,
        "python_executable": sys.executable,
        "gil_enabled": sys._is_gil_enabled(),
        "gil_disabled": sysconfig.get_config_var("Py_GIL_DISABLED"),
        "SDKROOT": os.environ.get("SDKROOT"),
        "options": {
            "runs": args.runs,
            "delay": args.delay,
            "fullscreen_runs": args.fullscreen_runs,
            "watchdog": args.watchdog,
            "checks": args.checks,
            "build": args.build,
        },
        "status": "running",
        "commands": [],
        "cases": [],
        "unverified": [
            "Physical display, real user input, user's Codex/Spaces",
            "Clean Mac, official distribution, Windows",
        ],
        "note": "Checkout metadata does not prove a supplied binary was built from it.",
    }
    planned = [
        f"{scenario}-{index:02}"
        for scenario in ("baseline", "import", "hooks")
        for index in range(args.runs)
    ]
    planned += [f"fullscreen-{index:02}" for index in range(args.fullscreen_runs)]
    if not args.fullscreen_runs:
        report["unverified"].append("Fullscreen startup")
    if not args.bundle:
        report["unverified"].append("Packaged bundle signature/manifest/relocation")
    else:
        planned += ["bundle-source", "bundle-relocated", "bundle-startup", "bundle-after-startup"]
    if not args.checks:
        report["unverified"].append("Source checks (not requested)")
    if not args.build:
        report["unverified"].append("Rebuilding supplied binaries (not requested)")
    expected = len(planned)
    report.update(expected_cases=expected, planned_cases=planned)
    save(output / "report.json", report)
    print(f"Output: {output}", flush=True)
    try:
        report["source_commit"] = subprocess.check_output(
            ["git", "rev-parse", "HEAD"], cwd=ROOT, text=True
        ).strip()
        report["working_tree_dirty"] = bool(
            subprocess.check_output(["git", "status", "--porcelain"], cwd=ROOT, text=True)
        )
        commands = []
        if args.checks:
            commands.extend(
                [
                    (
                        "pytest",
                        [
                            sys.executable,
                            "-m",
                            "pytest",
                            "-q",
                            "-rs",
                            "-W",
                            "error::DeprecationWarning",
                        ],
                    ),
                    (
                        "ruff",
                        [
                            sys.executable,
                            "-m",
                            "ruff",
                            "check",
                            "src",
                            "examples",
                            "tests",
                            "native/worker_bootstrap.py",
                        ],
                    ),
                    (
                        "ruff-format",
                        [
                            sys.executable,
                            "-m",
                            "ruff",
                            "format",
                            "--check",
                            "src",
                            "examples",
                            "tests",
                            "native/worker_bootstrap.py",
                        ],
                    ),
                    (
                        "cargo-fmt",
                        ["cargo", "fmt", "--manifest-path", "native/Cargo.toml", "--", "--check"],
                    ),
                    (
                        "cargo-test",
                        ["cargo", "test", "--manifest-path", "native/Cargo.toml", "--locked"],
                    ),
                    (
                        "clippy",
                        [
                            "cargo",
                            "clippy",
                            "--manifest-path",
                            "native/Cargo.toml",
                            "--locked",
                            "--all-targets",
                            "--",
                            "-D",
                            "warnings",
                        ],
                    ),
                    ("browser-contract", [sys.executable, "tests/browser_native_bridge.py"]),
                ]
            )
        if args.build:
            commands.append(
                (
                    "build",
                    [
                        "cargo",
                        "build",
                        "--manifest-path",
                        "native/Cargo.toml",
                        "--release",
                        "--locked",
                        "--target-dir",
                        str(ROOT / "native/target"),
                        "--bin",
                        "pyyaki",
                        "--example",
                        "macos_space_control",
                    ],
                )
            )
        for label, command in commands:
            print(f"Checking {label}", flush=True)
            report["active_command"] = label
            row = command_check(output, label, command)
            report["commands"].append(row)
            report.pop("active_command", None)
            if row["exit_code"] != 0:
                raise RuntimeError(f"{label} failed; see {label}.log")
        binary = args.binary.resolve(strict=True)
        control = args.fullscreen_control.resolve(strict=True) if args.fullscreen_runs else None
        report.update(binary=str(binary), binary_sha256=digest(binary))
        if control:
            report.update(control=str(control), control_sha256=digest(control))
        base = Path.home() / "Library/Application Support/PyYaki/prototype"

        def add(label, result):
            report["cases"].append({"name": label, **result})
            save(output / "report.json", report)
            print(f"{label}: ok={result['ok']}", flush=True)

        for scenario in ("baseline", "import", "hooks"):
            for index in range(args.runs):
                row = run_case(binary, output, base, scenario, index, args.delay, args.watchdog)
                row["validation_errors"] = validate_startup(
                    row["markers"], require_space_restore=True
                )
                row["ok"] &= not row["validation_errors"]
                save(output / f"{scenario}-{index:02}/result.json", row)
                add(f"{scenario}-{index:02}", row)
        for index in range(args.fullscreen_runs):
            folder = output / f"fullscreen-{index:02}"
            row = fullscreen_case(
                control,
                folder,
                lambda folder=folder: checked_case(binary, folder, base, args.watchdog),
            )
            add(folder.name, row)
        if args.bundle:
            bundle = args.bundle.resolve(strict=True)
            if bundle.suffix != ".app" or not bundle.is_dir():
                raise ValueError("Expected an existing .app directory")
            manifest_path = args.manifest.resolve(strict=True)
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
            report.update(
                bundle_source=str(bundle),
                manifest=str(manifest_path),
                manifest_sha256=digest(manifest_path),
            )
            row = verify_bundle(bundle, manifest, output, "bundle-source", report["commands"])
            add("bundle-source", row)
            if not row["ok"]:
                raise RuntimeError("Input bundle verification failed")
            relocated = output / "relocated/再配置 鯛焼き" / bundle.name
            relocated.parent.mkdir(parents=True)
            shutil.copytree(bundle, relocated, symlinks=True)
            add(
                "bundle-relocated",
                verify_bundle(relocated, manifest, output, "bundle-relocated", report["commands"]),
            )
            if not report["cases"][-1]["ok"]:
                raise RuntimeError("Relocated bundle verification failed")
            if control:
                folder = output / "bundle-fullscreen"
                row = fullscreen_case(
                    control,
                    folder,
                    lambda: bundle_case(relocated, folder / "startup", base, args.watchdog),
                )
            else:
                row = bundle_case(relocated, output / "bundle-startup", base, args.watchdog)
            add("bundle-startup", row)
            add(
                "bundle-after-startup",
                verify_bundle(relocated, manifest, output, "bundle-after", report["commands"]),
            )
        report["status"] = "passed" if completed(report, expected) else "failed"
    except KeyboardInterrupt:
        report.update(status="interrupted", error="KeyboardInterrupt")
    except Exception as error:
        report.update(status="error", error=f"{type(error).__name__}: {error}")
    finally:
        report["not_completed"] = [
            name for name in planned if name not in {row["name"] for row in report["cases"]}
        ]
        save(output / "report.json", report)
    print(f"Status: {report['status']}", flush=True)
    print("Unverified: " + "; ".join(report["unverified"]), flush=True)
    return 130 if report["status"] == "interrupted" else (0 if report["status"] == "passed" else 1)


def checked_case(binary, folder, base, watchdog):
    row = run_case(binary, folder, base, "baseline", 0, 0, watchdog)
    row["validation_errors"] = validate_startup(row["markers"], require_space_restore=True)
    row["ok"] &= not row["validation_errors"]
    save(folder / "baseline-00/result.json", row)
    return row


if __name__ == "__main__":
    raise SystemExit(main())
