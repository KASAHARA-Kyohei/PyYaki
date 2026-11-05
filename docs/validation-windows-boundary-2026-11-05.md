# Windowsの境界・異常系確認（2026-11-05）

実施日: 2026-11-05（日本時間）。優先依頼の[修正版の起動反復9/9](validation-worker-logs-2026-11-05.md#修正版の起動反復の追試2026-11-05)の後に実施した。**12ケース中11ケースの所定の確認に成功し、パイプ片側closeの1ケースは合格にしない。G1/G2全体の合格ではない。** 製品のRust/Python/SDKは変更せず、再実行できる専用検証器とfixtureを追加した。

本書は初回検証時点の記録。同日の[追加追試](validation-windows-boundary-followup-2026-11-05.md)では埋込Pythonでの完全切断、不要ハンドル非継承、Job割当失敗時の回収、実クリック拒否、孫プロセス回収を確認した。venvの部分closeは制約として残している。

## 対象と実施方法

最終検証器のコミットは`f8e9893e44de8be79f8af3a74ab8be8c92af3bcb`、開始時クリーン。MSVC releaseホストのSHA-256は`2f7e91d10c980daeec9356d60522bbc7ac5ac536a18ce437bcf3e8555687f5a5`で、起動反復と同一。Windows 11 Pro x64 / i5-8250U / CPython 3.14.10通常GIL版の既存venv / WebView2登録版156.0.4314.58を継続利用した。詳細な環境は[同日のログ修正記録](validation-worker-logs-2026-11-05.md)を参照する。Rustの再ビルドや依存更新はしていない。

```powershell
.\.venv\Scripts\python.exe tests\diagnostics_windows.py --binary native\target\release\pyyaki.exe --output test-results\windows-boundary-20261105-final
```

`tests/fixtures/windows-boundary/`をケースごとにtest-resultsへコピーし、そのコピーだけに静的設定・障害条件を追加する。`--case <ケース名>`の反復指定で個別追試できる。既存ホストのメンテナー用`--diagnostics`で結果を保存・自動終了する。故意のcrashや生パイプ書込みはこの専用fixtureだけにあり、helloや公開APIには追加しない。

各ケースは新しいホストを生成し、Toolhelpで親子関係を調べ、自分のホストと検出した子の終了確認用ハンドルを保持する。35秒の外部watchdogは自分のPopenハンドルだけを終了する。終了後は最大10秒観察する。最終12ケースともwatchdog終了なし、観測した全PIDの確認ハンドルを取得でき、残留0だった。短命で採取できなかったプロセス一般の不在や、長時間の安定性を保証しない。

障害画面の確認は実WebView2内のDOMテキスト・ログ案内・段階ログを使用した。今回の各障害について目視や録画を追加したものではない。期待した障害を確認したケースは診断結果で終了コード0にするため、アプリが正常起動した結果と混同しない。

## 最終結果

| ケース / 自分のホストPID | 結果・停止段階 |
| --- | --- |
| ui-json / 24704 | 接続後に不正JSONを送信。connectionでbackend_failed、JSON解析のEOF原因とログ案内を確認 |
| ui-type / 22536 | 未定義のUIメッセージを送信。connectionでbackend_failed、Invalid native UI messageの表示を確認 |
| oversize / 16572 | UIから1,048,577文字の本文を送信。入口で無視され、その後の通常RPC成功。キュー飽和や全境界長を網羅したものではない |
| worker-crash / 24156 | 接続後に専用コマンドでworkerがexit(17)。connectionでworker_failed/backend_failedとログ案内を確認 |
| pipe-close / 5332 | **未達**。worker自身のwrite fdだけを閉じて待機。2秒の呼出し期限ではTIMEOUTとなり、障害画面・EOF通知は来なかった。下記参照 |
| import-timeout / 23704 | コピーのimportを15秒待機。app_import_startedまで進み、SDKの既存10秒期限→backend_failed。期限・ログ案内を確認 |
| worker-frame-zero / 18212 | import途中に長さ0を注入。app_import_startedでinvalid frame size、welcomeなし |
| worker-frame-large / 23108 | import途中に長さ1,048,577を注入。app_import_startedでinvalid frame size、welcomeなし |
| worker-frame-json / 20216 | 長さ1の不完全JSONを注入。app_import_startedでJSON解析エラー、welcomeなし |
| assets / 23012 | 正常CSSの読込とstatus=200を対照に、UI外・二重エンコード・backslash・drive形式・予約/隠しパスのCSS読込みを拒否。その後のRPC成功 |
| navigation / 15704 | 別ローカルページ・外部URLへの移動後も元ページとRPCを維持。iframeの対象文書は読込まれず、window.openの返却値はnullまたはclosed |
| invalid-proxy / 22296 | 子プロセス限定の無効プロキシ指定下でUIと日本語RPC成功。下記の採取条件でTCP Listen/UDP endpointとも0 |

最終検証器の終了コードは**1**。pipe-closeを除外して全成功と表示していない。通常/import5秒/フック5秒の9回反復とは別の実行である。

資産試験では、UI外の`private.css`とUI内の`private/secret.css`、`_pyyaki/unknown.css`、`.secret.css`へ有効なCSSを用意した。単にファイル不存在や異なるMIMEで拒否されただけの確認を避けた。`%2e%2e`はブラウザによるURL正規化も含む。失敗した資産のResource TimingのresponseStatusは0だったため、これだけでHTTP相当404を観測したとはしない。URI全形式・reparse point・symlink・競合を網羅した試験でもない。

新規窓の結果にはブラウザのpopup抑制が関与し得るため、ユーザー操作を伴うホストの拒否ハンドラー単独の証明にはしない。ダウンロード拒否とiframe由来IPCの直接注入は未確認。

## パイプ片側closeの調査

初期検証器は「最終的に障害画面になれば成功」としており、このケースも誤って成功と数えた。元ログでは呼出しが約30秒でTIMEOUTとなった後、workerの処理終了・切断に伴って障害画面になっていた。期限内に通知した証拠にならないため、検証器を修正し、最終実行では未達・終了コード1としている。

独立した最小再現で、継承パイプのwrite handleをPython本体が閉じた後2秒待機する条件を比較した。既存venvのpython.exe経由ではEOFまで約2.17秒、同じ基盤Pythonの直接起動では約0.089秒だった。[CPython 3.14.10のvenvlauncher実装](https://github.com/python/cpython/blob/v3.14.10/PC/venvlauncher.c)も、継承可能ハンドルを持ったまま基盤Pythonを生成し、その終了を待つ構成である（2026-11-05参照）。この保持がEOFを遅らせる説明と整合する。

この故障注入では全writerを閉じた状態を作れていない。実際のworker異常終了の拒否・回収は別ケースで確認したが、workerプロセスが生きたままの部分切断を即座に検出できるとは扱わない。2秒は診断用invokeの期限で、新しい製品の受入基準ではない。開発venvを迂回する変更や、他プロセスのhandleを閉じる回避策は実装していない。対応候補と採否条件は[ADR-019](architecture.md#8-adr候補一覧)へ残した。

## 無効プロキシと待受の観測

検証ホストの環境だけに`WEBVIEW2_ADDITIONAL_BROWSER_ARGUMENTS=--proxy-server=http://127.0.0.1:9 --proxy-bypass-list=<-loopback>`を指定した。[Microsoftの開発用フラグの説明](https://learn.microsoft.com/en-us/microsoft-edge/webview2/concepts/webview-features-flags)に従う診断用途であり、製品の既定設定ではない（2026-11-05参照）。実WebView2の6プロセスをCIMで採取し、proxy引数が反映されたことを照合した。OSやブラウザのグローバル設定・セキュリティ設定は変更していない。

同じ観察区間の前後で対象ホストを含む10 PIDが稼働していることを確認し、Get-NetTCPConnection/Get-NetUDPEndpointの失敗を無視せず採取した。対象PIDのTCP Listen=0、UDP endpoint=0。単一標本であり、短時間の待受、DNS/外向き通信の全不在、企業PAC、物理的なネットワーク切断を証明するものではない。最初の採取はプロセス終了後だったため無効とし、最終試験で置き換えず別名に保存した。

## 保存先と残課題

最終の全ログ、UI内の観測値、起動段階、コピー、生成PID、回収結果、実プロセス引数は`test-results/windows-boundary-20261105-final/`。元の試行は`windows-boundary-20261105/`、個別追試は`windows-boundary-20261105-followup/`と`windows-boundary-20261105-assets*`へ保存した。実行全出力・終了コードは`test-results/windows-startup-20261105/boundary*.log/json`、集計と最小EOF再現は同ディレクトリの`boundary-summary.json`と`pipe-retention.json`にある。Git管理外なのでMacへ自動転送されない。

追加後のPython検査は112成功・19スキップ、Ruff lint/format成功。スキップ理由は[同日の記録](validation-worker-logs-2026-11-05.md)と同じ。Rust製品ソースは変えておらず、この検証器追加のためのMSVC再ビルド・Rustテスト・Clippyの再実行はしていない。

残る確認は、完全なパイプ切断と部分closeの区別、不要ハンドルの非継承/Job Object割当失敗の負例、ユーザー操作を伴う新規窓・download、全origin/iframe IPC、ネットワーク切断・PAC・外向きトレース、物理的先行描画・性能、企業ポリシー、時間単位の稼働。
