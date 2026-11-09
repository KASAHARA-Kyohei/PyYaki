# Macホスト同梱wheelのローカル試作

2026-11-09にMacホストの[強制終了時の専用guard回収](validation-macos-host-cleanup-2026-11-09.md)を追加した。新しいホストでwheel/.appを再生成すると反映され、既存生成物は自動更新されない。guardは同一実行ファイルを内部モードで再実行するため別バイナリ資材は不要。署名・全ハッシュ・GUIとheadless補助の確認範囲は同記録を参照する。

更新: 2026-11-09。`62ba686`でMac版`pyyaki run`、`98d3c9f`で明示資材からのMac専用wheel組立を追加した。公開・正式配布は行っていない。[検証結果と失敗](validation-macos-wheel-2026-11-08.md)を参照する。

## 利用者の導入と起動

対応するMacホスト同梱wheelが用意されていれば、導入・起動にRust/Cargo・Node・管理者権限は不要。CPython 3.14通常GIL版とvenvを使う。Pythonランタイム・アプリ固有依存・利用者のUIはこのwheelへ同梱しない。既存のPython同梱[.app生成](macos-build.md)とは別の開発用経路である。

```console
python3.14 -m venv .venv-native
.venv-native/bin/python -m pip install /path/to/pyyaki-0.1.0.dev0-py3-none-macosx_11_0_arm64.whl
.venv-native/bin/pyyaki run --project examples/hello
```

これはローカルwheelを指定した例。依存が未導入なら通常のpipは取得に接続する。オフライン導入は、対象Mac/Python向けの依存wheelを先に集め、ハッシュ固定lockを`--no-index --only-binary=:all: --require-hashes --find-links <wheelhouse> -r <lock>`で導入し、その後このwheelを`--no-index --no-deps`で導入する。生成レポートのSHA-256も照合する。例の`macosx_11_0_arm64`は実ホストのload command由来で、macOS 11全環境での動作確認済みという意味ではない。現状の実機確認はApple Silicon / macOS 27.0.1だけ。

`--project`省略時は現在のディレクトリ。既定のワーカーPythonはCLIを起動した`sys.executable`で、venvのsymlinkを解決せず絶対パスを渡す。PATHのPythonやCargo、開発リポジトリのホストへ暗黙に切り替えない。`--python /absolute/path/to/python`も指定できるが、そのPythonにも同版のPyYakiとCPython 3.14通常GIL環境が必要。終了はウィンドウを閉じるかCtrl+C。

同梱ホストのCPU・最低OS・版・ハッシュ・実行権限を検査し、静的設定を読んでから`os.execv`でRustホストへ置き換える。PID・メインスレッド・シグナルと既存の終了処理を保ち、シェルで引数を組み立てない。業務アプリのimportは従来どおり二重rAFのshell通知後に起動したワーカーで行う。MacのCLI入口ではPython自体の初期化が先にあるため、Python初期化前描画を保証するWindowsネイティブCLIとは区別する。

pure Python wheel・editable/source導入にはホストがないため、`run`は導入案内を表示して終了1。Rust開発者は[従来の直接起動](../native/README.md#macでの開発用確認)を使う。WindowsではこのPythonラッパーの`run`を拒否する。Windowsのネイティブexe配置は[専用wheel試作](windows-wheel.md)で実装した。MacのPythonラッパーとは入口を分ける。

## メンテナーのwheel生成

ホスト開発・Cargoビルドはメンテナー側の作業。ビルド済みホストと通常のsetuptools製pure wheelを明示し、同じCPUのMacで組み立てる。生成処理がCargo・pip取得を暗黙に呼ぶことはない。

```console
cargo build --manifest-path native/Cargo.toml --release --locked
.venv/bin/python -m build --wheel --no-isolation --outdir build/pure
.venv/bin/python -m pyyaki.build_macos_wheel \
  --wheel build/pure/pyyaki-0.1.0.dev0-py3-none-any.whl \
  --host native/target/release/pyyaki \
  --output dist/macos-host
```

このMacのCargoビルドでは`SDKROOT=/Library/Developer/CommandLineTools/SDKs/MacOSX26.5.sdk`を指定した。他Macで同じSDKパスの存在を仮定しない。検証用のコピー・生成物は`test-results`配下へ置く。

- pure wheelの版・タグ・全RECORDハッシュ/サイズ・相対パスを検査する。既にホストを含むwheel、署名付きRECORD、重複・非正規パスは受け付けない。
- thin 64-bit Mach-OのCPU・macOS deployment target・リンク先を読む。arm64/x86_64の同一CPUに限定し、外部/相対ライブラリ、universal2、クロス生成は未対応。`codesign --verify --strict`とホストの`--version`で検査し、ホストは変更・再署名せずコピーする。
- `pyyaki/_native/macos/pyyaki-host`とmanifestを追加する。`Root-Is-Purelib: false`、実ホスト由来の`py3-none-macosx_*`タグ、実行権限、全RECORDを生成する。Python ABIへリンクしないのでABIタグは`none`だが、対応PythonはMETADATAと実行時検査で別に制限する。
- macOS 11以降でminor deployment targetが0以外なら、互換性を広く表示しないよう次のmajorの`major_0`へ切り上げる。最低OSの実機検証を代替するものではない。
- 新規wheelと`.build.json`だけを公開し、既存パスへ上書きしない。レポートは入力wheel/ホスト/出力wheelのハッシュ、CPU・最低OS・タグ・署名検査を残す。2ファイルを同時に公開する原子的トランザクションや、電源断復旧は保証しない。

署名確認とハッシュは、Developer ID・公証・発行者認証・再配布許諾の完了を意味しない。現在のホストはアドホック署名。wheelの公開、ライセンス/NOTICE/SBOM、信頼したCIでの版ごとの資材生成は別作業として残る。

## 回帰検証

ローカル資材だけで新しい日本語・空白パスのvenvを作り、PATHからCargoを外して導入・実WKWebView・例外DOM・終了・同版置換・アンインストールを確認する。既存venv・プロジェクトを変更せず、出力先は新規`test-results`配下に限定する。

```console
.venv/bin/python tests/macos_wheel_regression.py \
  --wheel dist/macos-host/pyyaki-0.1.0.dev0-py3-none-macosx_11_0_arm64.whl \
  --pure-wheel build/pure/pyyaki-0.1.0.dev0-py3-none-any.whl \
  --wheelhouse /path/to/local/wheelhouse \
  --requirements examples/hello/requirements-macos.lock \
  --output test-results/macos-wheel-new
```

メンテナー用の`run --smoke-test`は自動終了、`run --diagnostics`は既存診断経路を有効化する。公開アプリ向けの権限や任意実行APIを追加するものではない。検証中に自分のネイティブ窓が表示される。全13項目がそろわない実行を合格にせず、原ログと未完了をreportへ保存する。異なる版へのupgrade、他Mac/x86_64、Windows実機、親SIGKILL回収はこの試験の合格に含めない。
