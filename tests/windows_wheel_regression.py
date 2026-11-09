"""Explicit local wheel installation and real WebView2 in a new Windows venv."""

import argparse
import csv
import hashlib
import io
import json
import os
import platform
import shutil
import socket
import subprocess
import sys
import time
import traceback
import zipfile
from pathlib import Path

import httpx
from startup_checks import validate_startup
from startup_probe import records, wait_owned
from windows_console import ConsoleRun

ROOT = Path(__file__).resolve().parents[1]
PLANNED = [
    "install",
    "integrity",
    "help-version-dispatch",
    "baseline-0",
    "baseline-1",
    "baseline-2",
    "import-0",
    "import-1",
    "import-2",
    "hooks-0",
    "hooks-1",
    "hooks-2",
    "native-errors",
    "failure-dom",
    "shutdown",
    "kill-tree",
    "dev-kill-tree",
    "same-version-reinstall",
    "replace-pure",
    "restore-native",
    "uninstall",
]


def save(path, value):
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding="utf-8")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ("wheel", "pure-wheel", "wheelhouse", "requirements", "output"):
        parser.add_argument(f"--{name}", type=Path, required=True)
    args = parser.parse_args()
    if sys.platform != "win32":
        parser.error("requires Windows x64/WebView2")
    output = args.output.resolve()
    if not output.is_relative_to(ROOT / "test-results"):
        parser.error("output must be a new directory under test-results")
    output.mkdir(parents=True, exist_ok=False)
    venv = output / "日本語 space venv"
    python, cli = venv / "Scripts/python.exe", venv / "Scripts/pyyaki.exe"
    native = venv / "Lib/site-packages/pyyaki/_native/windows"
    host, manifest_path = native / "pyyaki-host.exe", native / "manifest.json"
    env = os.environ.copy()
    env.pop("PYTHONHOME", None)
    env.pop("PYTHONPATH", None)
    env.update(
        PATH=f"{venv / 'Scripts'};{Path(env['SYSTEMROOT']) / 'System32'}",
        PIP_NO_INDEX="1",
        PIP_CONFIG_FILE=os.devnull,
        PYTHONUTF8="1",
        TEMP=str(output / "temp"),
        TMP=str(output / "temp"),
    )
    (output / "temp").mkdir()
    assert all(shutil.which(tool, path=env["PATH"]) is None for tool in ("cargo", "rustc", "cl"))
    report = {
        "status": "running",
        "planned": PLANNED,
        "cases": [],
        "commands": [],
        "platform": platform.platform(),
        "python": sys.version,
        "restricted_path": env["PATH"],
        "commit": subprocess.check_output(
            ["git", "rev-parse", "HEAD"], cwd=ROOT, text=True
        ).strip(),
        "dirty": bool(subprocess.check_output(["git", "status", "--porcelain"], cwd=ROOT)),
        "wheel_sha256": hashlib.sha256(args.wheel.read_bytes()).hexdigest(),
        "unverified": [
            "different-version upgrade",
            "clean PC without build tools",
            "physical offline",
            "physical frame presentation",
            "other Windows/CPU",
            "Authenticode",
        ],
    }

    def passed(name, **details):
        report["cases"].append({"name": name, "ok": True, **details})
        save(output / "report.json", report)
        print(name, flush=True)

    def command(name, arguments, expected=0, cwd=ROOT):
        folder = output / name
        folder.mkdir()
        with (folder / "command.log").open("wb") as log:
            child = subprocess.Popen(
                list(map(str, arguments)),
                cwd=cwd,
                env=env,
                stdout=log,
                stderr=log,
                creationflags=subprocess.CREATE_NO_WINDOW,
            )
            expired = wait_owned(child, 120)
        row = {
            "name": name,
            "pid": child.pid,
            "exit_code": child.returncode,
            "expected": expected,
            "timeout": expired,
        }
        report["commands"].append(row)
        save(folder / "result.json", row)
        assert not expired and child.returncode == expected, row
        return (folder / "command.log").read_text(encoding="utf-8")

    def install(name, wheel):
        command(
            name,
            [
                python,
                "-m",
                "pip",
                "install",
                "--no-index",
                "--no-deps",
                "--disable-pip-version-check",
                "--force-reinstall",
                wheel.resolve(),
            ],
        )

    def fixture(name, prefix="", suffix=""):
        folder = output / name
        folder.mkdir()
        project = folder / "日本語 project"
        shutil.copytree(
            ROOT / "examples/hello", project, ignore=shutil.ignore_patterns("__pycache__")
        )
        app = project / "app.py"
        instrumentation = """
import json, os, sys, pyyaki
from pathlib import Path
print("native_wheel_python=" + json.dumps(dict(pid=os.getpid(), prefix=sys.prefix,
    executable=sys.executable, package=pyyaki.__file__)), flush=True)
print("日本語ログ stdout", flush=True)
print("日本語ログ stderr", file=sys.stderr, flush=True)
@app.on_shutdown
async def wheel_shutdown():
    Path("shutdown.json").write_text(json.dumps(dict(pid=os.getpid())), encoding="utf-8")
"""
        app.write_text(
            prefix + app.read_text(encoding="utf-8") + instrumentation + suffix, encoding="utf-8"
        )
        return folder, project

    def launch(folder, project, flags=()):
        local = folder / "local"
        local.mkdir()
        case_env = {**env, "LOCALAPPDATA": str(local)}
        run = ConsoleRun(
            [cli, "run", "--project", project, *flags], ROOT, folder / "console", case_env
        )
        return run, local / "PyYaki/prototype"

    def logs(folder, run, base):
        started = json.loads((run.folder / "started.json").read_text(encoding="utf-8"))
        matches = [
            p
            for p in base.glob("*")
            if (p / "host.log").exists()
            and records(p / "host.log")[0]["data"] == {"pid": started["pid"]}
        ]
        assert len(matches) == 1, matches
        for name in ("host.log", "worker.log"):
            if (matches[0] / name).exists():
                shutil.copy2(matches[0] / name, folder / name)
        trace = records(folder / "host.log")
        worker = (folder / "worker.log").read_text(encoding="utf-8")
        info = json.loads(
            next(
                line.removeprefix("native_wheel_python=")
                for line in worker.splitlines()
                if line.startswith("native_wheel_python=")
            )
        )
        assert Path(info["prefix"]).samefile(venv)
        assert Path(info["executable"]).samefile(python)
        assert Path(info["package"]).samefile(venv / "Lib/site-packages/pyyaki/__init__.py")
        assert "日本語ログ stdout" in worker and "日本語ログ stderr" in worker
        return trace, info

    def wait_ready(run, base):
        deadline = time.monotonic() + 25
        while time.monotonic() < deadline:
            assert run.process.poll() is None, "console/host exited early"
            if any(
                any(r["phase"] == "sdk_welcome_posted" for r in records(p))
                for p in base.glob("*/host.log")
            ):
                return
            time.sleep(0.05)
        raise TimeoutError("native host not ready")

    try:
        command("venv", [sys.executable, "-m", "venv", venv])
        command(
            "dependencies",
            [
                python,
                "-m",
                "pip",
                "install",
                "--no-index",
                "--only-binary=:all:",
                "--disable-pip-version-check",
                "--require-hashes",
                "--find-links",
                args.wheelhouse.resolve(),
                "-r",
                args.requirements.resolve(),
            ],
        )
        install("install-native", args.wheel)
        command("pip-check", [python, "-m", "pip", "check"])
        passed("install")
        with zipfile.ZipFile(args.wheel) as archive:
            assert cli.read_bytes() == archive.read(
                next(n for n in archive.namelist() if n.endswith(".data/scripts/pyyaki.exe"))
            )
            assert host.read_bytes() == archive.read("pyyaki/_native/windows/pyyaki-host.exe")
        dist = next((venv / "Lib/site-packages").glob("pyyaki-*.dist-info"))
        rows = list(csv.reader(io.StringIO((dist / "RECORD").read_text(encoding="utf-8"))))
        assert any(row[0].replace("\\", "/").endswith("Scripts/pyyaki.exe") for row in rows)
        passed("integrity", manifest=json.loads(manifest_path.read_text(encoding="utf-8")))
        assert "PyYaki 0.1.0.dev0" in command("version", [cli, "--version"])
        assert "Usage:" in command("help", [cli, "--help"])
        command("dev-help", [cli, "dev", "--help"])
        command("build-help", [cli, "build", "windows", "--help"])
        assert "requires --host" in command("build-missing", [cli, "build", "windows"], expected=2)
        command("python-override", [cli, "run", "--python", python], expected=1)
        passed("help-version-dispatch")
        for scenario in ("baseline", "import", "hooks"):
            for index in range(3):
                name = f"{scenario}-{index}"
                folder, project = fixture(
                    name,
                    "import time\ntime.sleep(5)\n" if scenario == "import" else "",
                    "\n@app.on_startup\nasync def delay():\n    await asyncio.sleep(5)\n"
                    if scenario == "hooks"
                    else "",
                )
                run, base = launch(folder, project, ["--smoke-test"])
                code, process = run.wait()
                trace, info = logs(folder, run, base)
                errors = validate_startup(trace)
                smoke = [
                    json.loads(line.removeprefix("native_smoke="))
                    for line in (folder / "host.log").read_text(encoding="utf-8").splitlines()
                    if line.startswith("native_smoke=")
                ]
                save(
                    folder / "result.json",
                    {
                        "process": process,
                        "markers": trace,
                        "smoke": smoke,
                        "validation_errors": errors,
                    },
                )
                assert code == 0 and not errors and smoke[-1]["ok"]
                assert (project / "shutdown.json").exists()
                passed(name, pid=process["pid"], worker=info)
        data = host.read_bytes()
        original = manifest_path.read_bytes()
        errors = []
        try:
            host.unlink()
            errors.append(command("missing-host", [cli, "run"], expected=1))
            host.write_bytes(data + b"corrupt")
            assert "hash mismatch" in command("corrupt-host", [cli, "run"], expected=1)
            host.write_bytes(data)
            for key, value in (("framework_version", "other"), ("machine", "ARM64")):
                manifest = json.loads(original)
                manifest[key] = value
                save(manifest_path, manifest)
                assert "version/CPU mismatch" in command(key, [cli, "run"], expected=1)
        finally:
            host.write_bytes(data)
            manifest_path.write_bytes(original)
        detached = output / "detached"
        detached.mkdir()
        shutil.copy2(cli, detached / "pyyaki.exe")
        assert "venv" in command("non-venv", [detached / "pyyaki.exe", "run"], expected=1)
        passed("native-errors")
        folder, project = fixture(
            "failure-dom", suffix='\nraise RuntimeError("日本語例外 <img src=x>")\n'
        )
        js = project / "ui/main.js"
        js.write_text(
            """
window.addEventListener('__pyyaki_backend_failed__', () => setTimeout(() => {
  const text = document.body.innerText;
  window.chrome.webview.postMessage(JSON.stringify({v:1,type:'pyyaki_diagnostic',phase:'result',data:{
    ok:text.includes('日本語例外 <img src=x>') && !document.querySelector('img'), failure_text:text
  }}));
}, 100));
"""
            + js.read_text(encoding="utf-8"),
            encoding="utf-8",
        )
        run, base = launch(folder, project, ["--diagnostics"])
        code, process = run.wait()
        trace, info = logs(folder, run, base)
        diagnostic = [
            json.loads(line.removeprefix("pyyaki_diagnostic="))
            for line in (run.folder / "launcher.log").read_text(encoding="utf-8").splitlines()
            if line.startswith("pyyaki_diagnostic=")
        ]
        result = next(item["data"] for item in diagnostic if item["phase"] == "result")
        save(folder / "result.json", {"process": process, "markers": trace, "diagnostic": result})
        assert code == 0 and result["ok"] and any(r["phase"] == "backend_failed" for r in trace)
        assert "RuntimeError: 日本語例外" in (folder / "worker.log").read_text(encoding="utf-8")
        passed("failure-dom", pid=process["pid"], worker=info)
        for name in ("shutdown", "kill-tree"):
            folder, project = fixture(name)
            run, base = launch(folder, project)
            wait_ready(run, base)
            if name == "shutdown":
                run.interrupt()
            else:
                (run.folder / "kill-owned-child").touch()
            code, process = run.wait()
            trace, info = logs(folder, run, base)
            assert code == (0 if name == "shutdown" else 1)
            assert (project / "shutdown.json").exists() == (name == "shutdown")
            passed(name, process=process, worker=info)
        folder, project = fixture("dev-kill-tree")
        with socket.socket() as reservation:
            reservation.bind(("127.0.0.1", 0))
            port = reservation.getsockname()[1]
        run = ConsoleRun([cli, "dev", "--port", str(port)], project, folder / "console", env)
        try:
            deadline = time.monotonic() + 40
            with httpx.Client(trust_env=False, timeout=0.3) as client:
                while True:
                    assert run.process.poll() is None
                    try:
                        if client.get(f"http://127.0.0.1:{port}/").status_code == 200:
                            break
                    except httpx.TransportError:
                        pass
                    assert time.monotonic() < deadline, "delegated dev did not start"
                    time.sleep(0.05)
            (run.folder / "kill-owned-child").touch()
            code, process = run.wait()
            assert code == 1 and not (project / "shutdown.json").exists()
            passed("dev-kill-tree", process=process)
        finally:
            if run.process.poll() is None:
                run.interrupt()
                run.wait()
        install("reinstall", args.wheel)
        assert host.read_bytes() == data
        passed("same-version-reinstall")
        install("replace-pure-command", args.pure_wheel)
        assert not host.exists() and not manifest_path.exists()
        command("pure-run", [cli, "run"], expected=1)
        passed("replace-pure")
        install("restore-native-command", args.wheel)
        assert host.read_bytes() == data
        passed("restore-native")
        command("uninstall-command", [python, "-m", "pip", "uninstall", "-y", "pyyaki"])
        assert not cli.exists() and not host.exists() and not manifest_path.exists()
        command("removed-import", [python, "-I", "-c", "import pyyaki"], expected=1)
        passed("uninstall")
        assert [row["name"] for row in report["cases"]] == PLANNED
        report["status"] = "passed"
    except BaseException:
        report["status"] = "failed"
        (output / "failure.log").write_text(traceback.format_exc(), encoding="utf-8")
        raise
    finally:
        report["not_completed"] = [
            name for name in PLANNED if name not in {r["name"] for r in report["cases"]}
        ]
        save(output / "report.json", report)


if __name__ == "__main__":
    main()
