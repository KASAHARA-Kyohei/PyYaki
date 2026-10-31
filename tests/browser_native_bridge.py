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
                if path == "/sdk-test.html":
                    route.fulfill(
                        body="<!doctype html><title>SDK test</title>", content_type="text/html"
                    )
                    return
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
              window.sendNativeEvent = reply;
              window.ipc = {postMessage(text) {
                const message = JSON.parse(text);
                nativeMessages.push(message);
                if (message.type === 'hello') reply({
                  v: 1, type: 'welcome', mode: 'windows', capabilities: ['rpc', 'events'],
                  framework_version: '0.1.0.dev0', session_id: 'host-issued',
                });
                if (message.type === 'call') {
                  if (message.method === 'hang') return;
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

            # Reuse this page's per-navigation init script with a blank test document.
            page.goto("https://pyyaki.localhost/sdk-test.html")
            page.wait_for_load_state("networkidle")
            reports = []
            page.on(
                "console",
                lambda message: reports.append(message.text) if message.type == "error" else None,
            )
            outcome = page.evaluate(r"""async () => {
              const { pyyaki } = await import('/_pyyaki/sdk.js');
              const seen = [];
              const callback = value => { seen.push(value.n); value.n = 99; };
              const remove = pyyaki.on('progress', callback);
              const removeDuplicate = pyyaki.on('progress', callback);
              const removeBad = pyyaki.on('progress', () => { throw Error('listener failure'); });
              const removeAsync = pyyaki.on('progress', async () => {
                throw Error('async listener failure');
              });
              const other = [];
              const removeOther = pyyaki.on('progress', value => other.push(value.n));
              let invalid = 0;
              for (const [name, listener] of [['pyyaki.private', callback], ['Bad', callback],
                  ['progress', null], ['progress\n', callback]]) {
                try { pyyaki.on(name, listener); }
                catch (error) { if (error.code === 'BAD_REQUEST') invalid++; }
              }
              await pyyaki.ready();
              const emit = (name, payload, seq) => sendNativeEvent({
                v: 1, type: 'event', name, payload, seq,
              });
              emit('progress', {n: 1}, 1);
              await new Promise(resolve => setTimeout(resolve, 0));
              remove(); remove(); removeBad(); removeAsync();
              emit('unsubscribed', null, 2);
              emit('progress', {n: 2}, 3);
              await new Promise(resolve => setTimeout(resolve, 0));
              removeDuplicate(); removeOther();
              emit('progress', {n: 3}, 4);
              await new Promise(resolve => setTimeout(resolve, 0));
              await pyyaki.invoke('greet', {name: 'after-listener-failure'});
              const healthy = pyyaki.state === 'ready';
              emit('progress', {n: 4}, 6); // Out-of-order sequence must close the bridge.
              await new Promise(resolve => setTimeout(resolve, 0));
              return {seen, other, invalid, healthy, closed: pyyaki.state !== 'ready'};
            }""")
            assert outcome == {
                "seen": [1, 1, 2],
                "other": [1, 2],
                "invalid": 4,
                "healthy": True,
                "closed": True,
            }, outcome
            assert len(reports) == 2, reports
            assert not errors, errors
            page.goto("https://pyyaki.localhost/sdk-test.html")
            page.wait_for_load_state("networkidle")
            failure = page.evaluate("""async () => {
              const { pyyaki } = await import('/_pyyaki/sdk.js');
              await pyyaki.ready();
              const pending = pyyaki.invoke('hang').catch(error => error.code);
              window.dispatchEvent(new Event('__pyyaki_backend_failed__'));
              return {code: await pending, state: pyyaki.state, caps: pyyaki.capabilities};
            }""")
            assert failure == {"code": "BACKEND_FAILED", "state": "failed", "caps": []}, failure
            page.goto("https://pyyaki.localhost/sdk-test.html")
            page.wait_for_load_state("networkidle")
            failure = page.evaluate("""async () => {
              const { pyyaki } = await import('/_pyyaki/sdk.js');
              const ready = pyyaki.ready().catch(error => error.code);
              window.dispatchEvent(new Event('__pyyaki_backend_failed__'));
              return {code: await ready, state: pyyaki.state};
            }""")
            assert failure == {"code": "BACKEND_FAILED", "state": "failed"}, failure
            assert not errors, errors
            print("Native SDK bridge passed: events, unsubscribe, callback isolation, no HTTP/WS.")
        finally:
            browser.close()


if __name__ == "__main__":
    main()
