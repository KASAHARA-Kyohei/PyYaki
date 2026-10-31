"""Run via with_server.py; exercises the real SDK and browser network stack."""

from pathlib import Path

from playwright.sync_api import expect, sync_playwright

ROOT = Path(__file__).resolve().parents[1]


def main():
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=True)
        try:
            page = browser.new_page(viewport={"width": 1280, "height": 960})
            errors = []
            page.on("pageerror", lambda error: errors.append(str(error)))
            page.goto("http://127.0.0.1:8000")
            page.wait_for_load_state("networkidle")
            expect(page.locator("#connection")).to_have_text("Python 接続済み")
            page.get_by_label("お名前").fill("鯛焼き🐟")
            page.get_by_role("button", name="あいさつする").click()
            expect(page.locator("#result")).to_contain_text("こんにちは、鯛焼き🐟さん")
            page.get_by_role("button", name="エラーを試す").click()
            expect(page.get_by_role("alert")).to_contain_text("APP_DEMO")

            page.get_by_role("button", name="3秒の処理を試す").click()
            expect(page.locator("#activity")).to_have_text("WORKING")
            page.get_by_role("button", name="UI応答のカウンター").click(click_count=3)
            expect(page.locator("#count")).to_have_text("3")
            # A second Python command also completes before the delayed command.
            page.get_by_role("button", name="あいさつする").click()
            expect(page.locator("#result")).to_contain_text("こんにちは、鯛焼き🐟さん")
            expect(page.locator("#wait")).to_be_disabled()
            expect(page.locator("#progress")).to_contain_text("Pythonからの進捗:")
            expect(page.locator("#result")).to_contain_text("3秒の処理が完了", timeout=5000)
            expect(page.locator("#progress")).to_contain_text("3 / 3")
            expect(page.locator("#wait")).to_be_enabled()

            codes = page.evaluate("""async () => {
              const { pyyaki } = await import('/_pyyaki/sdk.js');
              const codes = [];
              for (const value of [undefined, NaN, 1n, new Date()]) {
                try { await pyyaki.invoke('greet', {name: value}); }
                catch (error) { codes.push(error.code); }
              }
              try { await pyyaki.invoke('wait', {seconds: 3}, {timeoutMs: 50}); }
              catch (error) { codes.push(error.code); }
              const controller = new AbortController();
              const work = pyyaki.invoke('wait', {seconds: 3}, {signal: controller.signal});
              controller.abort();
              try { await work; } catch (error) { codes.push(error.code); }
              await pyyaki.invoke('greet', {name: 'after-cancel'});
              return codes;
            }""")
            assert codes == ["BAD_REQUEST"] * 4 + ["TIMEOUT", "CANCELLED"], codes
            assert not errors, errors
            output = ROOT / "test-results"
            output.mkdir(exist_ok=True)
            page.screenshot(path=str(output / "hello-desktop.png"), full_page=True)
            page.set_viewport_size({"width": 390, "height": 844})
            assert page.evaluate("document.documentElement.scrollWidth <= innerWidth")
            page.screenshot(path=str(output / "hello-mobile.png"), full_page=True)
            print(
                "Browser smoke passed: result/error, responsive UI, concurrency, "
                "SDK validation/cancel."
            )
        finally:
            browser.close()


if __name__ == "__main__":
    main()
