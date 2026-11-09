# Macホスト強制終了時の回収（2026-11-09）

対象: `69acd4b`（本体・回収検証器）、`f141f41`（同梱Pythonでの検証時もbytecodeを書かない修正）。開始時は`1cdb787`、既存変更なし。Apple Silicon / macOS 27.0.1、CPython 3.14.3通常GIL版、SDKROOTはMacOSX26.5.sdk。Windows実機は使用していない。

## 再現と対策

変更前ホストのコピーで7条件を実行した。import待ち・起動フック待ち・GILを保持するC処理中はPythonとその子が残り、接続後の待機中はPythonの子が残った。計4条件が不合格。停止済みワーカー・ワーカークラッシュ・正常終了の3対照は残存なしだった。残存を記録した後、開始時刻・コマンド・PGIDを照合した自分のプロセスだけを検証器が回収した。

従来はホストの`Drop`でPython専用グループを終了していた。ホスト自体をSIGKILLするとDropが動かず、Python側もimport/起動処理中・GIL保持中にはEOFを処理できない。修正後のMacは`GUIホスト → guard（専用グループのリーダー）→ Python → 同じグループの子`という構成にした。

- guardは同じ実行ファイルの内部入口で、GUIやPythonを初期化しない。GUIのshell通知後にだけ起動し、従来どおりその後Pythonを生成する。二重rAF・両10秒期限・通常終了の2秒待ちは変更しない。
- ホストだけが持つ生存pipeのwriterが閉じると、guardがkqueueで通知を受ける。Python終了もNOTE_EXITで検知し、自分を含む専用グループへSIGKILLを送る。PythonのGIL・イベントループに依存しない。
- guardが先に異常終了しても、ホストは未回収のguard PIDを保持し、Dropでその専用グループを終了してからwaitする。PID再利用によって別のグループを終了しない。システム全体のPID探索を製品の終了処理に使わない。
- guard側は自分がグループリーダーであること、内部入口のFDが別々のpipeであることを確認する。Pythonへ継承するのは従来の通信用2 FDだけ。生存・準備通知・kqueue FDはclose-on-execとする。

Python/Rust/SDKの公開通信契約は維持し、追加依存はない。Macの`python_spawned.data`へ`worker_pid`・`guard_pid`・`process_group`を追加し、既存診断器の障害注入も、直接子またはログと実PPIDで一致するguard経由の所有関係だけを許可するよう更新した。WindowsのJob/継承コードは変更していない。

## 検証結果

| 検査 | 結果と範囲 |
| --- | --- |
| 共通Python | 214成功、skip 0。DeprecationWarningをerror扱い |
| Rust | library 9 + binary 7 = 16成功。生存pipe通知、SIGSTOPを終了と誤認しないkernelイベント試験を含む |
| 静的確認 | Ruff check/format、Cargo fmt、Mac Clippy全target、release build成功 |
| 実GUI回収 | `macos-host-cleanup-gui-final`は8/8成功。import、hooks、GIL保持RPC、idle、SIGSTOP後のホストSIGKILL、worker SIGKILL、guard SIGKILL、正常SIGTERM。検証器の追加killなし、観測期限3秒内の残存なし |
| コミット後GUI | `macos-host-cleanup-69acd4b-gui`は7/8。RPCケースはPython開始前のSpace/rAF待ちで未達。他7件は回収成功。上の8/8と同じバイナリだが、この実行全体はfailedのまま保持 |
| headless補助 | `macos-host-cleanup-69acd4b-headless`は7/7。実guardと実ワーカーを専用の所有者から起動。GIL保持中のheartbeat停止とSIGSTOPを確認してから障害注入。GUI描画やネイティブホストの起動順の証明には使わない |
| 既存診断 | freeze/crash/shutdownの3/3。guard経由の所有対象確認、SDKの失敗/取消・終了と既知プロセスの回収を確認。長時間・性能試験ではない |
| 同梱wheel | 更新ホストで生成し、新規日本語・空白venvから既存検証器13/13成功。guardの追加によってCLI exec・既定Python・署名・削除が壊れていないことを確認 |
| Python同梱.app | 新規生成・署名。初回GUIはPython開始前の描画待ちで失敗。独立した追試はスモーク・起動順・Space復元に成功。失敗の原ログは保持 |
| .appのguard補助 | .app内の実ホストと同梱CPython 3.14.3でheadless 7/7。GUIの強制終了マトリクスを.appで実行した扱いにはしない |
| .app整合性 | 補助検証後とGUI追試後も署名strict/deep検査、通常ファイル2,512件・symlink 8件の全manifest一致 |

記録されたPIDの最終照合も残存なしだった。全WebKit/XPCや未記録の子孫まで確認した意味ではない。

本体ホストSHA-256は`b4075fc4579f4fec44c8f683cdae0f8bb8ebeeaeb02a046fdf6564f4d3d826c0`。GUI 8/8の実行は本体コミット直前、コミット後の実行は同一ホストハッシュで照合した。署名はローカル用アドホックで、Developer ID・公証・他Macへの正式配布を確認したものではない。

## 原ログと途中の失敗

すべてこのMacのGit管理外の新規`test-results`配下に保存した。過去の失敗を再試行の成功で上書きしていない。

```text
macos-host-cleanup-20261109/           # 変更前ホストcopy、Rust/build/fmt/Clippyログ
macos-host-cleanup-before/            # 負対照7件、remainingとobserver_cleanup
macos-host-cleanup-guard-first/       # 初期GUI 7/7（追加の厳密化前）
macos-host-cleanup-guard-second/      # GUI 6/8、2件はPython前のSpace待ち
macos-host-cleanup-controlled/        # 自分のfullscreen制御でも開始条件未達
macos-host-cleanup-gui-final/         # 実GUI 8/8
macos-host-cleanup-69acd4b-gui/        # cleanコミット後7/8、未達を保持
macos-host-cleanup-69acd4b-headless/   # cleanコミット後7/7
macos-host-cleanup-app-guard/         # .app内ホスト/Pythonの補助7/7
macos-host-cleanup-wheel/             # wheel 13/13
macos-host-cleanup-artifacts/         # 新規wheel/.app、署名・全manifest、GUI追試・既存診断3件
macos-cleanup-pytest.log
```

各回収ケースには`before-fault.json`、判定時の`observed.json`、`result.json`、host/worker/launcherログを保存する。`remaining_before_observer_cleanup`で判定し、後から検証器がkillしたケースは合格にしない。終了済みのzombieも観測対象へ含め、PIDが再利用された対象へシグナルを送らないよう開始時刻・コマンド・PGIDで照合する。

途中のRust単体試験は、writer close直後の非待機通知を仮定して1件失敗した。単独実行は成功し、試験を2秒内の通知到達を確認する形に修正した。並行spawn時の一時的なFD継承が関係する可能性はあるが、原因の断定はしない。製品側は元から通知待ちで、期限を緩めた変更ではない。初回headless補助の正常終了ケースも、終了済みグループへの後始末シグナルでEPERMとなりfailedだった。補助所有者を製品と同じbest-effort kill後のwaitへ合わせ、再実行7/7を確認した。OSの権限設定は変えていない。

Space/rAF待ちの失敗は、`window_on_active_space=false`やhidden/scheduledのみ、`host_initialization_timeout`、Python生成なしとして記録されている。終了対策が動く前の失敗なので、回収成功へ読み替えない。今回Space配置や描画判定の追加修正は行っていない。

## 適用と再実行

ホストを再ビルドする。既存wheel/.appのホストは自動更新されないので、新しいホストを使って再生成する。公開SDKや業務アプリへの変更は不要。

```console
.venv/bin/python tests/macos_host_cleanup.py --binary native/target/release/pyyaki --output test-results/cleanup-new
.venv/bin/python tests/macos_host_cleanup.py --binary native/target/release/pyyaki --headless-owner --output test-results/cleanup-headless-new
```

`--fullscreen-control native/target/release/examples/macos_space_control`は任意の開始条件比較用。自分の制御fixtureを起動してもreadyにならなければfailed/未完了として止める。`--headless-owner`と同時には使わない。headlessではネイティブホストによるguard死亡検知は試験できないため、guard-crashを除く7条件だけを扱う。

## 限界と次の候補

guardが1プロセス増える。イベント待ちで定期ポーリングは追加しないが、起動・メモリ・長時間のコストは未計測。強制回収時のshutdownフック・処理の巻戻しは保証しない。`setsid`/`setpgid`等でグループを離れた子、ホストとguardの同時SIGKILL、全WebKit/XPCの回収、違う権限への移行は今回の保証範囲に含めない。

他Mac/CPU・正式署名・Windows実機・Windows型検査は今回未実施。Web開発サーバーの親SIGKILL回収も別の構成で、今回のMacネイティブ対策で解消した扱いにしない。次の候補はSpace/rAF未達条件の追加調査、guard込みの資源計測、またはWindows端末復帰後のネイティブCLI作業とする。
