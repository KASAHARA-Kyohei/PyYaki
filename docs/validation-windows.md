# Windows開発用ホストの実機検証

実施日: 2026-11-03（日本時間）。既存ホストの検証であり、Windows単一exe生成やG1/G2全体の合格判定ではない。手順の正は[native/README.md](../native/README.md)、現在地は[ロードマップ](roadmap.md#現在地と次の作業)、受入条件は[要件定義](requirements.md)を参照する。

## 環境と準備

| 項目 | 確認結果 |
| --- | --- |
| OS | Windows 11 Pro 25H2、10.0.26200.8246、64bit |
| CPU | Intel Core i5-8250U、x64 |
| Python | CPython 3.14.10、MSC v.1944、AMD64。`Py_GIL_DISABLED=0`、`sys._is_gil_enabled()`はtrue |
| Rust | 検証用に1.89.0 `x86_64-pc-windows-msvc`を追加。既定stableの1.84.1は変更せず、`+1.89.0`で選択 |
| MSVC | 既存のVisual Studio 2022 Community / Build Tools 17.14.31、C++ x64/x86 Tools格納先14.44.35207、link.exe 14.44.35226.0 |
| Windows SDK | 既存の10.0.22621.0 / 10.0.26100.0 |
| WebView2 | 実GUIは156.0.4314.58。初期照会の登録版は147.0.3912.98で、検証中に登録版も156へ変化。追加導入は行わず、更新原因は追跡していない |
| 実行権限 | 今回のトークンは非管理者（Administratorロールfalse）。事前導入済みMSVC/SDKを使用したビルド・実行であり、MSVC導入時の管理者権限不要を検証したものではない |
| ソース | 開始時`80bffc8`、既存変更なし。ホスト本体とCargo.lockの変更なし |

不足ツールの導入は利用者の承認を受けて行った。Pythonは公式x64インストーラーをユーザー専用の`%LOCALAPPDATA%\Programs\Python\Python314`へ導入し、PATH・既存Python・launcherを変更せず、free-threaded版は選択していない。Rustは別ツールチェーンとして追加した。

PythonインストーラーはAuthenticodeのStatusがValid、署名者はPython Software Foundation。SHA-256は`92480239264744fe0386a1bd8275786fefa126e3129e56a68e22b52c73329965`。ホストrelease exeのSHA-256は`2fe9b03853fb7746ba7dc45736a8965cc96738aa8edc325bcc4ee9f206ff6554`。

公式資料の確認日も2026-11-03。[Microsoft Rust環境手順](https://learn.microsoft.com/en-us/windows/dev-environment/rust/setup)、[Rust導入手順](https://rust-lang.org/tools/install/)、[Python Windows配布](https://www.python.org/downloads/windows/)、[Python Windows利用手順](https://docs.python.org/3.14/using/windows.html)を参照した。Defender、SmartScreen、プロキシ、シンボリックリンク作成権限などの設定変更は行っていない。

## ビルドと再確認

この端末では`py` launcherがなかったため、初回venv作成にPythonの絶対パスを使った。PowerShellでリポジトリ直下から実行する。以下は実行済みの開発用手順で、配布用lockや単一exe生成の例ではない。

```powershell
& "$env:LOCALAPPDATA\Programs\Python\Python314\python.exe" -m venv .venv
.\.venv\Scripts\python.exe -m pip install -e '.[dev]'
.\.venv\Scripts\python.exe -I -c "import pyyaki; print(pyyaki.__version__)"
cargo +1.89.0 build --manifest-path native/Cargo.toml --release --locked
.\native\target\release\pyyaki.exe run --project .\examples\hello --python .\.venv\Scripts\python.exe
```

`pip install`は完了と終了コードを確認してからホストを起動する。初回は導入中に起動してしまい、`ModuleNotFoundError: No module named 'pyyaki'`で失敗した。導入完了後の`-I`付きimport確認と再起動で解消した。これは実装の修正による解消ではない。

Python依存はWindows上で開発用に解決した。主な版はFastAPI 0.162.0、Starlette 1.10.0、Pydantic 2.14.2 / pydantic-core 2.50.2、Uvicorn 0.54.0、websockets 15.0.1、pytest 9.2.0。全版はローカルの`test-results/windows-20261103/requirements-observed.txt`へ保存した。Mac用`requirements-dev.lock`を流用していない。これはハッシュ固定したWindows配布用lockではない。

## 確認結果

| 確認 | 結果と証拠・限界 |
| --- | --- |
| MSVC releaseビルド | `cargo +1.89.0 build ... --release --locked`成功。実exeのリンクを確認 |
| 実GUI | 実WebView2でhello画面・日本語・「Python 接続済み」をスクリーンショットとアクセシビリティ情報で確認 |
| RPC・アプリエラー | あいさつ結果と`APP_DEMO`表示を確認。通常の3秒処理・進捗0/3から3/3を確認 |
| 処理中UI | 検証用コピーだけ待機を10秒に変更。`WORKING`・進捗5/10の状態でカウンター0→1を確認。製品サンプルは変更していない |
| 実スモーク | `--smoke-test`で`native_smoke`のok=true、終了コード0。mode、capabilities、あいさつ、アプリエラー、並行RPC、イベント順序、購読解除、取消を確認 |
| 通常終了 | 閉じるボタン操作後、事前に列挙したホスト・Python・WebView2・conhostの10 PIDに残留なし |
| 5秒のimport前遅延 | 検証用コピーで再実行時にスモーク成功。初回はSDKの10秒接続期限で失敗しており、安定性の合格にはしない（後述） |
| Pythonより先のHTML | 15秒のimport前遅延の別ケースで、Pythonが接続準備中のHTMLを実画面で確認。GDI起動画面の実提示順序・時刻は録画未確認 |
| 大量stdout | import時に1,245,184バイトをstdoutへ出力してもスモーク成功。専用`worker.log`のバイト数と一致 |
| 終了フック | 通常は`entered/completed`のファイルを確認。60秒待機するフックでは`entered`のみ、2秒期限のワーカーログとPython回収を確認。終了待ち時間の外部精密測定は未実施 |
| ホスト強制終了 | パスと引数で照合した自分の検証ホストだけを強制終了。3秒後、事前に列挙した10 PIDすべて消滅。Pythonワーカーとvenv launcherのJob Object回収をこのケースで確認 |
| import失敗 | 意図的な`RuntimeError('PYYAKI_IMPORT_PROBE')`をworker.logで確認。失敗画面・閉じる操作・Python残留なしを確認 |
| 初期化期限超過 | import前15秒待機で`UI/worker initialization timed out (10 seconds)`を表示。Python残留なし、閉じる操作可能 |
| 接続後のPython異常終了 | 検証用コピーの明示コマンドが`os._exit(17)`を実行。失敗画面へ移行し、既知のPython PIDが消滅。保留要求の拒否時間の精密測定は未実施 |
| 待受 | hello実行中のホストと子孫PIDに対する`Get-NetTCPConnection`でTCP Listenは0件。パケットキャプチャやネットワーク切断試験の代わりにはしない |
| Rust検査 | MSVC releaseテスト6件成功、doc-test 0件。全ターゲットClippy（`-D warnings`）とfmt成功。Mac固有テストは含まない |
| Python検査 | 修正後67件成功・14件スキップ。Ruff lint/format成功 |

スモーク再実行は次のコマンドで行う。WindowsではGUI subsystemのため、終了コードの確認にPowerShellの`Start-Process -Wait -PassThru`を使える。

```powershell
$smoke = Start-Process -FilePath .\native\target\release\pyyaki.exe -ArgumentList @('run', '--project', '.\examples\hello', '--python', '.\.venv\Scripts\python.exe', '--smoke-test') -WindowStyle Hidden -Wait -PassThru
$smoke.ExitCode
cargo +1.89.0 test --manifest-path native/Cargo.toml --release --locked
cargo +1.89.0 clippy --manifest-path native/Cargo.toml --release --locked --all-targets -- -D warnings
.\.venv\Scripts\python.exe -m pytest -q -W error::DeprecationWarning
```

## 失敗の調査と修正

- 初回の共通テストは42件成功、12件スキップ、2件失敗、24件エラー。Web共通fixtureが必ずsymlinkを作るため、WindowsのWinError 1314でRPC検査まで巻き込んでいた。リンク専用の検査を独立させ、1314の場合だけ理由付きでスキップするfixtureを追加した。Mac bundleのリンク検査にも同じfixtureを使う。他のOSErrorは失敗のままにする。
- ポート競合時、このWindowsではWinError 10013（アクセス拒否）を返した。CLIは既にアドレスとOSエラーを表示していたが、テストがPOSIX型のEADDRINUSEだけを想定していたため、Windowsのこの結果も確認する。すべてのアクセス拒否をポート競合へ置換する修正は行っていない。
- 修正後のスキップ14件は既存のWindows対象外12件と、権限不足のリンク検査2件。リンク境界検査をこの端末で実行済みとはしない。ホスト・SDK・Python製品コードの修正はない。
- 5秒遅延の初回スモークは`PyYakiError: Connection timed out`。SDKの接続タイマーは10秒で、ホストの初期化期限も10秒。ビルド検査と同時実行していたが、負荷との因果やPython初期化の内訳は未確定。同一fixtureの再実行は成功した。期限を延ばして合格にする変更はしていない。
- 初回の終了直後0.5秒の標本ではWebView2が残る場合があった。後続照会で消滅し、再実行では最大10秒の観察枠を設け、4ケースとも残留なし（終了後の回収観察は約0.4〜0.55秒）。初回の消滅時刻は測定していない。[Microsoftのプロセス説明](https://learn.microsoft.com/en-us/microsoft-edge/webview2/concepts/process-model)も参照したが、遅延の原因は確定していない。

テスト修正コミット: `49a9bd6`（`test: Windows権限下で共通検査を実行可能にする`）。本記録と関連文書のコミットはGit履歴で追跡する。

文書検査はAGENTS/README・native README・docsの計12ファイル、ローカルリンク/見出し92件とコードフェンスを確認し、`git diff --check`も成功した。文書反映後に無関係な実装試験は繰り返していない。

## 保存先と残課題

生ログ・検証用コピー・補助スクリプト・PID記録・JUnit XML・依存版・ハッシュは、このWindowsの`C:\Users\user\dev\PyYaki\test-results\windows-20261103\`へ保存した。Git管理外で、Macへ自動転送しない。各instanceの元ログとWebView2プロファイルは`%LOCALAPPDATA%\PyYaki\prototype\<instance>\`に残す。`probe-results-first.json`に初回失敗を保存し、`probe-results.json`に再実行を保存した。

残る事項は次のとおり。今回の結果で要件の受入条件やADRを一括採択しない。

- 5秒遅延時に接続期限へ達した問題の再現条件と時刻内訳。負荷との因果も未確定。GDI→HTML→Pythonの実提示を録画で確認し、外部t0から起動4指標を測定すること。
- import/異常終了の画面は`failed to fill whole buffer`とログパスに留まり、具体例外はログ参照が必要。長いパスも折り返さない。原因の分かりやすい表示は未改善。
- 外部・別ページ遷移、新規ウィンドウ、ダウンロード、iframe拒否の実WebView2試験。無効プロキシ、ネットワーク切断、企業ポリシー、Defender/SmartScreenとの共存の明示試験。設定は無効化していないが、すべての効果を検証済みとはしない。
- 不要ハンドルの非継承やJob Object割当失敗の負例、任意のアプリ孫プロセス、時間単位の稼働・高負荷・資源計測。今回のPython/venv launcher回収から一般化しない。
- Windows単一exe組立・展開、Python/Fixed Version WebView2同梱、Rust不要のwheel/CLI配置、Python/WebView2未導入クリーン環境での初回オフライン起動、署名・正式配布・再配布条件。いずれも未実装または未検証。
