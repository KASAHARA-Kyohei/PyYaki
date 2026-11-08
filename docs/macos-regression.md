# Mac回帰検証の自動実行

`tests/macos_regression.py`はメンテナー用の実装済み検証器。通常・遅延起動、独立したfullscreen開始、任意の既存.appの署名・全manifest・再配置をまとめる。GUIセッションを使えるMacが必要。リモートでも自分の検証窓で実行できるが、画面の実提示、実クリック、利用者のCodex表示や手動Space操作を確認したことにはしない。制御窓の準備条件を満たせなければ未達で停止する。

## 準備と実行

リポジトリのルート、CPython 3.14通常GIL版の開発venvで実行する。ソース検査には開発依存とPlaywrightのChromium、ビルドには既存のRust/Cargo 1.89以上と適合するSDKが必要。導入は[開発ガイド](development.md)を参照。新しいPython/Rust依存は追加していない。

```console
.venv/bin/python tests/macos_regression.py --checks --build --output test-results/macos-regression-new
```

この開発機はSDK 27とリンカーが不整合のため、実行時に`SDKROOT=/Library/Developer/CommandLineTools/SDKs/MacOSX26.5.sdk`を前置した。別のMacではSDKを確認し、必要な場合だけ指定する。既定SDK・toolchain・保護設定は変更しない。Cargoの依存取得が必要な環境ではネット接続が必要で、オフライン準備を自動で完了するコマンドではない。

| オプション | 実装済みの範囲 |
| --- | --- |
| `--checks` | pytest（非推奨警告error）、Ruff check/format check、Cargo fmt check/test、Clippy all-targets、Chromium仮ネイティブ契約をGUI検証の前に直列実行 |
| `--build` | 通常のreleaseホストと`macos_space_control` exampleをlockedビルド。既定のbinary/controlパスを使う場合だけ指定可能。自動整形はしない |
| `--binary` | 検証する既存ホスト。既定はnative/target/release/pyyaki |
| `--fullscreen-control` | 検証用のTao窓だけを操作する既存fixture。既定はnative/target/release/examples/macos_space_control。製品には同梱しない |
| `--runs` / `--delay` | 通常/import前/起動フックを各runs回。既定3回・5秒。runsは1〜30、delayは0〜5秒 |
| `--fullscreen-runs` | 独立したfullscreen開始。既定3回、0〜10回。0なら未確認として明記 |
| `--watchdog` | アプリ1起動の外部watchdog。既定30秒、20〜60秒。SDK/ホストの10秒期限を変更する値ではない |
| `--output` | test-results配下の新規ディレクトリだけ。既存出力を上書きしない。未指定時はUTC日時名 |

--checks/--buildを省略すれば既存バイナリを使えるが、対応する未依頼の検査・再ビルドはunverifiedへ記録する。Gitのsource_commitは作業ツリーのメタデータであり、渡したバイナリのビルド元を保証しない。実行したホスト・fixtureのSHA-256とdirty状態も保存する。

## 既存.appも検証する

信頼した自分のアプリと、その生成時のmanifestを明示する。アプリのコードを実際に起動する検証で、未信頼コードのsandboxではない。.appの生成自体は既存の[build macos](macos-build.md)で別途行う。

```console
.venv/bin/python tests/macos_regression.py --checks --build --bundle "dist/Hello PyYaki.app" --manifest "dist/Hello PyYaki.build.json" --output test-results/macos-regression-bundle-new
```

入力.appの全manifestとcodesign deep/strictを照合してから、出力内の`relocated/再配置 鯛焼き/`へコピーする。コピーの全件照合、system PATH・無効なPYTHONHOME/PYTHONPATHでの同梱スモーク、終了後の再照合を行う。fullscreen検証を有効にした場合は、このコピーの起動も自分のfullscreen制御窓から実行する。入力.appや既存レポートは変更せず、入力bundle内部をoutputにする指定も拒否する。

これは元のruntime/projectを隠す負対照、Python未導入の別Mac、物理オフライン、Developer ID署名・公証を代替しない。アドホック署名の検証を配布者の信頼性の保証にしない。

## 判定と原ログ

- 通常の9起動は、終了コード0とスモークokに加えて、二重rAF→shell→Space設定復元→Python→worker6段階→welcome、両10,000ms期限を照合する。
- fullscreenの各起動は、それぞれ別の制御プロセスでFullScreen・active Space・Visible遮蔽・application activeが2秒安定してから開始する。既定3回は独立した開始3件であり、前の検証器の「1組に3連続起動」と混同しない。
- 制御プロセスの終了コード0だけでは合格にしない。自分のPIDのready.json、準備状態、アプリ結果、制御reportのcompleted/ready/通常fullscreen解除を照合する。
- .appを指定した既定実行は16項目（開発ホスト12起動、.app 1起動、整合性検査3件）。省略した項目を追加で成功件数へ含めない。

report.jsonのstatusは実行中running、終了時passed/failed/error/interrupted。passedは**要求した全項目**を収集・確認できた場合だけで、unverifiedの項目の合格を意味しない。planned_casesとexpected_casesが予定、casesが収集済み結果、not_completedが結果未完了のケース名。重複した成功結果で欠けたケースを埋め合わせない。例外や準備未達による打切りはerror、中断はinterruptedとなり、検証の終了コードは0/1、中断は130。

ケースごとのprojectコピー、PID/起動引数/終了結果のprocess.json、launcher/host/worker.log、fullscreenのready/report/control.log、検査コマンドのlog/json、.appの照合結果とコピーを保持する。host_startedのPIDで自分のログだけを識別し、原ログをコピーしてから段階を解析する。Ctrl+C時は直接起動したPopen子を終了・回収し、可能な自分のログを保存してから中断を記録する。制御fixtureはアプリを子起動せず、自分の窓だけを所有する。

故障注入、GUIの例外表示/実close/download、SIGKILL、全WebKit XPCの回収・資源、性能/長時間稼働、Windows実機はこの検証器で網羅していない。既存の[起動診断](startup-diagnostics.md)・[Mac計測](performance-macos.md)と区別する。ウィンドウ/Spaceが切り替わるため、他のGUI検証や重いビルドを並行実行しない。
