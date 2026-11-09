# WindowsネイティブCLI・pip導入の実機検証

実施日: 2026-11-09（日本時間）。Windows向けのローカルwheel・ネイティブCLI実装と実機検証の区切り。未公開で、G1/G3・正式配布全体の受入完了ではない。手順と対応範囲は[windows-wheel.md](windows-wheel.md)、要件は[requirements.md](requirements.md)。Macの成功件数は以下に含めない。

## Git・対象と実装

開始時のmainと作業ツリーはclean。ローカルAGENTS.mdを確認し、fetch後のorigin/mainとHEADは`0949e8a3273b2d773e13e4494ab06ab03b58183a`で一致した。同コミットと`babe85e`・11-09更新を祖先に含む。最初のsandbox内fetch/mergeは.git書込み拒否だったため、同じff-only操作を許可された実行で確認した。stash/reset/既存変更の取消はせず、`codex/windows-native-cli`を作成した。pushはしていない。

製品実装の対象は`3d08baead8ab44f4f69011132b09eecf5c7ceac3`。

- `0c36f15`: Windows用console入口・venv静的探索・同版native wheel組立器。runはPythonを先に起動せず、同じメインスレッドのGUIコードへ入る。固定bootstrapでCPython 3.14通常GILを検査する。
- `1108b05`: Windowsのポート排他予約、dev/buildのPython生成前にCLIを専用Jobへ割当。唯一の非継承Job handleをCLI終了まで保持し、強制終了で委譲した子孫が残らないようにする。
- `3d08bae`: Windowsの同サイズ/同mtime保存の検出、reloadのselector loop、CLI/子サーバーのUTF-8テキスト出力。ネイティブworker・通常ASGIのloop、通常ファイルの既定encodingは変えない。
- `4b4b360`: Windows wheel/非表示の専用console検証器とWeb検証器のWindows対応。

最終native reportの開始時HEADは`3d08bae`、dirty=trueは未コミットの検証器/文書を含む。最終pure wheel内のPython/SDK等19ファイルは現在の`src/pyyaki`と全バイト一致、差異0（`package-source-match.json`）。Rust exeは`release-job.log`のMSVCビルドで、後続の製品変更はPythonだけ。各中間試験のHEAD/dirtyを最終SHAへ書き換えていない。dev-sixthは`1108b05`時点の検証venvコピーへ後の`3d08bae`と同じPython修正を反映した試行であり、cleanコミットをpip導入した試行と混同しない。

## 実環境と資材

| 項目 | 確認値 |
| --- | --- |
| OS / CPU | Windows 11 Pro 25H2、10.0.26200.9711、x64 / Intel Core i5-8250U |
| Python | `C:\Users\user\dev\PyYaki\.venv\Scripts\python.exe`、CPython 3.14.10、MSC v.1944 AMD64、Py_GIL_DISABLED=0。基底は`C:\Users\user\AppData\Local\Programs\Python\Python314` |
| Rust | 明示した1.89.0（29483883e）、x86_64-pc-windows-msvc。既定Cargo 1.84.1は変更していない |
| WebView2 / Edge | 既存Evergreen・Edge 157.0.4341.42。onefile生成には既存のFixed Version 156.0.4314.58 x64 |
| Python依存 | FastAPI 0.162.0、Pydantic 2.14.2、Uvicorn 0.54.0、websockets 15.0.1。Windows用lockの15 wheelをローカルで再利用 |
| 権限・保護 | Administrator=false。読取り確認したAntivirusEnabled / RealTimeProtectionEnabled / IsTamperProtectedは全てtrue。保護・プロキシ・OS設定を変更しない |

最初のCIM照会はsandboxで拒否された。許可された読取りでOSと保護状態を確認した。registryのProductNameだけではWindows 10と表示されるため、CIMのWindows 11 Pro・buildを記録する。メンテナーのRust/MSVCとPythonは既存資材を使用し、新規のツール・ブラウザ取得は行っていない。

最終成果物はGit管理外の`test-results/windows-native-cli-20261109/`に保存した。

| 資材 | SHA-256 |
| --- | --- |
| `wheel-complete/pyyaki-0.1.0.dev0-py3-none-win_amd64.whl` | `f8a3169d7ee92bc1a7eecfde9b650322f8fb4a6453c3d766345d8d6e83aac8d7` |
| `pure-complete/pyyaki-0.1.0.dev0-py3-none-any.whl` | `086c00169c5d227a5122463dd20caec1df5706664c6e34c23e31d8637fffb3bb` |
| native CLI（1,910,272 bytes） | `a36c3274a9a1808c2415d132c479e7d8262fefa1c346feb82b0b7888debefa02` |
| GUIホスト（2,056,704 bytes） | `ede4a485c0181080e542fbdf0a97923bd272bbef9bce07fa0cbb9915c88be136` |
| `build-dist/Hello.exe`（746,957,880 bytes） | `85ad9b902a3251b0aa420a322140db3094156c1e52242a8a34ee70b46e756a1a` |

タグは`py3-none-win_amd64`、Root-Is-Purelib=false。pipが`.data/scripts/pyyaki.exe`をvenvのScriptsへ配置し、RECORDに記録する。Python console scriptを重ねない。同梱GUIホストは`Lib/site-packages/pyyaki/_native/windows`。CLI/ホストはAuthenticode未署名（NotSigned）で、署名の信頼・配布許諾を確認した扱いにしない。Python・SDK・ホストの版、サイズ・ハッシュ、PE CPU/subsystemを検査する。

## 確認結果

| 検査 | 実際の結果・範囲 |
| --- | --- |
| Python全回帰 | **198成功・26スキップ**、失敗0。`pytest -q -rs -W error::DeprecationWarning`、pytest-complete.log |
| スキップ | symlink権限6、Mac symlink検証1、Darwin renamex_np 1、POSIX SIGINT 4、POSIX workerパイプ検証14。Windowsの合格へ算入しない |
| Rust | **20成功・2 ignored**。library 16、GUI binary 2、CLI binary 2。両binaryで共通試験を実行した件数で、20種類の独立ケースという意味ではない。ignoredは親が明示実行する子entry。doc-test 0 |
| 静的検査・ビルド | Ruff check/format（53ファイル）、Cargo fmt、all-target Clippy `-D warnings`、locked MSVC release成功。incremental cacheのhard link不可→コピーの警告は原ログに残す |
| native wheel一括 | **21/21項目、未完了0**。native-complete/report.json。日本語・空白の新規venv、`--no-index`・固定lock、Cargo/rustc/clが見つからないPATHで導入・pip check |
| CLI/venv | help/version、dev/build help、buildの必須資材不足の終了2、runのPython override拒否。worker executable/prefix/packageが導入venv由来。PATHの別Pythonへ切替なし |
| 実WebView2 | 通常/import前5秒/起動フック5秒を**各3回、9/9**。RPC・APP_DEMO・イベント・取消・終了、UTF-8 stdout/stderr・日本語例外DOM/HTML非実行を確認 |
| 起動順 | 全9回でnative_draw・二重rAF・shell後のPython生成・worker/welcomeの順序と10,000ms期限を共有検査器で照合。物理的な画面提示時刻・性能の合格にはしない |
| 終了 | 自作専用consoleへ実CTRL_C_EVENTを配送し、native通常終了0・終了フックを確認。native/委譲devのCLIだけを強制終了した場合はJob配下の観測PIDが残存0、終了フックは期待しない |
| 明示エラー | 同梱ホスト欠落・破損、manifest版/CPU不一致、venv外のコピー、runの--python指定を拒否。実ARM64機・破損exeの全形態を試験した意味ではない |
| 同版の導入ライフサイクル | native再導入、pure置換でnative資材消失、native復旧、uninstall後のCLI/host/manifest/import消失。異版更新は別項目で扱う |
| 別版の更新 | 新しい日本語・空白venvでdev0→ローカル検証用dev1へ`pip --upgrade`、dev1→dev0へdowngrade。両方向の版表示・旧dist-info消失・実WebView2スモーク2/2・起動順・観測PID残存0・最後のuninstall/import消失を確認。正式版リリース同士の互換性試験ではない |
| Web dev | **10/10項目**、Edgeのpageerror 0。sandbox有効、2ページRPC・HTML/CSS/JS更新、古いpyc/同サイズ同mtime保存、Python追加/削除、構文/TOMLエラー修正復帰、終了フック、active WebSocket付き実Ctrl+C、--no-reload、実ポート競合 |
| build windows | pip導入したnative CLIから、Cargo/MSVCのないPATHで明示ホスト・埋込Python・Fixed Version・15依存wheel・pure framework wheelを使い、未署名単一exeを新規生成して終了0 |
| 生成exeの回帰 | 実WebView2のスモーク**1/1**、watchdogなし、観測PID残存0。onefileの過去の全破損/中断/境界行列は繰り返していない |

Windows Web devはselector loopをreloadだけで使う。asyncioのpipe/subprocessと512を超えるsocketは対象外で、該当する開発は`--no-reload`を使う。Python/設定の内容ハッシュ読取りによる大規模監視の負荷は未測定。比較・一次資料は[ADR-024](architecture.md#8-adr候補一覧)。

native通信はカスタムプロトコル/匿名パイプを維持する。最終CLIのSHA-256を照合したPID6184の所有プロセスに絞ったnetstatの観測ではTCP LISTENは0、終了後の観測PID残存0。`manual-native-final/network.json`とconsole/result.jsonを保存した。WebView2の外向きconnected UDP（443）を観測したため、UDP endpoint 0・完全な通信無発生・物理的オフラインを合格にはしない。アプリRPCのlocalhost待受と、WebView2自体の外向き通信を区別する。

## 原ログと失敗

以下の失敗は後の成功で上書きしていない。生ログ・コピー・生成物は同じ`test-results/windows-native-cli-20261109/`配下に残した。

1. `pytest-initial.log`はsandbox内で無出力の停止。今回のPID/コマンドを再照合して中断し、`pytest-initial-abort.json`へ記録した。停止原因の断定はせず、許可された実行のpytest-secondは196/26、最終は198/26。
2. `pure-initial.log`/`pure-final.log`はsandboxの既定Temp書込み拒否。wheel-initialとupgrade-buildの初回はhard link作成拒否。新しいtest-results内Tempと、許可された実行で別出力へ生成した。OSの保護を無効化して回避していない。補助runnerのCP932出力エラーも残し、以後の補助出力を明示UTF-8にした。
3. wheel-regression-firstは検証器の環境変数キーの大文字/小文字ミス。secondは`\\?\`付きパスを別venvと誤判定した。secondのnativeスモーク自体は成功したが、試行全体は失敗。実ファイル同一性へ修正したthirdは20/20、Job回収を追加した最終は21/21。
4. dev-firstは初回起動の外部12秒待機で未完了。初回の停止理由は未確定。外部検証器の起動待ちだけを40秒にし、nativeの10秒期限は変えていない。
5. dev-secondはWindowsでSO_REUSEADDR同士の二重bindを許し、競合コマンドが15秒で終了しなかった。補助subprocessのpipeを子が保持して待ちが続き、旧CLIを終了させてもPythonが残った。所有PID・親子関係・起動時刻を`dev-second-owned-tree.json`等に残して回収。排他予約と委譲Jobを別修正し、最終の実競合・強制終了回収で確認した。
6. dev-thirdはPlaywright用Chromiumが未取得。既存Edgeを明示して再試行し、sandboxは維持した。
7. dev-fourthは同サイズ・同mtimeの保存を見逃した。Windowsのst_ctimeは作成時刻のため、Python/設定の内容ハッシュを追加した。
8. dev-fifthは再起動後の共有listenerを別IOCPへ登録してWinError 87。CP932の例外ログに対するUTF-8読取りでも失敗した。selector loopとCLI/子サーバーのUTF-8を反映したdev-sixthで10/10確認。生バイトの旧ログは変換しない。
9. port-testは既存テストの`"port" not in stderr`が試験ディレクトリ名のportにも反応して1失敗。エラー本文の意図に合う検査へ修正し、最終の全回帰で成功した。
10. 別版更新の初回は検証用dev1のSDKだけdev0で、shell/Python開始前に版不一致のスモーク失敗。worker.logがないことを検証器が想定せず、ログ複写でも失敗した。SDKも揃えた2回目は資材生成用PYTHONPATHがpipへ継承され、venv外sourceのdev1を導入済みと誤認した。いずれもfailed report・元資材・元ログを保持。最終の`upgrade-validation-final/report.json`は別の新規venv・環境変数除外・isolated pipでpassed、GUI 2/2。dev1は全ソースの版スタンプを変更したtest-results内のfixtureで、公開版を追加したものではない。

Computer Useでは所有窓のタイトル/アクセシビリティは取得できたが、後の画像は背後のdesktopを含み、窓の終了との競合もあった。**物理的なUI/GDI提示・録画は未確認**として扱う。実DOM/RPC・段階ログの成功へ目視結果を加算しない。

主な保存先:

終了時の読取り確認では、この検証パスに一致するPython/CLIプロセスは0（`final-owned-process-check.json`）。各GUI試験は観測handleで終了を判定しており、未観測の全子孫・全ブラウザの一般的な回収保証にはしない。関連13文書のローカルリンク282件（ファイル・見出し）、git diff空白検査に成功した。文書だけの最終更新に実装テストは追加していない。

```text
test-results/windows-native-cli-20261109/
  environment.json / os.json / defender.json / signature.json
  pytest-complete.log / rust-job.log / clippy-job.log / release-job.log
  package-source-match.json
  wheel-complete/*.whl / *.build.json
  native-complete/report.json
  native-complete/<case>/host.log / worker.log / console/*
  dev-first～dev-sixth/          # 各report・server.log・コピー・失敗
  dev-sixth/trace.zip / before.png / after.png
  build-cli-command.json / build-cli-command.log
  build-dist/Hello.exe / Hello.build.json
  built-onefile-smoke/smoke/result.json
  upgrade-validation*/          # 各試行のpip・版・GUI原ログ
```

## 残課題と次の候補

- Python/Rust/MSVC未導入のクリーンWindows、標準ユーザー・物理ネットワーク切断、MOTW/SmartScreen/企業PAC・AppLocker/WDAC。PATH制限と--no-indexで代替しない。
- 実画面の先行提示とNFR-01の正式計測、長時間・高負荷、別OS/CPU・最低WebView2、実署名後の索引/配布確認。
- venv writer保持による部分closeは既存制約のまま。今回のプロセス階層はnative runが同一PID、workerのvenv launcher/Jobは維持。部分close・全origin/ハンドル境界行列は今回再実行していない。
- システム導入/pip --user・venv移動・全再parse形態、使用中exeの更新/中断、実際の正式版同士のupgradeと署名/来歴。ローカルdev1 fixtureの検証とは区別する。
- shared bootstrap等を変更したためMacの回帰が次候補。今回Mac試験は実行していない。Windows reloadのselector制約を超える方式・監視負荷はADR-024で検討する。
- 正式ライセンス選択、LICENSE/NOTICE/SBOM、公開、依存バイナリ再配布は別々の判断。今回はGitのローカルコミットまで。
