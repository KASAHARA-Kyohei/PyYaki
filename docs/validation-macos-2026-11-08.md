# Windows変更取り込み後のMac回帰確認（2026-11-08）

実施日: 2026-11-08（日本時間）。[引き継ぎ手順](handoff-macos-2026-11-08.md)に従い、共通検査、実WKWebView、起動反復、ログ・例外・終了、Python同梱.appの再生成と再配置を確認した。**初回のSpace関連の起動失敗を保持し、目視・実クリック・正式配布等の未確認範囲を残す。G1/G2/G3全体の合格や性能達成の記録ではない。**

この記録後、利用者の追加依頼で行ったADR-017の[初期Space配置の比較・対策](validation-macos-space-2026-11-08.md)は別記録とする。以下の件数・失敗・未確認事項は対策前の結果として保持する。

## 対象・環境

着手時の`main`はクリーン。`git fetch origin`後にHEADとorigin/mainの左右差分が0/0であることを確認し、`git pull --ff-only origin main`はAlready up to dateだった。対象は`008ec68cd48e9122574f38a611deb7b8740c601b`。`008ec68`を含み、引き継ぎの基点`8986fb5`、`755630e`、`f40bdc1`、`430240c`、`dd9c66f`も履歴内にある。製品ソースは変更せず、このHEADからホストとframework wheelを再生成した。

| 項目 | 今回の実測・照合値 |
| --- | --- |
| OS / CPU | macOS 27.0.1、build 26A434 / Apple M2、arm64 |
| 開発Python | 既存`.venv/bin/python`、CPython 3.14.3、GIL有効、`Py_GIL_DISABLED=0` |
| Rust / Cargo | 既存1.89.0、`stable-aarch64-apple-darwin`。既定toolchainは変更なし |
| SDK | 既定の照会先はCLTのMacOSX.sdk→27.0。既定SDKのリンク失敗後、実在する26.5をコマンド単位のSDKROOTで指定 |
| 同梱Python | python-build-standalone 3.14.3+20260211、aarch64、通常GIL |
| releaseホスト SHA-256 | `d919d1f3e6a5d046b927ecbc7293eedc23bec59d138dabd120e3f68eafafa406` |
| 再生成framework wheel SHA-256 | `8c49f4cf0b2653217e5b16b869107d921ce3a53d70ba52d484688d5d06104c7a` |

保存先はすべてGit管理外の`test-results/macos-regression-20261108/`（以下「保存先」）。既存dist、過去の.app・ログ・venv・元runtimeは変更していない。検証用アプリ、故障注入、補助スクリプトは保存先のコピーのみ。終了操作はPopenの自分の子、またはPIDと完全なコマンドを照合した自分のホストだけに送った。OS・プロキシ・セキュリティ保護の設定変更はない。

## Python / Rust検査

| 検査 | 採用結果 | 元ログ |
| --- | --- | --- |
| `pytest -q -rs -W error::DeprecationWarning` | **145成功、スキップ0、失敗0** | `recheck/pytest.log` |
| Ruff check / format check | 成功。src・examples・tests・native/worker_bootstrap.pyを対象 | `recheck/ruff-*.log` |
| Cargo fmt | 成功 | `recheck/cargo-fmt.log` |
| Mac `cargo test --locked` | **13成功**（lib 9、main 4）、ignore/失敗0、doc-test 0 | `sdk26/cargo-test.log` |
| Mac Clippy | `--locked --all-targets -- -D warnings`成功 | `sdk26/cargo-clippy.log` |
| Mac release build | `--locked`成功 | `sdk26/cargo-release.log` |
| framework wheel | 同じHEADから`build --wheel --no-isolation`成功 | `wheel-build.log` |

Windows形式試験と`test_onefile_probe.py`のWindows検証器importを含め、Macの収集・実行は成功した。Windows専用Rustモジュールの実行、Windows GNU型検査、MSVCリンク・Windows実機試験は今回実施していない。以前のWindows件数を上表へ加えていない。

初回失敗も保持した。sandbox内のpytestは142成功・3失敗で、localhostのbindがOperation not permittedだった。Cargoはcrates.ioへの名前解決制限でtest/Clippy/buildが失敗した（直下の各logとchecks.json）。必要な権限での再検査ではPythonが成功した一方、既定SDK 27の`libSystem.B.tbd`の`arm64e.x1`を既存リンカーが解釈できず、Rustの3コマンドが失敗した（`recheck/cargo-*.log`）。その後、26.5指定で上表に成功した。SDKファイル、xcode-select、既定Rustを変更する回避策は使っていない。`environment.json`には、SDKパス照会は成功したが`xcrun --show-sdk-version`はSDKを発見できず失敗した結果も残した。

## 実WKWebView・起動反復

最初の開発ホストスモーク（PID65008、`smoke/`）は**失敗**。HTML/SDK取得は200、接続開始は約1,015ms、`scheduled`はDOM hiddenだった。その後はactive Space外・Visible遮蔽ビットなしで、frame1/frame2・shell・Python生成がないまま約10,469msにUI期限へ到達した。35秒の外部watchdogが自分のホストへSIGTERMを送り、ホスト終了コード1、観測プロセス残留0。Pythonを開始していないのでworker.logはない。

これは[ADR-017の既知経路](startup-diagnostics.md#初期描画待ちとspaceの比較2026-11-04)と整合するが、今回はCodexの通常/フルスクリーンの利用者申告を取得できておらず、モード別の対照実験ではない。後続の通常サイズの検証窓でactive Space・Visible遮蔽ビットとPython開始を確認してから反復を行った。通常表示から開始できたと断定せず、後続成功で初回失敗を解消扱いにしない。

```console
.venv/bin/python tests/startup_probe.py --binary native/target/release/pyyaki --runs 3 --delay 5 --output test-results/macos-regression-20261108/startup
```

**通常3/3、import前5秒3/3、起動フック内5秒3/3、計9/9成功**。開始時の作業ツリーはクリーン。全件でスモークok・終了コード0・watchdogなし。scheduled/frame1/frame2→shell→Python生成→worker6段階→welcomeを照合し、UI/worker期限はともに10,000ms。shell時は全件active Space内・Visible遮蔽ビットあり、失敗段階の記録は0。スモークは実WKWebViewでRPC、アプリエラー、並行要求、イベント順序・購読解除、取消を確認した。

| ケース / PID | shell通知 | Python生成完了→worker通知 | import区間 | 起動フック区間 | welcome送信 |
| --- | ---: | ---: | ---: | ---: | ---: |
| baseline-0 / 65983 | 450.3 | 114.6 | 8.5 | 0.1 | 584.1 |
| baseline-1 / 65997 | 457.8 | 94.2 | 4.8 | 0.1 | 566.8 |
| baseline-2 / 66010 | 478.3 | 100.9 | 5.0 | 0.1 | 594.3 |
| import-0 / 66023 | 468.5 | 95.4 | 5019.3 | 0.2 | 5599.1 |
| import-1 / 66034 | 457.0 | 97.9 | 5024.4 | 0.1 | 5594.2 |
| import-2 / 66054 | 474.4 | 95.0 | 5022.5 | 0.2 | 5598.5 |
| hooks-0 / 66072 | 456.4 | 100.6 | 6.3 | 5001.7 | 5578.9 |
| hooks-1 / 66091 | 473.7 | 100.9 | 5.3 | 5002.3 | 5602.5 |
| hooks-2 / 66104 | 464.8 | 100.4 | 4.9 | 5002.4 | 5594.1 |

単位ms。shell/welcomeはホスト入口からの受信・送信記録、他は通知間の差。スケジューリング・IPCを含み、画面の実提示やNFRの性能達成の値ではない。ビルド・重い試験とは重ねていない。元ログは`startup/`の各ケース、集計は`startup-summary.json`。

## UTF-8・例外表示・終了・有限拒否ログ

| ケース / ホストPID | 今回確認した範囲 |
| --- | --- |
| 開発用通常 / 65619 | stdout/stderr/loggingの日本語・絵文字・孤立サロゲート、起動/終了フック、worker PID65629の親子関係。照合したホストへのSIGTERMで終了コード0、観測プロセス残留0 |
| import例外・目視用 / 66148 | app_import_started→worker_failed→backend_failed。DOM診断ok、完全なtraceback。画面取得はツールのタイムアウトで失敗し、180秒watchdogで自分のホストを終了。終了コード1、残留0。利用者close成功にはしない |
| import例外・自動診断 / 76242 | 別コピーで同じ例外を注入。例外名・日本語/絵文字・停止段階・host/workerログ案内、pre-wrap、img要素非生成をDOMで照合。期待した障害の診断結果okで自動終了コード0、watchdog/残留なし。正常起動の成功とは区別 |
| 拒否操作 / 76287 | 同一ページ遷移・新規窓を各2回、1MiB超のIPCを2回送り、それぞれnavigation_rejected・new_window_rejected・native_ipc_rejected(reason=message_too_large)が各1件。場所を維持し後続RPC成功、終了コード0 |

通常のworker.logは129 bytes、2つの例外ログは1,347 / 1,352 bytesで、いずれもUTF-8 strict decode成功。`\ud800`/`\udcff`のエスケープ、日本語logging、完全なtracebackを照合した。`-I -B`を維持し、stdout/stderrはutf-8/backslashreplace。通常ファイルの既定encoding（UTF-8）とutf8_mode（0）は新しい隔離Pythonの対照と一致した。大量ログ・起動フック例外・パッケージimport前例外は今回のpytest内の実ワーカー試験でも確認している。

拒否操作は検証用JavaScriptからのプログラム操作で、trustedな実クリックではない。download属性のCSSリンクも試したがdownload_rejectedは0件で、downloadハンドラーへ到達した拒否は**未確認**。origin拒否、全URL/WebKit条件、ダウンロード先全体の監視も未実施。CSPや拒否条件は変更していない。

Computer Useはraw実行ファイルのアプリ解決に失敗し、検証用.appの画面取得も`timeoutReached`で失敗した。後者のツール応答は約14,263秒後だったが、対象ホストは180秒watchdogで終了済み。この待ち時間を連続稼働や性能値に使わない。Codex自身の表示照会もツールの安全制限で拒否された。目視確認、実クリック、赤いcloseボタン、Dock/FinderのC案表示は未確認のまま。ツールの応答は`tool-errors.json`へ転記し、ホスト障害と区別した。

## Python同梱.appの再生成・署名・再配置

ホストとwheelを今回のHEADから作り、既存Mac用ハッシュ固定lock・15依存wheelを再利用した。初回の既存`/private/tmp/pyyaki-standalone3143/python`コピーではencodings配下が空で、`Failed to import encodings module`となり生成失敗。元runtimeはそのまま残し、初回コピーと`bundle-build.log/json`を保持した。欠落原因は未確定。

[Mac生成手順](macos-build.md)で指定された公式アーカイブを同じ版のまま新規取得し、SHA-256 `348647e4c13b662f7b0d218ccf472688038679815fb1a429ca664b7dce324237`との一致を確認して新規展開した。取得以降の.app組立はローカルwheelだけを使うno-index経路で成功した。今回の資材再取得をオフラインビルド成功とは呼ばない。

| 検証 | 結果 |
| --- | --- |
| 新規`dist/Hello PyYaki.app` | 生成成功。アドホック署名、`codesign --verify --deep --strict --verbose=2`成功 |
| 全manifest照合 | 通常ファイル**2,508件**、symlink **8件**、欠落/追加/サイズ/ハッシュ/リンク不一致0。pyc 0、staging残留0 |
| 実WKWebViewスモーク / PID76088 | 同梱PythonでRPC・イベント/解除・取消を確認。終了コード0、watchdog/観測残留なし。起動後も署名と全manifest一致 |
| 日本語パスへの移動 / PID76489 | 自分の生成物を`relocated/再配置 鯛焼き/Hello PyYaki.app`へ移動。新規生成資材のruntime/projectコピーだけを一時renameし不在を確認、system PATHと存在しないPYTHONHOME/PYTHONPATHでスモーク成功。終了コード0、watchdog/観測残留なし。終了後にコピーの入力パスを復元 |
| 再配置・起動後の照合 | 同じ2,508件/8件が全一致、codesign strict成功、pyc 0 |
| 既定アイコン | CFBundleIconFile=PyYaki.icns、ハッシュ`cbff6b87993def10c02e807c198a2143d8c944c546de25fbc59cf7d9a157c211`が保存済みC案資産と一致。Dock/Finder目視は未確認 |

別のコピーアプリへ探索パス・UTF-8・終了フックの観測だけを加え、`dist/Instrumented PyYaki.app`を生成した。初回は検証コードがビルド時importでも孤立サロゲートを出力し、bootstrap外のstrict UTF-8でUnicodeEncodeErrorとなった（`instrumented-build.log`）。これは検証コードの失敗。元コードをapp-before.pyへ保存し、観測を起動フックへ移した再試行で生成成功した。製品bootstrapやビルド時のencodingを変更していない。

観測.appの通常起動（PID76559、worker76569）では実workerのexecutable/prefix/sys.path/PyYaki/Pydantic native拡張がすべてbundle内、isolated=1、bytecode書込みなし、通常GIL、UTF-8ログを確認。無効なPython環境変数を渡したままwelcome後に自分のPopenへSIGTERMを送り、終了フック・終了コード0・観測残留0を確認した。起動後も別のmanifest 2,508件/8件と署名が一致した。

## 原ログ・残課題と次の候補

保存先に`environment.json`、全検査log/json、`startup/`、各GUIケースのresult.json/launcher.log/host.log/worker.log、入力と失敗コピー、取得アーカイブ、新旧buildログ、.app・manifest、`bundle-verification.json`、`bundle-after-smoke.json`、`relocated-after-smoke.json`、`instrumented-after-normal.json`、`relocation-controls.json`、`evidence-summary.json`を保持した。各resultは自分のホストPID・観測した子の開始時刻/コマンド・終了結果を含む。既存startup_probeの9件は追加のプロセス監視をしておらず、他の8ケースの残留観測と区別する。全WebKit XPCの帰属・回収を証明したものではない。Gitには文書だけをコミットし、大きな資材や生ログは追加しない。

次の作業候補:

1. ADR-017。表示条件の申告を取得した通常/フルスクリーン・Space移動/復帰・生成.appの対照。二重rAF、先行描画順、公開readyと10秒期限の契約を保つ設計を別途検討する。今回本対応は未実装。
2. 画面取得環境が使える際の目視、実closeとdownload拒否ハンドラー、Dock/FinderのC案表示。通常終了は今回SIGTERMと自動スモークの範囲で確認した。
3. MacのホストSIGKILL回収、時間単位の稼働、全WebKit資源、Python未導入の別Mac/Intel、Developer ID署名・公証・quarantine・正式配布は未確認。アドホック署名はこれらの代替ではない。
4. WindowsのクリーンVM、実Authenticode署名、圧縮・性能、LICENSE/NOTICE/SBOM等は[Windows記録](validation-windows-onefile-2026-11-07.md)の別作業。今回のMac結果でWindows受入や以前の失敗原因を確定しない。
