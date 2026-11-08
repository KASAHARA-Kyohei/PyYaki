"""Real dev-server/browser regression; all edited fixtures and raw logs stay in test-results."""

import argparse
import json
import os
import platform
import py_compile
import re
import shutil
import signal
import socket
import subprocess
import sys
import time
import traceback
from pathlib import Path

import httpx
from playwright.sync_api import Error, expect, sync_playwright

ROOT = Path(__file__).resolve().parents[1]


def wait_for(check, timeout=12):
    deadline = time.monotonic() + timeout
    last_error = None
    while True:
        try:
            if check():
                return
        except Error as exc:
            last_error = exc
        if time.monotonic() >= deadline:
            raise AssertionError("Timed out waiting for regression condition") from last_error
        time.sleep(0.05)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    output = args.output.resolve()
    if not output.is_relative_to(ROOT / "test-results"):
        parser.error("output must be a new directory under test-results")
    output.mkdir(parents=True, exist_ok=False)
    project = output / "project"
    shutil.copytree(ROOT / "examples/hello", project, ignore=shutil.ignore_patterns("__pycache__"))
    (project / "helper.py").write_text('VALUE = "first"\n')
    py_compile.compile(str(project / "helper.py"), doraise=True)
    app_path = project / "app.py"
    app_path.write_text(
        app_path.read_text()
        + """

import os
from pathlib import Path
from helper import VALUE

@app.command("reload_probe")
async def reload_probe() -> dict[str, str]:
    return {"pid": str(os.getpid()), "value": VALUE}

@app.on_shutdown
async def record_shutdown():
    with Path("shutdown.log").open("a") as stream:
        stream.write(str(os.getpid()) + "\\n")
"""
    )
    with socket.socket() as reservation:
        reservation.bind(("127.0.0.1", 0))
        port = reservation.getsockname()[1]
    origin = f"http://127.0.0.1:{port}"
    report = {
        "status": "running",
        "commit": subprocess.check_output(
            ["git", "rev-parse", "HEAD"], cwd=ROOT, text=True
        ).strip(),
        "dirty": bool(subprocess.check_output(["git", "status", "--porcelain"], cwd=ROOT)),
        "platform": platform.platform(),
        "python": sys.version,
        "checks": [],
        "errors": [],
        "unverified": [
            "Windows execution",
            "native reload",
            "HMR/state preservation",
            "large tree performance",
        ],
    }
    process = None
    log = (output / "server.log").open("w")

    def passed(name):
        report["checks"].append(name)
        print(name, flush=True)

    def stop():
        if process is not None and process.poll() is None:
            process.send_signal(signal.SIGINT if os.name != "nt" else signal.SIGTERM)
            try:
                process.wait(12)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait(3)
                raise AssertionError("Owned supervisor did not shut down gracefully") from None

    def probe(page):
        expect(page.locator("#connection")).to_have_text("Python 接続済み", timeout=12000)
        return page.evaluate("""async () => {
          const { pyyaki } = await import('/_pyyaki/sdk.js');
          await pyyaki.ready();
          return pyyaki.invoke('reload_probe');
        }""")

    try:
        process = subprocess.Popen(
            [sys.executable, "-m", "pyyaki.cli", "dev", "--port", str(port)],
            cwd=project,
            stdout=log,
            stderr=subprocess.STDOUT,
        )
        report["supervisor_pid"] = process.pid
        with httpx.Client(base_url=origin, timeout=0.5, trust_env=False) as client:

            def started():
                assert process.poll() is None, "Development supervisor exited"
                try:
                    return client.get("/").status_code == 200
                except httpx.TransportError:
                    return False

            wait_for(started)
        with sync_playwright() as playwright:
            browser = playwright.chromium.launch(headless=True, chromium_sandbox=True)
            report["browser"] = browser.version
            context = browser.new_context()
            context.tracing.start(screenshots=True, snapshots=True, sources=True)
            pages = [context.new_page(), context.new_page()]
            for page in pages:
                page.on("pageerror", lambda error: report["errors"].append(str(error)))
                page.goto(origin)
            # A polling app has no guaranteed networkidle window; wait on its ready DOM.
            first = probe(pages[0])
            assert probe(pages[1])["pid"] == first["pid"]
            pages[0].screenshot(path=str(output / "before.png"))
            (output / "initial-dom.html").write_text(pages[0].content())
            passed("initial RPC in two browser pages")

            html = project / "ui/index.html"
            html.write_text(
                html.read_text().replace("</body>", '<p id="reload-check">更新済み</p></body>')
            )
            for page in pages:
                expect(page.locator("#reload-check")).to_have_text("更新済み", timeout=12000)
                assert probe(page)["pid"] == first["pid"]
            passed("HTML refreshes both pages without restarting Python")

            css = project / "ui/styles.css"
            css.write_text(css.read_text() + "\nbody { --reload-check: 777; }\n")
            wait_for(
                lambda: pages[0].evaluate(
                    "() => getComputedStyle(document.body).getPropertyValue('--reload-check')"
                    ".trim() === '777'"
                )
            )
            js = project / "ui/main.js"
            js.write_text(js.read_text() + '\ndocument.body.dataset.reloadCheck = "updated";\n')
            wait_for(
                lambda: pages[0].evaluate("() => document.body.dataset.reloadCheck === 'updated'")
            )
            assert probe(pages[0])["pid"] == first["pid"]
            passed("CSS and JavaScript refresh without restarting Python")

            helper = project / "helper.py"
            old_stat = helper.stat()
            helper.write_text('VALUE = "other"\n')  # same size, same timestamp: reject stale pyc
            os.utime(helper, ns=(old_stat.st_atime_ns, old_stat.st_mtime_ns))
            wait_for(lambda: (project / "shutdown.log").exists())
            wait_for(lambda: probe(pages[0])["value"] == "other")
            second = probe(pages[0])
            assert second["pid"] != first["pid"]
            assert first["pid"] in (project / "shutdown.log").read_text().splitlines()
            passed(
                "same-size dependency edit restarts Python, runs shutdown hook and reconnects RPC"
            )

            helper.write_text("this is invalid Python !!!\n")
            wait_for(lambda: "SyntaxError" in (output / "server.log").read_text())
            assert process.poll() is None
            helper.write_text('VALUE = "fixed"\n')
            wait_for(lambda: probe(pages[0])["value"] == "fixed")
            passed("syntax error retains supervisor and recovers after correction")

            config = project / "pyproject.toml"
            valid = config.read_text()
            config.write_text("[tool.pyyaki\n")
            wait_for(lambda: "TOMLDecodeError" in (output / "server.log").read_text())
            config.write_text(valid)
            wait_for(lambda: probe(pages[0])["value"] == "fixed")
            passed("invalid configuration recovers after correction")

            added = project / "added.py"
            before = probe(pages[0])
            added.write_text("# added\n")
            wait_for(lambda: probe(pages[0])["pid"] != before["pid"])
            before = probe(pages[0])
            added.unlink()
            wait_for(lambda: probe(pages[0])["pid"] != before["pid"])
            passed("Python file creation and deletion restart the server")

            final = probe(pages[0])
            pages[0].screenshot(path=str(output / "after.png"))
            assert not report["errors"], report["errors"]
            stop()
            assert process.returncode == (130 if os.name != "nt" else 143)
            assert final["pid"] in (project / "shutdown.log").read_text().splitlines()
            passed("shutdown with active RPC WebSockets exits and runs shutdown hook")
            context.tracing.stop(path=str(output / "trace.zip"))
            browser.close()

        # The escape hatch serves unchanged HTML and never starts a reload supervisor.
        process = subprocess.Popen(
            [sys.executable, "-m", "pyyaki.cli", "dev", "--port", str(port), "--no-reload"],
            cwd=project,
            stdout=log,
            stderr=subprocess.STDOUT,
        )
        with httpx.Client(base_url=origin, timeout=0.5, trust_env=False) as client:
            wait_for(started)
            assert client.get("/").content == html.read_bytes()
            assert client.get("/_pyyaki/dev").status_code == 404
            assert client.get("/_pyyaki/dev.js").status_code == 404
        stop()
        assert process.returncode == (130 if os.name != "nt" else 143)
        passed("--no-reload preserves HTML and omits development endpoints")
        report["status"] = "passed"
    except BaseException:
        report["status"] = "failed"
        (output / "failure.log").write_text(traceback.format_exc())
        raise
    finally:
        try:
            stop()
        finally:
            log.close()
            report["server_pids"] = re.findall(
                r"Started server process \[(\d+)\]", (output / "server.log").read_text()
            )
            (output / "report.json").write_text(json.dumps(report, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
