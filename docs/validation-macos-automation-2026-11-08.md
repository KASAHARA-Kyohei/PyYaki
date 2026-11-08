# Mac回帰検証器の自動化確認（2026-11-08）

利用者がリモート利用中に「Mac回帰検証の自動化を優先」と指定したため、[前回のSpace対策](validation-macos-space-2026-11-08.md)でGit管理外だった補助処理を、再実行可能なメンテナー用検証器へ整理した。[実行手順と対象範囲](macos-regression.md)を参照する。

## 対象・変更

着手時は`7303ee8`、作業ツリーはクリーン。検証器・fixture・テストのコミットは`be7e26f`。最終実行は同じファイル内容を未コミット状態で行い、検証対象7ファイルのハッシュを`validation-summary.json`へ保存した。製品のnative/src・src/pyyaki・Cargo設定/lock・サンプルアプリは変更していない。

- `tests/macos_regression.py`でソース検査・ビルド・通常/遅延/fullscreen・任意の既存.app確認を直列実行する。
- `native/examples/macos_space_control.rs`は自分の窓だけを所有し、2秒の安定したfullscreen状態を通知する。アプリを子起動する機能は持たせず、Python側が直接起動・回収する。製品へ同梱しない。
- 共有startup_probeは、自分のPopenの回収とPIDによるログ識別を整理し、二重rAF・段階順・両10秒期限を検査する。中断時は原ログをコピーしてから中断を記録する。
- 新しいMac検証器はSpace設定の復元、manifestの全件・相対パス・内部リンク、署名を追加照合する。予定件数だけでなくケース名も照合し、重複成功で未完了を埋め合わせない。

環境はmacOS 27.0.1 / arm64、CPython 3.14.3通常GIL、既存Rust/Cargo 1.89.0。SDKROOTをコマンド単位でMacOSX26.5.sdkへ指定した。ツール・依存・保護設定の変更や、資材の新規取得はしていない。開発ホストSHA-256は前回と同じ`c1faac88f012ca3cb184581bc41224ec6ec64608511edabd9704e391addaf4be`。

## 最終実行

```console
SDKROOT=/Library/Developer/CommandLineTools/SDKs/MacOSX26.5.sdk .venv/bin/python tests/macos_regression.py --checks --build --bundle "test-results/adr017-20261108/dist/ADR017 PyYaki.app" --manifest "test-results/adr017-20261108/dist/ADR017 PyYaki.build.json" --output test-results/macos-automation-final-20261108
```

| 確認 | 実際の結果 |
| --- | --- |
| Python（DeprecationWarningをerror） | **181成功、スキップ/失敗0**。追加36件は誤合格防止・manifest・中断/回収の検査 |
| Rust | **14成功**、ignore/失敗0。新しいexampleもコンパイルされたが、GUIを単体テストとして実行した意味ではない |
| Ruff / format、Cargo fmt、Clippy | 全件成功。ClippyはMac all-targets・-D warnings |
| release build / Chromium仮ブリッジ | host/fixtureのlockedビルドと既存SDK契約検査に成功 |
| 通常/import前5秒/起動フック5秒 | 各3回、**9/9成功** |
| 独立したfullscreen開始 | **3/3成功**。各回別制御プロセスの安定状態を確認してから、開発ホストを1件起動 |
| .appの全manifest・アドホック署名 | 入力、再配置コピー、起動後の3段階すべて成功。各2,508通常ファイル・8 symlink、不一致0 |
| 再配置コピーの実WKWebView | system PATH・無効PYTHONHOME/PYTHONPATH、独立したfullscreen制御窓から1起動成功 |
| 最終report | **passed、16/16項目、not_completed=[]、終了コード0** |

16項目はGUI起動13件と整合性検査3件であり、独立したfullscreen開始16回ではない。通常ホストの12起動と.appの1起動で二重rAF・起動順・10秒期限・Space設定の復元を照合した。入力.appは変更せず、コピーと生ログはtest-resultsだけに保存した。.app生成そのものは今回の検証器へ追加していない。

## 負対照と中断

開発中の初回16/16成功も別出力へ保存し、その後、未完了ケース名と実行オプションの記録を整えた最終版で上表を再実行した。以下の負対照・中断を成功した通常起動の件数へ加えていない。

| ケース | 結果 |
| --- | --- |
| 旧ホスト・fullscreen省略・待機0秒の3件 | Space復元の証拠不足を検出し、status=failed・終了コード1。3件ともok=false |
| `--fullscreen-control /usr/bin/true` | 終了コード0だけではready証拠にならず、status=error・終了コード1。通常3件の後に打切り、予定4件を合格にしなかった |
| 実際のimport前5秒中の中断 | 別セッションの自分の検証器PID89020が起動したホストPID89057を、親PID・完全なコマンド・app_import_startedで識別してから、検証器だけへSIGINT。ホスト終了コード0で回収し、verifierは130・status=interrupted |
| 中断時の保存 | host/workerの原ログをコピーし、process.jsonにinterrupted=true。完了はbaseline 1件だけ、not_completedはimport/hooks/fullscreenの3件 |

旧ホスト・不正controlはそれぞれ専用の出力を保持した。中断確認は直接起動したホストの回収までで、未観測workerや全WebKit XPCの残留監視の証明ではない。新しい共有startup_probeのWindows実機での再確認は今回行っていない。

## 保存先と限界

Git管理外の`test-results/macos-automation-final-20261108/`へ最終report、検査log/json、ケース別project/host/worker/launcher/process、controlのready/report、再配置.app、全件照合結果、validation-summary.json、execution.logを保存した。初回正例は`test-results/macos-automation-20261108/`、負対照は同名に`-legacy-negative`/`-control-negative`、中断は`-interrupted`を付けた別ディレクトリ。初回側にnegative-summary.jsonとinterruption-result.json、観測・中断用の補助スクリプトを保持する。

最後に、最終レポートの直接起動28 PID（アプリ13・制御4・検査コマンド11）を起動コマンドと照合し、一致する残留は0だった。読み取りだけを行い、結果はfinal-process-check.jsonへ保存した。子孫・全WebKitの監視結果へ一般化しない。

実画面・実クリック・利用者のCodex/Space操作、Macの故障注入GUI・長時間/性能、クリーンMac・正式配布、Windows受入は未確認のまま。reportのunverifiedと手順書の対象外を維持した。自動化のpassedをG1/G2/G3全体やMVP完了に読み替えない。
