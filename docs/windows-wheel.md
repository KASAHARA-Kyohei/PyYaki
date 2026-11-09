# WindowsネイティブCLI同梱wheel（ローカル試作）

更新: 2026-11-09。CPython 3.14通常GIL版・Windows x64のvenv向け。未公開のローカル資材であり、PyPIから取得できるという意味ではない。[要件](requirements.md)のFR-05/07全体やクリーン環境・正式配布の受入を完了した扱いにはしない。

## 利用者の導入と起動

対応Python、pip、事前取得したwheelhouse、利用可能なWebView2 Runtimeを用意する。Rust/Cargo/MSVC、管理者昇格、導入時の実行ファイル自動取得は不要。Pythonランタイム・WebView2・アプリ固有依存はwheelに含まない。

```powershell
python -m venv "日本語 space venv"
& ".\日本語 space venv\Scripts\python.exe" -m pip install --no-index --require-hashes --find-links C:\local\wheelhouse -r examples\hello\requirements-windows.lock
& ".\日本語 space venv\Scripts\python.exe" -m pip install --no-index --no-deps C:\local\pyyaki-0.1.0.dev0-py3-none-win_amd64.whl
& ".\日本語 space venv\Scripts\pyyaki.exe" run --project examples\hello
```

上記の資材パスは利用者が用意する例。通常のpure wheel・editable導入にはWindowsネイティブCLIを含まない。Pythonラッパー経由の`run`は引き続き拒否する。同版のnative/pure wheel置換ではpipが旧CLIを削除して新しい入口を配置する。

- `--help`、`--version`、`run --help`はPythonなしで出力するコンソール用ネイティブexe。ターミナルでの利用を対象とし、ExplorerからCLIを直接起動した際のconsole非表示は保証しない。
- `run`は同じプロセス・メインスレッドで既存のGUI処理へ入る。ネイティブ描画→HTMLの二重rAF→shell通知→Python生成の順と10秒期限を維持する。ホスト・CLIのハッシュ検査は描画前であり、速度目標を保証しない。localhostのHTTP/WebSocket待受は作らない。
- 自身の`Scripts/pyyaki.exe`と親の`pyvenv.cfg`から、`Scripts/python.exe`と`Lib/site-packages`を決める。PATHのPython・VIRTUAL_ENVは参照しない。`run --python`はメンテナー用GUIホストだけで使い、導入したCLIでは拒否する。日本語・空白引数はOS文字列のまま渡し、shellで結合しない。
- manifest、METADATAの版、CLI自身と同梱GUIホストのサイズ・SHA-256・x64 PE/subsystem、Pythonの配置を静的に検査する。通常GIL/CPython 3.14の実判定はshell後の固定worker bootstrapでも行う。ハッシュは発行者認証ではなく、同じ権限の改変者への防御境界ではない。
- `dev`・`build windows`は同じvenvのPythonで`-I -m pyyaki.cli`を起動し、出力・終了コードを引き継ぐ。`dev`はブラウザを自動で開かない。`build windows`は従来どおり全資材の明示が必要で、自動取得・Cargo実行をしない。
- `run`のCtrl+C/Ctrl+BreakはGUIの通常終了経路へ通知する。`dev`/`build`ではCLI親がconsoleイベントで先に終了せず、同じconsoleのPythonへ配送し、その終了を待つ。Python生成前にCLI自身を専用Jobへ割り当て、唯一の非継承Job handleをプロセス終了まで保持するため、CLI強制終了時も委譲した子孫を回収する。強制終了では終了フックを保証しない。runの既存worker Job・ハンドル許可リストは維持する。
- WindowsのWeb devはポートを排他的に予約する。reloadでは共有listenerの世代ごとのIOCP再登録を避けるselector loopを使い、asyncio pipe/subprocessと512を超えるsocketは扱えない。そのような開発では`--no-reload`を使う。WindowsのPython・設定監視は内容ハッシュも読み、同サイズ・同mtimeの保存を検出する。大規模ツリーの負荷は未測定。
- venv外、`pip --user`、移動したvenv、他CPU/他Pythonは未対応。CLIが対応Pythonを静的に特定できない場合は明示エラーとし、別Pythonへ切り替えない。対応OS/WebView2の最低版の保証も未完了。

## メンテナーの生成

メンテナー側だけRust/Cargo 1.89以降・MSVC/Windows SDKが必要。既定toolchainは変更せず、導入済みの対応版を明示する。以下は今回の端末の例。

```powershell
cargo +1.89.0 build --manifest-path native/Cargo.toml --release --locked
.venv\Scripts\python.exe -m build --wheel --no-isolation --outdir test-results\wheel-input\pure
.venv\Scripts\python.exe -m pyyaki.build_windows_wheel --wheel test-results\wheel-input\pure\pyyaki-0.1.0.dev0-py3-none-any.whl --host native\target\release\pyyaki.exe --cli native\target\release\pyyaki-cli.exe --output test-results\wheel-input\native
```

組立器は同版のpure wheelの全RECORD・版・タグ・パスを検査し、明示exeのx64 PE/subsystemと`--version`出力を検査する。GUIホストはWindows subsystem、CLIはconsole subsystem。Python ABIへリンクしないためタグは`py3-none-win_amd64`、Python範囲はMETADATAと実行時判定で別に限定する。

CLIを`pyyaki-<version>.data/scripts/pyyaki.exe`、GUIテンプレートを`pyyaki/_native/windows/pyyaki-host.exe`へ入れ、同名のPython console/gui entryを除く。`Root-Is-Purelib: false`と全RECORDを作り直す。CLIのrunにも同じGUIコードをコンパイルするので、wheelにはGUIコードを持つ2つのexeがある。プロセスの中継を増やさず既存のJob・PID/段階ログを維持する代わりに、容量と二重ビルドの保守負担がある（ADR-003）。

既存出力へ上書きせず、新規wheelと`.build.json`を生成する。レポートは入力wheel/CLI/ホスト/出力wheelのハッシュとタグを保持する。hard linkを利用するため、そのファイルシステム・実行権限が必要。2ファイル同時の原子的公開・電源断復旧は保証しない。Authenticode検査・実署名、信頼したCIの資材来歴、LICENSE/NOTICE/SBOM、ライセンス正式採用・公開・依存バイナリ再配布は別判断として残る。

## 再確認

検証器はtest-resultsの新規コピーだけを変更し、専用の非表示console内で自分の子だけに実CTRL_C_EVENTを配送する。Mac専用の検証器をWindowsで実行しない。今回の環境ではPlaywright専用Chromiumが未取得だったため、Web検証では既存Edgeを`--browser-channel msedge`で明示した。sandboxは有効にする。

```powershell
.venv\Scripts\python.exe -X utf8 tests\windows_wheel_regression.py --wheel test-results\wheel-input\native\pyyaki-0.1.0.dev0-py3-none-win_amd64.whl --pure-wheel test-results\wheel-input\pure\pyyaki-0.1.0.dev0-py3-none-any.whl --wheelhouse C:\local\wheelhouse --requirements examples\hello\requirements-windows.lock --output test-results\windows-wheel-new
.venv\Scripts\python.exe -X utf8 tests\browser_dev_reload.py --cli "test-results\windows-dev-venv\Scripts\pyyaki.exe" --browser-channel msedge --output test-results\windows-dev-new
```

wheel検証器は最後にPyYakiをuninstallするため、Web確認には別途導入した検証用venvを指定する。通常/import前5秒/起動フック5秒の各3回、実WebView2のスモーク・UTF-8・例外DOM・終了・Job回収、破損/欠落/版・CPU不一致、同版再導入/pure置換/削除を確認する。異版upgrade・物理的な画面提示・実際にネットワークを切断した環境は別試験である。
