# 開発と動作確認

更新: 2026-11-08。リポジトリを開き直した際や、作業を再開する際の手順をまとめる。現在の機能と残作業は[ロードマップ](roadmap.md#現在地と次の作業)、契約・制限値は[API設計](api-design.md)、受入条件は[要件定義](requirements.md)を正とする。

2026-11-08にWindowsからMacへ戻る際の準備・回帰確認の順序と貼り付け用プロンプトは、[Mac作業引き継ぎ](handoff-macos-2026-11-08.md)を参照する。

同日の[Mac回帰結果](validation-macos-2026-11-08.md)はPython 145件・Rust 13件、WKWebView反復9/9、UTF-8/例外DOM/終了、再生成.appの署名・全ハッシュ・再配置を確認。初回のSpace関連失敗と目視等の未確認範囲も保持している。既存standalone Pythonのencodings欠落で再取得が必要だったため、過去の資材パスの存在だけで再利用可能と判断せず、同書の失敗と公式ハッシュを確認する。

## 最初に確認すること

1. [AGENTS.md](../AGENTS.md)の作業ルールを読む。実装開始は承認済みで、作業した変更は目的ごとにコミットする。
2. `git status --short`で既存変更を確認する。既存変更は作業へ混ぜたり取り消したりしない。
3. [ロードマップの現在地](roadmap.md#現在地と次の作業)から未完了の範囲を確認する。過去の検証記録は各作業時点の結果として読む。

主な開発機はMac。初回のPython環境構築は[README](../README.md#macで試作を動かす)を参照する。以下はCPython 3.14通常GIL版の`.venv`を準備した状態で、リポジトリのルートから実行する。Node.jsはアプリ実行に不要。Rustホストの開発・再ビルドにはRust/Cargoが必要で、Rust不要の利用者向け配布は今後の作業。

## 起動する

Webはプロジェクトのディレクトリで起動する。

```console
cd examples/hello
../../.venv/bin/pyyaki dev
```

`http://127.0.0.1:8000`をブラウザで開く。ポート変更は末尾に`--port 8001`、終了はCtrl+C。ブラウザは自動では開かない。

Macネイティブはリポジトリのルートから、ビルド済みのRustバイナリを直接起動する。

```console
./native/target/debug/pyyaki run --project examples/hello --python .venv/bin/python
```

ウィンドウを閉じるかCtrl+Cで終了する。Python版CLIの`pyyaki run`は未対応。MacのPython同梱.appローカル生成は[専用手順](macos-build.md)を参照する。Windowsの開発用コマンドは[native/README.md](../native/README.md#windowsでの開発用確認)を参照する。

未ビルド、Rust/SDKを変更した、またはSDKのJSを変更した場合は、先に次を実行する。

```console
cargo build --manifest-path native/Cargo.toml --locked
```

この開発機では既定SDK 27とリンカーの不整合があったため、必要な場合はビルドコマンドの先頭に`SDKROOT=/Library/Developer/CommandLineTools/SDKs/MacOSX26.5.sdk`を付ける。別のMacへこのSDKの存在を前提にしない。

## 変更する場所と反映方法

| 変更内容 | 主な場所 | 現在の反映方法 |
| --- | --- | --- |
| 業務コマンド / UI | `examples/hello/app.py`、`examples/hello/ui/` | Webはサーバー再起動とページ再読込。ネイティブはホストを終了して再起動 |
| 共通Python処理 | `src/pyyaki/app.py`、`context.py`、`_rpc.py` | editable導入したvenvでサーバー/ホストを再起動 |
| Web / ワーカー | `src/pyyaki/web.py`、`worker.py` | サーバー/ホストを再起動 |
| SDK | `src/pyyaki/static/sdk.js` | Webはサーバー再起動とページ再読込。ネイティブはRustの再ビルドと再起動 |
| ウィンドウ / プロセス / 資産配信 | `native/src/` | Rustの再ビルドと再起動 |
| 専用スモーク確認 | `native/smoke.js` | Rustの再ビルド後、`--smoke-test`で起動 |

SDKと`native/failure.js`・`native/failure.css`・`native/worker_bootstrap.py`はネイティブホストへコンパイル時に埋め込まれる。これらの変更はRustの再ビルドが必要。通常のPythonパッケージや利用者UIはホスト生成時に同梱しない開発方式なので、その変更は再起動で反映する。自動再読込は未実装で、ネイティブのページ再読み込み・別ページへの遷移も許可していない。

アイコン資産も埋め込まれるため、[変換手順](../assets/branding/README.md)を実行したらRustホストとwheel/.appを再生成する。Windowsターゲットの検査・ビルドではリソース用のrc.exe（MSVC）またはwindres（GNU）も必要。[アイコンの組み込み](../native/README.md#アイコン)を参照。

ワーカーの内部契約を変更した場合も、対応するRustホストを再ビルドする。2026-11-04の起動通知追加はこの変更に該当する。

新しいコマンドは`@app.command`で非同期関数として明示登録し、必要なら`*, ctx: Context`を宣言する。イベント・フックの使い方は[Python API](api-design.md#3-python-api)と[JavaScript API](api-design.md#4-javascript-api)を参照。UIはHTML/CSS/JSの独立ファイルへ置き、Pythonの文字列へ埋め込まない。

## 検証する

以下はすべてルートで実行する。直近の成功件数はロードマップの実行記録であり、この手順を読んだだけで再検証した扱いにはしない。

```console
.venv/bin/python -m pytest -q -W error::DeprecationWarning
.venv/bin/ruff check src examples tests native/worker_bootstrap.py
.venv/bin/ruff format --check src examples tests native/worker_bootstrap.py
cargo fmt --manifest-path native/Cargo.toml -- --check
cargo test --manifest-path native/Cargo.toml --locked
cargo clippy --manifest-path native/Cargo.toml --locked --all-targets -- -D warnings
```

Cargoの検査でも、上記のSDK指定が必要な場合がある。Windows GNUターゲットのClippyは[native/README.md](../native/README.md#macで実行できる検査)を参照し、Windows実機試験と区別する。

ブラウザ試験には開発依存のPlaywrightとテスト用Chromiumを準備する。

```console
.venv/bin/python -m playwright install chromium
```

Webを起動した状態で、別ターミナルのルートから次を実行する。スクリプトはポート8000を使う。

```console
.venv/bin/python tests/browser_smoke.py
```

仮のWebView2ブリッジ確認はWebサーバーを必要としない。これはChromium上のSDK契約試験で、WindowsでのWebView2実行ではない。

```console
.venv/bin/python tests/browser_native_bridge.py
```

実WKWebViewのスモーク確認は次のコマンドで行い、自動終了する。

```console
./native/target/debug/pyyaki run --project examples/hello --python .venv/bin/python --smoke-test
```

速度・連続試験には専用アプリとrelease版を使う。30分の追試では`--duration 1800 --startup-runs 1 --prevent-idle-sleep`を指定する。[計測手順](../native/README.md#macの速度安定性を計測する)と[初期結果](performance-macos.md)を参照し、通常のスモーク確認と区別する。

表示状態によるタイマーの遅れは`--timer-probe-seconds 15 --prevent-idle-sleep`で7条件を短く比較できる。自分の検証窓が切り替わるため、通常の開発作業と同時に走らせず、[手順と記録の読み方](../native/README.md#macのウィンドウ状態とタイマーを比較する)を確認する。

Markdownだけの変更では、関連文書・ローカルリンク・見出し・API例の整合性を確認する。無関係な実装テストの追加や、5分試験の再実行は必要としない。

## 問題を切り分ける

| 状態 | 確認内容 |
| --- | --- |
| `pyproject.toml`が見つからない | Web CLIはプロジェクトのディレクトリで実行。Rustホストはルートから`--project`を指定 |
| ポートが使用中 | Webの`--port`を変更。ネイティブのRPCはHTTP/WebSocket待受を使わない |
| コマンドが見つからない / 型検証で失敗 | `entry`と登録名、名前付きparams、型注釈を確認。ContextはJSONから渡さない |
| ネイティブにJS変更が反映されない | SDK変更ならRustホストを再ビルド。通常のUI変更ならホストを再起動 |
| Macで初回だけPython開始前に期限へ達する | `sdk_shell_progress`のscheduled/frame1/frame2と`ui_window_state`のSpace・遮蔽状態を照合する。フルスクリーンからの起動で描画待ちが止まる例を確認済み。通常ウィンドウ表示で再試験できるが、本対応は未実装。[同じバイナリでの比較](startup-diagnostics.md)を参照 |
| ready失敗 / BACKEND_FAILED | [段階ログ・反復検証](startup-diagnostics.md)で切り分け、entry・依存・起動フックを確認。Macのホスト/ワーカーログは`~/Library/Application Support/PyYaki/prototype/<instance>/` |
| 呼び出しを重ねた後に切断 | 標準SDKは連番IDで履歴を蓄積しない。旧SDK/独自クライアントのlegacyモードには4,096件の上限が残る。SDK・Python・Rustホストを同じソースで更新する |

ログとプロファイルは試作では自動削除しない。ブラウザ画像、診断のraw標本、生成物は`test-results/`等のGit管理外へ保存する。起動/終了フックはワーカーまたはASGI lifespanごとに実行し、ブラウザの接続単位の状態はContextへ置く。取消は協調的で、同期CPU処理や取消を無視する処理の停止・巻戻しを保証しない。

## Windows実機での再確認

2026-11-03にWindows 11 x64のMSVC releaseビルドと実WebView2の主要動作を確認した。[実機記録](validation-windows.md)に端末のPythonパス、既定Rustを変えない`+1.89.0`指定、実行コマンド、失敗と残課題を記載している。`pip install`の完了後、`python -I -c "import pyyaki"`も確認してからホストを起動する。

当日のPython検査はWindowsで67件成功・14件スキップ。symlink作成がWinError 1314ならリンク専用2件だけをスキップし、他のWeb検査は実行する。当日はPOSIXワーカー等12件もWindows対象外だった。新規検査を含む更新版の件数は再実行で確認する。権限やOS設定を変えて回避しない。

起動期限の再現確認には[起動の切り分け](startup-diagnostics.md)の`tests/startup_probe.py`を使う。2026-11-04に`ae3889c`をWindowsで再検証し、起動9/9、Python 108件成功・19件スキップ、Rust 10件、Clippyと実GUIスモークに成功した。[再検証記録](validation-windows-2026-11-04.md)を参照。以前の初回失敗は未再現で、原因解消とは扱わない。当日のworker.logはCP932だったため、過去ログをUTF-8と決めつけて上書きしない。

2026-11-05の修正後は、パッケージimport前の固定bootstrapがstdout/stderrをUTF-8へ設定する。WindowsのPython 112成功・19スキップ、Rust 10成功、MSVC/Clippy/実GUIを[確認](validation-worker-logs-2026-11-05.md)した。新しいworker.logはPowerShellの`Get-Content -Encoding UTF8 <パス>`等で読む。通常ファイルの既定encodingは変えず、アプリの生バイト出力・ストリーム再設定は対象外。Macの再ビルド・WKWebView・.app再生成は[11-08に回帰確認](validation-macos-2026-11-08.md)した。ビルド時のentry importはworker bootstrap外なので、そこでのstdoutのencodingまで変更する機能ではない。

実ホストの境界・異常系は`tests/diagnostics_windows.py --binary native/target/release/pyyaki.exe --output <新規のtest-results配下>`で確認できる。実行は上記venvのPythonを使う。コピーに故意の異常終了・不正フレーム等を加えるメンテナー専用検証で、通常のpytestとは別。`--case`で絞り込める。11-05の[初回12ケース](validation-windows-boundary-2026-11-05.md)に続き、[追試](validation-windows-boundary-followup-2026-11-05.md)では既定の自動15件が14確認・pipe-close 1制約、終了コード1。`--python`でワーカーPythonを明示した埋込環境の対照4件は成功し、全writer切断とvenvの部分closeを区別した。実クリックは明示的な`--case user-actions`だけで実行する。先にRustホストを再ビルドし、拒否ハンドラーの有限ログも照合する。

Windows配布の[単一exe生成・起動手順](windows-onefile.md)を追加した。`build windows`へ未署名の対応ホスト・公式埋込Python・Fixed Version・ハッシュ固定wheelを明示するローカル試作。[前段のフォルダー確認](windows-onefile-prototype.md)は資材準備の履歴として残す。既存venvを上書きせず、同じソースのホストとframework wheelを使う。Windowsワーカーは`-I -B`で起動し、bytecodeを書かない。準備画面はlauncher.log、従来のGUI/Pythonは子ホストのhost.log/worker.logで切り分ける。実署名・クリーンVM等の未確認範囲を残す。
