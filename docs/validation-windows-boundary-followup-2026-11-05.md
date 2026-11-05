# Windows境界・異常系の追試（2026-11-05）

実施日: 2026-11-05（日本時間）。[前回の境界検証](validation-windows-boundary-2026-11-05.md)の続きとして、完全なパイプ切断、不要ハンドルの非継承、Job割当失敗、実クリックによる新規窓/download拒否、アプリの孫プロセス回収を確認した。**venvの部分closeは制約として残す。ネットワーク切断・企業PAC・全origin境界等は未確認で、G1/G2全体の合格にはしない。**

## 対象・環境・変更

最終対象コミットは`755630e29285c3c1cef4431cfc96c878109ed937`。最終の起動反復・venv・埋込Python・実クリックの各検証は開始時クリーンで、同一MSVC release exeを使用した。SHA-256は`723214986615bca77d143ec6ff06db5f3e49e13789ac016b9cd12d97675f6ce3`。

| 項目 | 今回の確認値 |
| --- | --- |
| OS / CPU | Windows 11 Pro 25H2、26200.8246、64bit / Intel Core i5-8250U、Architecture=9 |
| 開発Python | 既存venv、CPython 3.14.10、MSC v.1944 AMD64、Py_GIL_DISABLED=0、GIL有効 |
| 対照Python | 前回準備した公式3.14.10 x64埋込Pythonとハッシュ固定wheel。探索パス等は[資材記録](windows-onefile-prototype.md)を参照 |
| Rust | 明示指定の1.89.0（29483883e）、x86_64-pc-windows-msvc |
| WebView2 | 既存Runtime登録版156.0.4314.58 |
| トークン / Defender | Administrator=false。読み取ったAntivirusEnabled、RealTimeProtectionEnabled、IsTamperProtectedはいずれもtrue |

新しいツール・依存・ランタイムは導入せず、準備済みの環境を使用。OS・プロキシ・保護設定を変更していない。アプリへの変更・故障注入はtest-resultsのコピーだけで実行した。

変更は次の2目的でコミットした。

- `2f8a537`: Jobへの割当とスレッド再開を既存と同じ処理のprivate関数へ切り出し、実Win32 APIによる正常対照・割当失敗の回収テストを追加。
- `755630e`: 拒否ハンドラーの有限ログと、診断用`--python`指定・ハンドル同一性対照・孫生成・実クリックケースを追加。拒否条件・公開RPC・CSP・二重rAF・Python開始順・10秒期限は維持。

## 最終検査の件数

| 検査 | 実際の結果 |
| --- | --- |
| MSVC release build / Clippy | locked build、全ターゲットClippyの-D warningsに成功 |
| Rust | **11成功・1 ignored**、失敗0、doc-test 0。ignoredは親テストから明示実行する子エントリーで、親テスト内の正常対照では実際に起動している |
| Python | **112成功・19スキップ**、失敗0。非推奨警告をerror指定 |
| Ruff / Cargo fmt / diff | lint・format・空白検査に成功 |
| 通常/import前5秒/フック5秒 | 各3回、**9/9成功**。全件でスモークok・終了コード0・watchdogなし |
| venv自動境界ケース | **15中14確認、pipe-close 1制約**。終了コード1を保持 |
| 埋込Pythonの対照ケース | **4/4確認**。完全close・非継承・孫の通常/強制終了回収 |
| 実クリックケース | **1/1確認**。trusted入力とホスト拒否ログ、後続RPC、終了回収 |

Pythonのスキップはsymlink権限WinError 1314が3、Darwin renamex_npが1、POSIX SIGINTが1、既存POSIXワーカー検証器が14。Windowsでそれらを実行済みとはしない。今回の追加ハンドル試験は実Windowsの継承と専用パイプを使用した。

ビルド/単体検査終了後、GUI検証を順に単独実行した。最終境界ケースはすべて観測したPIDの終了確認ハンドルを取得でき、watchdog回収なし・残留0。強制終了ケースのホスト終了コード1は意図した結果で、診断器の全件成功/失敗とは区別する。

## 完全切断とvenvによる保持を分ける

同じprivateコマンドでworker自身のwrite fdだけを閉じ、その後30秒待つ。プロセス終了を故障注入の代わりにせず、保留RPCと障害画面を確認した。

| 起動経路 / ホストPID | 結果 |
| --- | --- |
| 既存venv / 5548 | ready後にcloseを実行。2秒の呼出し期限でTIMEOUT。worker_failed/backend_failedなし、診断結果falseを保持 |
| 埋込Python / 19540 | ready後にcloseを実行。connectionでworker_failed/backend_failed、保留RPCはBACKEND_FAILED、障害画面の接続段階・ログ案内を確認 |

埋込Pythonではready通知4,580.4ms、worker_failed 4,582.6ms、backend_failed 4,584.8ms、保留RPCの拒否の観測4,598.0ms。これらはホスト受信時刻であり、closeの厳密な遅延や新たな応答時間保証ではない。既存venvは前回の最小再現と同じくlauncher側にwriterが残る。Microsoftの[匿名パイプの説明](https://learn.microsoft.com/en-us/windows/win32/ipc/anonymous-pipe-operations)では、読取りが切断を認識する条件に全write handleのcloseを挙げている（2026-11-05参照）。

前回の「未達」は全writerを切った状態の未確認と、venvの部分closeを混ぜていた。今回、前者は対照で確認した。後者を直ったことにはせず、venvを迂回する起動変更・ハートビート・他プロセスのhandleを閉じる回避策は追加していない。[ADR-019](architecture.md#8-adr候補一覧)の追加対応は未採択のまま。

## 不要ハンドルとJob割当失敗

非継承試験は新規のsentinelファイルを継承可能にして自分のRustホストへ渡す。自分のPopen子から読取り用にDuplicateHandleし、ホストに同じファイルが届いたことを確認した。DUPLICATE_CLOSE_SOURCEは使わず、他プロセスのhandleを閉じない。

worker側は別に開いたsentinelの正常対照を確認してから、元の数値のhandleが同じファイルを指すか検査する。[GetFileInformationByHandle](https://learn.microsoft.com/en-us/windows/win32/api/fileapi/nf-fileapi-getfileinformationbyhandle)のvolumeとfile indexで照合し、数値handleの再利用による誤判定を避けた（2026-11-05参照）。venv（ホスト6936）・埋込Python（22088）とも正常対照一致・元handleの同一ファイル非継承・回収成功。任意のhandle種類や全プロセスへの一般化はしない。

Jobの負例はWin32の[AssignProcessToJobObject](https://learn.microsoft.com/en-us/windows/win32/api/jobapi2/nf-jobapi2-assignprocesstojobobject)へ、Jobではない有効なNULファイルhandleを渡すテスト。正常対照の子6264は実行マーカーを作成して終了。負例の子23348はCREATE_SUSPENDEDのままOSエラー6で割当に失敗し、既存のエラー経路で終了コード1へ回収、実行マーカーなしを確認した。テスト失敗時も対象は自分の子handleに限定して回収する。

これは実APIによる1つの割当失敗の確認であり、企業のJob制限・アクセス権拒否・全API失敗を再現したものではない。GUIへ失敗を注入する新しい製品フラグや公開APIは追加していない。

## 実クリック・孫プロセス・既存ケース

実クリックはホスト17500の検証ボタンをcomputer-useスキルで操作した。3クリックともevent.isTrustedとnavigator.userActivation.isActiveがtrue。新規窓はnew_window_rejected、同一originのCSS downloadはその直後のdownload_rejectedが記録され、後続RPCも成功した。初回記録だけの有限ログなので、後続data URL操作についてURLごとのハンドラー呼出し件数は判定しない。記録しないURLや保存先、Downloads全体の調査はしていない。

アプリが固定コードで60秒待機する子を生成し、親子関係・観測handleを照合して回収を試験した。

| worker経路 | 通常終了：ホスト / 生成した子 | ホスト強制終了：ホスト / 生成した子 |
| --- | --- | --- |
| venv | 5624 / 14200 | 16884 / 25268 |
| 埋込Python | 23844 / 25332 | 9656 / 7860 |

いずれも観測した子孫に残留なし。強制終了は自分のPopenホストだけをkillし、子孫の回収はJob Objectへ任せた。任意のアプリが生成する全種類の子・breakaway要求・長時間稼働の保証ではない。

既存の不正UI JSON/種別、過大本文、worker異常終了・不正フレーム、import期限、資産/遷移、無効プロキシも最終ホストで再確認した。oversizeはnative_ipc_rejectedのmessage_too_large、遷移はnavigation_rejectedを照合。無効プロキシ標本では対象プロセス稼働中のTCP Listen/UDP endpointはともに0、WebView2の6プロセスでproxy引数を照合した。物理切断や全外向き通信の不在を証明しない。

## 起動回帰・ログ変更・再実行

起動9回はscheduled/frame1/frame2、shell後のPython生成、workerの6段階、welcome、両10,000ms期限を照合した。失敗段階の記録なし。通知時刻にはばらつきがあり、通常1件でPython生成完了→worker通知が約6.59秒だった。負荷・キャッシュ等を固定した性能試験ではなく、NFRの性能達成・性能回帰の原因判断には使用しない。

新たなログはnavigation_rejected / new_window_rejected / download_rejectedが各WebViewの初回1件、native_ipc_rejectedがorigin / message_too_largeそれぞれ初回1件。最大5件で、URL・保存先・本文は記録しない。拒否条件の変更ではない。[段階ログ](startup-diagnostics.md)も参照する。

```powershell
.\.venv\Scripts\python.exe tests\diagnostics_windows.py --binary native\target\release\pyyaki.exe --output test-results\boundary-next
```

この既定実行は実クリックを除く15件で、現venvのpipe-close制約により終了コード1となる。完全切断は、同じ版のPyYakiと依存を準備したPythonを`--python <絶対パス>`へ明示して`--case pipe-close`で比較する。今回の埋込Python配置は[資材記録](windows-onefile-prototype.md)を参照する。

`--case user-actions`は明示指定時だけ有効。画面のボタンを実クリックするメンテナー試験で、外部watchdogのみ180秒。ホスト・SDKの初期化期限は変更していない。

## 元ログ・残る項目

保存先は`test-results/windows-boundary-followup-20261105/`。最終はstartup/、final-venv/、final-embedded/、final-user-actions/とevidence-summary.json、startup-summary.json。build-final/rust-test-final/clippy-final/pytest-final等のlog/json、環境・ツールチェーン、画面/UIA証拠も保持した。Job負例は`test-results/windows-job-16460-1793845289194053700/`に保存。Git管理外で、Macへは自動転送されない。

初回追加試験はfixtureの未対応戻り値型でapp importが失敗し、緩い判定が別段階の失敗を成功と数えた。元ログをvenv/へ残し、戻り値型と段階・ready後の注入確認を修正した。最終の全検証で再確認済み。初回Clippyのunsafeコメント位置の失敗も元ログを残し、修正後の成功とは別に保持している。

未確認: ネットワークの物理切断・企業PAC・外向き通信トレース、iframe由来IPCの直接注入と全origin条件、reparse point/全資産境界、キュー飽和・全長境界、Jobの全失敗条件、GDI実提示・性能、企業ポリシー/MOTW/署名、時間単位の稼働。共通host.rsのログ追加はMacビルド・WKWebViewで未確認。CSP等を緩めたり、端末全体のネットワークを切ったりして確認範囲を広げる操作はしていない。単一exe生成も引き続き未実装。
