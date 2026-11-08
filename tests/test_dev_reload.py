import time

import pytest
from fastapi.testclient import TestClient

from pyyaki import App
from pyyaki._dev import snapshot
from pyyaki.config import Config
from pyyaki.web import create_asgi, reload_html

ORIGIN = "http://127.0.0.1:8000"


@pytest.mark.parametrize("closing", [b"</HEAD>", b"</body>", b""])
def test_reload_injection_preserves_comments_script_text_and_original_bytes(closing):
    prefix = '<!-- </head> --><script>const x="</head>";</script>鯛焼き'.encode()
    content = prefix + closing
    updated = reload_html(content, "test-revision")
    script = b'<script type="module" src="/_pyyaki/dev.js?revision=test-revision"></script>'
    assert updated == prefix + script + closing


def test_snapshot_tracks_creation_deletion_and_same_size_edit(tmp_path):
    path = tmp_path / "app.py"
    first = snapshot(tmp_path, python_only=True)
    path.write_text("hello")
    second = snapshot(tmp_path, python_only=True)
    path.write_text("world")
    third = snapshot(tmp_path, python_only=True)
    path.unlink()
    assert first == snapshot(tmp_path, python_only=True)
    assert first != second != third
    (tmp_path / "pyproject.toml").write_text("# config")
    assert tmp_path / "pyproject.toml" in snapshot(tmp_path, python_only=True)
    (tmp_path / "ui.css").write_text("body {}")
    assert snapshot(tmp_path) != snapshot(tmp_path, python_only=True)


def test_snapshot_ignores_caches_hidden_paths_and_symlinks(tmp_path, symlink):
    for name in (".venv", ".git", "node_modules", "__pycache__", "test-results", "build"):
        directory = tmp_path / name
        directory.mkdir()
        (directory / "ignored.py").write_text("ignored")
    (tmp_path / ".secret.py").write_text("ignored")
    (tmp_path / "backup.py~").write_text("ignored")
    outside = tmp_path.parent / f"{tmp_path.name}-outside.py"
    outside.write_text("ignored")
    symlink(tmp_path / "escape.py", outside)
    symlink(tmp_path / "escape-dir", tmp_path.parent)
    assert snapshot(tmp_path) == {}


@pytest.mark.parametrize("reload", [False, True])
def test_dev_endpoints_html_and_security(tmp_path, reload):
    ui = tmp_path / "ui"
    ui.mkdir()
    source = b"<!doctype html><HTML><HEAD></HEAD><body>hello</body></HTML>"
    page = ui / "index.html"
    page.write_bytes(source)
    config = Config(tmp_path, "app:app", ui, "index.html")
    with TestClient(create_asgi(App(), config, dev_reload=reload), base_url=ORIGIN) as client:
        response = client.get("/")
        assert "script-src 'self'" in response.headers["content-security-policy"]
        assert "unsafe-inline" not in response.headers["content-security-policy"]
        assert page.read_bytes() == source
        if not reload:
            assert response.content == source
            assert client.get("/_pyyaki/dev", headers={"origin": ORIGIN}).status_code == 404
            assert client.get("/_pyyaki/dev.js").status_code == 404
            return
        script = response.text.index('<script type="module"')
        assert script < response.text.index("</HEAD>")
        assert client.get("/_pyyaki/dev.js").status_code == 200
        assert client.get("/_pyyaki/dev").status_code == 403
        assert (
            client.get("/_pyyaki/dev", headers={"origin": "https://evil.test"}).status_code == 403
        )
        assert (
            client.get("/_pyyaki/dev", headers={"sec-fetch-site": "cross-site"}).status_code == 403
        )
        assert client.get("/_pyyaki/dev", headers={"host": "evil.test"}).status_code == 400
        headers = {"sec-fetch-site": "same-origin"}
        revision = client.get("/_pyyaki/dev", headers=headers).json()["revision"]
        assert f"revision={revision}" in response.text
        page.write_bytes(source.replace(b"hello", b"updated"))
        deadline = time.monotonic() + 3
        while client.get("/_pyyaki/dev", headers=headers).json()["revision"] == revision:
            assert time.monotonic() < deadline, "UI watcher did not publish a new revision"
            time.sleep(0.05)
        assert "updated" in client.get("/").text
