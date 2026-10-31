# PyYaki native host prototype

2026-10-31時点の開発用ネイティブホスト。Rustのtao/wry/WebView2で同じ`examples/hello/ui`を読み、別プロセスのPythonワーカーへ専用匿名パイプで接続するソースを実装した。MacではRust共通処理・Python子プロセス・SDK契約を試験し、Windows GNUターゲットの型検査を行った。**Windowsでのリンク・GUI実行は未確認で、G1合格や単一exe配布の完成を意味しない。macOS用の開発ホストも後述のとおり追加した。**

共通APIの現在地は[ロードマップ](../docs/roadmap.md#現在地と次の作業)、SDK変更時の再ビルドや検査の使い分けは[開発ガイド](../docs/development.md)を参照する。

## Macでの開発用確認

利用者の依頼によりMac開発用ホストを追加した。tao/wry/WKWebView、同じ静的UI・SDK・Pythonワーカーを使用する。Rust 1.89以降とCPython 3.14の開発venvを用意し、リポジトリ直下で実行する。

```console
cargo run --manifest-path native/Cargo.toml --locked -- run --project examples/hello --python .venv/bin/python
```

MacのWebViewは`pyyaki://localhost/`から資産を取得し、HTTP/WebSocketサーバーは立てない。送信元と初回ナビゲーションを制限する。Rust→JSはJSONを二重シリアライズしてJSON.parseへ渡す固定スクリプトで配送する。SDK/Contextのmodeは`macos`。Pythonパスはvenvのシンボリックリンクを解決せず保持する。

Windows専用処理を`process.rs`、Macを`process_macos.rs`に分け、イベントループ・資産配信・プロトコルは`host.rs`で共用。Macでは子プロセス専用のプロセスグループとCLOEXEC付き匿名パイプを使い、子のexec直前に必要な2fdだけを継承可能にする。終了時はshutdownを通知して最大2秒待ち、残るグループを回収する。Ctrl+C/SIGTERMも同じ終了経路を使う。SIGKILLなどホストを強制終了した場合の子プロセス回収は未保証で、正式配布前の課題。

ログは`~/Library/Application Support/PyYaki/prototype/<instance>/`。Python同梱.appのローカル生成試作は[専用手順](../docs/macos-build.md)を参照。Developer ID署名/公証・他Macへの正式配布は未対応。SDK 27とのリンクエラーが出るこの開発機では、インストール済みSDK 26.5を`SDKROOT=/Library/Developer/CommandLineTools/SDKs/MacOSX26.5.sdk`で選択してビルドした。

`--smoke-test`を末尾に追加すると、`examples/hello`専用の実WKWebView確認を行い、自動終了する。あいさつ・エラー・並行呼び出し・進捗イベントの順序/購読解除・取消に成功すれば`native_smoke`のokがtrueとなり、終了コード0を返す。通常起動ではこの確認用スクリプト・終了通知は使用しない。

## Macの速度・安定性を計測する

メンテナー用の測定器はrelease版ホストと実WKWebViewを使う。リポジトリ直下で実行する。この開発機では前述の`SDKROOT`指定もビルド時に必要。

```console
cargo build --manifest-path native/Cargo.toml --release --locked
.venv/bin/python tests/diagnostics_macos.py --duration 300 --startup-runs 10
```

起動10回、5秒のPython起動遅延、一時停止/再開、クラッシュ、終了時の待機、5分の連続稼働を順に確認する。`test-results/diagnostics-<UTC日時>/`に専用アプリ・raw標本・メモリスナップショット・結果JSON・ログを保存する。既存の出力先は上書きしない。時間を変える場合は`--duration <秒>`、出力先は`--output <新規ディレクトリ>`で指定する。

`--diagnostics`はこの専用アプリが使うRustホストのメンテナー用フラグで、`--smoke-test`と併用しない。明示した場合だけ、内部`pyyaki_diagnostic`マーカーをstderrへ出力し、専用アプリの結果で自動終了する。通常起動や公開SDKには計測・障害注入APIを追加しない。停止/強制終了はPython測定器が親子関係を照合した自分の子に対してだけ行う。

通知時刻には配送遅延が含まれ、画面の実提示時間ではない。毎回新規プロファイルを使うがOSキャッシュは制御しない。RSSはホストと検出できた子だけを対象とし、帰属不明のWebKit XPCは含まない。[定義と限界](../docs/requirements.md#54-mac開発用ホストの補助測定)、[初期結果](../docs/performance-macos.md)を参照。

## Windowsでの開発用確認

利用者からWindows 11、CPUはx64の見込み、Python/Rustはこれから導入すると回答を得た。CPUとツールの実測は未確認。Windowsの単一exe実装を作り込む前に、以下の既存ホストのMSVCビルドと起動を確認する。

[MicrosoftのRust環境手順](https://learn.microsoft.com/en-us/windows/dev-environment/rust/setup)に従い、MSVC Build Toolsの「C++によるデスクトップ開発」（MSVC x64/x86とWindows SDKを含む）、RustのMSVCツールチェーン、CPython 3.14通常GIL版を用意する。導入後にPowerShellを開き直し、次を確認する。

```powershell
Get-CimInstance Win32_Processor | Select-Object Name, Architecture
py -3.14 --version
rustc -vV
```

[Architectureは9がx64、12がARM64](https://learn.microsoft.com/en-us/windows/win32/cimwin32prov/win32-processor)。Rustのhostが`x86_64-pc-windows-msvc`であることを確認する。異なる場合は対応候補を確定せず、結果を共有して検証環境を確認する。

以下はフレームワークのメンテナー向け。Windows x64、CPython 3.14通常GIL版、利用可能なWebView2 Runtimeと、Rust 1.89以降・MSVCビルド環境を事前に用意する。アプリ利用者向けのRust不要wheel配布は未実装であり、この手順を正式なアプリ導入手順にはしない。ランタイムを自動ダウンロード・インストールする処理はない。

PowerShellでリポジトリのルートから実行する想定。実機で試す前にOS・CPUを確認する。Mac用`requirements-dev.lock`をWindows用lockの代用にはしない。ここでのPython依存解決は開発検証用で、Windowsの固定依存・再配布管理は別途必要。

```powershell
py -3.14 -m venv .venv
.\.venv\Scripts\python.exe -m pip install -e .
cargo build --manifest-path native/Cargo.toml --release --locked
.\native\target\release\pyyaki.exe run --project .\examples\hello --python .\.venv\Scripts\python.exe
```

Python版CLIの`pyyaki run`は引き続き未対応。必ず上記のRust版exeを直接起動する。Python経由のラッパーをWindows先行描画の入口にしない。開発用ホストは既存のWebView2を利用し、Fixed Version同梱やPython未導入端末ではまだ試せない。

確認項目:

1. Rust起動画面 → HTML → Python接続済みの順に進み、あいさつ・エラー・3秒待機中のUI操作が動く。
2. アプリのimport前に意図的な5秒遅延を入れても、起動画面・HTMLと閉じる操作が先に動く。描画マーカーは画面提示の証明ではないため、録画等でも確認する。
3. Pythonのimport失敗・終了・期限超過で原因を表示し、閉じるとワーカーを回収する。通常終了待ちは最大2秒で、Job Objectへの割当失敗はエラーにする。
4. `stdout`への大量出力がRPCを壊さず、`%LOCALAPPDATA%\PyYaki\prototype\<instance>\worker.log`へ記録される。
5. 外部・別ページへの遷移、新規ウィンドウ、ダウンロード、iframeからの操作を拒否する。無効プロキシ・ネットワーク切断下のUI/RPCと、HTTP/WebSocket待受がないことを実機トレースで確認する。

同じinstance内の`host.log`にはホスト内の経過時間とエラーを記録する。外部`CreateProcess`前のt0とは異なり、`T_native`等の性能目標の達成証拠には使わない。プロファイル・ログは試作時点では自動削除しない。終了後に対象instanceを削除でき、業務データ保存先には使用しない。

## 通信と制限

- UIとSDKは`pyyaki://localhost/`をWebView2内部で解決する。Windowsの合成originは`https://pyyaki.localhost`。Rustはトップページの送信元と本文サイズを確認し、SDKはホストのブートストラップ情報から経路を選ぶ。
- UIの二重`requestAnimationFrame`マーカー受信後にだけPythonを起動する。SDKの`ready()`を使わないUIではマーカーが届かず、10秒で初期化エラーになる。
- 子プロセスは`CreateProcessW` + `STARTUPINFOEX`の継承ハンドル許可リストで生成し、Job Objectへ割り当ててから再開する。stdinはNUL、stdout/stderrはログ、RPCは各方向1本の別パイプ。
- フレームは4バイトlittle-endian長 + UTF-8 JSON。サイズ上限1MiBは内部配送の包みも含むため、境界近くの公開メッセージは包みの分だけ小さくする必要がある。nonceはパイプ内だけで交換し、コマンドライン・環境変数へ入れない。
- 内部`worker_hello/worker_welcome`で版・nonceを照合。以後は`dispatch`でホスト発行session_idと公開JSONを包む。公開helloにnonceは不要。`shutdown`で終了を要求する。スキーマは[API設計](../docs/api-design.md)を参照。
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

`browser_native_bridge.py`はChromium上の仮のWebView2ブリッジを使う契約テストで、実際のWindows/WebView2実行ではない。Pythonワーカーの実パイプ結合テストはPOSIXのfd継承で実行するため、Windowsでは別の実機確認が必要。

未実装: ネイティブexeのpip配置、汎用ホスト資材の配布、単一exe組立・展開・署名、Fixed Version WebView2/Python同梱。詳細な配布方針は[ビルド設計](../docs/build-system.md)を正とする。
