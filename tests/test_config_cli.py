import json
import os
import signal
import socket
import subprocess
import sys
import time
from pathlib import Path

import httpx
import pytest
from websockets.sync.client import connect

from pyyaki import __version__
from pyyaki.config import load_config

ROOT = Path(__file__).resolve().parents[1]


def test_example_configuration():
    config = load_config(ROOT / "examples/hello")
    assert config.entry == "app:app"
    assert (config.ui_dir / config.ui_entry).is_file()


@pytest.mark.parametrize(
    "settings",
    [
        'ui-dir = "../outside"',
        'ui-entry = "../private.html"',
        'entry = "os:system(1)"',
        "ui-dir = 1",
    ],
)
def test_config_rejects_unsafe_paths_and_entries(tmp_path, settings):
    (tmp_path / "pyproject.toml").write_text("[tool.pyyaki]\n" + settings)
    with pytest.raises(ValueError):
        load_config(tmp_path)


def cli(*arguments):
    return subprocess.run(
        [sys.executable, "-m", "pyyaki.cli", *arguments],
        cwd=ROOT / "examples/hello",
        text=True,
        capture_output=True,
        timeout=10,
    )


def test_cli_version_and_unsupported_commands():
    assert "PyYaki 0.1.0.dev0" in cli("--version").stdout
    for arguments in (("run",), ("build",), ("build", "windows")):
        result = cli(*arguments)
        assert result.returncode != 0
        assert "not implemented" in result.stderr
    assert cli("dev", "--port", "0").returncode != 0


def test_cli_port_conflict_has_explanation():
    with socket.socket() as listener:
        listener.bind(("127.0.0.1", 0))
        listener.listen()
        result = cli("dev", "--port", str(listener.getsockname()[1]))
        assert result.returncode != 0
        assert "cannot open project or listen" in result.stderr


@pytest.mark.skipif(os.name == "nt", reason="POSIX SIGINT behavior")
def test_ctrl_c_with_active_websocket_exits_without_traceback():
    with socket.socket() as listener:
        listener.bind(("127.0.0.1", 0))
        port = listener.getsockname()[1]

    process = subprocess.Popen(
        [sys.executable, "-m", "pyyaki.cli", "dev", "--port", str(port)],
        cwd=ROOT / "examples/hello",
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    origin = f"http://127.0.0.1:{port}"
    try:
        with httpx.Client(base_url=origin, timeout=1) as client:
            deadline = time.monotonic() + 10
            while True:
                try:
                    response = client.get("/_pyyaki/session", headers={"Origin": origin})
                    break
                except httpx.ConnectError:
                    if time.monotonic() >= deadline or process.poll() is not None:
                        pytest.fail("Development server did not start")
                    time.sleep(0.05)
            assert response.status_code == 200
            cookie = client.cookies.get("pyyaki_session")
            with connect(
                f"ws://127.0.0.1:{port}/_pyyaki/ws",
                origin=origin,
                additional_headers={"Cookie": f"pyyaki_session={cookie}"},
                open_timeout=3,
            ) as websocket:
                websocket.send(
                    json.dumps(
                        {
                            "v": 1,
                            "type": "hello",
                            "client_version": __version__,
                            "nonce": response.json()["nonce"],
                        }
                    )
                )
                assert json.loads(websocket.recv(timeout=3))["type"] == "welcome"
                process.send_signal(signal.SIGINT)
                stdout, stderr = process.communicate(timeout=10)
        assert process.returncode == 130
        assert "Application shutdown complete" in stderr
        assert "Traceback" not in stderr
        assert "KeyboardInterrupt" not in stderr
        assert "PyYaki prototype" in stdout
    finally:
        if process.poll() is None:
            process.kill()
            process.communicate(timeout=3)
