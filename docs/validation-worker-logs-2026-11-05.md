# WindowsワーカーログのUTF-8化と実機確認（2026-11-05）

実施日: 2026-11-05（日本時間）。[前回のCP932ログと重複説明](validation-windows-2026-11-04.md)への対応。修正コミットは`8986fb5b010749ecdfd3959eb567ca3ff62713b8`。`efe2cba`のクリーンな作業ツリーから開始し、この修正と同一のソースを未コミット状態でビルド・検証した後にコミットした。新機能やWindows単一exe生成は追加していない。

## 修正した範囲

Windowsのリダイレクト先のPython標準出力・標準エラーがCP932だった。ホストに埋め込む固定の`native/worker_bootstrap.py`で、PyYakiパッケージのimportより前に両ストリームをUTF-8、`errors="backslashreplace"`、行バッファリングへ設定する。日本語・絵文字を保持し、孤立サロゲートは`\ud800`等へエスケープする。通常の改行出力をログへ流すが、fsyncや強制終了直前の全出力の保存を保証するものではない。

ホストは`python -I -c <コンパイル時に固定したコード>`で専用ワーカーを起動する。利用者からコードを受け取る評価APIではなく、`-I`、専用パイプ、継承ハンドル制限、Job Object、二重rAF後のPython開始、既存の10秒期限は維持する。Macも同じ固定コードを使い、既存の`-B`は維持する。Macの実ビルド・WKWebView・生成.appは今回未確認。

UTF-8化の対象はPythonのテキストストリーム。通常の`open()`の既定文字コード・UTF-8 modeは変えない。アプリがストリームを再設定・差し替えた場合、`sys.stdout.buffer`、`os.write`、native拡張の生バイト、設定前のインタープリター障害は対象外。旧ログを変換・上書きしない。

障害説明は`RuntimeError: メッセージ`の形に整理し、同じ停止段階を二重に付加しない。停止段階は画面の専用欄とhost.logの`stage`/`last_worker_phase`に保持する。公開RPCと内部失敗フレームのスキーマ・上限は変更していない。

選択肢と公式資料は[ADR-018](architecture.md#8-adr候補一覧)を参照する。

## 環境と成果物

| 項目 | 今回確認した値 |
| --- | --- |
| OS / CPU | Windows 11 Pro、10.0.26200、64bit / Intel Core i5-8250U、Architecture=9（x64）。前回の25H2環境を継続利用 |
| Python | 既存`.venv\Scripts\python.exe`、CPython 3.14.10、MSC v.1944 AMD64、`Py_GIL_DISABLED=0`、GIL有効 |
| Rust | 明示指定した1.89.0（29483883e）、`x86_64-pc-windows-msvc` |
| WebView2 | 既存Runtime登録版156.0.4314.58 |
| release exe SHA-256 | `2f7e91d10c980daeec9356d60522bbc7ac5ac536a18ce437bcf3e8555687f5a5` |

既存ツールと依存を使用し、セキュリティ・権限設定は変更していない。検証用アプリは`test-results/worker-logs-20261105/`のコピーだけを変更した。

## 検査結果

| 検査 | Windowsでの実際の結果 |
| --- | --- |
| MSVC releaseビルド（`--locked`） | 成功、終了コード0 |
| Python全体（非推奨警告をerror指定） | **112成功・19スキップ・失敗0** |
| 新規ログ結合検査 | 上記112件のうち4件。単独実行も4/4成功 |
| Rust releaseテスト | **10成功**（lib 9、host 1）、失敗/ignore 0、doc-test 0 |
| Clippy | MSVC release・全ターゲット・`-D warnings`成功 |
| Ruff / format / Cargo fmt / diff空白検査 | 成功。新規bootstrapも明示してRuff対象に含めた |
| 既存実GUIスモーク | 単独1回成功、`native_smoke.ok=true`、終了コード0、watchdog終了なし |

スキップ19件は、symlink権限WinError 1314が3、Darwin renamex_npが1、POSIX SIGINTが1、既存POSIX fdワーカー検証器が14。今回追加した検査はWindowsの実継承ハンドルを使って実行しており、この14件を実行済みへ変更していない。

追加した結合検査では通常/import失敗/起動フック失敗の3ケースで、標準出力・標準エラー・logging・日本語/絵文字・孤立サロゲートの扱い・厳密なUTF-8 decodeを確認した。通常ケースは10,000行出力後もRPC応答とshutdownが成功。隔離モードは有効で、ファイルの既定encodingとUTF-8 modeは新しい`python -I`の値と一致した。4件目はCP932を設定した状態からPyYakiパッケージ自体のimport失敗を注入し、worker本体に入る前のtracebackもUTF-8となることを確認した。

ビルドと全Python検査は経過時間が約26分だった。途中の端末状態を継続観測しておらず、性能値には使用しない。GUI検査はそれらとClippyの完了後、順に単独で実行した。

## 実GUIと終了処理

| ケース / 自分で生成したホストPID | 結果 |
| --- | --- |
| 既存スモーク / 9824 | UI/RPC、エラー、並行呼出し、イベント順序/購読解除、取消に成功 |
| 通常起動・終了 / 22208 | Python接続済みを実画面で確認し、閉じる操作で終了コード0。終了フックの日本語・絵文字もログに保存 |
| 意図したimport例外 / 12732 | `app_import_started`→`worker_failed`→`backend_failed`。`app_imported`とwelcomeなし。例外表示後に閉じて終了コード1（期待した失敗） |
| ホスト強制終了 / 12340 | 起動フックで記録したworker PIDと親子関係を照合後、自分のPopenハンドルで強制終了。終了コード1、終了フック未実行（期待どおり） |

通常・import例外・強制終了の3ケースのworker.log（144 / 1,306 / 115 bytes）はいずれもUTF-8 strict decode成功。標準出力・標準エラー・loggingの日本語/絵文字を照合した。例外では完全なtracebackと日本語例外を保存。画面は「停止した段階：アプリの読み込み」と`RuntimeError: PYYAKI_UTF8_PROBE: 検証用import例外 😀 2026-11-05`、両ログの案内を表示し、英語の段階説明の重複がないことを画面・UIAテキスト・host.logで確認した。

3ケースとも観測した全子プロセスの終了ハンドルを保持でき、Python/WebView2の残留は0。ホスト終了から回収確認・ログ複写を経て結果を記録するまでの経過は通常約1.37秒、import失敗約2.62秒、強制終了約0.77秒だった。純粋な回収時間の測定ではない。この観測の範囲外の子プロセス一般や、出力の完全な永続化を保証しない。全ケースでshell_markerより後にPython生成が記録されている。

## 生ログと残課題

生ログはGit管理外の`test-results/worker-logs-20261105/`に保存した。`build`/`pytest`/`rust-test`/`clippy`/`gui-*`のlogと実行JSON、`smoke/report.json`と各ログ、`manual-{normal,import-error,forced}/`のコピー・host/workerログ・生成PID・終了結果、`gui-import-0.png`・UIA記録、`evidence-summary.json`、`windows-environment.json`を保持する。GitHub経由では本書を共有できるが、生ログや画面は自動的には転送されない。

このログ修正直後にはimport/フック5秒の各3回反復を行わず、下記の追加追試で確認した。前回の9/9を修正版へ流用していない。引き続き未確認: Macの変更後の実ビルド・WKWebView・Python同梱.app、以前のWindows突発失敗の原因・解消、物理的先行描画と性能、ネットワーク/境界試験の残り、企業環境、長時間稼働。単一exe生成は引き続き未実装。

## 修正版の起動反復の追試（2026-11-05）

対象コミット`df2cded5bd25f3bc056b90cf38525a71d015394f`、開始時クリーン。上記と同一のexeハッシュ、Windows 11 x64・既存CPython 3.14.10通常GIL版のvenvを使用した。Rust/WebView2は上記の既存環境を継続利用し、追加ビルド・依存更新はしていない。他のビルド・試験と並行せず、次を実行した。

```powershell
.\.venv\Scripts\python.exe tests\startup_probe.py --binary native\target\release\pyyaki.exe --runs 3 --delay 5 --output test-results\windows-startup-20261105\startup
```

**通常3/3、import前5秒3/3、起動フック内5秒3/3、合計9/9成功**。全件で終了コード0、スモークok、watchdog終了なし、`sdk_welcome_posted`あり。二重rAFのscheduled/frame1/frame2、shell通知後のPython生成、workerの6段階の固定順序、UI/worker期限がともに10,000msであることを元ログから照合した。worker_failed/backend_failed/SDK期限/ホスト期限の記録は0件。

| ケース | shell通知 | Python生成完了→worker通知 | import区間 | 起動フック区間 | SDK welcome送信 |
| --- | ---: | ---: | ---: | ---: | ---: |
| baseline-00 | 2101.7 | 1032.3 | 58.0 | 0.5 | 3223.3 |
| baseline-01 | 2085.5 | 908.2 | 63.4 | 0.6 | 3090.4 |
| baseline-02 | 2210.3 | 862.8 | 68.0 | 0.9 | 3173.0 |
| import-00 | 1900.1 | 1222.6 | 5112.9 | 0.7 | 8273.0 |
| import-01 | 1951.6 | 998.8 | 5124.5 | 1.3 | 8118.3 |
| import-02 | 1791.4 | 1209.4 | 5158.8 | 1.4 | 8201.0 |
| hooks-00 | 1871.2 | 920.2 | 87.8 | 5002.1 | 7922.0 |
| hooks-01 | 1952.2 | 937.1 | 65.7 | 5009.2 | 8007.5 |
| hooks-02 | 1972.4 | 952.8 | 70.7 | 5014.8 | 8050.4 |

単位ms。shell/welcomeはホスト入口からの通知時刻、他は通知間の差で、スケジューリング・IPCを含む。物理的な画面提示やNFRの性能達成の測定ではない。以前の突発失敗は未再現であり、原因解消やMacのSpace問題との同因とは判断しない。

元ログ・アプリのコピー・実行結果は`test-results/windows-startup-20261105/startup/`、実行全出力は同親の`startup.log`、コマンドと終了コードは`startup.json`、段階照合は`startup-summary.json`へ保存。Macには自動転送されない。今回は既存反復検証器を使い、追加のプロセス残留監視や録画は行っていない。
