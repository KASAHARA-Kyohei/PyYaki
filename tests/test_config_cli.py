import socket
import subprocess
import sys
from pathlib import Path

import pytest

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
