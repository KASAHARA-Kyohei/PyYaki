# ネイティブ起動の切り分け

更新: 2026-11-04。Windows実機検証で発生した「5秒遅延の初回だけ接続期限に到達」の原因を追うため、共通ホストとワーカーに段階記録を追加した。原因やWindowsでの解消を確認したものではない。元の失敗条件は[Windows実機記録](validation-windows.md)を参照する。

## 記録と障害画面（実装済み）

通常起動でも、各instanceの`host.log`へ`pyyaki_startup=`に続く1行JSONを出力する。保存先はWindowsでは`%LOCALAPPDATA%\PyYaki\prototype\<instance>\`、Macでは`~/Library/Application Support/PyYaki/prototype/<instance>/`。`--diagnostics`指定時だけ同じ段階を`pyyaki_diagnostic=`としてstderrにも出力する。nonce・RPC引数は起動記録に含めない。

| 主なphase | 記録する地点 |
| --- | --- |
| `host_started` | 設定とログの準備後。data.pidは検証器が自分の子を識別するために使用 |
| `native_draw` / `webview_created` | 起動表示の描画呼出し / WebView生成の完了 |
| `asset_requested` / `asset_response` | HTMLトップページとSDKの初回取得・応答準備。HTTP status相当値とバイト数 |
| `ui_deadline_started` | WebView生成後のHTML準備期限（10秒）の開始 |
| `sdk_connect_started` | SDKが接続を開始した旨の通知をホストで受信 |
| `sdk_shell_progress` | 二重rAFのscheduled/frame1/frame2と、待機中のvisibility/focus/blur。DOMのvisibility・focusを最大16件記録 |
| `ui_window_state` | HTML準備待ちの間、約1秒ごとのネイティブ窓のvisible/focused/minimized。Macはアプリのactive/hidden、WebViewのhidden祖先・visibleRect寸法、所属NSWindowのocclusionStateのbitsも記録 |
| `shell_marker` / `sdk_hello_received` | レイアウト後の通知 / 公開helloをホストで受信 |
| `backend_deadline_started` | shell通知後のワーカー準備期限（10秒）の開始 |
| `python_spawn_started` / `python_spawned` | バックグラウンドでのワーカー生成呼出し前後 |
| `worker_entered` / `config_loaded` | 内部helloの検証後 / アプリ設定の読取後 |
| `app_import_started` / `app_imported` | アプリimportとentry検査の前後 |
| `startup_hooks_started` / `startup_hooks_completed` | lifespanの開始処理の前後 |
| `worker_welcome` / `backend_ready` | 内部welcome受信 / GUIで接続準備完了を処理 |
| `sdk_welcome_posted` | 公開welcomeをWebViewへ送信。SDK受信完了の保証ではない |
| `sdk_connect_timeout` / `host_initialization_timeout` | SDK接続期限の通知 / ホスト側の期限到達 |
| `worker_failed` / `backend_failed` | ワーカー通信・初期化の失敗 / GUI側の障害処理 |

`host_elapsed_ms`はRustのrun入口を基準とした、その記録を行うスレッド上の経過時間。外部CreateProcess前のt0ではない。ワーカーの6段階には、内部hello検証後を基準とした`data.worker_elapsed_ms`も付く。差分にはパイプ送受信、スレッド/asyncioのスケジューリングも含む。Python生成から`worker_entered`までにはインタープリター・PyYaki自体のimportも含み、各々の純粋なCPU時間を分離した値ではない。

描画待ちの記録は2026-11-04の追加切り分け用。scheduledだけなら最初のrAF待ち、frame1だけなら2回目の待ち、frame2後ならshell通知の配送等を調べる。DOMの表示判定とネイティブ窓の状態は別々に記録し、物理的な提示の証明とはしない。JSの監視はshell通知前または接続終了時に解除する。記録を増やす変更であり、初期化期限とPythonを起動する順序は変えていない。

SDKの接続タイマーは`ready()`による接続開始時から10秒、ホストのワーカー準備期限はshell通知の処理時から10秒。`ready({timeoutMs})`の呼出し単位の待機期限も別にある。今回これらの期限を延長・統合していない。SDKの内部接続期限に達した場合は、ホストにも通知して障害画面とワーカー回収へ進む。Web経路にこの通知は追加しない。

import・起動フック等の通常例外では、Pythonが先に完全なtracebackを`worker.log`へ保存し、専用パイプで例外名（最大128文字）とメッセージ（最大1,024文字）をホストへ通知する。障害画面は停止段階、メッセージ、host/workerログのパスを表示する。すべてtextContentで扱い、CSSは予約資産`/_pyyaki/failure.css`から配信して長い文字列を折り返す。例外文字列に含まれる秘密情報の自動除去は行わない。

Python/PyYaki自体の起動失敗、強制終了、パイプ切断では構造化した例外が届く保証はない。その場合も停止段階とログの案内を出す。ワーカー進行通知は6件の固定順序・版・nonce・有限かつ単調な経過時間を検査し、初期化完了前にwelcomeを許可しない。内部スキーマは[API設計](api-design.md#54-内部ワーカー接続)を参照する。

## Windowsで再現条件を集める

Pythonはeditable導入済みのvenvを使い、ホストを再ビルドする。内部起動通知を追加したため、以前のホストと新しいPythonを混ぜない。これは開発用の検証手順で、単一exe生成の例ではない。

```powershell
cargo +1.89.0 build --manifest-path native/Cargo.toml --release --locked
.\.venv\Scripts\python.exe tests\startup_probe.py --binary native\target\release\pyyaki.exe --runs 5 --delay 5
```

`+1.89.0`は2026-11-03の端末で追加したツールチェーンを選ぶ指定。別の端末では導入済みの対応版を確認する。Macで同じ検証器を使う場合は次のように実行する。

```console
.venv/bin/python tests/startup_probe.py --binary native/target/release/pyyaki --runs 5 --delay 5
```

通常起動、import前5秒待機、起動フック内5秒待機を各5回、順番に起動して実スモークを実行する。サンプル本体は変更せず、出力先のコピーだけに遅延を加える。既定は新規の`test-results/startup-<UTC日時>/`で、`--output <新規ディレクトリ>`でも指定できる。report.jsonには環境、コミット、未コミット変更の有無、ホストのハッシュ、各起動の成否と段階を保存する。各ケースへhost/worker/launcherログを複写する。プロファイルは複写しない。

自動スモークが30秒以内に終了しなければ、検証器が自分の子だけを終了させ、`closed_by_harness: true`の失敗として残す。タイマーの実行遅延等も含めた原因はログを調べる。終了コード0だけで合格にはせず、スモーク結果とwelcome送信も確認する。失敗後も残りのケースを続け、いずれか失敗なら検証器の終了コードは1となる。失敗ログを上書きせず、まず他のビルド/試験と並行させずに測り、その後必要に応じて負荷条件を分ける。

共有するのはreport.jsonと該当ケースのhost/workerログを基本とする。ログにはローカルパスやアプリ例外が含まれるため、共有前に内容を確認する。結果を受けてから、遅延がHTML/SDK取得、shell通知、Python起動、import、起動フック、welcome配送のどこにあるかを切り分ける。

## Macで確認した範囲（2026-11-04）

- Python 82件、Rust 12件。MacとWindows GNUターゲットのClippy。Windows MSVCでの新しいコードのビルド・実行は未確認。
- Chromiumの仮ネイティブブリッジで、SDK期限通知、既存RPC/イベント/障害通知、320px幅での長いログパスの折り返しと例外文字列のHTML非実行を確認。
- 実WKWebViewでimport失敗・起動フック失敗・15秒import待機の3ケースを実行。例外または10秒期限、障害画面のログ案内・CSS、検証終了を確認。15秒待機では`sdk_connect_timeout`が記録された。期待した障害を検証用UIで確認して終了した結果であり、そのアプリの正常起動ではない。
- 既存診断を`--duration 0 --startup-runs 1`で6ケース実行し、遅延・一時停止/再開・クラッシュ・終了待ちの回帰確認に成功。連続稼働時間の延長や性能基準の合格には使わない。

最初の反復検証（`test-results/startup-20261103T232624Z/`）は6件中5件成功。初回通常起動だけshell通知がなく、Python生成前にホストの10秒期限へ達した。検証器が30秒で終了させて失敗を保存した。この時点ではHTML/SDK取得の記録がなく、原因は未確定。取得記録とSDK接続開始通知を追加した最終コードでの再検証（`test-results/startup-20261103T232953Z/`）は、通常/import遅延/起動フック遅延の各2回、6件成功。記録の追加を初回失敗の修正や安定性の証明と扱わない。

実WKWebViewの障害検証は`test-results/native-startup-failures/report.json`、短い既存診断は`test-results/diagnostics-20261103T232707Z/report.json`に保存した。これらはMacのGit管理外の記録であり、Windowsへ自動転送されない。UTCの保存先名と日本時間の実施日は区別する。画面の実提示、Windowsの元の失敗解消、時間単位の安定性、G1/G2全体は未検証のまま。

## 表示状態の比較時に再現した起動失敗（2026-11-04）

タイマー比較とは別に、比較用フラグを付けないhelloのreleaseスモークで、HTML/SDKは取得できたがshell通知が届かず、Python生成前の10秒期限へ達するケースを再確認した。失敗した自分のPIDとコマンドを照合してSIGTERMで終了した。ログと実行条件は`test-results/timer-startup-20261104/`に保存した。表示・フォーカス状態はこの起動で記録しておらず、原因を断定しない。

続く既存反復検証器の`--runs 1 --delay 0`では3件中2件成功、最初のbaselineだけ失敗した。接続開始437ms、ホスト期限10,326ms、shell通知・Python生成なし。30秒の外部watchdogで終了し、成功したimport/hooksケースも待機0秒なので、5秒遅延の確認には使わない。元記録は`test-results/startup-20261104T044233Z/report.json`とケースごとのログ。再試行の成功で初回失敗を解消扱いにしない。

SDKは2回のrequestAnimationFrame後にshell通知を送る実装である。[WebKit公式説明](https://webkit.org/blog/8970/how-web-content-can-affect-power-usage/)（2019-08-27公開、2026-11-04参照）には非アクティブ時のrAF停止とタイマー抑制が記載されており、表示条件は調査候補になる。ただし[今回の窓状態比較](performance-macos.md)はready後の測定で、初期rAFの停止を直接測っていない。起動時の表示状態とrAF進行を記録する切り分けが残る。先行描画の順序や10秒期限は変更していない。

同日のアイコン組み込み作業でも、開発ホストの初回スモークと1回の再実行が失敗した（`test-results/icon-proof/`）。最初の失敗はSDK接続開始1,478ms、ホスト期限10,742msで、shell通知とPython生成なし。Dock設定はTaoの起動後、最初の描画呼出しの後に行うよう整理したが、最終コード`cf3c2c6`の`--runs 1 --delay 0`も3件中2件成功・初回だけ失敗（`test-results/icon-proof-final-startup/report.json`）。この再確認は.app生成処理と一部並行しており、起動性能の計測には使わない。アイコン入り.appのスモーク成功とは分け、元の失敗を上書きせず、原因・解消とも未確定として残す。
