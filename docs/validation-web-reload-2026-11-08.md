# Web開発時の自動再読込: Mac検証

実施日: 2026-11-08。対象実装は`f8ed9486002d6e92b6e5cb7f7c20df7c9f91ff7e`。開始時は既存変更なし、`main`は`origin/main`より6コミット先行していた。既存変更の取り消しは行っていない。今回の実装コミットはその後に追加したもので、`008ec68`を祖先に含む。

## 実装範囲

FR-06の`pyyaki dev`向け自動更新を追加した。プロジェクト内のPythonとルート設定の変更・追加・削除はspawn子サーバーを再起動し、UI内の変更はPythonを再起動せずページ全体を更新する。親が確保したloopbackソケットは再起動間も維持する。追加依存はない。比較と残る判断はADR-021、監視対象・除外・終了動作は[開発ガイド](development.md#web開発時の自動更新)を参照する。

HTML応答に外部moduleを挿入し、元のHTMLを変更しない。更新確認は同一originの開発専用経路で行い、CSPを緩めず、RPCのOrigin・cookie/nonce・期限・制限値を維持する。`--no-reload`と`app.asgi()`には監視・更新通知を挿入しない。ネイティブの二重rAF・10秒期限・Python開始順、SDKには変更がない。

## 環境と結果

Mac ARM64 / macOS 27.0.1、CPython 3.14.3通常GIL版。FastAPI 0.162.0、Pydantic 2.14.2、Uvicorn 0.54.0、websockets 15.0.1、Playwright 1.64.1、Chromium 156.0.8078.4。ブラウザはheadless、`chromium_sandbox=True`で起動した。セキュリティ機能は無効化していない。

| 検査 | 結果・確認範囲 |
| --- | --- |
| Python全回帰 | `pytest -q -W error::DeprecationWarning`で191成功、スキップ0 |
| 静的確認 | `ruff check`と`ruff format --check`を`src examples tests native/worker_bootstrap.py`に実行し成功 |
| UI更新 | HTML変更が2ページへ反映。CSSとJavaScriptの変更も反映し、Python PIDが変わらないことを確認 |
| Python更新 | 依存ファイルの同サイズ編集とファイル追加/削除でPIDが変わり、ページ更新後にSDKから新しい値を取得 |
| pyc | 古いpycを先に生成し、依存ファイルを同サイズ・同mtimeで編集しても更新内容を取得 |
| 修正復帰 | 起動済みサーバーへ構文エラー、続いてTOMLエラーを注入。親が残り、各修正後にページとRPCが復帰 |
| 終了 | 再起動時・最終終了時の終了フックを確認。接続中WebSocketを含むSIGINTで終了130、終了時の不要なtracebackなし |
| Ctrl+Cの範囲 | pytestで親だけへのSIGINTと、自分で新規セッションとして起動したプロセスグループへのSIGINTを、reload有効/無効の双方で確認 |
| 無効化と境界 | `--no-reload`はHTMLをそのまま返し開発経路は404。`app.asgi()`相当の既定アダプターにも挿入なし。外部Origin/Host・cross-site拒否、CSP維持を確認 |
| HTML挿入 | コメントやscript内の`</head>`文字列を挿入位置と誤認せず、元の日本語バイト列を保つ単体試験に成功 |

最終ブラウザ検証は9/9項目、pageerrorは0。実装コミット後、作業ツリーがcleanの時点で検証を開始した。原ログ・reportはこのMacのGit管理外に保存している。

```text
test-results/web-dev-reload-final-pytest.log
test-results/web-dev-reload-f8ed948.log
test-results/web-dev-reload-f8ed948/
  report.json
  server.log
  initial-dom.html
  before.png / after.png
  trace.zip
  process-check.txt
  project/                 # 編集したhelloのコピーと終了フックのログ
```

記録された親1件・サーバー7件のPIDを終了後に照合し、残存なしを確認した。これは全子孫・ブラウザ内部プロセスの完全な資源回収確認ではない。テストfixtureの編集はすべて`test-results`配下で行い、他のサーバーや利用者のアプリを終了していない。

## 失敗と切り分け

1. 初回のPython検査は実行環境のsandboxによりloopbackのbindが`Operation not permitted`で拒否され、3失敗/44成功だった。`test-results/web-dev-reload-initial-pytest.log`を残した。ローカル通信を許可した実行で47成功、その後の追加試験を含む最終191成功を確認した。製品のCSPやOS設定を変更して回避していない。
2. 初回のブラウザ検証はHTML・2ページ更新まで成功し、CSS待機の`wait_for_function`がCSPの`unsafe-eval`拒否で失敗した。製品ではなく検証コードの待機方法を修正し、CSPは維持した。原ログとfailed reportは`test-results/web-dev-reload-browser-first.log`、同名ディレクトリの`server.log`・`failure.log`・`report.json`に保存した。その後のブラウザ確認と最終コミットの実行は9/9成功した。
3. 最終`server.log`内のSyntaxError・TOMLDecodeErrorは修正復帰を確認するための意図的な注入であり、隠していない。初回起動時のエラーからCLIを残す仕様には変更していない。

## 未確認・対象外

- この機能のWindows実機でのspawn/socket受け渡し、Ctrl+C、ファイル保存・再起動。Macでの成功をWindows合格にはしない。
- 巨大なツリー・多数のブラウザ・ネットワークドライブでの負荷、監視漏れ、長時間稼働。250/500msは検査間隔であり反映時間の保証ではない。
- 親のSIGKILL時の子回収、停止しない業務コードと孫プロセスの完全回収。正常終了待ち超過時のterminate/kill分岐の実故障注入は未実施。
- プロジェクト外の依存、シンボリックリンク、除外ディレクトリの変更監視。手動再起動が必要。
- HMR、画面入力やContextの維持、処理中RPCの引き継ぎ、ネイティブ自動更新は対象外。
- Safari/Firefox、利用者の実画面、Rust・実WKWebView・生成.appは今回再検証していない。

## 再実行と次の候補

手順は[開発ガイド](development.md#検証する)を参照する。ブラウザ検査は新しい出力先を指定し、事前にサーバーを起動しない。

```console
.venv/bin/python tests/browser_dev_reload.py --output test-results/web-reload-new
```

次はWindows実機で同じ開発更新・終了を確認し、必要に応じて大規模ツリーの監視負荷を計測する。起動高速化は実アプリの遅い区間が測定されてから判断する。Rust不要のネイティブCLI配置・配布は別の残作業として維持する。
