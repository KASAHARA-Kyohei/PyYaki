"""SDK native transport contract with a fake WebView2 bridge, not a Windows GUI test."""

from pathlib import Path
from urllib.parse import urlparse

from playwright.sync_api import expect, sync_playwright

ROOT = Path(__file__).resolve().parents[1]


def main():
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=True)
        try:
            page = browser.new_page()
            errors = []
            page.on("pageerror", lambda error: errors.append(str(error)))

            def asset(route):
                path = urlparse(route.request.url).path
                file = ROOT / "examples/hello/ui" / (path.lstrip("/") or "index.html")
                if path == "/_pyyaki/sdk.js":
                    file = ROOT / "src/pyyaki/static/sdk.js"
                mime = {".js": "text/javascript", ".html": "text/html", ".css": "text/css"}
                route.fulfill(
                    body=file.read_bytes(),
                    content_type=mime[file.suffix],
                    headers={"Content-Security-Policy": "default-src 'self'; connect-src 'none'"},
                )

            page.route("https://pyyaki.localhost/**", asset)
            page.add_init_script("""(() => {
              window.__PYYAKI_NATIVE__ = {mode: 'windows', version: '0.1.0.dev0'};
              const listeners = new Set();
              window.chrome ??= {};
              window.chrome.webview = {
                addEventListener(type, listener) { listeners.add(listener); },
                removeEventListener(type, listener) { listeners.delete(listener); },
              };
              window.nativeMessages = [];
              window.networkAttempts = 0;
              window.fetch = () => { window.networkAttempts++; throw Error('Unexpected fetch'); };
              const OriginalSocket = window.WebSocket;
              window.WebSocket = class extends OriginalSocket {
                constructor() {
                  super('ws://invalid'); window.networkAttempts++;
                  throw Error('Unexpected socket');
                }
              };
              const reply = (data) => queueMicrotask(() => listeners.forEach(fn => fn({data})));
              window.ipc = {postMessage(text) {
                const message = JSON.parse(text);
                nativeMessages.push(message);
                if (message.type === 'hello') reply({
                  v: 1, type: 'welcome', mode: 'windows', capabilities: ['rpc'],
                  framework_version: '0.1.0.dev0', session_id: 'host-issued',
                });
                if (message.type === 'call') {
                  if (message.method === 'fail') reply({v: 1, type: 'error', id: message.id,
                    error: {code: 'APP_NATIVE', message: 'Native test error'}});
                  else reply({v: 1, type: 'result', id: message.id,
                    result: {message: 'Native bridge: ' + message.params.name}});
                }
              }};
            })()""")
            page.goto("https://pyyaki.localhost/")
            page.wait_for_load_state("networkidle")
            expect(page.locator("#connection")).to_have_text("Python 接続済み")
            page.get_by_label("お名前").fill("鯛焼き")
            page.get_by_role("button", name="あいさつする").click()
            expect(page.locator("#result")).to_have_text("Native bridge: 鯛焼き")
            page.get_by_role("button", name="エラーを試す").click()
            expect(page.locator("#error")).to_contain_text("APP_NATIVE")
            messages = page.evaluate("nativeMessages")
            assert messages[0] == {"v": 1, "type": "pyyaki_shell_ready"}
            assert messages[1] == {"v": 1, "type": "hello", "client_version": "0.1.0.dev0"}
            assert page.evaluate("networkAttempts") == 0
            assert not errors, errors
            print("Native SDK bridge contract passed; no HTTP session or WebSocket connection.")
        finally:
            browser.close()


if __name__ == "__main__":
    main()
