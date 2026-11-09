# PyYaki native host prototype

2026-11-09追記: `pyyaki-cli.exe`はWindows wheel用のconsole入口、従来の`pyyaki.exe`はGUI/onefile用ホスト。利用者の導入・dev/run/buildは[同梱wheelの手順](../docs/windows-wheel.md)、実機結果・失敗は[検証記録](../docs/validation-windows-wheel-2026-11-09.md)を参照。以下のCargo手順はメンテナーだけに必要。

2026-11-07時点の開発用ネイティブホスト。Rustのtao/wry/WebView2で同じ`examples/hello/ui`を読み、別プロセスのPythonワーカーへ専用匿名パイプで接続するソースを実装した。MacではRust共通処理・Python子プロセス・SDK契約を試験し、Windows GNUターゲットの型検査を行った。**2026-11-03にWindows 11 x64のMSVC releaseビルド・実WebView2のUI/RPC・イベント・取消・終了時のPython回収を確認した。G1/G2全体や単一exe配布は未完了。失敗・未確認事項は[Windows実機記録](../docs/validation-windows.md)を参照する。**

共通APIの現在地は[ロードマップ](../docs/roadmap.md#現在地と次の作業)、SDK変更時の再ビルドや検査の使い分けは[開発ガイド](../docs/development.md)を参照する。

## アイコン

C案を既定アイコンとして組み込む。Macでバイナリを直接`run`する場合はAppKitへ埋め込み512px PNGを設定し、生成.appは`Contents/Resources/PyYaki.icns`と`CFBundleIconFile`を使う。既存のバイナリ・.appには自動反映されないので、再ビルド・再生成する。

Windowsは`build.rs`がICOをリソースID 1へ組み込み、Taoがそのリソースからウィンドウとタスクバーの画像を読み込む。開発ビルドにはWindows SDKのrc.exeが必要（既存のMSVC/SDK構成）。GNUターゲットではwindresが必要で、Macからの型検査でもリソースをコンパイルする。コンパイラーが見つからない場合にアイコンなしで黙って成功させない。これらは開発用の資材で、配布後実行には不要。

形式変換の手順と原本は[ブランド資産](../assets/branding/README.md)を参照。通常のRustビルドやMac .app生成は保存済み資産を使い、Pillowを実行しない。独自アイコン用の設定、Webのfavicon、Windows単一exe生成はこの変更に含まれない。2026-11-04に更新版のMSVCビルドとExplorer/ウィンドウのC案実表示を確認した。タスクバーは利用者の目視回答で確認し、自動スクリーンショットとは区別する。[再検証記録](../docs/validation-windows-2026-11-04.md)を参照。

2026-11-04にMacのdebug/releaseビルド、ICNSを含むwheel、.appの生成・署名・資産ハッシュを確認した。Windows GNUターゲットのreleaseホストもMacで生成し、PEのICON_GROUP ID 1の7画像がICOの各PNGとバイト単位で一致した（`test-results/icon-proof/windows-resources.json`）。Python等のペイロードを同梱したWindows配布アプリの生成・実機起動ではない。Dockの画面取得はタイムアウトしたため、実表示の目視確認は未完了。

## Macでの開発用確認

2026-11-09に[ホストSIGKILL時の回収対策](../docs/validation-macos-host-cleanup-2026-11-09.md)を追加した。Macでは同じ実行ファイルの内部guardがPythonと同じ専用グループを保持し、生存pipe切断/ワーカー終了時に回収する。新規ホストで同梱wheel/.appを再生成する。既存の二重rAF・10秒期限・shutdownの2秒待ちは維持する。全子孫やグループ離脱、guardとの同時SIGKILLを保証せず、Windows Jobの代替ではない。

Macホスト同梱wheelを導入済みなら、Rust/Cargoなしで`pyyaki run --project <project>`を使える。[導入・wheel生成と検証](../docs/macos-wheel.md)を参照。以下はRustホストを開発するメンテナー向けの直接起動で、pure wheel/editable導入ではこちらを使う。WindowsのネイティブCLI配置は[Windows wheel試作](../docs/windows-wheel.md)を参照。

利用者の依頼によりMac開発用ホストを追加した。tao/wry/WKWebView、同じ静的UI・SDK・Pythonワーカーを使用する。Rust 1.89以降とCPython 3.14の開発venvを用意し、リポジトリ直下で実行する。

```console
cargo run --manifest-path native/Cargo.toml --locked -- run --project examples/hello --python .venv/bin/python
```

MacのWebViewは`pyyaki://localhost/`から資産を取得し、HTTP/WebSocketサーバーは立てない。送信元と初回ナビゲーションを制限する。Rust→JSはJSONを二重シリアライズしてJSON.parseへ渡す固定スクリプトで配送する。SDK/Contextのmodeは`macos`。Pythonパスはvenvのシンボリックリンクを解決せず保持する。

Windows専用処理を`process.rs`、Macを`process_macos.rs`に分け、イベントループ・資産配信・プロトコルは`host.rs`で共用。Macでは子プロセス専用のプロセスグループとCLOEXEC付き匿名パイプを使い、子のexec直前に必要な2fdだけを継承可能にする。終了時はshutdownを通知して最大2秒待ち、残るグループを回収する。Ctrl+C/SIGTERMも同じ終了経路を使う。SIGKILLなどホストを強制終了した場合の子プロセス回収は未保証で、正式配布前の課題。

ログは`~/Library/Application Support/PyYaki/prototype/<instance>/`。Python同梱.appのローカル生成試作は[専用手順](../docs/macos-build.md)を参照。Developer ID署名/公証・他Macへの正式配布は未対応。SDK 27とのリンクエラーが出るこの開発機では、インストール済みSDK 26.5を`SDKROOT=/Library/Developer/CommandLineTools/SDKs/MacOSX26.5.sdk`で選択してビルドした。

2026-11-08の[Mac回帰確認](../docs/validation-macos-2026-11-08.md)は、更新ホストでPython 145件・Rust 13件・Mac Clippy/release、実WKWebView反復9/9、UTF-8/例外DOM/SIGTERM終了、再生成.appの署名・全ハッシュ・再配置を確認した。初回のSpace関連失敗を保持し、利用者の表示モード申告・目視・実close/download拒否等は未確認。今回も既定SDK 27のリンクが失敗し、実在を確認した26.5をコマンド単位で指定した。Windows GNU/MSVC検査を今回実行した記録ではない。

続く`9e9302a`の[初期Space配置対策](../docs/validation-macos-space-2026-11-08.md)は、Mac主窓のcollectionBehaviorをtao起動activate前から最初のshell通知までMoveToActiveSpaceへ一時変更し、成功/失敗/終了時に元へ復元する。追加の前面化、全Space常時表示、二重rAF・10秒期限・Python開始順の変更はない。Rust 14件、自作fullscreen窓から独立した開始3組（連続対照含む9/9）、5秒遅延反復9/9、生成.app回帰を確認した。利用者のCodex fullscreen・Space移動/復帰等は未確認。旧バイナリ・.appは再ビルド・再生成しないと変わらない。

`--smoke-test`を末尾に追加すると、`examples/hello`専用の実WebView確認（MacはWKWebView、WindowsはWebView2）を行い、自動終了する。あいさつ・エラー・並行呼び出し・進捗イベントの順序/購読解除・取消に成功すれば`native_smoke`のokがtrueとなり、終了コード0を返す。通常起動ではこの確認用スクリプト・終了通知は使用しない。

## Macの回帰検証をまとめて実行する

リモートでも自分の検証窓で再実行する[自動回帰検証](../docs/macos-regression.md)を追加した。`.venv/bin/python tests/macos_regression.py --checks --build --output test-results/macos-regression-new`でソース検査とhost/専用exampleのビルドから直列実行する。通常/遅延9起動と独立したfullscreen開始3件を確認し、既存.appとmanifestを渡せば署名・全件照合・再配置コピーのスモークも追加する。実画面や利用者のSpace操作は未確認として残す。製品CLIや公開APIの追加ではない。

## Macの速度・安定性を計測する

メンテナー用の測定器はrelease版ホストと実WKWebViewを使う。リポジトリ直下で実行する。この開発機では前述の`SDKROOT`指定もビルド時に必要。

```console
cargo build --manifest-path native/Cargo.toml --release --locked
.venv/bin/python tests/diagnostics_macos.py --duration 300 --startup-runs 10
```

起動10回、5秒のPython起動遅延、一時停止/再開、クラッシュ、終了時の待機、5分の連続稼働を順に確認する。`test-results/diagnostics-<UTC日時>/`に専用アプリ・raw標本・メモリスナップショット・結果JSON・ログを保存する。既存の出力先は上書きしない。時間を変える場合は`--duration <秒>`、出力先は`--output <新規ディレクトリ>`で指定する。

2026-11-04の追加試験は`--duration 1800 --startup-runs 1 --prevent-idle-sleep`で通常起動1回と追加5シナリオを確認する。連続区間は約1秒ごとに4並列で呼び、20バッチごとに取消・期限・資源を確認して`soak-00/progress.json`へ保存する。`--prevent-idle-sleep`は自分のホストが動く間だけ`caffeinate -i`の一時アサーションを保持し、終了時に解放する。壁時計と単調時計の差が5秒を超えたら連続試験を無効として終了する。OSの恒久設定や蓋を閉じたときの動作は変更しない。各ケースの`observation.json`に経過時間と差を保存する。標準SDKのsequenceモードでは要求ID履歴を蓄積しない。旧形式のlegacyクライアントは4,096件の上限を維持する。

`--diagnostics`はこの専用アプリが使うRustホストのメンテナー用フラグで、`--smoke-test`と併用しない。明示した場合だけ、内部`pyyaki_diagnostic`マーカーをstderrへ出力し、専用アプリの結果で自動終了する。通常起動や公開SDKには計測・障害注入APIを追加しない。停止/強制終了はPython測定器が親子関係を照合した自分の子に対してだけ行う。

通知時刻には配送遅延が含まれ、画面の実提示時間ではない。毎回新規プロファイルを使うがOSキャッシュは制御しない。RSSはホストと検出できた子だけを対象とし、帰属不明のWebKit XPCは含まない。[定義と限界](../docs/requirements.md#54-mac開発用ホストの補助測定)、[初期結果](../docs/performance-macos.md)を参照。

### Macのウィンドウ状態とタイマーを比較する

```console
.venv/bin/python tests/diagnostics_macos.py --timer-probe-seconds 15 --prevent-idle-sleep
```

通常の6シナリオを省略し、前面・一部が見える非フォーカス・検証窓で被覆・最小化・復元・非表示・再表示の7区間を各15秒で比較する。秒数は15〜60。対象は自分の検証ホストと2つの検証窓だけで、他アプリの操作や画面の消灯はしない。比較中は窓の切替が起こる。消灯条件が混ざらないよう、この短いモードで`--prevent-idle-sleep`を付けた場合は一時的にcaffeinate -iに加えて-dも保持する。終了時に解放し、OSの恒久設定は変えない。

Rustの`--diagnostics --timer-probe-seconds <秒>`が必要で、通常起動では検証窓を作らない。JSのタイマーが抑制されてもRust側で区間を切り替えて復元する。実際のネイティブ窓のvisible/focused/minimizedとDOMのvisibilityState/hasFocus、JSタイマー・Rustの100msポーリング・RPCを別々に保存する。切替直後2秒を分けた集計と全rawを保持し、要求が区間をまたいだRPCは集計対象から分ける。

`timer-00/observation.json`とreport.jsonのtimer_segmentsで結果を確認する。区間名だけで状態を決めず、実際の状態件数とphase_ack_ms（ネイティブ通知からJS受信の確認まで）を確認する。表示の物理的な提示や入力応答の保証値ではない。

切替後2秒以降のネイティブ状態が予定と一致しない区間はnative_state_matches=falseとなり、比較全体も失敗とする。試験中に窓操作をしない。背面・被覆のDOM状態はOSの判定をそのまま保存し、窓の幾何学的な重なりや物理的な画面提示の検証とは区別する。

## Windowsでの開発用確認

追加の境界追試と実画面は[11-09追試](../docs/validation-windows-followup-2026-11-09.md)。Pythonがない検証端末では[Windows標準の補助確認器](../docs/release-readiness.md#python不要のwindows確認器)を使える。既定のcargo runはGUIホストを選び、wheel向けconsole binは`--bin pyyaki-cli`で明示する。

2026-11-03にWindows 11 Pro 25H2 / Intel Core i5-8250U x64、CPython 3.14.10通常GIL版、Rust 1.89.0 MSVC、既存MSVCとWebView2でビルド・起動を確認した。詳細は[実機記録](../docs/validation-windows.md)。以下は開発用の汎用手順。[単一exeローカル試作](../docs/windows-onefile.md)の資材生成・検証とは区別する。

[MicrosoftのRust環境手順](https://learn.microsoft.com/en-us/windows/dev-environment/rust/setup)に従い、MSVC Build Toolsの「C++によるデスクトップ開発」（MSVC x64/x86とWindows SDKを含む）、RustのMSVCツールチェーン、CPython 3.14通常GIL版を用意する。導入後にPowerShellを開き直し、次を確認する。

```powershell
Get-CimInstance Win32_Processor | Select-Object Name, Architecture
py -3.14 --version
rustc -vV
```

[Architectureは9がx64、12がARM64](https://learn.microsoft.com/en-us/windows/win32/cimwin32prov/win32-processor)。Rustのhostが`x86_64-pc-windows-msvc`であることを確認する。異なる場合は対応候補を確定せず、結果を共有して検証環境を確認する。

この端末は`py` launcherがなくPythonの絶対パスを使い、既定Rust 1.84.1を維持して`cargo +1.89.0`で検証した。手順中の`py -3.14`や`cargo`は環境に合わせて選択する。`pip install`は完了とimport成功を確認してからホストを起動する。実スモークは起動引数末尾へ`--smoke-test`を追加する。

以下はフレームワークのメンテナー向け。Windows x64、CPython 3.14通常GIL版、利用可能なWebView2 Runtimeと、Rust 1.89以降・MSVCビルド環境を事前に用意する。アプリ利用者向けには[Rust不要のローカルwheel](../docs/windows-wheel.md)を実装した。この手順はホスト開発者向けで、正式公開の導入手順ではない。ランタイムを自動ダウンロード・インストールする処理はない。

PowerShellでリポジトリのルートから実行する想定。実機で試す前にOS・CPUを確認する。Mac用`requirements-dev.lock`をWindows用lockの代用にはしない。ここでのPython依存解決は開発検証用で、Windowsの固定依存・再配布管理は別途必要。

```powershell
py -3.14 -m venv .venv
.\.venv\Scripts\python.exe -m pip install -e .
cargo build --manifest-path native/Cargo.toml --release --locked
.\native\target\release\pyyaki.exe run --project .\examples\hello --python .\.venv\Scripts\python.exe
```

WindowsのPython版CLIの`pyyaki run`は引き続き未対応。pure/editable導入では上記のRust版exeを直接起動する。同梱wheelではScriptsのネイティブCLIを使う。Python経由のラッパーをWindows先行描画の入口にしない。この開発手順では既存のWebView2とvenvを使い、Fixed Version資材の準備・同梱や未導入端末の検証を含まない。

11-05の[フォルダーでの資材確認](../docs/windows-onefile-prototype.md)に続き、`build windows`の[単一exeローカル試作](../docs/windows-onefile.md)を追加した。引数なし起動で先行GDI→別スレッド展開/全ハッシュ検証→同じexeのGUI子ホストへ渡す。停止状態の子ホストをランチャーのJobへ割り当ててから再開し、そのPython Jobも含めて回収する。Windowsワーカーは開発用runも含め`-B`でbytecodeを書かない。自動取得・実署名・未導入クリーン環境は未完了。

確認項目:

1. Rust起動画面 → HTML → Python接続済みの順に進み、あいさつ・エラー・3秒待機中のUI操作が動く。
2. アプリのimport前に意図的な5秒遅延を入れ、起動画面・HTMLと閉じる操作が先に動く。描画マーカーは画面提示の証明ではないため、録画等でも確認する。
3. Pythonのimport失敗・終了・期限超過で原因を表示し、閉じるとワーカーを回収する。通常終了待ちは最大2秒で、Job Objectへの割当失敗はエラーにする。
4. `stdout`への大量出力がRPCを壊さず、`%LOCALAPPDATA%\PyYaki\prototype\<instance>\worker.log`へ記録される。
5. 外部・別ページへの遷移、新規ウィンドウ、ダウンロード、iframeからの操作を拒否する。無効プロキシ・ネットワーク切断下のUI/RPCと、HTTP/WebSocket待受がないことを実機トレースで確認する。

同じinstance内の`host.log`にはホスト内の経過時間とエラーを記録する。外部`CreateProcess`前のt0とは異なり、`T_native`等の性能目標の達成証拠には使わない。プロファイル・ログは試作時点では自動削除しない。終了後に対象instanceを削除でき、業務データ保存先には使用しない。

2026-11-05の修正では、ホストに埋め込む`worker_bootstrap.py`がPyYakiのimport前にPythonのstdout/stderrをUTF-8・backslashreplace・行バッファリングへ設定する。`-I`と通常ファイルの既定文字コードは維持し、固定コードを`-c`で起動する。生バイト出力・アプリによる再設定・bootstrap以前の障害は対象外。過去のCP932ログは変換しない。[Windowsの実機結果とMacで残る回帰確認](../docs/validation-worker-logs-2026-11-05.md)を参照。bootstrap変更後はRustホストを再ビルドする。

同日の[境界追試](../docs/validation-windows-boundary-followup-2026-11-05.md)では、通常/import5秒/フック5秒の反復9/9、自動境界15件中14、埋込Pythonの対照4/4、実クリックによる新規窓・download拒否を確認した。不要ハンドルの非継承、実APIでのJob割当失敗時回収、アプリの孫の通常/強制終了時回収も確認。venv launcherがwriterを保持する部分closeは制約として残り、自動15件の終了コードは1。全writerが閉じる埋込Pythonのパイプは障害通知・保留要求拒否・回収に成功した。新しい有限拒否ログの反映には再ビルドする。端末全体のネットワークや保護設定は変えない。

## 起動期限の再確認

2026-11-04に起動段階ログ・障害画面・反復検証器を追加した。ホストとワーカーを同じソースから更新し、[起動の切り分け](../docs/startup-diagnostics.md)に従って通常/import遅延/起動フック遅延を確認する。同日に`ae3889c`をWindowsで[再検証](../docs/validation-windows-2026-11-04.md)し、実GUIスモークと起動反復9/9、意図的なimport例外の画面、終了時回収を確認した。以前の初回失敗は未再現で、原因解消とは扱わない。

同日に二重rAFの進行・DOM表示状態とネイティブ窓の状態記録も追加した。MacではCodexのフルスクリーン表示時に起動窓が別Spaceとなり、frame2待ちで止まる経路を確認。同じバイナリで通常表示9/9成功、フルスクリーン2/3成功だが、本対応やWindowsの原因確定ではない。一度だけの前面化の試作も初回失敗が残り、採用していない。通常表示との比較手順は上記の切り分け記録を参照し、描画マーカーをDOMロードへ置き換えたり、期限を延長したりして回避した結果とは扱わない。

## 通信と制限

- UIとSDKは`pyyaki://localhost/`をWebView2内部で解決する。Windowsの合成originは`https://pyyaki.localhost`。Rustはトップページの送信元と本文サイズを確認し、SDKはホストのブートストラップ情報から経路を選ぶ。
- UIの二重`requestAnimationFrame`マーカー受信後にだけPythonを起動する。SDKの`ready()`を使わないUIではマーカーが届かず、10秒で初期化エラーになる。
- 子プロセスは`CreateProcessW` + `STARTUPINFOEX`の継承ハンドル許可リストで生成し、Job Objectへ割り当ててから再開する。stdinはNUL、stdout/stderrはログ、RPCは各方向1本の別パイプ。
- フレームは4バイトlittle-endian長 + UTF-8 JSON。サイズ上限1MiBは内部配送の包みも含むため、境界近くの公開メッセージは包みの分だけ小さくする必要がある。nonceはパイプ内だけで交換し、コマンドライン・環境変数へ入れない。
- 内部`worker_hello/worker_progress/worker_welcome/worker_failed`で版・nonceと起動の進行・通常例外を照合。以後は`dispatch`でホスト発行session_idと公開JSONを包む。公開helloにnonceは不要。`shutdown`で終了を要求する。スキーマは[API設計](../docs/api-design.md)を参照。
- Windows/Mac/Webは同じ`_rpc.Connection`と`App.dispatch`を使用。capabilitiesは`["rpc", "events"]`。接続単位のContext、64件のイベントキュー、起動・終了フックを共用する。起動フック完了後にworker_welcomeを返し、終了時はコマンド取消・Context破棄の後に終了フックを呼ぶ。
- UI→ホストの未処理キュー32件、ホスト→ワーカー32件、ワーカー→GUIの受信待ち64件（イベント/応答の合計）、資産読込は同時16件・各16MiBを上限とする試作制約。超過は明示的な失敗にする。長時間稼働の採否は未評価。
- wryの既定追加引数がSmartScreen保護を無効にする指定を含むため、追加引数を明示的に空にしている。OS・企業ポリシーやWebView2環境による実際の効果はWindowsで確認する。

## Macで実行できる検査

```console
cargo test --manifest-path native/Cargo.toml --locked
cargo check --manifest-path native/Cargo.toml --locked --target x86_64-pc-windows-gnu
cargo clippy --manifest-path native/Cargo.toml --locked --target x86_64-pc-windows-gnu -- -D warnings
python -m pytest -q
python tests/browser_native_bridge.py
```

Windowsターゲットは型検査用に事前導入する。Windows向けexeのクロスビルド・実行を保証する手順ではない。今回のMacでは既定SDK 27とリンカーが不整合だったため、インストール済みのSDK 26.5を`SDKROOT`で選び直して検査した。SDKファイルやOS設定は変更していない。

`browser_native_bridge.py`はChromium上の仮のWebView2ブリッジを使う契約テストで、実際のWindows/WebView2実行ではない。`test_worker.py`はPOSIX fd継承専用。`test_worker_logging.py`はWindowsの継承ハンドル/POSIX fdの両方を扱い、ログ・RPC・通常例外を実ワーカーで確認する。実GUIとホストのJob Object回収は別の実機確認が必要。

未実装: 汎用ホスト資材の正式配布、資材の自動準備、実署名。単一exe組立・検証付き展開・Fixed Version WebView2/Python同梱は明示資材を使う[Windowsローカル試作](../docs/windows-onefile.md)として実装済みで、クリーン環境等の受入は残る。詳細な配布方針は[ビルド設計](../docs/build-system.md)を正とする。
