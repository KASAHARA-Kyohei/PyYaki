import errno
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
from pyyaki import cli as cli_module
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


def cli(*arguments, cwd=ROOT / "examples/hello"):
    return subprocess.run(
        [sys.executable, "-m", "pyyaki.cli", *arguments],
        cwd=cwd,
        text=True,
        capture_output=True,
        timeout=10,
    )


def test_cli_version_and_unsupported_commands():
    assert "PyYaki 0.1.0.dev0" in cli("--version").stdout
    for arguments in (("run",), ("build",)):
        result = cli(*arguments)
        assert result.returncode != 0
        assert "not implemented" in result.stderr
    result = cli("build", "windows")
    assert result.returncode != 0
    assert "requires --host" in result.stderr
    assert cli("dev", "--port", "0").returncode != 0


def test_cli_port_conflict_has_explanation():
    with socket.socket() as listener:
        listener.bind(("127.0.0.1", 0))
        listener.listen()
        result = cli("dev", "--port", str(listener.getsockname()[1]))
        assert result.returncode != 0
        if sys.platform == "win32" and "[WinError 10013]" in result.stderr:
            # Windows may report an exclusive/occupied socket as access denied.
            # Do not misclassify every access denial as EADDRINUSE in the CLI.
            assert f"cannot listen on 127.0.0.1:{listener.getsockname()[1]}" in result.stderr
            return
        assert f"port {listener.getsockname()[1]} is already in use" in result.stderr
        assert "pyyaki dev --port 8001" in result.stderr


def test_cli_missing_project_file_explains_where_to_run(tmp_path):
    result = cli("dev", cwd=tmp_path)
    assert result.returncode == 1
    assert str(tmp_path / "pyproject.toml") in result.stderr
    assert "project configuration not found" in result.stderr
    assert "project directory" in result.stderr
    assert "port" not in result.stderr


def test_cli_invalid_project_configuration_identifies_file(tmp_path):
    (tmp_path / "pyproject.toml").write_text("[tool.pyyaki\n")
    result = cli("dev", cwd=tmp_path)
    assert result.returncode == 1
    assert "invalid project configuration" in result.stderr
    assert str(tmp_path / "pyproject.toml") in result.stderr


def test_cli_application_io_failure_is_not_reported_as_port_problem(tmp_path):
    (tmp_path / "pyproject.toml").write_text('[tool.pyyaki]\nentry="app:app"\n')
    (tmp_path / "ui").mkdir()
    (tmp_path / "ui/index.html").write_text("<h1>hello</h1>")
    (tmp_path / "app.py").write_text('open("nonexistent-business-file")\n')
    result = cli("dev", cwd=tmp_path)
    assert result.returncode == 1
    assert "cannot load application 'app:app'" in result.stderr
    assert "nonexistent-business-file" in result.stderr
    assert "already in use" not in result.stderr


def test_cli_other_bind_failure_is_not_reported_as_port_conflict(monkeypatch, capsys):
    monkeypatch.chdir(ROOT / "examples/hello")
    monkeypatch.setattr(sys, "argv", ["pyyaki", "dev"])

    def deny_bind(self, address):
        raise OSError(errno.EACCES, "permission denied")

    monkeypatch.setattr(socket.socket, "bind", deny_bind)
    with pytest.raises(SystemExit) as error:
        cli_module.main()
    assert error.value.code == 1
    stderr = capsys.readouterr().err
    assert "cannot listen on 127.0.0.1:8000" in stderr
    assert "permission denied" in stderr
    assert "already in use" not in stderr


def test_cli_suggests_a_different_port_when_8001_is_in_use(monkeypatch, capsys):
    monkeypatch.chdir(ROOT / "examples/hello")
    monkeypatch.setattr(sys, "argv", ["pyyaki", "dev", "--port", "8001"])

    def busy_bind(self, address):
        raise OSError(errno.EADDRINUSE, "address already in use")

    monkeypatch.setattr(socket.socket, "bind", busy_bind)
    with pytest.raises(SystemExit) as error:
        cli_module.main()
    assert error.value.code == 1
    assert "pyyaki dev --port 8002" in capsys.readouterr().err


@pytest.mark.skipif(os.name == "nt", reason="POSIX SIGINT behavior")
@pytest.mark.parametrize("arguments", [[], ["--no-reload"]])
@pytest.mark.parametrize("process_group", [False, True])
def test_ctrl_c_with_active_websocket_exits_without_traceback(arguments, process_group):
    with socket.socket() as listener:
        listener.bind(("127.0.0.1", 0))
        port = listener.getsockname()[1]

    process = subprocess.Popen(
        [sys.executable, "-m", "pyyaki.cli", "dev", "--port", str(port), *arguments],
        cwd=ROOT / "examples/hello",
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        start_new_session=True,
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
                if process_group:
                    assert os.getpgid(process.pid) == process.pid
                    os.killpg(process.pid, signal.SIGINT)
                else:
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
