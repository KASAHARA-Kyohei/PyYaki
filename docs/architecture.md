# PyYaki アーキテクチャ設計

状態: 初期設計案 + Web試作・Windowsホストのソース実装 / 技術調査日: 2026-10-30 / 更新: 2026-11-08 / Windows既存ホストの実機確認済み・Windowsベンチマーク未実施

初期試作では、`web.py`と通信に依存しない`app.py`、共有セッション`_rpc.py`、匿名パイプの`worker.py`、Windows用Rustホスト`native/`を実装した。RPC、接続単位のContext、順序付きイベントとSDK購読、起動・終了フックを提供する。MacのWeb/ワーカー試験とWindowsの型検査に加え、WindowsのGUI/RPCと通常終了・ホスト強制終了時のPython回収を2026-11-03に[実機確認](validation-windows.md)した。ネイティブwheel・ネットワーク/企業環境等は未完了で、Windows用のADRやMVP全体の採択とは扱わない。

Windows単一exeは[ローカル生成・全ハッシュ検証付き展開](windows-onefile.md)を追加し、[実機での結果と限界](validation-windows-onefile-2026-11-07.md)を記録した。初回GDI窓から、別スレッドでの検証/展開を経て同じexeのGUI子ホストへ渡す2段階の試作で、同一窓での連続合流の設計案と区別する。正式採択はADR-005/020の残る条件を評価する。

開発用ホストはtao 0.34.8 / wry 0.53.5をCargo.lockで固定し、既存WebView2を使う。Python生成をHTMLマーカー後へ遅延し、STARTUPINFOEXの継承ハンドル許可リストとJob Objectを使うコードを実装した。wry既定のSmartScreen保護無効化引数は追加引数を空にして除外し、実際の効果・先行描画・ハンドル継承・企業環境との互換性はWindowsで確認する。

2026-10-31のネイティブ実装時確認資料: [wry 0.53.5 API](https://docs.rs/wry/0.53.5/wry/struct.WebViewBuilder.html)、[wry Windows拡張](https://docs.rs/wry/0.53.5/wry/trait.WebViewBuilderExtWindows.html)、[RustのWindowsプロセス拡張](https://doc.rust-lang.org/std/os/windows/process/trait.CommandExt.html)。標準ライブラリの任意STARTUPINFOEX拡張は実験APIのため、stable Rustでは限定したWin32 FFIで実装する。各unsafe箇所に所有権・生存期間の根拠を記載した。

2026-10-31の実装時確認資料: [FastAPI WebSockets](https://fastapi.tiangolo.com/advanced/websockets/)、[Pydantic Strict Mode](https://docs.pydantic.dev/latest/concepts/strict_mode/)、[Starlette Host検査](https://www.starlette.io/middleware/#trustedhostmiddleware)。本番Web向けの認証・公開ホスト設定はまだ実装せず、Hostは127.0.0.1/localhostに限定している。

macOS開発用ホストも利用者の依頼で追加。Windowsのtao/wryホストを`host.rs`として共用し、MacではWKWebView・専用プロセスグループ・POSIX匿名パイプを使用する。専用fdの継承、venvパス維持、JSON経由のJS配送を実装し、実WKWebViewからPythonコマンドを確認した。Macの起動画面はウィンドウタイトルで準備状態を示し、WindowsのGDI先行描画条件とは別に扱う。

## 1. 既存OSSとの比較

最初に既存手段で要件を満たせるかを評価する。速度の優劣は未測定であり、下表は構造と公式に記載された機能の比較である。

| 選択肢 | 公式資料で確認できる特徴 | PyYaki要件との差・評価 |
| --- | --- | --- |
| [pywebview](https://pywebview.flowrl.com/guide/architecture.html) | Python APIからWebViewを生成。JSブリッジ、HTTP配信、サーバーを使わないHTMLロードを提供 | 既に自由なHTML UIとPython連携がある。Python起動前描画は通常のPythonエントリーでは実現できず、別ホストが必要。サーバー不使用の方式自体は既存なので新規性としない |
| [NiceGUI](https://github.com/zauberzeug/nicegui#architecture) | Python/FastAPI、Vue/Quasar、socket.io、ブラウザ/ネイティブ表示 | Web共通化に強い。Python主体・サーバー主体の構成から、Windowsでサーバーを排除し、Pythonより先に起動するには変更が必要 |
| [Flet](https://flet.dev/docs/) | PythonからUIを構築し、Web/デスクトップ等へ展開 | 共通コードの先行例。FlutterベースのUIモデルを中心とし、生のHTML/CSS/DOMを主な編集対象にしたい方針と異なる。起動性能は比較測定が必要 |
| [Tauri 2 + Python sidecar](https://v2.tauri.app/develop/sidecar/) | Rustホストに外部バイナリを同梱できる | ウィンドウ・権限管理を再利用できる有力代替。汎用ホストの事前ビルド、Python共通API、単一exe梱包は別途設計。アプリごとのCargoビルドを必須にしない工夫が必要 |
| [PyTauri / pytauri-wheel](https://pytauri.github.io/pytauri/latest/usage/pytauri-wheel/) | TauriのPythonバインディング。ビルド済みwheelではRustコンパイラ不要 | 最も近い先行例。通常の起動はPythonから行い、venvのエントリーポイントとポータブル配布は区別される。Python起動前の描画、Webアダプター、単一exeの条件で再評価する |
| [tao](https://docs.rs/tao/latest/tao/) + [wry](https://docs.rs/wry/latest/x86_64-pc-windows-msvc/wry/struct.WebViewBuilder.html) | Rustのウィンドウ/イベントループ、WebView、資産用カスタムプロトコル、JS IPC | 最小構成を選びやすい。権限、キャッシュ、更新、IPC、安全な配布を自前で保守するコストがある。最初の検証候補 |

**推奨案**は汎用のtao/wryホストと別プロセスPython。ただしADR-001の試作で、Tauri側の再利用が同じ起動順・配布要件をより少ない保守負担で満たすなら変更する。既存OSSより高速、軽量、企業利用に最適といった比較広告は実測まで行わない。

Web開発時更新の追加比較（参照日2026-11-08）: [Uvicorn標準reload](https://uvicorn.dev/settings/#development)はPython変更のポーリングを提供し、watchfiles導入時はinclude/excludeも指定できる。[watchfiles](https://watchfiles.helpmanual.io/)はRustのNotifyを使い、主要OSのwheelと[子プロセス再起動](https://watchfiles.helpmanual.io/api/run_process/)を提供する。いずれもブラウザのページ更新は別途必要。今回は追加wheelの資材・来歴管理とUvicorn内部supervisor APIへの依存を避け、標準ライブラリの監視・spawn親と既存Uvicornサーバーを組み合わせた。UIだけの変更ではPythonを再起動せず、Python/設定の追加・削除も比較する。独自の監視・終了処理を保守する負担と大規模ツリーの走査負荷がある。OS通知へ移す判断は実測後とし、速度の優位は主張しない（ADR-021）。

## 2. 技術候補と採用方針

| 要素 | 推奨する初期案 | 理由・代替・未検証事項 |
| --- | --- | --- |
| ウィンドウ/HTML | tao + wry + Windows WebView2 | OSイベントループとWebViewを分離。互換版の組合せを固定する。Win32直接利用はWindows固有の起動画面・プロセス管理に限定する案 |
| 初回起動画面 | Rust + Win32/GDIの小さな描画 | WebView2未展開でも表示するため。tao自身は描画エンジンではない。UIスレッドで描画・閉じる処理を維持できるか要検証 |
| Python連携 | 独立CPythonワーカー + 継承匿名パイプ | ホストのPython DLL依存をなくし、初期化・クラッシュ・GILを分離。ハンドル継承と終了処理はWindows実機で検証 |
| PyO3 | MVPでは使用しない | 同一プロセス連携は可能だが、ABI、DLL探索、asyncioとGUI、クラッシュ分離が増える。将来性能測定で必要性を確認してから検討 |
| Web | FastAPI + Uvicorn、WebSocket | 同じPythonディスパッチャーにASGI経由で接続。Starletteだけに縮小する案もあるが、型検証と実装の容易さを優先 |
| UI/SDK | HTML/CSS/ES modules + 小さなJS SDK | DOMの直接編集と既存Web UIの利用を容易にする。npmやビルドツールは任意 |
| Python配布 | CPython Windows Embeddable Package | 最小の同梱環境。開発venvと同一視せず、依存wheelを事前配置する |
| exe配布 | ビルド済みRustホスト + 署名前に付加したペイロード | アプリごとのRust再ビルドを避ける。単一exeからユーザーキャッシュへ安全に展開する。署名・企業制限・初回時間が主要な検証点 |

PyO3の公式資料では、Python拡張モジュールとRustからのPython埋め込みは別の配布方式として扱われ、動的埋め込みではPython共有ライブラリが必要になる。単に`abi3`を指定してすべての同梱依存が互換になるわけではない。[PyO3のビルド・埋め込み](https://pyo3.rs/latest/building-and-distribution.html)

FastAPIのWebSocket機能は公式に提供されているが、PyYakiのセッション分離、origin検査、制限値は独自に実装・検証する必要がある。[FastAPI WebSockets](https://fastapi.tiangolo.com/advanced/websockets/)

## 3. 構成と責務

### 3.1 Windows

```text
単一配布exe / pipで導入したネイティブ pyyaki.exe
  │
  ├─ Rustホスト: 主スレッド
  │    ├─ 起動画面 / ウィンドウ / OSイベントループ
  │    ├─ WebView2の生成・ナビゲーション制御
  │    └─ JSメッセージを検証し、UIイベントとして配送
  │
  ├─ Rustホスト: 作業スレッド
  │    ├─ ペイロード検証・安全な展開・キャッシュ管理
  │    ├─ ローカルUI資産応答（HTTPサーバーではない）
  │    └─ IPC入出力 / Pythonワーカー監視
  │
  ├─ WebView2のブラウザ/レンダラー等の子プロセス
  │    └─ 共通HTML/CSS/JS + PyYaki SDK
  │
  └─ CPythonワーカー（別プロセス、1アプリ1ワーカー）
       ├─ 共通Pythonディスパッチャー
       └─ 利用者のPythonアプリと依存パッケージ

JS SDK ←WebViewメッセージ→ Rust ←専用の双方向匿名パイプ→ Python
```

WebView2が複数プロセスを使うため、全体を「RustとPythonの2プロセスだけ」と説明しない。Pythonワーカーはサンドボックスではなく利用者と同等のOS権限で動く。

### 3.2 Web

```text
ブラウザ: 同じHTML/CSS/JS + 同じSDK
  │  同一originのHTTP（静的資産・セッション初期化）/ WebSocket（RPC・イベント）
FastAPI / Uvicorn（開発時はloopback、公開時はASGI運用）
  └─ 同じPythonディスパッチャー
       └─ 同じ利用者Pythonアプリ
```

WebではPythonはサーバーで実行される。Windowsの「HTTPサーバー不使用」をWebまで適用しない。共有対象は業務コマンド、JSON契約、静的UIであり、OS固有機能やファイルの意味まで自動で一致させない。

## 4. 初期化順序

### 4.1 単一exeの初回起動

1. Windowsがexeを起動。Rustホストは最小のヘッダーだけを読み、Pythonやアプリコードをロードしない。
2. 主スレッドで起動画面を**描画**し、閉じる操作を処理する。ウィンドウ生成だけで描画完了と判断しない。
3. 作業スレッドでペイロードを検証し、ユーザー専用のstagingディレクトリへ展開する。進捗をUIへ通知する。
4. 必要なPython、UI、Fixed Version WebView2等の検証を終え、キャッシュを原子的に確定する。詳しい手順は[build-system.md](build-system.md)を参照。
5. 主スレッドで指定したWebView2環境を作り、同梱HTMLシェルを表示する。起動画面はHTMLの描画確認まで残す。
6. HTML初描画マーカーをRustが受信した後、別スレッドからPythonワーカーを生成する。GUIスレッドで待機しない。
7. Pythonが共通ディスパッチャーとアプリをimportし、登録・起動フックを完了する。
8. 内部ワーカー接続の版確認を終え、SDKのhello/welcomeを完了する。JSの`ready()`が解決し、業務コマンドを使える。

初回画面はアプリの操作可能状態を意味しない。HTMLが描画されるまではネイティブの起動表示、Python準備中はHTML内の準備中表示を使う。ライブラリimportの5秒遅延や失敗でも、画面表示と終了操作は動作させる。

### 4.2 再起動と開発時

- **展開済みexe**: 同じ起動画面を表示し、キャッシュ検証後にHTMLを表示する。展開省略と完全性確認の両立はADR-005で実測する。
- **`pyyaki run`**: Windows用ネイティブコマンドがTOMLの静的設定を読み、プロジェクトのUIを表示してからvenvのPythonを起動する。起動設定の取得に`import app`を使わない。
- **`pyyaki dev`**: Python起動後にFastAPI/Uvicornとブラウザを使用する。Windowsデスクトップの先行描画保証は適用しない。
- **`pyyaki build windows`**: Pythonによる依存解決・組立を行ってよい。配布先の起動経路とは異なる。

Windowsで一般的なPython `console_scripts`ラッパーを`run`の入口にすると、この初期化順を満たせない。[wheel内ネイティブコマンドの配布案](build-system.md#2-pip導入とネイティブcli)をADR-003で検証する。

### 4.3 状態と障害

| ホスト状態 | 表示 | 遷移・処理 |
| --- | --- | --- |
| STARTING | Rust起動画面 | 展開/検証へ。設定を読めなくてもエラーを表示 |
| PREPARING | Rust起動画面と進捗 | キャンセル可能。破損時は対象キャッシュだけを隔離 |
| UI_READY | HTMLシェル | Python起動へ。業務操作は無効化 |
| BACKEND_STARTING | HTMLの準備中表示 | 起動フック完了・ハンドシェイクでREADYへ |
| READY | 操作可能UI | RPC、イベントを処理 |
| FAILED | ネイティブまたはHTMLのエラー | 原因・ログ場所・再試行/閉じる。自動再実行しない |
| CLOSING | 終了処理 | 新規要求を止め、Pythonを回収し、展開中ファイルを後で清掃 |

開発用ホストの実装は、WebView生成後のUI準備に10秒、shell通知後のワーカー準備に10秒を設ける。SDK接続開始から10秒の内部期限は別で、呼出し単位のready待機期限とも区別する。[段階ログと失敗記録](startup-diagnostics.md)で切り分け、Windowsの初回期限到達の原因は未確定として残す。

Macのready後の[表示状態比較](performance-macos.md#ウィンドウ状態とタイマーの比較)では、最小化・非表示でJSタイマーの周期が約1秒へ伸び、前面復帰後に回復した。非表示時のUI周期を業務処理の正確なスケジューラーとみなさない。Python開始前のshell待ち失敗には、11-08に[起動中だけのSpace配置対策](validation-macos-space-2026-11-08.md)を追加した。利用者のSpace移動/復帰等は未確認で、非表示時のタイマー抑制・先行描画順序・期限は変更していない。

Pythonの起動タイムアウトは起動後10秒の提案。展開中の経過時間は別に表示し、この10秒に含めない。ワーカークラッシュ時は保留要求を失敗させる。再試行は利用者操作で新しいセッションを作り、副作用のある呼出しは自動再送しない。

## 5. 通信とセッション

### 5.1 Windowsの資産配信

wryのカスタムプロトコルを初期候補とする。`pyyaki://localhost/index.html`に相当するURLはWindowsではHTTPSの合成origin（候補`https://pyyaki.localhost`）として扱い、要求をホスト内で同梱ファイルに解決する。**URLにlocalhostが含まれても、DNS接続やlocalhostのHTTPサーバーを必要とする設計ではない。** 全要求がWebView2内で処理されることを実機トレースで確かめる。

カスタムプロトコルのWindows上のorigin変換とHTTPS設定は公式APIに存在する。[wryのプロトコルAPI](https://docs.rs/wry/latest/x86_64-pc-windows-msvc/wry/struct.WebViewBuilder.html)、[WindowsのHTTPS設定](https://docs.rs/wry/latest/x86_64-pc-windows-msvc/wry/trait.WebViewBuilderExtWindows.html)

代替はWebView2の仮想ホスト名マッピング。静的配信を簡素化できる一方、利用するwry版での公開APIや必要なWindows拡張の保守を確認する。`file://`はoriginやWeb APIの差異があるため既定案にしない。[WebView2のローカルコンテンツ方式比較](https://learn.microsoft.com/en-us/microsoft-edge/webview2/concepts/working-with-local-content)

資産解決は許可したUIルート配下だけに限定し、URLをデコード・正規化してから開く。`..`、二重エンコード、絶対パス、UNC、代替データストリーム、シンボリックリンク/reparse pointによる脱出を拒否する。存在しない資産を外部ネットワークへフォールバックしない。

ネットワーク試験では、UI資産・RPCのためのDNS/TCP/HTTPが不要であることと、ネットワーク切断・無効プロキシ下の動作を確認する。WebView2やOSが独自に行う背景通信の完全な不在までは保証せず、アプリの通信と分けて記録する。

### 5.2 WindowsのRPC

- JS→RustはWebViewメッセージ。Rust→JSはJSONメッセージを優先し、任意文字列をJavaScriptソースへ連結しない。選定wry版で安全なAPIが足りない場合はWebView2 API利用をADR-004で評価する。
- Rust→PythonとPython→Rustは、各方向に1本ずつの専用匿名パイプ。`STARTUPINFOEX`の許可リストで子に必要なハンドルだけを継承する案。
- stdin/stdout/stderrはログ等のために残す。利用者の`print()`やネイティブ拡張の標準出力がRPCを壊さない構成にする。
- パイプの各フレームは「4バイトlittle-endianのUTF-8 JSON長 + 本文」。長さ検証後に本文を確保し、部分read/writeを処理する。
- 起動ごとのランダムな接続nonceを継承したパイプ内で交換し、プロトコル版とワーカーのフレームワーク版を確認する。環境変数・コマンドラインへ秘密を置かない。
- 接続情報はJSから指定させない。Rustがウィンドウ/ナビゲーション世代とセッションを結び、Pythonへの内部配送で付加する。公開メッセージ仕様と上限は[api-design.md](api-design.md)を正本とする。
- パイプ読書きはGUI外で行い、GUIへの通知にはイベントループのプロキシを使う。無制限キューを置かない。

### 5.3 WebのRPC

- 静的資産とSDK、HTTPセッション初期化、WebSocketを同一originへ配置する。`GET /_pyyaki/session`で短命の接続用nonceを返し、SDKがWebSocketの最初のhelloで送る案。
- セッション初期化はHost検査と同一origin要求に限定。開発時はランダムなHttpOnly/SameSite cookieを発行し、初期化応答は`Cache-Control: no-store`とする。Cookieが必要な本番運用ではHTTPS/Secureを必須にする。
- 接続時にHost/Originを検査し、cookieとnonceを照合する。nonceをURL、永続ストレージ、ログに載せず、未認証の接続はタイムアウトさせる。CORSのワイルドカードを使わない。
- これらはCSRFや接続の混線への対策であり、ユーザーのログイン認証ではない。公開時はアプリが認証・認可をASGIミドルウェア等で追加する。MVPの`dev`は外部公開オプションを提供しない案。
- ブラウザ接続ごとにディスパッチのContextと状態を分離する。MVPは1サーバーワーカー。モジュールのグローバル変数にセッション状態を保存しない。

イベントは接続ごとの64件キューでJSON文字列として保持し、応答と共通の送信ロックを使う。ネイティブホストのワーカー→GUI受信待ちもイベント/応答を合わせて64件に制限し、上限超過はホストの明示的な失敗とする。emit時にJSONを検証・固定し、上限超過はBUSYとして通知する。切断時はイベント送信とコマンドを取り消し、Contextの状態と残るイベントを破棄する。起動フック完了後に接続を利用可能にし、ワーカー/ASGI lifespan終了時に終了フックを実行する。

切断で保留要求を失敗させ、再接続時は新しいセッションを発行する。イベントの再送・永続化・exactly-onceは提供しない。

### 5.4 終了と子プロセス

Macでは`waitid(WNOWAIT)`で子を回収せずに監視し、PIDを保持したままDropで専用プロセスグループを終了してから回収する。2026-10-31の実機試験で、一時停止時にもwaitidから通知が返ることを再現したため、PIDの有無に加えて`CLD_EXITED/CLD_KILLED/CLD_DUMPED`を照合する。一時停止を終了と誤判定しない。バックエンド障害の検出時はSDKの保留要求を`BACKEND_FAILED`で解放し、原因を画面へ表示する。

RustはPythonをWindows Job Objectで管理する案。通常終了ではshutdownを通知し、2秒を上限に停止を待ってから子プロセスを終了する。クラッシュ時はパイプEOFとJob Objectで孤児を防ぐ。WebView2の終了・プロファイル解放も追跡する。管理ソフトが既にJob Objectへ入れている環境での挙動は要検証。

## 6. 信頼境界

1. **配布物→展開先**: 署名、ペイロードマニフェスト、資産のハッシュ、私有ACL、安全な展開を使用する。独立した信頼の起点がない同梱ハッシュだけで発行者の真正性を証明できるとは説明しない。
2. **UI→ホスト**: バンドルしたトップレベルoriginだけにブリッジを許可。毎回送信元を確認し、ナビゲーション世代が変わったらセッションを破棄する。外部ページ、iframe、新規ウィンドウからの特権呼出しを拒否する。
3. **ホスト→Python**: 明示コマンドのallowlist、JSONスキーマ検証、版・サイズ制限。Pythonは登録関数だけを呼び出す。
4. **Web→サーバー**: origin/Host、cookie、接続nonce、アプリの認証・認可。サーバーのファイル操作がブラウザ端末のローカルファイル操作になると誤解させない。

既定のUIポリシー案は`default-src 'self'; script-src 'self'; style-src 'self'; img-src 'self' data:; font-src 'self'; object-src 'none'; frame-src 'none'; base-uri 'none'; form-action 'none'`。`connect-src`はWindowsで`'none'`、Webで同一originのWebSocketだけを追加する。外部コンテンツを利用する際は明示的な許可を設け、同一UIへ任意HTMLを挿入しない。

開発時もブリッジ境界を無効にしない。製品版ではdevtools、任意ナビゲーション、無断ダウンロードを既定で無効化する。wryが付加するWebView2起動引数も確認し、保護機能を弱める既定値をそのまま採用しない。

WebView2は送信元の継続確認と具体的なメッセージAPIを推奨している。Pythonを公開する以上、XSSはホスト操作に波及し得るため、ブリッジをブラウザの通常の安全境界と同一視しない。[WebView2セキュリティ指針](https://learn.microsoft.com/en-us/microsoft-edge/webview2/concepts/security)

## 7. 技術的リスク

| ID | リスク・現時点の評価 | 検証/対策・不合格時の扱い |
| --- | --- | --- |
| R-01 | 単一exe + WebView2で初回展開が重い / 高 | 初回・温起動を分けて測定。Rust先行描画、圧縮方式/ファイル数を評価。単一exe要件を黙って外さない |
| R-02 | Fixed Versionの更新責任とOS条件 / 高 | Windows 11 x64を最初に評価。Windows 10の追加ACL条件やUNC不可を確認し、対応外を明示 |
| R-03 | ネイティブ依存DLLと埋込Python / 高 | ABIとwheelを固定。代表的なネイティブ依存を実機importし、DLL探索とデータファイルを検証 |
| R-04 | ネイティブCLIのpip配置とvenv解決 / 高 | `console_scripts`を避けるwheelを試作。新規venv、空白/日本語パス、アップグレード/アンインストールを検証 |
| R-05 | キャッシュの競合・改ざん・部分更新 / 高 | 私有staging、ロック、全検証、atomic rename。既存ファイルを信頼しない。電源断/多重起動試験 |
| R-06 | 署名・Defender・企業実行制限 / 高 | 最終exeと同梱実行コードの署名状態を確認。署名済みでも許可保証なし。企業の正式な許可申請用情報を提供 |
| R-07 | origin変換・プロキシ・ブリッジ境界 / 高 | カスタムプロトコルと仮想ホストを比較し、外向き要求、遷移後IPC、iframe、CSPを検証 |
| R-08 | asyncio/GUI/終了処理のデッドロック / 中〜高 | 別プロセス、専用I/O、キュー上限、タイムアウト、killとEOFで検証。GUIでPythonを待機しない |
| R-09 | Web版のセッション混線・権限差 / 高 | 共通契約試験と2ブラウザ試験。Contextを分離。ネイティブ専用APIはcapabilityで区別 |
| R-10 | 小規模OSSが汎用ホストを保守する負担 / 高 | Tauri/PyTauriとの再利用比較、対応環境を限定。公開前に依存更新の担当と手順を用意 |
| R-11 | ライセンス・再配布条件が異なる / 高 | Rust/Python/JS依存とWebView2/VC runtimeを別管理。権利条件未確認の資材は公開成果物に入れない |
| R-12 | Macで開発を先行し、Windows固有方式の問題発見が遅れる / 高 | 開発機はMac、手元のWindowsで後から確認する体制。Macでは共通処理・UI・Web経路を先行し、Windows依存部分はG1で成立を確認してから作り込む。Windows実測まで起動・配布方式や性能を確定しない |

Microsoftは通常Evergreenを推奨するが、オフライン初回起動とインストール不要の要件からFixed Version同梱を推奨案とする。セキュリティ更新をアプリ配布者が継続管理する代償がある。[企業向けWebView2管理](https://learn.microsoft.com/en-us/deployedge/webview2-enterprise)

Windows 10のFixed Version 120以降にはunpackaged Win32アプリのACL条件があり、Fixed VersionはUNCから実行できない。Windows 11へ限定する案の根拠であり、WebView2が存在すれば全Windowsで無条件に動くという設計にはしない。[WebView2配布条件](https://learn.microsoft.com/en-us/microsoft-edge/webview2/concepts/distribution)

## 8. ADR候補一覧

以下のADRは、MVPの技術・ライセンス全体としては**候補・未採択**。名称PyYaki/`pyyaki`と、ADR-007の初期試作用Pythonの選択は2026-10-31に利用者が承認した。初期試作の選択と、配布互換性を検証した採択を区別する。その他の推奨案は設計上の提案であり、採用記録ではない。採択時は日付、結果、測定物、採択理由、廃案の影響を同じIDに追記する。

| ID | 論点 / 選択肢 | 推奨案と影響 | 採択条件・時期 |
| --- | --- | --- | --- |
| ADR-001 | tao/wry、Tauri、PyTauri再利用 | tao/wryを最初の試作対象。小さい起動経路と引換えにホスト保守が増える | G1で同じ描画順・配布条件・保守量を比較 |
| ADR-002 | PyO3埋込 / 別プロセス | 匿名パイプ + CPython別プロセス。コピーコストとプロセス数が増える | G1/G2でPython遅延・クラッシュ・終了・RPCを評価 |
| ADR-003 | Python wrapper / ネイティブCLI | Windows wheelへネイティブexeを直接配置。起動前Pythonを排除 | G1でpip/venvの配置・復旧・更新を検証 |
| ADR-004 | カスタムプロトコル / 仮想ホスト | wryカスタムプロトコル + HTTPS合成origin。セキュリティ制御が必要 | G1/G2で外向き通信、origin、CSP、JS応答APIを検証 |
| ADR-005 | 単一exeの実行方式・圧縮・検証 | **単一exe必須は確定**。方式はユーザーキャッシュ展開を推奨。容量と初回遅延あり | G1/G3で署名、全ハッシュ確認、キャッシュ競合、初回時間を確認 |
| ADR-006 | Evergreen / Fixed Version | 配布exeはFixed Version同梱。開発runは既存Evergreenまたは明示したFixed Version | G1/G3で未導入・オフライン・標準権限を確認。再配布条件を確認 |
| ADR-007 | OS/CPU/Python対応範囲 | 初期試作はCPython 3.14通常GIL版の1 minorに限定。複数版対応を後回しにして検証範囲を絞るため、2026-10-31に利用者が承認。同日Macの3.14.3で初期Web試作を確認。Windows 11 x64と配布Pythonの組合せは候補のまま | G1でWindows検証用の版を固定し、G3でnative wheel代表群を確認。配布互換性不成立なら方式・対応範囲への影響を記録して再検討 |
| ADR-008 | FastAPI / Starlette、通信契約 | FastAPI/Uvicorn + WebSocket、共通JSON契約。Python側依存を許容 | G2でWindows/Webの契約試験とセッション分離に合格 |
| ADR-009 | 起動予算と計測法 | requirements.mdの暫定値と4指標の計測。初回/再起動を分離 | Mac補助測定は[初期報告](performance-macos.md)へ記録。WindowsのG1で4指標を実測し、G4で再現可能な測定報告を公開 |
| ADR-010 | ライセンス・商標・公開名 | 名称PyYaki/`pyyaki`は2026-10-31に採用済み。ライセンスはApache-2.0を提案し、MITも比較 | 名称調査はREADMEを参照。登録可否は公開時に再確認。ライセンスは権利者が選択し、公開前にLICENSEを追加 |
| ADR-011 | 署名・来歴・更新 | 最終exeのアプリ配布者署名、SBOM、署名マニフェスト、手動更新 | G3/G4で署名後改変検出と企業環境評価。署名費用は別途判断 |
| ADR-012 | native依存とDLL探索 | 対応wheel限定、通常のsite初期化と`.pth`は既定不使用 | G3で`.pth`依存wheelを含め確認し、必要な例外だけ宣言 |
| ADR-013 | Mac開発用ホスト / Webのみ | 利用者の依頼でtao/wry/WKWebViewを試作実装。host.rs/RPCを共用し、macOSのプロセス管理とWebKit差異を保守 | 実WKWebViewのRPC・正常終了・Python停止/クラッシュ時の挙動を確認。ホストSIGKILL時の回収、時間単位の稼働、正式配布は未検証 |
| ADR-014 | MacのPython同梱 / PyInstaller / frameworkコピー | ローカル試作はpython-build-standalone + ハッシュ固定wheel + 既存Rustホスト。Rust不要の生成を事前取得資材から行うが、資材配布は別途必要 | M2で移動後のRPCと署名を確認。他Mac、Developer ID署名/公証、ライセンス・来歴の正式確認は後続。詳細は[Mac .app生成](macos-build.md) |
| ADR-015 | 要求IDの全履歴 / 有限履歴 / 交渉した単調連番 | 公開前試作では標準SDKがsequenceを交渉し、最後の番号だけで再送を拒否。旧形式は4,096件上限を維持。有限履歴の単純な破棄は古い再送を受理するため採らない。全面的なID形式変更と比べ既存クライアントを維持できるが、2モードの保守が必要 | Webと実パイプで4,100件・重複拒否、再入SDK呼出しを確認。Mac実機の連続試験とWindows更新版を確認して正式採択を判断 |
| ADR-016 | 既定アイコン / 独自アイコン設定、RC直接呼出し / embed-resource / winresource | 初期試作は利用者が選んだC案を既定として埋め込み。Mac .appはbundle資産、開発Dockは既存objc2バインディング。Windowsはembed-resourceで事前変換ICOをコンパイルする。開発時にリソースコンパイラーを要するが、実行時の画像変換やアプリごとのRust不要という配布方針は変えない | Mac生成・署名とGNU版PEの画像照合を確認。2026-11-04にWindows MSVCとExplorer/ウィンドウを実画面、タスクバーを利用者目視で確認（[記録](validation-windows-2026-11-04.md)）。Mac Dock目視は残る。独自設定と署名前のコンパイラー不要の差替えは別途判断 |
| ADR-017 | 起動中だけMoveToActiveSpace / 表示をtao Initまで遅延 / 非表示待ちと期限の分離 / 一度の前面化 / DOM準備で描画を代替 | 11-08に初期Space配置の[比較とローカル対策](validation-macos-space-2026-11-08.md)を実装。tao起動activate前にMoveToActiveSpaceを設定し、shell/失敗/終了時に元へ戻す。全Space常時表示・追加のactivate・期限変更はしない。表示遅延だけは失敗が残り不採用。過去の一度の前面化も不採用。DOM準備を描画扱いにしない | 独立したfullscreen開始は変更前0/2、最終対策3/3成功（連続対照含む9/9）、5秒遅延反復9/9、通知欠落・終了時復元、生成.app回帰を確認。全体の正式採択は未了。利用者のCodex fullscreen・Space移動/復帰・複数画面とWindows実機等は未確認。長い非表示待ちを許すならSDK/公開ready/UI期限の整合を別途検討 |
| ADR-018 | PYTHONIOENCODING / -X utf8 / worker内reconfigure / パッケージimport前の固定bootstrap | 固定bootstrapでstdout/stderrだけをUTF-8・backslashreplace・行バッファリングへ設定する試作。-Iと通常ファイルの既定encodingを維持し、パッケージimport失敗も対象にする。生バイト出力とアプリによる再設定は対象外 | 2026-11-05にWindowsの実パイプ4検査・実WebView2・通常/強制終了・日本語例外を確認（[記録](validation-worker-logs-2026-11-05.md)）。11-08にMacのビルド・WKWebView・再生成.appのUTF-8/隔離/探索パス/終了を[回帰確認](validation-macos-2026-11-08.md)。目視・実close等は未確認で、正式採択とは区別する |
| ADR-019 | EOFのみの障害検出 / 明示的異常通知 / venvの起動入口変更 | 11-05の[初回調査](validation-windows-boundary-2026-11-05.md)ではvenv launcherがwriterを保持する制約を確認。[追試](validation-windows-boundary-followup-2026-11-05.md)で埋込Pythonの全writer closeは障害通知・保留RPC拒否・回収に成功し、venvの部分closeとは区別した。追加通知や入口変更は未実装・未採択 | 生存中の部分closeへどの通知が必要かを、全writer切断・worker/launcher終了と区別して判断する。入口変更ならvenvのsys.prefix・依存探索・隔離・GIL・終了回収と先行描画順の維持が必要。元の独立再現と公式ソース、実パイプ対照は上記記録を参照 |
| ADR-020 | 非圧縮blob / ZIP等の圧縮、同一窓での展開 / 別GUI子ホスト | [単一exeローカル試作](windows-onefile.md)は新しい圧縮ライブラリを選定せず、非圧縮blobを採用。準備GDI窓→別スレッド検証/展開→同じexeのGUI子ホストで、既存の描画順/期限を維持する。全件検証のI/Oと容量、2段階の窓・ホストが増える | 試作限定。正式採択には圧縮形式との比較、1窓合流、実署名後読取、初回/キャッシュ起動の定義した計測、未導入クリーンVM、容量不足/電源断と安全な清掃を確認する |
| ADR-021 | Uvicorn reload / watchfiles / 標準ライブラリの監視親 | Web dev限定の250ms監視・spawn子と500msのページ更新確認を試作。追加依存はなくUI/Python更新を分離するが、独自の終了管理と走査コストが増える | [Mac検証](validation-web-reload-2026-11-08.md)で古いpyc、エラー復帰、RPC再接続、終了を確認。Windows実機・大規模監視・親SIGKILL回収が残り、正式採択は未了 |

2026-10-31のMac同梱方式比較（Windowsの配布方式の採択とは別）: [PyInstaller](https://pyinstaller.org/en/stable/operating-mode.html)はPythonと依存の収集を再利用できる一方、動的import・データの収集指定が必要。Python.orgのframework型をそのままvenvからコピーする方法は再配置確認が別途必要。[python-build-standalone](https://gregoryszorc.com/docs/python-build-standalone/main/running.html)はinstall_onlyの事前ビルドとライセンスファイルを提供し、既存Rustホストの後段ワーカーとして使えるため、Macローカル試作ではこちらを採用して再配置を確認した。全依存・OS・配布署名の互換性や法的な再配布確認は未完了。

ゲートの定義は[roadmap.md](roadmap.md)を参照する。

ADR-017の起動順・Space復元・独立したfullscreen開始と既存.appは、[Mac自動回帰検証](macos-regression.md)で再実行できる。専用の制御fixtureはメンテナー用exampleで、製品窓・公開API・起動期限へ検証フラグを増やさない。画面の実提示や利用者のSpace操作の検証とは区別する。

2026-11-05のログ方式比較: CPython標準機能を使い、新しい依存は追加しない。[`-I`](https://docs.python.org/3.14/using/cmdline.html#cmdoption-I)はPYTHON系環境変数を無視するためPYTHONIOENCODINGだけでは設定できない。[UTF-8 mode](https://docs.python.org/3.14/library/os.html#python-utf-8-mode)は通常のopen等にも影響するため、今回のログだけの変更には採らない。[TextIOWrapper.reconfigure](https://docs.python.org/3.14/library/io.html#io.TextIOWrapper.reconfigure)で両テキストストリームだけを設定する。worker.py内での設定では先行するPyYakiパッケージのimport失敗を捕捉できないため、ホストに埋め込む固定bootstrapで設定後にrunpyを使う。行バッファリングによる書込み頻度への影響があり、性能効果は未測定。公開APIへ任意評価を追加するものではない。資料参照日: 2026-11-05。

2026-11-04のアイコン実装比較: [embed-resource 3](https://docs.rs/embed-resource/3.0.12/embed_resource/)は既存RCのコンパイルとMSVC/GNUのツール探索を扱い、[winresource](https://docs.rs/winresource/0.1.34/winresource/)はRC生成とバージョン情報も提供する。今回は固定したICON定義1件だけなので前者を使い、ツール探索の自作を避けた。Macは[CFBundleIconFile](https://developer.apple.com/documentation/bundleresources/information-property-list/cfbundleiconfile)と[applicationIconImage](https://developer.apple.com/documentation/appkit/nsapplication/applicationiconimage)を使い、既存のobjc2 0.6/objc2-app-kit 0.3へ必要な依存を明示した。形式変換はsips/iconutilでのMac限定手順と[PillowのICO/ICNS対応](https://pillow.readthedocs.io/en/12.3.0/handbook/image-file-formats.html)を比較し、Windows用PNGのRGBA化と複数サイズの出力を同じスクリプトで扱えるPillowを開発用だけに採用した。PNG形式のWindowsアイコンには[32bpp ARGBが必要](https://devblogs.microsoft.com/oldnewthing/20101022-00/?p=12473)なため、不透明な原本にもalpha=255を持たせる。原本の再生成・デザイン変更はしない。
