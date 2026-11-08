# Macホスト同梱wheel・CLIの検証

実施日: 2026-11-08。実装は`62ba686`（同梱ホスト検査・Mac CLI exec）と`98d3c9f73f416df98fa57576d89ebb51dc8db229`（wheel組立・検証器）。最終入力と検証開始時のGitは後者、作業ツリーclean。開始時は`8341e0d`で既存変更なし、`008ec68`を含む既存履歴を維持した。既存venv・利用者のプロジェクトは置換していない。

## 環境・対象

Apple Silicon / macOS 27.0.1、CPython 3.14.3通常GIL版。FastAPI 0.162.0、Pydantic 2.14.2、Uvicorn 0.54.0、websockets 15.0.1。RustホストはSDKROOTをMacOSX26.5.sdkに指定して`cargo build --release --locked`で確認した。Cargo/SDK自体はこのMacに導入済みだが、導入・実行検証のPATHには新規venvとOS標準ディレクトリだけを設定し、Cargoが見つからないことを確認した。

成果物は以下。タグの最低OSはMach-OのLC_BUILD_VERSIONの`minos 11.0`由来で、実機確認済み最低OSではない。

```text
test-results/macos-native-wheel-final-inputs/native/
  pyyaki-0.1.0.dev0-py3-none-macosx_11_0_arm64.whl
  pyyaki-0.1.0.dev0-py3-none-macosx_11_0_arm64.build.json
```

- wheel SHA-256: `53c1ce86e0e3e7b388a69746bbc02dfce0924a4e86555f48827a8d9c85779b40`
- ホストSHA-256: `c1faac88f012ca3cb184581bc41224ec6ec64608511edabd9704e391addaf4be`
- 署名: 有効な`adhoc,linker-signed`。TeamIdentifierなし。Developer ID署名・公証ではない。

ソース・ホストのコピー、pure wheelと生成ログは同じ`final-inputs`配下に保存した。入力READMEは実装コミット時点の版であり、本記録と[現在の導入手順](macos-wheel.md)がその後の文書更新となる。生成物はGit管理外・未公開。実行に必要なPython・アプリ固有依存はwheelに含めていない。

## 結果

Python全回帰は`pytest -q -W error::DeprecationWarning`で212成功・スキップ0。Rustはlibrary 9件とbinary 5件の計14成功。Ruff check・format checkは`src examples tests native/worker_bootstrap.py`に成功した。Rust・SDK/JSの起動順・期限は変更していない。

最終`tests/macos_wheel_regression.py`は13/13項目、未完了0。15コマンドの期待終了コードを確認した。この13項目はGUI起動13回の意味ではなく、実WKWebView起動は5件である。

| 項目 | 確認内容 |
| --- | --- |
| install-pure / pure-run-error | 日本語・空白を含む新規venvへ、ハッシュ固定lockとlocal wheelのみで依存・pure wheelを導入。pip check成功。ホストなしの`run`は案内付き終了1 |
| install-native / integrity | 同版のnative wheelへ置換。platlib配置・実行権限・ホストバイト一致・manifestを確認し、導入後にcodesignのstrict検査と署名表示を保存 |
| baseline / import / hooks | CLI既定のPythonで通常、import前5秒、起動フック5秒を各1回。実WKWebViewのRPC・APP_DEMO・イベント・取消・shutdownスモーク成功。二重rAF・10秒期限・shell→Python生成・Space設定復元を段階ログで照合 |
| venv対応 | 3スモークと例外/終了の各workerログで、`sys.prefix`・`sys.executable`・PyYakiパッケージが新規venv由来と確認。CLIがexecしてもPopen PIDとhost_started PIDが一致 |
| corrupt-host | 自分のvenv内のホストコピーだけを破損させ、exec前のハッシュ不一致で終了1を確認。その後元バイトへ復元 |
| failure-dom | コピーしたアプリへ日本語・`<img src=x>`を含むimport例外を注入。元traceback、worker_failed/backend_failed、実WKWebViewの障害DOMに日本語が文字として表示されimg要素を作らないことを診断経路で確認 |
| shutdown | welcome後、自分で起動・ログで識別したCLI/ホストPIDへSIGTERM。終了0とワーカーのshutdownフックを確認 |
| replace-with-pure / reinstall-native | 同版native→pureで同梱ホスト/manifestが消えること、同版native再導入後のバイト一致・署名を確認 |
| uninstall | pip uninstall後、CLI・ホストとimportが残らないことを確認。他のvenvを触っていない |

failure-domの終了0は、診断用UIが障害表示を確認した結果を返して自動終了したコードであり、通常運用のアプリ失敗を成功終了へ変更したものではない。目視による画面の実提示確認とは区別する。単体試験ではCPU/最低OS/版/ハッシュの不一致、外部ライブラリ、不正wheel/RECORD、上書き拒否・公開失敗時の自分のレポート回収も確認した。

最終検証が記録したCLI・ホスト・ワーカー・コマンドの25 PIDは終了後に残存なし。全子孫・全WebKitの完全な資源回収や親SIGKILL時の保証ではない。署名・OSの保護機能は無効化していない。

## 原ログと失敗

原ログはすべてこのMacのGit管理外に保存している。

```text
test-results/native-wheel-final-pytest.log
test-results/macos-native-wheel-20261108/cargo-release.log
test-results/macos-native-wheel-20261108/cargo-test.log
test-results/macos-native-wheel-final.log
test-results/macos-native-wheel-final/
  report.json / process-check.txt
  signature-display/command.log
  baseline|import|hooks|failure-dom|shutdown/
    launcher.log / host.log / worker.log
  日本語 space venv/          # 終了時はPyYakiをuninstall済み
```

検証器の失敗を後の成功で上書きしていない。

1. `macos-native-wheel-first`: 4項目完了後、起動ログ検査関数の引数名を誤りTypeError。ネイティブ通常スモーク自体は終了0だったが、未評価としてreportはfailed。`expect_macos_restore`を既存契約の`require_space_restore`へ修正した。
2. `macos-native-wheel-second`: 8項目完了後、エラーDOMの結果なしでStopIteration。検証用JSのIPC送信先を既存の`window.ipc`へ修正した。
3. `macos-native-wheel-third`: 同じく8項目完了。元helloのtop-level `await pyyaki.ready()`より後へ監視コードを追加していたため、失敗イベントの後に監視登録していた。コピー内で監視登録をawaitより前へ移した。2・3回目はwatchdogで自分の障害窓を終了し、これを合格扱いにしていない。
4. `macos-native-wheel-fourth`: 13/13成功。その後、実装コミットとソース・ホストの新規コピーからwheelを再生成した`macos-native-wheel-final`でも13/13成功した。

各試行の`.log`、同名ディレクトリの`report.json`・`failure.log`と各起動の原ログを残している。2・3回目も元Python例外・backend_failedは記録され、製品の期限・起動順・セキュリティ設定を変えて回避していない。

## 残る確認と次の候補

- Windowsのネイティブ`pyyaki.exe`配置、Python初期化前のCLI分岐、pip/venv解決・実機検証。MacのPythonラッパーを流用して合格にはしない。
- 他Mac、x86_64、deployment target付近の古いOS、Rust/SDK未導入のクリーンMac、ネットワークを実際に切断した端末。今回はpipの`--no-index`とPATH制限による確認である。
- 異なるPyYaki版をまたぐupgrade/downgrade、venv外・`pip --user`、venv移動、署名/公証/組織の実行許可。今回は同一版のpure/native置換であり、異版upgradeと呼ばない。
- 正式ライセンス・再配布通知・SBOM・信頼したCI資材・PyPI公開。wheelハッシュやアドホック署名だけで発行者を認証した扱いにしない。
- 親SIGKILLや孫プロセスの完全回収、長時間・性能、利用者の実画面。.appは今回再生成していない。

Macローカル試作としては[導入・生成・再実行手順](macos-wheel.md)で一区切り。次はWindowsネイティブCLIの最小wheel配置と、Pythonを先に初期化しないvenv探索を別に実装・実機確認する候補となる。
