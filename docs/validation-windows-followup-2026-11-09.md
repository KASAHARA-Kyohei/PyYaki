# Windows残作業の追加検証（2026-11-09）

開始HEADは`41884920c7a36fcfa49aa102a8f83b27e71856dd`、作業ツリーclean。origin/mainをfetchし、既存5コミットがmainより先行していることを確認した。既存変更の取消やmainへの強制更新はしていない。今回もコピー・故障注入・原ログは`test-results/windows-followup-20261109/`配下へ保存した。

**この端末だけで残作業の全受入は完了していない。** クリーンWindows VMとMacの接続情報、正式ライセンス、実署名用証明書/方式が必要な点は回答待ち。VMツール/Sandbox実行ファイル・CurrentUserの対応コード署名証明書は確認できなかった。未発見を環境が絶対に存在しない証明にはしない。OS機能の有効化、保護無効化、アダプター停止、証明書購入、自己署名による代用は行っていない。

## 実施した内容

実機は前段と同じWindows 11 Pro 25H2 x64 / CPython 3.14.10通常GIL / Rust 1.89.0 MSVC / WebView2 157.0.4341.42。onefileはFixed Version 156.0.4314.58を同梱した前段の`Hello.exe`を使用した。[前段の資材・SHA-256](validation-windows-wheel-2026-11-09.md)と区別する。

| 項目 | 結果・限界 |
| --- | --- |
| Windows実画面 | computer-useで自分の`PyYaki followup visual 1109`窓を前面へ出し、接続済みUIの画像・日本語を確認。実クリックのあいさつ応答と閉じる操作に成功。補助結果はvisual/result.json、終了0・観測PID残存0 |
| 初期提示 | 上記はready後の実画面。Python前の最初のGDI/HTML提示時刻・録画・ETW、正式T_native等は未確認のまま |
| 現行venv境界 | 15件中14確認、pipe-closeは既存制約として再現。watchdogなし。host-kill-treeの終了1は意図した終了で、検証全体の終了1はpipe-close未達を保持したもの |
| 埋込対照 | 依存入りの展開済みruntimeでpipe-close、不要ハンドル非継承、孫の正常/強制回収の4/4確認。観測PID残存0。部分closeを解消したわけではない |
| Cargo既定bin | `8d969b3`でdefault-runをpyyakiへ固定。Windows CLI bin追加で従来のcargo runが曖昧になる問題を修正。`cargo +1.89.0 run --release --locked -- --version`がGUIホストを選び、期待する版を出して終了0 |
| Python全回帰 | hash大文字入力の最終追加前に**206成功・26スキップ**。DeprecationWarningをerror指定。追加8件は依存棚卸し5件・版確認3件。最後の大文字入力1件を加えた対象検査は9/9成功。全回帰を207件実行した扱いにしない。スキップは前段と同じWindows symlink権限・POSIX/Mac専用条件 |
| 静的確認 | Ruff check/formatはtools/check_versions.pyも含め成功。PowerShell構文解析成功。Rustコードは変えておらず、前段の20成功/2 ignoredを今回再実行した件数に足していない |
| SBOM/通知草稿 | `cf70f67`のローカル棚卸し器で137コンポーネント、230件の元本文参照を生成。CycloneDX 1.6形式の草稿・実ファイルhash・不足一覧を保存。schema全体の検証、法的互換性、全バイナリ内包物の監査、正式公開の承認ではない |
| source版一致 | Python・SDK・Rust・Cargoをアプリimportなしで確認。元コードの一致、SDK/ホストだけ変更した負対照を確認。前段のdev1 fixtureで起きたSDK更新漏れを検出するためのメンテナー補助 |

棚卸しの137件はframework wheel 1、依存wheel 15、選択したCargo source/build graph 119、Python/WebView2資材2。本文不足はPyYaki本体/Rust root、webview2-com関連3 crate。さらにPython/CRT/WebView2の内包ライブラリ・再配布条件を未解決として残した。元のライセンス本文を改変したり、MIT/Apache等の選択肢から勝手に選択したりしない。生成物はGit管理外で、通知の正式採択ではない。

## 新しい確認器・引き継ぎ

`tools/verify_windows_onefile.ps1`はPython不要のWindows補助確認器。実行前の環境・署名ゲート、Process handleによる所有確認、watchdog、外部からの段階ログ観測、CPU/Working Set/Private Bytesの標本と原ログを残す。外部観測はポーリング/PowerShell起動・CIM等の負荷を含み、物理提示や定義した性能指標へ読み替えない。

`-RequireCleanEnvironment`はこの開発PCを正しく拒否した。Python/Cargo/既存WebView2/稼働ネットワークがある端末をクリーンVMと数えない。`-RequireSignature`も未署名exeを拒否するための負対照で、実署名の成功ではない。個々の結果は下記原ログとreportを正とする。

所有ログだけへ絞った`ps-observe-third`はwarmup/cachedの**2/2成功**、各終了0・残存0・watchdogなし。結果statusはobservedであり、クリーン環境gateはfalseのまま。初回展開とcache再利用の標本・資源標本は取得したが、30回群や物理提示の代わりにはしない。署名負対照はNotSigned→終了1、環境負対照はcleanGate=false→終了1を確認した。

詳しい実行方法・制約は[配布前の準備](release-readiness.md)。未起動の`.wsb`設定例はguestネットワークを無効、inputをreadonly・outputだけwrite、ProtectedClientを有効にする。ホストの設定を変えず、既存のVM/機能が使える場合だけ利用する。Sandboxの存在・設定例だけで未導入/オフライン端末や基準メモリ条件の証拠にしない。

Macは[専用の引き継ぎ](handoff-macos-after-windows-2026-11-09.md)を用意した。接続できるMac上で全Python/Rust・WKWebView・guard回収・Web更新・新wheel/.appの署名と再配置を実行する必要がある。Windows上のパーサー/単体検査をMac実機の合格にしない。

## 保持した失敗

1. 埋込対照の初回は、依存未配置の入力runtimeを指定し、PyYaki import前に4件とも失敗。`boundary-embedded/`を保持した。onefileから展開された依存入りruntimeを指定した`boundary-embedded-corrected/`が4/4。元runtimeを上書きしていない。
2. PowerShell確認器の初回は親PowerShell 7から引き継いだモジュールパスにWindows PowerShell標準モジュールがなく、Get-FileHashを解決できず検証前に失敗。`ps-clean-negative/`・`ps-observe/`を保持した。検証プロセス内だけOS標準module pathを追加し、最後に元へ戻す。ExecutionPolicyはRemoteSignedのまま。
3. `ps-observe-second/`はwarmupのスモーク成功後、cachedケースへ前回のログまで混ぜて2件と数え、検証器が失敗にした。両アプリの終了0・残存0でも試行全体を合格にしない。新規作成時刻・今回保持したPIDに一致するinstanceだけを採用するよう補助確認器を修正した。

原ログ・生成物:

```text
test-results/windows-followup-20261109/
  visual/result.json / visual/local/PyYaki/prototype/*/*.log
  boundary-venv*/report.json / <case>/{host,worker,launcher}.log
  boundary-embedded*/report.json
  default-run.log
  followup-pytest.log / followup-ruff.log / followup-format.log
  cargo-metadata.json
  inventory-first/{bom.cdx.json,inventory.json,THIRD-PARTY-NOTICES.draft.md,licenses/}
  ps-clean-negative*/report.json
  ps-observe*/report.json / run-*/result.json
  signature-negative/report.json
```

## まだ必要な実行・判断

- 所有者・接続方法が確認されたクリーンWindows VMとMac。未導入・標準ユーザー・物理オフラインとMac回帰をそれぞれ実施する。
- Python前のGDI/HTMLの実提示、30回群・再起動後10回、基準環境・profile条件を揃えた正式計測。ready後の画像や少数の補助標本では代替できない。
- 既存証明書/署名サービスで実署名し、署名後の索引・全hash・起動・更新・MOTW/組織条件を検証する。
- 正式ライセンスの選択、3 crateの原文不足、CPython/VC runtime/WebView2の再配布条件、CI来歴・脆弱性監査・公開先/版/配布経路。source branchのpushとpackage公開は区別する。

これらは無断の環境構築・購入・公開や保護無効化で埋めず、必要な回答/資材が揃った後に続行する。

## コミットと再開点

`8d969b3`はCargo既定bin、`cf70f67`は棚卸し/版確認と試験、`86314e9`はlockのhex大文字対応、`edb4c6c`はPython不要の観測器と未起動のSandbox例。原資材・生成SBOM/本文・生ログはGitへ追加しない。

`origin`（https://github.com/KASAHARA-Kyohei/PyYaki.git）の`codex/windows-native-cli`への通常pushは、実行前の自動承認レビューにより拒否された。理由は「外部GitHubへリポジトリ内容を送信する操作について対象remote・branchへの明示承認を確認できない」。Gitコマンドは開始されておらず、push未実施。迂回せず対象と内容を示して明示回答を求める。mainの書換え・force push・GitHub Release/PyPI公開は行わない。

関連9文書のローカルリンク224件とdiff空白検査に成功。原ログはdocs-verification.json。最終の所有プロセス読取りはfinal-owned-process-check.jsonに保存し、観測していない全プロセスの一般的な回収保証とは区別する。
