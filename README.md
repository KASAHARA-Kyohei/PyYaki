# PyYaki

Pythonの業務ロジックとHTML/CSS/JavaScriptのUIを共有し、WindowsデスクトップアプリとWebアプリを開発するためのOSSフレームワークの初期試作です。

名称は「鯛焼き」にかけたものです。表示名を**PyYaki**、パッケージ・Python import・CLI・JavaScript SDKの識別子を**`pyyaki`**に統一します。

**現在は初期試作です。Mac向けWeb・ネイティブ経路に加え、Windows開発用Rustホスト・専用パイプのワーカー・ネイティブSDK経路のソースを実装しました。Windowsの型検査に加え、2026-11-03にWindows 11 x64でMSVCビルド・実WebView2のUI/RPC・イベント・取消・終了時のPython回収を確認しました。[失敗・未確認事項](docs/validation-windows.md)が残り、G1/G2全体は未完了です。公開パッケージはありません。Windows単一exeは未署名のローカル生成試作です。MacはPython同梱`.app`のローカル生成試作を追加しました。Macネイティブでは初期計測と5分・30分の連続試験を実施しましたが、Windowsの性能・時間単位の稼働は未検証です。**

起動期限やPython起動失敗は[段階ログ・障害画面・反復検証の手順](docs/startup-diagnostics.md)で切り分けられます。MacではSpace移動で初期描画待ちが止まる経路を確認し、2026-11-08に[起動中だけ窓配置を調整する対策](docs/validation-macos-space-2026-11-08.md)を追加しました。自作fullscreen窓で比較確認しましたが、利用者のSpace移動等の未確認条件は残ります。Windows更新版は2026-11-04に[起動反復9/9と実画面・終了処理を再検証](docs/validation-windows-2026-11-04.md)しましたが、以前の失敗の原因解消は未確定です。

[C案の既定アイコン](assets/branding/README.md)を、Mac .app・開発ホストのDock設定と、Windows開発ホストのexe・ウィンドウ・タスクバーへ組み込む実装を追加しました。[反映方法と検証範囲](native/README.md#アイコン)を参照してください。WindowsのExplorer/ウィンドウは実画面で、タスクバーは利用者の目視回答でC案の表示を確認しました。

2026-11-05にワーカーのテキストログをUTF-8へ統一し、障害画面の説明の重複を整理しました。[Windowsでの検査・実GUI確認](docs/validation-worker-logs-2026-11-05.md)を記録しています。反映にはRustホストの再ビルドが必要です。

2026-11-08の対策前の[Mac回帰確認](docs/validation-macos-2026-11-08.md)では、Python 145件・Rust 13件、実WKWebView反復9/9、UTF-8ログ・例外DOM・SIGTERM終了、同梱.appの署名・全ハッシュ・再配置を確認しました。初回のSpace関連失敗は保持しています。その後の配置対策ではRust 14件、独立したfullscreen開始3組と5秒遅延の反復がそれぞれ9/9成功しました。二重rAF・10秒期限・Python開始順は維持しています。目視・実クリックclose/download・Dock/Finder表示、正式署名・公証等は未確認です。

2026-11-05の[境界追試](docs/validation-windows-boundary-followup-2026-11-05.md)では起動9/9、完全切断・不要ハンドル非継承・Job割当失敗回収・実クリック拒否・孫回収を確認しました。venvの部分closeにはwriter保持の制約が残ります。[資材の前段確認](docs/windows-onefile-prototype.md)に続いて、[Windows単一exeの生成・検証付き展開](docs/windows-onefile.md)をローカル試作として追加しました。[実機の確認結果と失敗の切り分け](docs/validation-windows-onefile-2026-11-07.md)を記録しました。実署名・未導入クリーン環境・性能の受入は残っています。

開発を再開するときは[開発ガイド](docs/development.md)、現在の実装と残作業は[ロードマップの現在地](docs/roadmap.md#現在地と次の作業)を参照してください。

Macの通常・遅延・fullscreen起動と既存.appの署名・ハッシュ・再配置は、[自動回帰検証](docs/macos-regression.md)でまとめて再実行できます。検証用の自分の窓だけを操作し、実画面や利用者のSpace操作は未確認として区別します。

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

ブラウザで`http://127.0.0.1:8000`を開きます。あいさつ、想定内エラー、3秒待機中のUI操作を確認できます。ポート変更は`pyyaki dev --port 8001`、終了は`Ctrl+C`です。UI変更はページを自動更新し、プロジェクト内のPython・`pyproject.toml`変更はサーバーを再起動してページを更新します。入力やContextは引き継ぎません。無効化は`--no-reload`。監視範囲やエラー時の挙動は[開発時の自動更新](docs/development.md#web開発時の自動更新)を参照してください。

起動できない場合、`pyproject.toml`が見つからなければプロジェクトのディレクトリで実行し、設定エラーの場合は表示されたファイルと項目を確認してください。ポートが使用中の場合は、使用しているプロセスを終了するか`--port`で別の番号を指定できます。アプリ読み込みに失敗した場合は、設定の`entry`と必要な依存を確認してください。

実装済み: `App`、非同期コマンド登録、引数・戻り値検証、`AppError`/`FrameworkError`、接続単位の`Context`、イベント、起動・終了フック、ローカルASGI/WebSocket、SDKの`ready`/`invoke`/`on`、期限・取消・切断処理。capabilitiesは`["rpc", "events"]`です。3秒の処理ではPythonから届く進捗を表示します。

Mac向け`build macos`は、準備済みのホスト・standalone Python・wheelから`.app`を生成するローカル試作として実装済みです。[生成手順](docs/macos-build.md)を参照してください。

未実装: ネイティブの自動再読込、Windowsネイティブexeのpip配置。Windows `build windows`は明示資材を使うローカル試作として実装済みです。Macホスト同梱wheelでは`pyyaki run`を実装しました。pure wheel・editable導入にはホストがなく、WindowsのPythonラッパー経由の`run`は提供しません。`app.asgi()`は自動更新を挿入しないloopbackの試作用で、本番Web運用は未検証です。以下の将来構成と区別してください。

テストはルートで`python -m pytest -q`、静的確認は`ruff check src examples tests`を実行します。ブラウザ・ネイティブ検査の使い分けと、変更後の反映方法は[開発ガイド](docs/development.md#検証する)に記載しています。

## Macでネイティブ起動する

Macホストの強制終了時の回収は[2026-11-09の条件付き確認](docs/validation-macos-host-cleanup-2026-11-09.md)を参照してください。既存wheel/.appへ反映するには新しいホストでの再生成が必要です。

Macホスト同梱wheelを導入したvenvでは`pyyaki run --project examples/hello`で起動できます。利用者の導入・実行にRust/Cargoは不要です。まだ未公開のローカル試作で、Pythonランタイムは同梱しません。[導入・生成・制約](docs/macos-wheel.md)を参照してください。以下はホストを開発するメンテナー向けの直接起動です。

開発用ホストはRustとWKWebViewを使います。リポジトリのルートから、上記のvenvを準備した状態で実行してください。

```console
cargo run --manifest-path native/Cargo.toml --locked -- run --project examples/hello --python .venv/bin/python
```

ブラウザではなくネイティブウィンドウを開き、同じUIとPythonコマンドを使います。終了はウィンドウを閉じるか、ターミナルのCtrl+Cです。実際のWKWebView経由のRPCは確認済みですが、appバンドルの正式配布・Developer ID署名/公証・長時間稼働は未対応または未検証です。[Macの詳しい手順](native/README.md#macでの開発用確認)も参照してください。

この開発機ではSDKとリンカーの組合せの都合で、ビルド時に`SDKROOT=/Library/Developer/CommandLineTools/SDKs/MacOSX26.5.sdk`を指定しています。すでにビルドしたものは`native/target/debug/pyyaki run --project examples/hello --python .venv/bin/python`で起動できます。

## Windows開発用ネイティブホスト

`native/`にRust製の起動画面・WebView2ホストと、専用パイプ経由で同じPythonコマンドを呼ぶ実装を追加しました。HTTP/WebSocketサーバーを起動するコードは使用しません。Windows 11 x64で既存ホストのMSVC releaseビルドと実WebView2の主要動作を確認しました。負荷時の接続期限、先行描画の実提示・性能、ネットワーク/企業環境などの残りは[Windows実機記録](docs/validation-windows.md)を参照してください。

まだメンテナー向けの試作で、Rust/MSVCと開発用Python・既存WebView2が必要です。Windowsでのビルド・起動・確認項目は[native/README.md](native/README.md)を参照してください。開発runと、明示資材から生成する[単一exe試作](docs/windows-onefile.md)を区別してください。Rust不要のpip導入、Python/WebView2未導入クリーン端末の受入は後続作業です。

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

正式配布では利用者のRust/Cargo/MSVC/Node.js導入が不要な構成を目指します。現在のネイティブ試作は、メンテナーがRust/Cargoでビルドする段階です。MVPの検証範囲はWindows 11 x64とCPython 3.14通常GIL版の組合せを提案しています。Windows向けビルドはWindows上で行う設計です。

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

Pythonの例（実装済みAPI。Windows開発用ホストでもRPCを確認）:

```python
from pyyaki import App

app = App()

@app.command("greet")
async def greet(name: str) -> dict[str, str]:
    return {"message": f"Hello, {name}!"}

asgi = app.asgi()
```

JavaScriptの例（実装済みAPI）:

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

主な開発機はMacです。最初のMac試作はCPython 3.14通常GIL版で、HTMLからPythonコマンドを呼び、結果・エラーを表示し、非同期処理中もUIが応答するところまでとする方針を2026-10-31に承認し、実装を開始しました。2026-11-03には手元のWindowsで既存ホストのMSVCビルドと主要動作を確認しました。残る先行描画・境界・ネットワーク等の確認と、未実装の単一exeの最小試作へ進む段階です。Windows固有の起動・配布方式全体は未確定です。詳しい順序は[ロードマップ](docs/roadmap.md)に記載しています。

高速性と安定性を重視します。起動時間の計測に加え、GUIの応答性、Pythonのクラッシュ・終了処理、キャッシュ破損等を評価する設計です。Mac開発用ホストの起動・RPC・5分/30分の連続稼働・表示状態別タイマー・Python障害時の結果を[計測記録](docs/performance-macos.md)に記録しています。Windowsの性能と長時間稼働の数値基準は今後の検討事項です。

## 文書

| 文書 | 内容 |
| --- | --- |
| [AGENTS.md](AGENTS.md) | Codexの開発ルールと文書の責務 |
| [要件定義](docs/requirements.md) | 必須要件、受入条件、MVP範囲、起動速度の測定 |
| [アーキテクチャ](docs/architecture.md) | 既存OSS比較、技術選定、起動順、IPC、リスク、ADR候補 |
| [API設計](docs/api-design.md) | Python/JavaScript API、通信契約、エラー、制限値 |
| [ビルド・配布設計](docs/build-system.md) | pip導入、単一exe、ランタイム、署名、更新、依存管理 |
| [ロードマップ](docs/roadmap.md) | 現在地、技術検証ゲート、実装順序、公開条件 |
| [開発ガイド](docs/development.md) | 作業再開、起動、変更の反映、検査、問題の切り分け |
| [Windows実機記録](docs/validation-windows.md) | 既存ホストのMSVC・GUI確認、環境、失敗と残課題 |
| [Mac計測記録](docs/performance-macos.md) | 起動・RPC・5分/30分の連続試験、表示状態とタイマーの比較、測定上の限界 |
| [Mac .app生成](docs/macos-build.md) | Python同梱bundleのローカル生成手順・入力と制限 |

## ライセンス

OSSとして公開する方針です。ライセンスはApache-2.0を第一候補として比較中で、**正式採用前**です。依存OSS、CPython、Microsoft WebView2等の条件はそれぞれ別に確認し、公開前にLICENSE/NOTICEと再配布条件を整備します。
