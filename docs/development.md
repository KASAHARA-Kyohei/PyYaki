# 開発と動作確認

更新: 2026-11-03。リポジトリを開き直した際や、作業を再開する際の手順をまとめる。現在の機能と残作業は[ロードマップ](roadmap.md#現在地と次の作業)、契約・制限値は[API設計](api-design.md)、受入条件は[要件定義](requirements.md)を正とする。

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

SDKはネイティブホストへコンパイル時に埋め込まれる。Pythonや利用者UIはホスト生成時に同梱しない開発方式なので、Python/UIだけの変更でRustの再ビルドは不要。自動再読込は未実装で、ネイティブのページ再読み込み・別ページへの遷移も許可していない。

新しいコマンドは`@app.command`で非同期関数として明示登録し、必要なら`*, ctx: Context`を宣言する。イベント・フックの使い方は[Python API](api-design.md#3-python-api)と[JavaScript API](api-design.md#4-javascript-api)を参照。UIはHTML/CSS/JSの独立ファイルへ置き、Pythonの文字列へ埋め込まない。

## 検証する

以下はすべてルートで実行する。直近の成功件数はロードマップの実行記録であり、この手順を読んだだけで再検証した扱いにはしない。

```console
.venv/bin/python -m pytest -q -W error::DeprecationWarning
.venv/bin/ruff check src examples tests
.venv/bin/ruff format --check src examples tests
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

速度・5分の連続試験には専用アプリとrelease版を使う。[計測手順](../native/README.md#macの速度安定性を計測する)と[初期結果](performance-macos.md)を参照し、通常のスモーク確認と区別する。

Markdownだけの変更では、関連文書・ローカルリンク・見出し・API例の整合性を確認する。無関係な実装テストの追加や、5分試験の再実行は必要としない。

## 問題を切り分ける

| 状態 | 確認内容 |
| --- | --- |
| `pyproject.toml`が見つからない | Web CLIはプロジェクトのディレクトリで実行。Rustホストはルートから`--project`を指定 |
| ポートが使用中 | Webの`--port`を変更。ネイティブのRPCはHTTP/WebSocket待受を使わない |
| コマンドが見つからない / 型検証で失敗 | `entry`と登録名、名前付きparams、型注釈を確認。ContextはJSONから渡さない |
| ネイティブにJS変更が反映されない | SDK変更ならRustホストを再ビルド。通常のUI変更ならホストを再起動 |
| ready失敗 / BACKEND_FAILED | entry・依存・起動フックを確認。Macのホスト/ワーカーログは`~/Library/Application Support/PyYaki/prototype/<instance>/` |
| 呼び出しを重ねた後に切断 | 現在は1接続で累積4,096要求の上限がある。Webはページ再読込、ネイティブはホスト再起動で新しい接続にする |

ログとプロファイルは試作では自動削除しない。ブラウザ画像、診断のraw標本、生成物は`test-results/`等のGit管理外へ保存する。起動/終了フックはワーカーまたはASGI lifespanごとに実行し、ブラウザの接続単位の状態はContextへ置く。取消は協調的で、同期CPU処理や取消を無視する処理の停止・巻戻しを保証しない。

## Windows実機での再確認

2026-11-03にWindows 11 x64のMSVC releaseビルドと実WebView2の主要動作を確認した。[実機記録](validation-windows.md)に端末のPythonパス、既定Rustを変えない`+1.89.0`指定、実行コマンド、失敗と残課題を記載している。`pip install`の完了後、`python -I -c "import pyyaki"`も確認してからホストを起動する。

Python検査はWindowsで67件成功・14件スキップ。symlink作成がWinError 1314ならリンク専用2件だけをスキップし、他のWeb検査は実行する。既存のPOSIXワーカー等12件もWindows対象外。権限やOS設定を変えて回避しない。
