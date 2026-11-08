"""Install explicit local wheels into a new venv; exercise its CLI and real WKWebView."""

import argparse
import hashlib
import json
import os
import platform
import shutil
import subprocess
import sys
import time
import traceback
import zipfile
from pathlib import Path

from startup_checks import validate_startup
from startup_probe import ROOT, observe, records, wait_owned

PLANNED = [
    "install-pure",
    "pure-run-error",
    "install-native",
    "integrity",
    "baseline",
    "import",
    "hooks",
    "corrupt-host",
    "failure-dom",
    "shutdown",
    "replace-with-pure",
    "reinstall-native",
    "uninstall",
]


def save(path, value):
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding="utf-8")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ("wheel", "pure-wheel", "wheelhouse", "requirements", "output"):
        parser.add_argument(f"--{name}", type=Path, required=True)
    args = parser.parse_args()
    if sys.platform != "darwin":
        parser.error("requires macOS and a matching prebuilt wheel")
    output = args.output.resolve()
    if not output.is_relative_to(ROOT / "test-results"):
        parser.error("output must be a new directory under test-results")
    output.mkdir(parents=True, exist_ok=False)
    venv = output / "日本語 space venv"
    python, cli = venv / "bin/python", venv / "bin/pyyaki"
    env = os.environ.copy()
    env.pop("PYTHONPATH", None)
    env.pop("PYTHONHOME", None)
    env["PATH"] = f"{venv / 'bin'}:/usr/bin:/bin:/usr/sbin:/sbin"
    assert shutil.which("cargo", path=env["PATH"]) is None
    env["PIP_CONFIG_FILE"] = os.devnull
    env["PIP_NO_INDEX"] = "1"
    base = Path.home() / "Library/Application Support/PyYaki/prototype"
    report = {
        "status": "running",
        "planned": PLANNED,
        "cases": [],
        "commands": [],
        "platform": platform.platform(),
        "python": sys.version,
        "commit": subprocess.check_output(
            ["git", "rev-parse", "HEAD"], cwd=ROOT, text=True
        ).strip(),
        "dirty": bool(subprocess.check_output(["git", "status", "--porcelain"], cwd=ROOT)),
        "path_without_cargo": env["PATH"],
        "inputs": {},
        "unverified": [
            "Windows native CLI",
            "other Macs/CPU/OS",
            "Developer ID/notarization",
            "upgrades between different framework versions",
            "parent SIGKILL cleanup",
        ],
    }
    for name in ("wheel", "pure_wheel", "requirements"):
        path = getattr(args, name).resolve(strict=True)
        report["inputs"][name] = {
            "path": str(path),
            "sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
        }

    def command(label, arguments, expected=0):
        folder = output / label
        folder.mkdir()
        with (folder / "command.log").open("wb") as log:
            process = subprocess.Popen(
                list(map(str, arguments)), cwd=ROOT, env=env, stdout=log, stderr=subprocess.STDOUT
            )
            expired = wait_owned(process, 120)
        row = {
            "name": label,
            "pid": process.pid,
            "exit_code": process.returncode,
            "ok": not expired and process.returncode == expected,
        }
        report["commands"].append(row)
        assert row["ok"], row
        return (folder / "command.log").read_text()

    def passed(name, **details):
        report["cases"].append({"name": name, "ok": True, **details})
        save(output / "report.json", report)
        print(name, flush=True)

    def install(label, wheel):
        command(
            label,
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
@app.on_shutdown
async def wheel_shutdown():
    Path("shutdown.json").write_text(json.dumps(dict(pid=os.getpid())))
"""
        app.write_text(prefix + app.read_text() + instrumentation + suffix, encoding="utf-8")
        return folder, project

    def worker_info(folder):
        text = (folder / "worker.log").read_text(encoding="utf-8")
        info = json.loads(
            next(
                line.removeprefix("native_wheel_python=")
                for line in text.splitlines()
                if line.startswith("native_wheel_python=")
            )
        )
        assert Path(info["prefix"]).resolve() == venv.resolve(), info
        assert Path(info["executable"]).parent == python.parent, info
        assert Path(info["package"]).is_relative_to(venv), info
        return info

    try:
        command("create-venv", [sys.executable, "-m", "venv", venv])
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
        install("install-pure-command", args.pure_wheel)
        command("pip-check", [python, "-m", "pip", "check"])
        passed("install-pure")
        text = command("pure-run-error", [cli, "run"], expected=1)
        assert "no bundled macOS host" in text
        passed("pure-run-error")
        install("install-native-command", args.wheel)
        passed("install-native")
        text = command(
            "locate-host",
            [
                python,
                "-I",
                "-c",
                "from pyyaki._native_host import bundled_host;print(bundled_host())",
            ],
        )
        host = Path(text.strip())
        assert host.is_relative_to(venv)
        with zipfile.ZipFile(args.wheel) as archive:
            data = archive.read("pyyaki/_native/macos/pyyaki-host")
            assert host.read_bytes() == data
        command("installed-codesign", ["/usr/bin/codesign", "--verify", "--strict", host])
        command("signature-display", ["/usr/bin/codesign", "--display", "--verbose=2", host])
        passed("integrity", host=str(host), sha256=hashlib.sha256(data).hexdigest())
        for name in ("baseline", "import", "hooks"):
            prefix = "import time\ntime.sleep(5)\n" if name == "import" else ""
            suffix = (
                "\n@app.on_startup\nasync def delay():\n    await asyncio.sleep(5)\n"
                if name == "hooks"
                else ""
            )
            folder, project = fixture(name, prefix, suffix)
            row = observe(
                [str(cli), "run", "--project", str(project), "--smoke-test"],
                folder,
                base,
                35,
                env=env,
            )
            errors = validate_startup(row["markers"], require_space_restore=True)
            save(folder / "result.json", {**row, "validation_errors": errors})
            assert row["ok"] and not errors, (row, errors)
            info = worker_info(folder)
            assert json.loads((project / "shutdown.json").read_text())["pid"] == info["pid"]
            passed(name, pid=row["pid"], worker=info)
        host.write_bytes(data + b"corrupted")
        try:
            text = command("corrupt-host", [cli, "run"], expected=1)
            assert "hash mismatch" in text
        finally:
            host.write_bytes(data)
        passed("corrupt-host")

        folder, project = fixture(
            "failure-dom", suffix='\nraise RuntimeError("日本語例外 <img src=x>")\n'
        )
        js = project / "ui/main.js"
        js.write_text(
            """
window.addEventListener('__pyyaki_backend_failed__', () => setTimeout(() => {
  const text = document.body.innerText;
  window.ipc.postMessage(JSON.stringify({
    v: 1, type: 'pyyaki_diagnostic', phase: 'result', data: {
      ok: text.includes('日本語例外 <img src=x>') && !document.querySelector('img'),
      failure_text: text,
    },
  }));
}, 100));
"""
            + js.read_text()
        )
        row = observe(
            [str(cli), "run", "--project", str(project), "--diagnostics"], folder, base, 25, env=env
        )
        diagnostic = [
            json.loads(line.removeprefix("pyyaki_diagnostic="))
            for line in (folder / "launcher.log").read_text().splitlines()
            if line.startswith("pyyaki_diagnostic=")
        ]
        result = next(item["data"] for item in diagnostic if item["phase"] == "result")
        save(folder / "result.json", {**row, "diagnostic_result": result})
        assert row["exit_code"] == 0 and not row["closed_by_harness"] and result["ok"]
        assert "RuntimeError: 日本語例外" in (folder / "worker.log").read_text()
        assert any(item["phase"] == "backend_failed" for item in row["markers"])
        passed("failure-dom", pid=row["pid"], worker=worker_info(folder))

        folder, project = fixture("shutdown")
        before = set(base.iterdir())
        instance = None
        with (folder / "launcher.log").open("wb") as log:
            process = subprocess.Popen(
                [str(cli), "run", "--project", str(project)],
                cwd=ROOT,
                env=env,
                stdout=log,
                stderr=log,
            )
            try:
                deadline = time.monotonic() + 20
                while True:
                    assert process.poll() is None, "host exited before shutdown test"
                    for candidate in set(base.iterdir()) - before:
                        path = candidate / "host.log"
                        if path.is_file():
                            trace = records(path)
                            if trace and trace[0].get("data") == {"pid": process.pid}:
                                instance = candidate
                    if instance and any(
                        r["phase"] == "sdk_welcome_posted" for r in records(instance / "host.log")
                    ):
                        break
                    assert time.monotonic() < deadline, "host did not become ready"
                    time.sleep(0.05)
                process.terminate()
                assert not wait_owned(process, 5)
            finally:
                wait_owned(process, 1)
                if instance:
                    for name in ("host.log", "worker.log"):
                        shutil.copy2(instance / name, folder / name)
        assert process.returncode == 0 and (project / "shutdown.json").exists()
        passed("shutdown", pid=process.pid, worker=worker_info(folder))

        install("replace-with-pure-command", args.pure_wheel)
        assert not host.exists() and not (host.parent / "manifest.json").exists()
        passed("replace-with-pure")
        install("reinstall-native-command", args.wheel)
        assert host.read_bytes() == data
        command("reinstalled-codesign", ["/usr/bin/codesign", "--verify", "--strict", host])
        passed("reinstall-native")
        command("uninstall-command", [python, "-m", "pip", "uninstall", "-y", "pyyaki"])
        assert not cli.exists() and not host.exists()
        command("removed-import", [python, "-I", "-c", "import pyyaki"], expected=1)
        passed("uninstall")
        assert [row["name"] for row in report["cases"]] == PLANNED
        report["status"] = "passed"
    except BaseException:
        report["status"] = "failed"
        (output / "failure.log").write_text(traceback.format_exc())
        raise
    finally:
        report["not_completed"] = [
            name for name in PLANNED if name not in {r["name"] for r in report["cases"]}
        ]
        save(output / "report.json", report)


if __name__ == "__main__":
    main()
