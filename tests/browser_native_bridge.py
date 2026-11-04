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
                elif path == "/_pyyaki/failure.css":
                    file = ROOT / "native/failure.css"
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
                  limits: {request_id_mode: 'sequence'},
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
            assert messages[0] == {"v": 1, "type": "pyyaki_connect_started"}
            assert messages[1] == {"v": 1, "type": "pyyaki_shell_ready"}
            assert messages[2] == {
                "v": 1,
                "type": "hello",
                "client_version": "0.1.0.dev0",
                "request_id_mode": "sequence",
            }
            ids = [message["id"] for message in messages if message["type"] == "call"]
            assert ids == [str(index) for index in range(1, len(ids) + 1)]
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
              let nested;
              const reentrant = {get name() {
                nested ??= pyyaki.invoke('greet', {name: 'nested'});
                return 'outer';
              }};
              await pyyaki.invoke('greet', reentrant);
              await nested;
              const ids = nativeMessages.filter(message => message.type === 'call')
                .map(message => Number(message.id));
              if (!ids.every((id, index) => index === 0 || id > ids[index - 1])) {
                throw Error('Out-of-order reentrant request IDs');
              }
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

            # A native SDK timeout must reach the host, even if Python never welcomes it.
            page.clock.install()
            page.goto("https://pyyaki.localhost/sdk-test.html")
            page.evaluate("""async () => {
              window.ipc.postMessage = text => nativeMessages.push(JSON.parse(text));
              const { pyyaki } = await import('/_pyyaki/sdk.js');
              window.probeSdk = pyyaki;
              window.timedReady = pyyaki.ready({timeoutMs: 120000}).catch(error => error.code);
            }""")
            page.clock.run_for(10100)
            assert page.evaluate("timedReady") == "TIMEOUT"
            assert page.evaluate("probeSdk.state") == "failed"
            assert page.evaluate("nativeMessages.at(-1)") == {
                "v": 1,
                "type": "pyyaki_connect_timeout",
            }
            assert page.evaluate("networkAttempts") == 0

            page.goto("https://pyyaki.localhost/sdk-test.html")
            page.evaluate("""async () => {
              window.ipc.postMessage = text => {
                const message = JSON.parse(text);
                if (message.type === 'pyyaki_connect_timeout') throw Error('Host gone');
                nativeMessages.push(message);
              };
              const { pyyaki } = await import('/_pyyaki/sdk.js');
              window.probeSdk = pyyaki;
              window.timedReady = pyyaki.ready({timeoutMs: 120000}).catch(error => error.code);
            }""")
            page.clock.run_for(10100)
            assert page.evaluate("timedReady") == "TIMEOUT"
            assert page.evaluate("probeSdk.state") == "failed"

            details = {
                "message": 'ValueError: 原因 <img src=x onerror="window.injected=true"> 😀',
                "phase": "app_import_started",
                "host_log": "C:\\Users\\" + "長いフォルダ名\\" * 30 + "host.log",
                "worker_log": "C:\\Users\\検証 space\\worker.log",
            }
            page.set_viewport_size({"width": 320, "height": 800})
            page.evaluate((ROOT / "native/failure.js").read_text(), details)
            expect(page.get_by_role("alert")).to_contain_text(details["message"])
            expect(page.get_by_role("alert")).to_contain_text("アプリの読み込み")
            expect(page.get_by_role("alert")).to_contain_text(details["host_log"])
            page.wait_for_function(
                "getComputedStyle(document.querySelector('pre')).whiteSpace === 'pre-wrap'"
            )
            assert page.locator("img").count() == 0
            assert page.evaluate("window.injected === undefined")
            assert page.evaluate("document.documentElement.scrollWidth <= innerWidth")
            output = ROOT / "test-results/native-failure.png"
            output.parent.mkdir(exist_ok=True)
            page.screenshot(path=str(output), full_page=True)
            assert not errors, errors
            print("Native SDK bridge passed: RPC/events, failure/timeout, wrapping UI, no HTTP/WS.")
        finally:
            browser.close()


if __name__ == "__main__":
    main()
