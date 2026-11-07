# Windows単一exeローカル試作の検証（2026-11-07）

実装・予備試験は2026-11-05、最終実機試験は2026-11-07（日本時間）。利用者が依頼した「3」の最小単一exe試作を実施した。[生成・起動手順と制約](windows-onefile.md)を参照。**FR-08/G1/G3全体や正式配布を合格にはしない。**

## 対象と環境

| 項目 | 最終確認 |
| --- | --- |
| 製品ソース | `f40bdc103cae9b363b0c79adcf6e769f8c4ed8ce`。生成時はgit clean。後続は検証器と文書のみ |
| ホスト SHA-256 | `999587229ce5c20e081ff9afac1f19dde5574bd61d05b23710743351d600e25d` |
| framework wheel SHA-256 | `a8f69828423a9346750559fcc89a3dcd976a2b1b0c33b6949ccb1ad78ba1b04f` |
| 生成exe SHA-256 | `e5269f35a68623ded54276905521804fe0a33d1fe5adeb8105ec856a57a87aec` |
| 生成exe | 746,925,088 bytes、未署名・非圧縮。入力blob合計 744,730,206 bytes / 874通常ファイル |
| OS / CPU | Windows 11 Pro 25H2 x64、10.0.26200.9711 / Intel Core i5-8250U。11-05の予備試験は26200.8246で、最終試験と区別 |
| Python | 開発venv・同梱候補ともCPython 3.14.10 x64、通常GIL。実ワーカーでGIL有効・Py_GIL_DISABLED=0を確認 |
| Rust / MSVC | rustc/Cargo 1.89.0、x86_64-pc-windows-msvc。導入済みtoolset 14.44.35207、cl 19.44.35226.0 / link 14.44.35226.0 |
| WebView2 | 既存登録版・同梱Fixed Versionとも156.0.4314.58。実プロセスのパスが検証用キャッシュ内のFixed Versionであることを確認 |
| 権限・保護 | Administrator=false。Defender Antivirus/RealTimeProtection/TamperProtectionはtrue。設定変更・保護無効化なし |

既存変更がないことを確認して着手。開発venv、既定Rust、既存Python/WebView2を更新・削除していない。公式取得資材と15依存のlockは[前段記録](windows-onefile-prototype.md)を継続利用し、framework wheelだけを今回のソースから生成した。生成用子環境のPATHを`C:\WINDOWS\System32`に限定してCargo/rustcがない状態でも、ネット取得なし・事前取得wheelだけの組立に成功（58.404秒）。これはRust/MSVC未導入のクリーンPCでのpip導入試験ではない。

## 検査

| 検査 | Windowsでの結果 |
| --- | --- |
| MSVC release `build --locked` | 成功。上記ホストと同一ハッシュ |
| Python `pytest -q -rs -W error::DeprecationWarning` | 125成功、20スキップ、失敗0 |
| スキップ内訳 | symlink権限 WinError1314が4、Darwin renamex_npが1、POSIX SIGINTが1、既存POSIX pipe harnessが14。junctionの実機追試とは別 |
| Rust release `test --locked` | library 14 + main 2 = 16成功、1 ignore（Job負対照が明示起動する子entry）、doc-test 0、失敗0 |
| Clippy / fmt / Ruff | MSVC release all-targets -D warnings、Cargo fmt、Ruff check/formatに成功 |
| 既存ホスト反復 | 通常/import前5秒/起動フック5秒を各3回、9/9成功。全件で二重rAF→shell→Python→worker6段階→welcome、両期限10,000msを確認 |

反復のレポートはHEAD=`430240c`・working_tree_dirty=trueを記録している。その差分は文書と検証器だけで、Rust/Python製品ソースはf40bdc1と同じ。バイナリのSHA-256も上記と一致する。Macのビルド・WKWebView・生成.appの再確認は未実施。

## 単一exeの実機結果

下表の**採用14ケースはすべて所定の確認に成功**。GUIスモークは6件でUI/RPC・エラー・並行要求・イベント/解除・取消を確認。通常closeとホスト強制終了はwelcome後に別確認した。各採用ケースでwatchdogなし、観測プロセスの確認ハンドル取得漏れなし、観測したプロセスの残留なし。終了コード1は意図した強制終了または拒否であり、正常終了のコード0とは区別する。

| ケース | ランチャーPID | 終了コード | 外部起動～終了 秒 | キャッシュ |
| --- | --- | --- | --- | --- |
| 未完了staging後の初回確定/スモーク | 18764 | 0 | 31.473 | 新規 |
| キャッシュ再利用/スモーク | 13716 | 0 | 18.186 | 再利用 |
| GUIメイン窓の通常close | 3392 | 0 | 23.421 | 再利用 |
| welcome後のランチャー強制終了 | 760 | 1 | 18.303 | 再利用 |
| 実展開中のメイン窓close | 14256 | 0 | 0.454 | 未確定 |
| 実展開中のランチャー強制終了 | 22380 | 1 | 0.449 | 未確定 |
| 実展開中断後の再起動/スモーク | 3200 | 0 | 25.973 | 新規 |
| 共用キャッシュ同時起動1 | 4160 | 0 | 35.563 | 新規 |
| 共用キャッシュ同時起動2 | 632 | 0 | 50.079 | 再利用 |
| キャッシュ改変の拒否 | 16544 | 1 | 6.192 | 未確定 |
| exe同梱blob改変の拒否 | 1204 | 1 | 24.078 | 未確定 |
| exe索引改変の拒否 | 10672 | 1 | 32.250 | 未確定 |
| キャッシュjunctionの拒否 | 21448 | 1 | 5.952 | 未確定 |
| 日本語パス・読取専用exeだけの再配置 | 9704 | 0 | 60.969 | 新規 |

- 実展開中の取消はstagingへ28ファイル/224,588 bytes、強制終了は22ファイル/58,387 bytesが書かれたことを保存。未完了stagingは実行せず残し、後続起動は新しく確定したキャッシュから成功した。取消時は`Preparation cancelled`がログへ残る場合がある。
- 同時起動は別Popen・別プロファイルの2件だけを意図して重ねた。片方が新規確定、もう片方が全ハッシュ検証して再利用した。終了・強制終了は自分のハンドル/PID/メイン窓だけが対象。
- キャッシュ改変は`Cache hash mismatch`、blob改変は`Onefile payload hash mismatch`、索引改変は`Onefile index hash mismatch`、junctionは`Reparse point is not allowed`で準備段階に停止。GUI子ホスト・Pythonを開始していない。自動上書き修復はしない。共有する検証用キャッシュのapp.py/UIだけをマニフェスト一致の元データへ戻し、改変exeは別コピーのまま保存した。
- GUI動作8件で、実Python executable/prefix/sys.path/PyYaki/native pydが採用キャッシュ内、isolated=1、site未import、bytecode書込みなし、通常GILを照合。6件の実スモークに加えて通常close/強制終了も含む。外部PYTHONHOME/PYTHONPATHとWebView2パスは存在しない値を子環境へ渡して負対照にした。
- 再配置先には同じSHA-256のexeだけを置き、exeの読取専用属性を設定して日本語の配置先/ユーザー領域で成功。**配置ディレクトリの書込み拒否ACLを検証したものではない。**

## 失敗・中断と切り分け

1. 初期の再起動検証器で、関数とローカルsetの名前が衝突してTypeErrorとなった。原ログ`cached-trial.log`を保持し、関数名を修正した`cached-recheck`で成功。製品の起動失敗とは区別する。
2. 最終初回の試行は外側の検証プロセスが34.058秒でコード`1073807364`に終了し、result.jsonがなかった。原因は未確定。PID7736のログはpreparation_startedまで、未完了stagingだけが残った。合格にせず、同じ領域で別の試行として再起動したものが表のPID18764。
3. 修正前のmatrixは8件終了時点で6確認・通常close/準備closeの2件が120秒watchdogとなった。同じPIDの全窓へCloseを送る検証器だった。可視窓だけに絞った中間追試も一意選択できず、Close未送信でwatchdogに達した。**これらを製品の正常な利用者closeの結果に採用しない。**
4. `control.json`でメイン窓`Window Class`のほか、内部`Tao Thread Event Target`もvisible=true、IME窓も存在することを確認。固定版taoの窓クラス・スタイル・所有PIDでメイン窓1つを識別するよう検証器を修正し、通常close/実展開close/障害画面closeを追試して成功。製品コードは変えていない。
5. 不適切なCloseを続けないため、識別済みのmatrix Python PID16160と、その子の検証exe PID11024だけをハンドルで照合して終了。中断理由とPID/生成時刻を`matrix-abort.json`へ保存した。破損注入で残った自分のapp.pyを、build manifest一致の元コピーから復元して追試した。この打切りmatrix全体を合格とはしない。

非採用の失敗・中断、途中のログを上書きしていない。準備スクリプトの環境変数名表記の修正、CIM照会のsandbox拒否後の読取専用再照会も、製品修正と分ける。

## 時間の観測と限界

表の秒数はスモークのRPC待ちや終了までを含み、起動時間そのものではない。少数標本でOSページキャッシュを制御せず、検証器のSHA読取りも行っているため、NFR-01の正式計測やp95にはしない。

未確定キャッシュからのPID18764は、外部起動から描画ログの最初の観測2.076秒、welcomeログ28.520秒、終了31.473秒。再利用PID13716はそれぞれ0.218秒、15.369秒、18.186秒だった。別の再配置・初回実行では描画ログまで29.892秒、welcomeまで54.746秒を要した。アプリ内部のGDI呼出しログの短さだけで先行描画の速度を保証しない。外部起動・ログ観測とアプリ内部時計との差の原因、実画面の提示時刻、Defenderとの因果は未確定で、保護を無効化した比較はしていない。

Computer Useで生成exeの窓情報は得られたが、画面取得は`Computer Use app approval timed out`で未取得。**今回生成した単一exeの画面・Explorer/タスクバーのC案の目視は未確認**。既存開発ホストでの以前の表示確認と区別する。実WebView2でのDOM/RPC確認は上記スモークの範囲。

## 生ログ・コミットと次の作業

着手日の名前を維持した`test-results/windows-onefile-20261105/`に、最終生成物`dist/Hello.exe`/`Hello.build.json`、`assemble-final.json`、環境/ツール版、全検査ログ、`evidence-summary.json`、`final-startup/`、採用表の各result.jsonとhost/worker/launcher.logを保存した。表の根拠パスはevidence-summaryのraw列にある。`final-matrix/`は元の失敗を含む。検証用コピー・改変exe・未完了staging・全補助スクリプトも保持し、Gitへバイナリや生ログを追加していない。Macへ自動転送されるものではない。

コミット: `f40bdc1`（製品・CLI・形式・基本試験・手順）、`f579b70`（対象SHA）、`5b20417`（可視窓限定の中間修正）、`cb8a0d7`（内部イベント窓を除外する最終修正）、`430240c`（書込み開始後の中断モード）。本記録と現在地の更新は文書コミットへ分ける。

残課題の候補:

1. Python/WebView2未導入・標準ユーザー・物理オフラインのクリーンWindows VM。既存環境のパス負対照では代替しない。VC/UCRT/外部DLLと企業実行制限も確認する。
2. 実Authenticode署名後の索引読取・改ざん拒否とMOTW/配布試験。今回のPE証明書テーブル試験は人工構造で、実署名の成功ではない。
3. 圧縮方式、二段階窓/二ホストの見直し、初回/キャッシュの定義した性能測定。速度のために全ハッシュ検証や保護を黙って弱めない。
4. 容量不足、電源断、展開完了とcloseの同時到着、使用中旧版・stagingの清掃、ACL/全reparse形態、全native依存、署名の信頼起点、LICENSE/NOTICE/SBOM、wheelへのホスト配置・資材配布。正式なライセンス採用・公開・依存バイナリ再配布は別の判断とする。
5. Macの共通CLI回帰、WKWebView・生成.app、未解消のSpace問題。Windowsの既知の初回失敗と同じ原因と断定しない。
