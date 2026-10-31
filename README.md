# PyYaki

Pythonの業務ロジックとHTML/CSS/JavaScriptのUIを共有し、WindowsデスクトップアプリとWebアプリを開発するためのOSSフレームワーク構想です。

名称は「鯛焼き」にかけたものです。表示名を**PyYaki**、パッケージ・Python import・CLI・JavaScript SDKの識別子を**`pyyaki`**に統一します。

**現在は初期試作です。Mac向けWeb・ネイティブ経路に加え、Windows開発用Rustホスト・専用パイプのワーカー・ネイティブSDK経路のソースを実装しました。Windowsの型検査は通過しましたが、Windowsでのリンク・GUI実行は未確認です。公開パッケージ・単一exeビルドはなく、起動性能・長時間稼働も未検証です。**

2026-10-31の名称調査では、[PyPIの`pyyaki`公式API](https://pypi.org/pypi/pyyaki/json)は404で、[GitHubの名前検索](https://github.com/search?q=pyyaki+in%3Aname&type=repositories)でも該当する公開リポジトリは見つかりませんでした。名称は採用しましたが、PyPIでの登録可能性を保証するものではなく、公開時に再確認します。

## Macで試作を動かす

リポジトリのルートで実行します。CPython 3.14通常GIL版が必要です。初回の依存取得にはネット接続を使い、アプリの実行に外部サービス・CDN・Node.jsは使いません。

```console
python3.14 -m venv .venv
source .venv/bin/activate
python -m pip install --require-hashes -r requirements-dev.lock
python -m pip install --no-deps --no-build-isolation -e .
cd examples/hello
pyyaki dev
```

ブラウザで`http://127.0.0.1:8000`を開きます。あいさつ、想定内エラー、3秒待機中のUI操作を確認できます。ポート変更は`pyyaki dev --port 8001`、終了は`Ctrl+C`です。ファイル変更後は手動でサーバー再起動・ページ再読み込みを行ってください。

起動できない場合、`pyproject.toml`が見つからなければプロジェクトのディレクトリで実行し、設定エラーの場合は表示されたファイルと項目を確認してください。ポートが使用中の場合は、使用しているプロセスを終了するか`--port`で別の番号を指定できます。アプリ読み込みに失敗した場合は、設定の`entry`と必要な依存を確認してください。

実装済み: `App`、非同期コマンド登録、引数・戻り値検証、`AppError`/`FrameworkError`、接続単位の`Context`、イベント、起動・終了フック、ローカルASGI/WebSocket、SDKの`ready`/`invoke`/`on`、期限・取消・切断処理。capabilitiesは`["rpc", "events"]`です。3秒の処理ではPythonから届く進捗を表示します。

未実装: 自動再読込、Windows `build windows`、ネイティブexeのpip配置。Python版CLIの`run`は未対応で、Windows開発用ホストは別のRust版exeを直接起動します。`app.asgi()`も現在はloopbackの試作用で、本番Web運用は未検証です。以下の将来構成と区別してください。

テストはルートで`python -m pytest -q`、静的確認は`ruff check src examples tests`を実行します。ブラウザテストの手順と確認環境は[検証記録](docs/roadmap.md#2026-10-31-最初のmac向けweb試作)に記載しています。

## Macでネイティブ起動する

開発用ホストはRustとWKWebViewを使います。リポジトリのルートから、上記のvenvを準備した状態で実行してください。

```console
cargo run --manifest-path native/Cargo.toml --locked -- run --project examples/hello --python .venv/bin/python
```

ブラウザではなくネイティブウィンドウを開き、同じUIとPythonコマンドを使います。終了はウィンドウを閉じるか、ターミナルのCtrl+Cです。実際のWKWebView経由のRPCは確認済みですが、appバンドルの正式配布・署名・長時間稼働は未対応または未検証です。[Macの詳しい手順](native/README.md#macでの開発用確認)も参照してください。

この開発機ではSDKとリンカーの組合せの都合で、ビルド時に`SDKROOT=/Library/Developer/CommandLineTools/SDKs/MacOSX26.5.sdk`を指定しています。すでにビルドしたものは`native/target/debug/pyyaki run --project examples/hello --python .venv/bin/python`で起動できます。

## Windows開発用ネイティブホスト

`native/`にRust製の起動画面・WebView2ホストと、専用パイプ経由で同じPythonコマンドを呼ぶ実装を追加しました。HTTP/WebSocketサーバーを起動するコードは使用しません。Windowsでの起動・実測はこれからで、現時点の確認はMacの子プロセス試験とWindowsターゲットの型検査です。

まだメンテナー向けの試作で、Rust/MSVCと開発用Python・既存WebView2が必要です。Windowsでのビルド・起動・確認項目は[native/README.md](native/README.md)を参照してください。Rust不要のpip導入や、Python/WebView2未導入端末向けの単一exeは後続作業です。

## 目指す使い方（全体の設計）

RustやCargoをインストールせず、Python環境から次の操作を行える構成を目指します。

```console
pip install pyyaki
pyyaki dev
pyyaki run
pyyaki build windows
```

| コマンド | 想定する動作 |
| --- | --- |
| `pyyaki dev` | ブラウザ向けの開発サーバーをloopbackで起動 |
| `pyyaki run` | WindowsのネイティブGUIホストを直接起動 |
| `pyyaki build windows` | Python・UI・依存・WebView2を含む単一のポータブルexeを生成 |

基本構成で利用者にRust/Cargo/MSVC/Node.jsを要求しません。フレームワーク本体のメンテナーはRustのビルド環境を使用します。MVPの検証範囲はWindows 11 x64とCPython 3.14通常GIL版の組合せを提案しています。Windows向けビルドはWindows上で行う設計です。

「Pythonだけ」はRust等をアプリ開発者が書く必要をなくす方向性です。自由なUIはHTML/CSS/JavaScriptで編集します。PythonだけでUIを宣言するDSLはMVPに含みません。

## 想定するアプリ構造

```text
my-app/
├─ pyproject.toml
├─ requirements-windows.lock
├─ app.py
├─ domain/
└─ ui/
   ├─ index.html
   ├─ styles.css
   ├─ main.js
   └─ assets/
```

Pythonの例（設計案）:

```python
from pyyaki import App

app = App()

@app.command("greet")
async def greet(name: str) -> dict[str, str]:
    return {"message": f"Hello, {name}!"}

asgi = app.asgi()
```

JavaScriptの例（設計案）:

```javascript
import { pyyaki } from "/_pyyaki/sdk.js";

await pyyaki.ready();
const result = await pyyaki.invoke("greet", { name: "Yaki" });
document.querySelector("#message").textContent = result.message;
```

同じコマンドとUIを両環境で利用し、フレームワークが通信経路を切り替えます。静的設定やイベントを含む詳細は[API設計](docs/api-design.md)を参照してください。

## Windows版とWeb版

Windows版は、ビルド済みRustホストが先に起動画面を描き、単一exe内の依存をユーザー専用キャッシュへ展開します。続いてWebView2にHTMLを表示し、その後で別プロセスのPythonを初期化する構成案です。空白のウィンドウ枠、起動画面、アプリUI、操作可能状態の速度を区別して計測します。

WindowsのUI配信とRPCにはlocalhostサーバーを使わず、WebView内部の資産配信と専用パイプを使う案です。配布先でPython/WebView2の別途導入、管理者権限、ネット接続を必要としないことを必須条件とします。単一exeは配布ファイルが1つという意味で、実行時にはキャッシュやプロファイルを保存します。

Web版はFastAPI/Uvicorn上のPythonとブラウザを組み合わせ、同じディスパッチャーとUIを使います。ブラウザだけでPythonを実行する方式や、静的ホスティングだけで動く方式はMVP対象外です。本番Webには配布者による認証・認可・TLS等の運用が必要です。

## 技術選定と検証課題

初期候補はtao/wry、別プロセスCPython、FastAPI/Uvicorn、Windows Embeddable Package、Fixed Version WebView2です。PyO3のプロセス内埋め込みはMVP対象外の提案です。Tauri/PyTauri等の再利用も比較し、実測後に採否を決めます。

特に、単一exeの初回展開時間、WebView2の容量と更新、native wheelのDLL依存、ネイティブCLIのpip配置、署名と企業の実行制限を検証します。署名してもDefender/SmartScreen/企業ポリシーによる制限が必ず解消するとは保証しません。

開発機はMacで、ある程度開発が進んだ段階で手元のWindowsを使って確認します。最初のMac試作はCPython 3.14通常GIL版で、HTMLからPythonコマンドを呼び、結果・エラーを表示し、非同期処理中もUIが応答するところまでとする方針を2026-10-31に承認し、実装を開始しました。Windows確認へ移ったら、最初に単一exeからPythonを待たずに描画する最小試作を検証します。Windows固有の起動・配布方式は、その検証まで未確定です。詳しい順序は[ロードマップ](docs/roadmap.md)に記載しています。

高速性と安定性を重視します。起動時間の計測に加え、GUIの応答性、Pythonのクラッシュ・終了処理、キャッシュ破損等を評価する設計です。性能は未測定で、RPC応答速度や長時間稼働の数値基準は今後の検討事項です。

## 設計文書

| 文書 | 内容 |
| --- | --- |
| [AGENTS.md](AGENTS.md) | Codexの開発ルールと文書の責務 |
| [要件定義](docs/requirements.md) | 必須要件、受入条件、MVP範囲、起動速度の測定 |
| [アーキテクチャ](docs/architecture.md) | 既存OSS比較、技術選定、起動順、IPC、リスク、ADR候補 |
| [API設計](docs/api-design.md) | Python/JavaScript API、通信契約、エラー、制限値 |
| [ビルド・配布設計](docs/build-system.md) | pip導入、単一exe、ランタイム、署名、更新、依存管理 |
| [ロードマップ](docs/roadmap.md) | 技術検証ゲート、実装順序、公開条件 |

## ライセンス

OSSとして公開する方針です。ライセンスはApache-2.0を第一候補として比較中で、**正式採用前**です。依存OSS、CPython、Microsoft WebView2等の条件はそれぞれ別に確認し、公開前にLICENSE/NOTICEと再配布条件を整備します。
