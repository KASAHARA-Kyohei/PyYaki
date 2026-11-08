# PyYaki 公開API設計

状態: 共通API・Mac開発用ホストを実装、配布等は設計案 / 作成: 2026-10-30 / 更新: 2026-11-08 / Windows開発用ホストの主要動作を実機確認

本書は公開API、JSON契約、エラー、制限値の正本。以下はMVP全体の設計で、実装済み範囲は次の表に限定する。Context・イベント・起動/終了フックも実装済みで、実行可能なサンプルは`examples/hello`。未対応のCLI・ビルド等は設計例として区別する。プロセスと信頼境界は[architecture.md](architecture.md)を参照する。

| 実装範囲（2026-11-04） | 状態 |
| --- | --- |
| `App`、`@app.command`、型検証、`AppError`/`FrameworkError`、`app.asgi()` | 実装済み。ASGIは現状loopback限定の試作用 |
| JS `ready`/`invoke`/`on`、`PyYakiError`、state/capabilities、期限・取消 | 実装済み。SDK/Python版は`0.1.0.dev0`で一致させる |
| hello/welcome、call/result/error、cancel、event | WebSocketと専用パイプ経路を実装。capabilitiesは`["rpc", "events"]` |
| `Context`、イベント、`on`、起動・終了フック | 共通処理として実装済み。Windows実WebView2のイベント・取消・終了フックも確認 |
| Mac/Windowsブリッジ・内部ワーカー通信 | Macは実WKWebView・実パイプで確認。WindowsもMSVCビルド・実WebView2のRPC・Python回収を確認。不要ハンドル非継承の負例等は未確認。[実機記録](validation-windows.md)を参照 |

試作固有の制約: 接続初期化nonceは30秒・一度限りで、未使用nonceは全体256件を上限に古いものから失効。JSONの深さは64まで、標準SDKはhelloで`request_id_mode="sequence"`を交渉し、要求履歴を蓄積しない。旧形式のクライアントだけ要求IDを最大4,096件まで追跡し、超過時は接続を閉じる。通常の接続復帰はページの手動再読み込み。Web devのファイル変更による自動更新は第7節を参照。JSON本文1MiB、同時16要求、invoke既定30秒/最大120秒は設計値を使用。送信待ちは2秒で打ち切り、プロトコル違反・版不一致は接続を閉じる。これらの試作制約はMVPの最終採択ではなく、長時間稼働は未検証。

ネイティブ試作ではページ再読み込み/遷移を許可せず、再接続はホストを閉じて再起動する。内部フレームは配送用の包みを含めて1MiB、UI→ホストとホスト→ワーカーのキューはそれぞれ32件、ワーカー→GUIの受信待ちはイベント/応答を合わせて64件。UI資産は同時16読込・各16MiBまで。超過時は明示エラーとしてホストを失敗状態にする試作上の制限で、MVPの採択は今後評価する。

Mac開発用ホストも実装。公開helloはWindows同様nonceなし、welcomeのmodeは`macos`。`pyyaki://localhost/`とホストのブートストラップ情報から経路を選び、WebSocketへフォールバックしない。内部ワーカースキーマ・capabilities・制限値はWindows開発用ホストと共通。

## 1. API設計原則

- 同じ`App`のコマンド登録をWindowsとWebの両アダプターが使う。
- UIは同じES module SDKをimportする。OS判定を業務コードへ広げない。
- JavaScriptにはPythonオブジェクト全体を公開せず、名前を登録したコマンドだけを公開する。
- Python/JS間で転送するものは厳密なJSON。関数、例外オブジェクト、パス、ファイルハンドルは転送しない。
- ネイティブ機能の差異はcapabilityとして明示する。MVPの共通機能は`rpc`と`events`だけ。
- JSON契約の検証は両経路で同じPythonディスパッチャーが行う。WebのFastAPI検証だけに依存しない。

## 2. 利用者アプリの配置案

```text
my-app/
├─ pyproject.toml            # 静的な起動・ビルド設定
├─ requirements-windows.lock # 対象環境の推移依存も固定し、ハッシュを含む
├─ app.py                    # Appと公開コマンド
├─ domain/                   # OS/通信から独立したPython業務処理
└─ ui/
   ├─ index.html
   ├─ styles.css
   ├─ main.js
   └─ assets/
```

UIをPythonの巨大なHTML文字列やRustバイナリのソースへ埋め込まない。生成SDKの置き場と利用者が編集するファイルを分ける。`/_pyyaki/`はフレームワーク予約パスで、利用者資産を配置しない。

### 2.1 静的設定の例

以下はMVP全体の`pyproject.toml`設計例。現在の読み取り範囲は後の表に示す。サンプルの`examples/hello/pyproject.toml`にはwindow設定がなく、ネイティブは既定値を使用する。

```toml
[tool.pyyaki]
app-id = "com.example.hello"
name = "Hello PyYaki"
version = "0.1.0"
entry = "app:app"
ui-dir = "ui"
ui-entry = "index.html"

[tool.pyyaki.window]
title = "Hello PyYaki"
width = 960
height = 640

[tool.pyyaki.build.windows]
python = "3.14"
dependencies = "requirements-windows.lock"
include = ["app.py", "domain/**", "ui/**"]
webview2 = "fixed"
```

| 設定 | 現在の実装 |
| --- | --- |
| `entry` | PythonのWeb CLI/ワーカーが読み取る。既定は`app:app` |
| `ui-dir` / `ui-entry` | PythonとRustが読み取る。既定は`ui` / `index.html`。プロジェクト外への参照を拒否 |
| `window.title` / `width` / `height` | Rustホストが読み取り、Mac/Windows buildも検査して生成設定へ保存する。既定の開発runは`PyYaki` / 960 / 720の論理サイズ。幅320〜4,096、高さ240〜4,096の整数を許可 |
| `app-id` / `name` / `version` | 開発runは直接使用しない。Mac buildはbundle識別子・名・版へ使用。Windows試作はapp-idをキャッシュ、nameを既定ファイル名・窓titleへ使用し、versionの配布メタデータ反映は未実装 |
| `build.windows.include` | 単一exeローカル試作の相対glob配列。上のpython/dependencies/webview2設定は設計例で未使用。資材はCLI引数で明示する。[詳細](windows-onefile.md)を参照 |
| `build.macos.include` | .appローカル生成のプロジェクト相対glob配列。[詳細](macos-build.md#設定と制限)を参照 |

`app-id`は配布者が管理する安定した識別子として使う設計。Windows単一exe試作は検証済みapp-idと索引SHA-256で実行キャッシュを分離する。ログ/プロファイルと開発runは起動ごとのinstance方式。業務データ保存先の公開APIは未実装。例の識別子は正式な配布者を示さない。

`entry`はモジュールと`App`オブジェクトを表す。現在のRustホストはUI/window設定を読み、HTMLマーカー後に生成するPythonワーカーがentryを読み取ってimportする。UIパスはプロジェクトルート基準で検証する。将来のビルドではPythonパッチ版、Rustホスト版、WebView2版とハッシュをリリース資材と解決結果に記録する。

`include`はアプリ独自ファイルの指定。依存wheelの資産を自動推定して削除する設定ではない。`.env`、秘密鍵、開発用venv、VCS、キャッシュは既定で除外し、必要なデータは明示する。

## 3. Python API

### 3.1 共通コマンド

```python
from pyyaki import App, Context

app = App()

@app.command("greet")
async def greet(name: str, *, ctx: Context) -> dict[str, str]:
    await ctx.emit("greeted", {"name": name})
    return {"message": f"Hello, {name}!"}

# Web公開時だけ必要なASGIエントリー。生成はimport時に副作用を起こさない。
asgi = app.asgi()
```

同じファイルを、現在はRustホストの`run --project ... --python ...`がワーカーでimportし、Python CLIの`pyyaki dev`がASGI経由で使う。Macホスト同梱wheelでは`pyyaki run [--project <path>] [--python <path>]`を実装し、既定は現在のプロジェクトとCLIのPythonを使う。静的設定と同梱ホストを検査してexecし、業務importはホストのshell通知後へ維持する。pure wheel/editable導入とWindows Pythonラッパーには`run`を提供しない。[導入と制約](macos-wheel.md)を参照する。`asgi`の生成はサーバー起動、GUI表示、業務処理開始を行わない。ライフサイクル開始はホスト/ASGI lifespanが担当する。

### 3.2 公開するクラスとメソッド

| API | 契約 |
| --- | --- |
| `App()` | コマンド・フックの登録器。プロセス起動やポート待受を行わない |
| `@app.command(name)` | `async def`だけを登録。重複名、予約名、不正な注釈は起動時エラー |
| `@app.on_startup` | 引数なしの非同期フック。ワーカー起動/ASGI lifespan開始時に1回実行し、完了後にreadyを許可 |
| `@app.on_shutdown` | 引数なしの非同期フック。終了時の資源解放。プロセス強制終了時の実行保証はない |
| `app.asgi()` | 共通ディスパッチャーと静的資産を提供するASGIアプリを返す。MVPは1ワーカー運用 |
| `Context.session_id` | 接続ごとの不透明な識別子。ログイン済みユーザーIDとして使わない |
| `Context.mode` | `"windows"`、`"macos"`または`"web"`。通常の業務コマンドに分岐を要求しない |
| `Context.state` | 接続単位の辞書。切断時に破棄し、他セッションへ共有しない。永続化・複数ワーカー同期はない |
| `await ctx.emit(name, payload)` | 同じ接続にイベントを送る。他セッションへのbroadcastはMVPで提供しない |
| `FrameworkError` | フレームワーク起因のPython例外。`code`、`message`、任意のJSON `details`を参照できる |
| `AppError(code, message, details=None)` | アプリが想定内の失敗を返す例外。`APP_`で始まるコードのみ使用可能 |

`ctx`は型が`Context`で名前が`ctx`のkeyword-only引数としてフレームワークが注入する。JSONのparamsには含めず、paramsからの`ctx`指定はVALIDATION_ERRORで拒否する。既定値は付けず、Contextが不要なコマンドは`ctx`を省略できる。

`ctx.emit`はキュー超過で`FrameworkError(code="BUSY")`、不正なイベント形式/JSONで`SERIALIZATION_ERROR`、接続終了後は`DISCONNECTED`を送出する。コマンド内で未捕捉のFrameworkErrorは同じcodeで要求元へ返す。未知のPython例外は`INTERNAL_ERROR`へ変換する。

起動フックは登録順、終了フックは逆順で実行する。ASGIオブジェクトの生成・ブラウザの接続ごとには実行せず、ワーカーまたはASGI lifespanの開始/終了ごとに実行する。同じAppのlifespanを同時に複数開始することは拒否する。ASGIサーバーではlifespanを有効にする。起動フックの失敗はready前の起動失敗とし、その場合も登録された終了フックを試みるため、終了フックは部分初期化にも対応する。終了フックの例外はログに残して次へ進み、終了フック全体には2秒の協調的な取消期限を置く。ネイティブホストの終了待ちも全体2秒であり、長い要求の後片付け等でフックの実行時間が短くなる場合がある。同期処理や取消を無視する処理・強制終了では、完了を保証しない。

### 3.3 型と検証

MVPの引数・戻り値は`str`、`bool`、安全範囲の`int`、有限の`float`、`None`、`list[T]`、`dict[str, T]`、それらの単純なoptionalを対象とする案。入力と戻り値を厳密検証し、`"1"`から`1`のような暗黙変換はしない。

- paramsは名前付きJSON objectのみ。余分な引数、不足、型違いを`VALIDATION_ERROR`とする。
- コマンド名・アプリイベント名は`[a-z][a-z0-9_.-]{0,63}`。`pyyaki`で始まる名前は予約する。
- 署名の`*args`、`**kwargs`、無注釈引数、Contextの誤った位置は起動時に拒否する。
- 整数は`-(2^53-1)`〜`2^53-1`に限定。それ以上の整数、Decimal、日時、bytesはアプリが明示的に文字列等へ変換する。
- NaN/Infinity、循環参照、JSのundefined/BigInt、任意クラスのインスタンスは拒否する。戻り値の不正は`SERIALIZATION_ERROR`とする。
- 現在はPydanticのstrictなTypeAdapterで検証する。Pydantic独自モデルは公開コマンドの引数・戻り値として対応していない。

Pythonコマンドは1つのasyncioイベントループで実行する。CPU処理や同期I/Oをそのループで長時間実行すると全セッションが遅くなる。自動的なプロセス移送はしない。アプリが明示的にスレッド等へ移した処理は、呼出し取消後も継続し得る。

## 4. JavaScript API

```javascript
import { pyyaki } from "/_pyyaki/sdk.js";

const unsubscribe = pyyaki.on("greeted", ({ name }) => {
  document.querySelector("#last-name").textContent = name;
});

await pyyaki.ready();
const result = await pyyaki.invoke("greet", { name: "Yaki" });
document.querySelector("#message").textContent = result.message;

// この画面のイベント購読を終了するときに呼ぶ。
unsubscribe();
```

`sdk.js`は同じURLと公開APIを両環境で提供する。Windowsではホストが供給したブートストラップ情報によりネイティブ経路を選び、Webでは同一originのセッション初期化とWebSocketを使用する。SDKの選択をUser-Agent文字列だけに依存させない。

| API | 結果・動作 |
| --- | --- |
| `ready({ timeoutMs = 10000 } = {})` | welcome受信後に`{ mode, capabilities, protocolVersion }`を返すPromise。同時呼出しは同じ接続の状態を共有。失敗・切断はreject |
| `invoke(name, params = {}, { timeoutMs = 30000, signal } = {})` | 結果JSONを返すPromise。ready前は`NOT_READY`、切断後は`DISCONNECTED`。暗黙のキュー待ち・再接続・再送をしない |
| `on(name, callback)` | イベントのローカル購読を登録し、解除関数を返す。ready前にも登録可能。同期例外・Promiseの失敗は他の購読を止めずconsole.errorで報告。各購読には独立したJSONコピーを渡す。不正な名前・callbackはBAD_REQUEST。解除関数は繰り返し呼出し可能 |
| `capabilities` | ready後の読み取り専用文字列配列。MVPは`["rpc", "events"]`。ready前は空 |
| `state` | `connecting / ready / failed / disconnected`。画面の接続状態表示用 |

Windows特有の機能は将来別名前空間で設計し、非対応時は`UNSUPPORTED_CAPABILITY`を返す。Web版にサーバーのファイルパスを返して「ローカルファイルAPIの共通化」としない。

`AbortSignal`または期限超過でSDKはPromiseを失敗させ、cancelをbest-effort送信する。Python側の協調的取消は可能だが、副作用の巻戻し、スレッド/ネイティブ関数の即時停止は保証しない。呼出し後に接続が切れた場合、処理が完了したか不明なため再送しない。

## 5. 通信プロトコル v1

### 5.1 ハンドシェイク

SDKは接続したらhelloを送信する。Webは取得した接続nonceを必須とし、Windowsの公開helloにはnonceを含めず、ホストが内部パイプの別nonceでワーカーを検証する。

```json
{"v":1,"type":"hello","client_version":"0.1.0.dev0","nonce":"web-only-one-time-value","request_id_mode":"sequence"}
```

Windowsではhelloの`nonce`を省略する。helloは接続につき1回だけ受け付ける。対応版とワーカーreadyの確認後にwelcomeを返す。

```json
{"v":1,"type":"welcome","session_id":"opaque-session-id","mode":"windows","capabilities":["rpc","events"],"framework_version":"0.1.0.dev0","limits":{"request_id_mode":"sequence","max_message_bytes":1048576,"max_inflight":16,"default_timeout_ms":30000,"max_timeout_ms":120000}}
```

`session_id`は診断用にも使えるが、後続の呼出しでクライアントが指定する宛先には使わない。ホスト/サーバーが接続に結びつけたセッションで配送する。

v1は整数のプロトコル版。未対応v、未知のメッセージtype、予期しないフィールドは拒否する。MVPではSDK、Python、ホストのフレームワーク版を完全一致に固定し、互換表のない任意混在を許容しない。

### 5.2 要求と応答

```json
{"v":1,"type":"call","id":"1","method":"greet","params":{"name":"Yaki"},"timeout_ms":30000}
```

```json
{"v":1,"type":"result","id":"1","result":{"message":"Hello, Yaki!"}}
```

```json
{"v":1,"type":"error","id":"1","error":{"code":"VALIDATION_ERROR","message":"name must be a string","details":{"field":"name"}}}
```

標準SDKの`sequence`モードでは、IDは`1`〜`9007199254740991`の正規化されたASCII十進文字列。先頭ゼロ・符号・指数表記を許可しない。受信順に厳密な増加を要求し、欠番は許可する。サーバーは最後の番号1個だけを保持し、それ以下を未使用の番号でも拒否する。結果・エラー・BUSYのいずれも受理した番号を消費し、古いIDの取消は新しい要求へ作用しない。SDKは引数をスナップショットしてから番号を割り当て、getterによる再入呼出しでも送信順の増加を保つ。

helloの任意フィールド`request_id_mode`は`sequence`または`legacy`。省略時は従来の`legacy`で、接続中に一意な最大128文字のASCII文字列を使う。legacyは最大4,096件の履歴上限を残す。welcomeの`limits.request_id_mode`で選択結果を返し、標準SDKはsequenceの確認前にreadyにならない。未知のモードや追加フィールドは拒否する。

並行呼出しの応答順は要求順と一致しない。result/errorは同一IDにつきいずれか1つを最終応答とする。重複IDは接続のプロトコル違反として未処理要求を失敗させ、接続を閉じる。

`timeout_ms`は送信側の待機上限であり、サーバーも受信からの期限で処理を制限する。二つの時計を直接比較しない。遅れて届いた応答はSDKが破棄する。再試行可能性を通信層で決めず、業務上必要ならアプリがidempotency keyをparamsに設計する。

### 5.3 イベント・取消

```json
{"v":1,"type":"event","name":"greeted","payload":{"name":"Yaki"},"seq":1}
```

```json
{"v":1,"type":"cancel","id":"1"}
```

イベントは接続内でseqを1から増やし、接続中のイベント送信順を維持する。`ctx.emit`は検証済みJSONのキュー投入で完了し、UIでの受信・処理完了を保証する通知ではない。イベントと要求の最終応答の到着順には依存しない。要求の最終応答と別のメッセージであり、SDKの購読の有無にかかわらず接続先へ送る。購読なしの場合SDKが破棄する。切断中のイベント再送、欠番の復元、永続化は行わない。

取消要求は存在する同一接続のIDだけに作用する。未知または完了済みIDのcancelは無視し、別セッションの処理には届かない。

### 5.4 内部ワーカー接続

Mac/Windowsホストとワーカーには、公開メッセージとは別の制御メッセージとして`worker_hello / worker_progress / worker_welcome / worker_failed / dispatch / shutdown`を実装している。worker_helloはnonce、ホスト版、プロトコル版を照合し、worker_welcomeはアプリ初期化完了後に返す。dispatchはホストが発行したセッション識別子と公開メッセージを包む。Webアダプターは同じConnection/ディスパッチャーを直接使う。

この制御面は公開APIではないが版管理の対象とし、JSから制御typeや他セッションIDを投入できないようにする。初期試作で使う内部JSONスキーマを以下に示す。残る制御面はG2で定義し、本書の公開契約と区別する。

初期ネイティブ試作で実装した内部スキーマ（各フレームのフィールドは以下に限定、`v`は整数1）:

| type | 他のフィールド | 意味 |
| --- | --- | --- |
| `worker_hello` | `nonce`、`framework_version` | 64文字nonceと`0.1.0.dev0`をパイプ内で通知。ワーカーは照合後にだけアプリをimport |
| `worker_progress` | `nonce`、`framework_version`、`phase`、`worker_elapsed_ms` | 起動の6段階を固定順で通知。Rustは同じnonce/版と、有限・非負・単調な経過時間を検査 |
| `worker_welcome` | `nonce`、`framework_version` | import・登録・起動フック完了後に同じnonce/版を返す。Rustは6段階完了後の完全一致を確認 |
| `worker_failed` | `nonce`、`framework_version`、`phase`、`worker_elapsed_ms`、`error_type`、`message` | 通常例外をログ保存後に通知。phaseは直近の起動段階、welcome後は`connection`。例外名は1〜128文字、メッセージは最大1,024文字 |
| `dispatch` | `session_id`、`message` | ホスト発行の接続IDと公開JSON。ワーカーは接続途中のID変更を拒否し、JSへ制御面を公開しない |
| `shutdown` | なし | 新規要求を停止し、協調的に終了 |

ホストは同じトップページからの`{"v":1,"type":"pyyaki_shell_ready"}`を起動用制御マーカーとして別処理する。SDKがレイアウト後に送信し、ホストはその後にPythonを生成する。画面提示の保証は録画・トレースが必要。ネイティブの公開helloはnonceなし、公開welcomeのmodeは`windows`または`macos`、capabilitiesは`["rpc", "events"]`とする。メンテナー用の明示フラグでのみ使う確認マーカーは[native/README.md](../native/README.md)を参照。

2026-11-04に追加した起動段階は`worker_entered → config_loaded → app_import_started → app_imported → startup_hooks_started → startup_hooks_completed`。通知の繰返し・逆順・追加フィールドを拒否し、公開SDKへ中継しない。SDKからホストへの`{"v":1,"type":"pyyaki_connect_started"}`と`{"v":1,"type":"pyyaki_connect_timeout"}`も内部専用で、接続開始と内部10秒期限を記録する。期限通知時は障害画面とワーカー回収へ進む。呼出し単位のready待機期限とは区別する。記録の基準時刻・再検証方法は[起動の切り分け](startup-diagnostics.md)を参照。開発中の内部契約変更なので、ホストとワーカーを同じソースから更新する。

2026-11-08のMac初期Space配置対策は、ホストログに`macos_initial_space_behavior`の適用/復元を記録する。ワーカー通知や公開APIの追加ではない。最初の二重rAF後のshell通知で元の窓設定へ戻してからPythonを生成し、既存の各10秒期限を維持する。[比較・未確認範囲](validation-macos-space-2026-11-08.md)を参照。

SDKの描画待ち観測は内部専用の`pyyaki_shell_progress`で送り、フィールドは`v=1`、`type`、`stage`、`visibility`、`focused`だけとする。stageはscheduled/frame1/frame2/visibility/focus/blur、visibilityはvisible/hidden、focusedはbooleanに限定する。ホストのログは最大16件で、Pythonへ転送しない。監視の追加でshell通知・helloを先送りまたは前倒しせず、公開APIとして扱わない。

### 計測専用のウィンドウ巡回

Macの明示フラグ`--diagnostics --timer-probe-seconds <15..60>`では、固定した7つの自分の窓状態をRustが順に切り替える。JSの`pyyaki_diagnostic`の`timer_probe_ready`（dataは空object）で開始し、`timer_phase_seen`で確認する。この2マーカーは追加フラグなしでは拒否する。Rustは`__pyyaki_timer_phase__`イベントでindex/name/native_start_msを通知し、JSが抑制されても復元へ進む。任意の窓操作を受け付ける公開APIではない。測定手順は[native README](../native/README.md#macのウィンドウ状態とタイマーを比較する)を参照。

## 6. エラーと制限値

| code | 意味・返却元 |
| --- | --- |
| `NOT_READY` | ready前のinvoke。SDK側で失敗 |
| `DISCONNECTED` | 接続切断。未完了の副作用は不明 |
| `BAD_REQUEST` | JSON/メッセージ形式・サイズ等が不正 |
| `PROTOCOL_MISMATCH` | プロトコル版または固定した構成版が不一致 |
| `METHOD_NOT_FOUND` | 未登録コマンド |
| `VALIDATION_ERROR` | paramsまたは引数型の検証失敗 |
| `SERIALIZATION_ERROR` | 戻り値またはイベントがJSON契約外 |
| `TIMEOUT` | 期限超過。処理停止や巻戻しを意味しない |
| `CANCELLED` | 呼出元の中止、または協調的取消 |
| `BUSY` | 同時実行数/送信キューの上限超過 |
| `BACKEND_FAILED` | 起動失敗、起動後のクラッシュ |
| `UNSUPPORTED_CAPABILITY` | 要求した機能が利用できない |
| `INTERNAL_ERROR` | 想定外の例外。利用者向けメッセージと診断IDだけを返す |
| `APP_*` | アプリが定義した想定内エラー |

SDKは上記を`PyYakiError`としてrejectし、`code`、`message`、任意のJSON `details`を提供する。Pythonのトレースバック、絶対パス、秘密情報は本番応答へ含めない。プロトコル破損など要求IDを復元できない場合は接続を閉じ、保留要求を`DISCONNECTED`で失敗させる。

ネイティブホストがバックエンド障害を検出した場合は、予約した内部DOMイベント`__pyyaki_backend_failed__`でSDKへ通知する。SDKはready待ち・保留invokeを`BACKEND_FAILED`で失敗させ、購読とブリッジのリスナーを解放してstateをfailedにする。要求ごとの期限を待つ動作にはしない。これはWebSocketの公開メッセージtypeではなく、アプリから発行する公開APIでもない。通常起動でのホスト障害は終了コード1として扱い、ウィンドウは原因表示と閉じる操作のため維持する。

| 制限 | MVP提案値 |
| --- | --- |
| 最大メッセージ | UTF-8 JSON本文で1,048,576 bytes。WebSocket・パイプ・WebViewの各入口で検査 |
| 同時実行 | 1セッション16要求。超過は即時BUSYで、無制限に待たせない |
| 待機期限 | invoke既定30,000ms、最大120,000ms。0・負数・最大超過はBAD_REQUEST |
| イベント送信キュー | 1セッション64件。満杯では`ctx.emit`がcode=BUSYのFrameworkErrorになり、黙って上書きしない |
| 接続/バックエンド準備 | SDK ready既定10,000ms。ホストのPython起動待ちも生成から10秒。展開時間とは区別 |
| 正常終了待ち | shutdown通知から2,000msで打切り、必要なら子を終了 |

現在の`ready({ timeoutMs })`は呼出元の待機時間を指定する。SDK内部の接続初期化は別に10秒で打ち切るため、この値を大きくしても接続初期化の期限は延長しない。

巨大ファイル転送、バイナリストリーム、処理途中の任意ストリームはMVP対象外。大量イベントを出すアプリは集約頻度を制御する。制限値はG2/G4で測定し、変更時は両アダプターと全設計書を更新する。

## 7. Web公開と開発時更新

本番Webの起動例は`uvicorn app:asgi --host 127.0.0.1 --port 8000 --workers 1`を想定する。認証・TLS終端・リバースプロキシ・監視は配布者の運用責任であり、`pyyaki dev`をそのまま公開する設計にしない。

`dev`は既定でUI資産を監視してページ全体を再読込する。プロジェクト内のPython・設定変更は子サーバー再起動となり、応答が復帰した世代でページを再読込する。入力・画面状態・Context・イベント購読・処理中のRPCは引き継がない。`--no-reload`で無効化し、`app.asgi()`には挿入しない。監視範囲・エラー後の修正復帰は[開発ガイド](development.md#web開発時の自動更新)、Macでの確認と未確認範囲は[検証記録](validation-web-reload-2026-11-08.md)を参照する。

開発専用の内部経路は`GET /_pyyaki/dev`（世代を示す`revision`のみ）と`GET /_pyyaki/dev.js`。HTMLへ同一originの外部moduleを挿入し、元ファイルを編集せずCSPを維持する。世代取得は同一origin/Fetch Metadataを検査し、ファイル名や任意コマンドは返さない。これは公開RPCや一般的なSDK再接続APIではない。通常の接続断は従来どおり手動で再読込する。状態を維持するHMR、編集中コードの差替え、ネイティブrunの自動更新はMVPに含めない。

## 8. 契約の検証項目

同一の契約ケースをWindowsアダプターとASGIアダプターへ適用する。

- 型検証、戻り値、未知コマンド、アプリ例外、Unicode、JSON境界値。
- ready前呼出し、版不一致、重複ID、不正JSON、長さ超過、並行要求、期限、取消、切断後応答。
- イベントのセッション分離、順序、購読解除、キュー超過、ページ遷移後のセッション破棄。
- 2ブラウザ間のContext分離、外部origin/Host拒否、nonceの再利用拒否、未認証接続のタイムアウト。
- Windowsでの大量`print()`、Pythonクラッシュ、親終了、無効プロキシ、待受ポート不在。

Windowsを含む全契約の試験は未完了。現在の実装・直近の確認結果は[ロードマップの現在地](roadmap.md#現在地と次の作業)、起動・連続試験の条件と結果は[Mac初期計測](performance-macos.md)を参照。過去の検証記録は各時点の範囲を示す。
