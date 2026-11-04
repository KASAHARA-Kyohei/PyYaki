# Windows更新版ホストの実機再検証（2026-11-04）

実施日: 2026-11-04（日本時間）。対象ソースは`ae3889c7d848a7ce810943c69afb26d90efff503`。MSVCビルド、Python/Rust検査、起動反復9回、C案アイコン、import例外の画面、通常終了・ホスト強制終了時のPython回収を確認した。**今回の成果物は検証記録であり、Windows単一exe生成やG1/G2全体の合格ではない。**

以前の失敗は[2026-11-03の実機記録](validation-windows.md)、段階ログの定義と手順は[起動の切り分け](startup-diagnostics.md)、現在地は[ロードマップ](roadmap.md#現在地と次の作業)を参照する。

## 対象と環境

開始時の作業ツリーはクリーン。`git fetch origin`後も`origin/main`は上記コミットで、`git merge --ff-only origin/main`はAlready up to dateだった。指定の`ae3889c`を含むことを確認した。既存venv・ツールチェーンを使用し、新しいツールの導入や依存の更新はしていない。Cargoは更新版Cargo.lockに従って追加のビルド依存を取得した。

| 項目 | 実際に確認した値 |
| --- | --- |
| OS | Windows 11 Pro 25H2、10.0.26200.8246、64bit |
| CPU | Intel Core i5-8250U @ 1.60GHz、Architecture=9（x64） |
| Python | `.venv\Scripts\python.exe`、CPython 3.14.10、MSC v.1944、AMD64 |
| GIL | `Py_GIL_DISABLED=0`、`sys._is_gil_enabled()`=true |
| Python導入形式 | editable。import先はリポジトリの`src/pyyaki/__init__.py` |
| Rust | `rustc +1.89.0`、1.89.0（29483883e）、host=`x86_64-pc-windows-msvc` |
| WebView2 Runtime | 登録版156.0.4314.58。実画面確認も既存ランタイムを使用 |
| 実行権限 | Administratorロールfalse。既存MSVC/SDKでビルド・実行。MSVC導入時の管理者権限不要を確認したものではない |
| ホスト | `native/target/release/pyyaki.exe`、MSVC releaseビルド |
| exe SHA-256 | `70f8987378bf5fc91031843c2c1b004544401093b84239b6be0ed4f5da9d75c6` |

検証開始から反復検証完了まで製品ソース・テスト本体・Cargo.lockに変更なし。検証用のアプリ変更と補助スクリプトは`test-results/windows-20261104/`のコピーだけに置いた。以下の結果を得た後に、docsとREADMEの記録・案内を更新した。セキュリティ機能、プロキシ、権限制限、初期化期限、二重rAF、Python開始順は変更していない。

## ビルドと検査

リポジトリ直下で順に実行した。各コマンドの全出力・終了コードは保存している。

```powershell
cargo +1.89.0 build --manifest-path native/Cargo.toml --release --locked
.\.venv\Scripts\python.exe -m pytest -q -ra -W error::DeprecationWarning --junitxml=test-results/windows-20261104/pytest.xml
cargo +1.89.0 test --manifest-path native/Cargo.toml --release --locked
cargo +1.89.0 clippy --manifest-path native/Cargo.toml --release --locked --all-targets -- -D warnings
```

| 検査 | Windowsでの結果 |
| --- | --- |
| MSVC releaseビルド | 成功、終了コード0。更新されたアイコンリソースも含めてリンク |
| Python | **108件成功・19件スキップ・失敗/エラー0件** |
| Rust | **10件成功・失敗/ignore 0件**（lib 9件、host 1件）。doc-test 0件 |
| Clippy | MSVC全ターゲット、警告をエラー扱いで成功 |
| 既存実GUIスモーク | 単独で1回成功。`native_smoke.ok=true`、終了コード0、`sdk_welcome_posted`あり、watchdogによる終了なし |

Pythonのスキップ19件の内訳は次のとおり。Macの127件成功やRust 13件を、Windowsの実行件数として転記していない。

| 件数 | 理由 |
| --- | --- |
| 3 | symlink作成がWinError 1314。外部リンク資産・壊れたレポートリンク等の検査で、権限設定は変更しなかった |
| 1 | Darwinの`renamex_np`結合検査 |
| 1 | POSIX SIGINT動作 |
| 14 | POSIX fd継承のワーカーパイプ検証器。Windows実機ホストの回収確認とは別の検査 |

既存スモークは`tests/startup_probe.py`の`run_case`を補助スクリプトから1回呼び出し、helloのコピーと`--smoke-test`を使用した。30秒watchdogを維持し、launcher/host/workerログを保存した。mode・capabilities、あいさつ・アプリエラー、並行RPC、イベント順序・購読解除、取消の既存スモーク内容を変更していない。

## 起動反復：通常・import前5秒・起動フック内5秒

ビルド・単体検査・Clippy・単独スモークの終了後、他のビルド/試験と並行させずに次を実行した。既定の30秒watchdogとアプリ側の既存期限を使用し、OSキャッシュは消去していない。表示条件を変える追加試験は行っていない。

```powershell
.\.venv\Scripts\python.exe tests\startup_probe.py --binary native\target\release\pyyaki.exe --runs 3 --delay 5 --output test-results\windows-20261104\startup
```

**通常3/3、import遅延3/3、起動フック遅延3/3、計9/9成功**。全件で終了コード0、`closed_by_harness=false`、スモークok、SDKへのwelcome送信あり。検証器の終了コードは0。report.jsonの`source_commit`は上記フルコミット、`working_tree_dirty=false`で、ホストハッシュも一致した。

各起動の段階時刻・区間は次のとおり。単位はms、小数1桁へ丸めた。shellとwelcomeはホストrun入口から、その他は該当通知間の差。IPC・スケジューリング等を含み、物理的な画面提示やNFRの起動性能達成の証拠ではない。

| ケース | shell通知 | Python生成完了→worker通知 | import区間 | 起動フック区間 | SDK welcome送信 |
| --- | ---: | ---: | ---: | ---: | ---: |
| baseline-00 | 1860.6 | 1060.7 | 74.9 | 0.7 | 3031.7 |
| baseline-01 | 2175.6 | 2353.0 | 97.3 | 0.8 | 4670.6 |
| baseline-02 | 1879.4 | 1123.0 | 59.2 | 0.8 | 3097.2 |
| import-00 | 1794.5 | 867.2 | 5085.6 | 0.9 | 7785.5 |
| import-01 | 1624.0 | 860.1 | 5064.6 | 0.6 | 7576.0 |
| import-02 | 1843.9 | 830.2 | 5069.9 | 0.7 | 7771.1 |
| hooks-00 | 1757.3 | 842.5 | 86.2 | 5013.3 | 7728.4 |
| hooks-01 | 1637.6 | 855.4 | 67.1 | 5013.5 | 7605.1 |
| hooks-02 | 2116.0 | 942.2 | 122.0 | 5016.9 | 8232.5 |

全9件で以下を照合した。

- `sdk_shell_progress`はscheduled → frame1 → frame2。各通知のDOM visibilityはvisible、focusedはtrue。
- `shell_marker`の後に`python_spawn_started`と`python_spawned`がある。shell以前にPython生成はない。
- workerの6段階は`worker_entered → config_loaded → app_import_started → app_imported → startup_hooks_started → startup_hooks_completed`の固定順。その後に`worker_welcome`、`backend_ready`、`sdk_welcome_posted`がある。
- `ui_deadline_started`と`backend_deadline_started`はともにtimeout_ms=10000。worker/backend失敗・SDK接続期限・ホスト初期化期限の記録はない。
- ネイティブ窓の標本はvisible=true、minimized=false、focused=falseだった。DOMのfocusと値が異なることも原記録に残し、同じ判定とは扱わない。

今回の9回では、以前のWindowsの突発的な接続期限到達を再現していない。したがって**その失敗の停止段階・根本原因・解消は未確定**。MacのSpace切替時のframe2待ち停止と同じ原因とは判断しない。Windowsの今回のログに、hiddenへの遷移やframe2欠落はない。

## 実画面・障害表示・終了処理

実画面はWindowsのcomputer-useで確認し、スクリーンショットとアクセシビリティ情報をローカルへ保存した。タスクバーだけは取得可能な対象窓に含まれず、起動中に利用者へ目視確認を依頼し、「C案アイコンが表示されている」との回答を得た。

| 対象 | 結果と証拠の種類 |
| --- | --- |
| Explorer | releaseフォルダーの`pyyaki.exe`を大きいアイコン表示にし、赤茶背景・クリーム色のC案を画面で確認 |
| ウィンドウ | PyYakiのタイトルバー左上にC案を画面で確認。正常画面と障害画面の両方で表示 |
| タスクバー | **利用者による目視確認**。自動スクリーンショットによる確認ではない |
| import例外 | 検証用コピーの先頭でRuntimeErrorを送出。例外名、日本語を含む`PYYAKI_WINDOWS_IMPORT_PROBE: 検証用import例外 2026-11-04`、停止段階「アプリの読み込み」、host/workerログ案内を画面で確認。長いパスも折り返されている |
| 通常終了 | 自分のhost PID 17080を閉じるボタンで終了。終了コード0、終了フック完了ファイルあり。Python実ワーカー20048、venv launcher 2168を含む観察対象11プロセスに残留なし |
| ホスト強制終了 | 自分のPopen子ホスト22112だけを所有ハンドルで終了。事前に実ワーカー12600が自分の子孫であることを照合。launcher 15344を含む観察対象10プロセスに残留なし。終了コード1と終了フック未完了はこの注入の期待した結果 |
| 障害画面を閉じる | import例外のhost 21004を閉じる操作で終了。終了コード1、検証器による強制終了なし。Pythonを含む観察対象10プロセスに残留なし |

終了観察では、Toolhelpの親子関係を記録して自分の子孫だけにSYNCHRONIZEハンドルを保持し、終了後最大10秒の観察枠でそのハンドルの終了状態を確認した。観察ハンドルを取得できなかったPIDは0件。任意の他アプリや既存Pythonを終了していない。これは任意の孫プロセスやJob Object割当失敗の負例まで検証したものではない。

### 意図したimport失敗の停止段階

import失敗のログは、ホスト基準で約2,898msにPython生成開始、2,950msに生成完了、4,860msにworker開始、4,867msに設定読取完了、4,869msに`app_import_started`。約4,957msに`worker_failed`、4,958msに`backend_failed`を記録した。停止段階は双方とも`app_import_started`。`app_imported`以降、起動フック、welcomeはない。タイムアウトや描画通知待ちによる失敗ではなく、コピーに加えた例外が原因と確認できる。

### 日本語ログの文字コード

画面とJSON段階ログの日本語は正常。一方、このWindowsの`worker.log`の生バイトはUTF-8のstrict decodeに失敗し、CP932では正常にデコードでき、例外の日本語全文を確認した。元ログは変更せず、補助的に`worker-log-decoded-cp932.txt`へUTF-8変換したコピーを保存した。ログの文字コード統一や案内の改善は次の作業候補とし、今回の検証で実装や環境変数を変更していない。

## 保存先・残課題・次の候補

生ログの共通保存先は、このWindowsの`C:\Users\user\dev\PyYaki\test-results\windows-20261104\`。以下はGit管理外であり、この検証記録のコミットに生ログや画像を同梱していない。Macへ自動転送していない。

| 保存先（共通保存先からの相対パス） | 内容 |
| --- | --- |
| `environment.json`、`requirements-observed.txt` | 環境・ソース・Python依存の観測値 |
| `build.*`、`pytest.*`、`rust-tests.*`、`clippy.*` | 全コマンド出力・終了コード。PythonはJUnit XMLも保存 |
| `smoke/report.json`、`smoke/baseline-00/` | 単独スモークの結果・launcher/host/workerログ |
| `startup/report.json`、`startup/<scenario>-<index>/` | 9回の結果・全段階・検証用コピー・元ログ |
| `startup-summary.json`、`evidence-summary.json` | 段階差分・順序・表示状態・終了確認・文字コードの集計 |
| `manual-normal/`、`manual-forced/`、`manual-import-error/` | 各コピー・起動情報・プロセスと所有ハンドルの観察結果・元ログ |
| `normal-window-*`、`explorer-icon-*`、`import-error-screen-*`、対応するJSON | 実画面とアクセシビリティ情報 |
| `taskbar-observation.json` | 利用者の目視回答と確認方法 |

確認できていない範囲と、次の作業候補を次に残す。今回の範囲を広げる新機能実装は行っていない。

1. 以前のWindowsの初回起動失敗は今回未再現。負荷・表示状態・冷起動を分けた追加条件の必要性を検討し、再発時に今回の段階ログを保存する。9/9の成功を原因解消や長時間安定性の証明にしない。
2. `worker.log`の文字コードを明示または統一する方法を検討する。障害画面の段階説明が英語で重複する表示も改善候補。今回の日本語例外・ログ案内・折り返しは確認済み。
3. GDI→HTMLの物理的な実提示の録画・外部t0からの起動4指標、Windowsの最小化/非表示条件、ネットワーク切断・無効プロキシ・企業環境、境界拒否の実WebView2試験、長時間・高負荷・資源計測は未確認。
4. Windows単一exe生成・展開、ランタイム同梱、Rust不要のwheel/CLI配置、クリーン環境の初回オフライン起動、署名・再配布条件は次の作業。今回の記録で実装済み・合格にはしない。

実際に検証した製品ソースは冒頭の`ae3889c`で固定。本記録と関連文書の追加コミットはGit履歴で区別して追跡する。

記録反映後、関連Markdown 15ファイルのローカルリンク・見出し145件とコードフェンス、`git diff --check`を確認した。製品・テスト本体の差分がないことも照合し、文書変更だけを理由に実装試験や起動反復を繰り返していない。
