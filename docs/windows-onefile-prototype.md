# Windows単一exe試作の準備と同梱候補の確認

これは2026-11-05の**実装前の資材確認の履歴**。後続の実装済み範囲は[単一exeローカル試作](windows-onefile.md)を参照。以下の「未実装」は当時の状態。

更新・実施日: 2026-11-05（日本時間）。**単一exe生成・自己展開・`pyyaki build windows`は未実装。今回確認したのは、その前段の「固定資材を配置したフォルダーから既存ホストを動かす」段階である。** フォルダーをFR-08の単一exe成果物の代替にはしない。

優先依頼の[起動反復9/9](validation-worker-logs-2026-11-05.md#修正版の起動反復の追試2026-11-05)と[境界・異常系](validation-windows-boundary-2026-11-05.md)を終えてから、同梱候補を準備した。製品コードとCLIは変更せず、コピーと取得資材・補助スクリプトをtest-resultsへ置いた。既存Python/WebView2/Rustの更新・アンインストール、セキュリティ設定変更、公開・再配布はしていない。

## 確認した入力

| 入力 | 確認した版・内容 |
| --- | --- |
| 開発端末 | Windows 11 Pro x64、i5-8250U。今回のトークンでもAdministrator=false |
| ホスト | 既存MSVC release、SHA-256 `2f7e91d10c980daeec9356d60522bbc7ac5ac536a18ce437bcf3e8555687f5a5` |
| Python | 公式CPython 3.14.10 x64 Embeddable Package、通常GIL版 |
| WebView2 | 公式Fixed Version 156.0.4314.58 x64。展開後のFileVersion/ProductVersionも同じ |
| framework | `f3fb286`時点の製品ソースからローカル生成した`pyyaki-0.1.0.dev0-py3-none-any.whl`。後続の変更は検証器・文書で、製品コードは同じ |
| 依存 | 既存Windows環境の実際の推移依存15件と同じ版を公式PyPIからwheel限定で取得。framework wheelと合わせた16件をハッシュ固定して配置 |

Python ZIPは[公式リリースページ](https://www.python.org/downloads/release/python-31410/)掲載のSHA-256と一致した。ファイルは`python-3.14.10-embed-amd64.zip`、12,619,664 bytes、SHA-256は`90905e64a840557d8c1bc4f5b74db3d7ee85e3ffb217611cac1c7813acea4159`。

Fixed Version CABは[Microsoft公式配布ページ](https://developer.microsoft.com/en-us/microsoft-edge/webview2)掲載のx64リンクから取得し、Windowsのexpandで257ファイルへ展開した。CABは310,195,262 bytes、実際に得たSHA-256は`857dcfd6f7035508a29645fa2978c3a6a841f220df77cc366d318894e19f4792`。これは観測したハッシュで、公開済みの別ハッシュとの照合ではない。Pythonのpython.exe/python314.dllはPython Software Foundation、msedgewebview2.exeはMicrosoft CorporationのAuthenticodeでStatus=Validだった。全ファイルの署名・来歴・再配布条件を確認済みという意味ではない。

依存15件の固定版・使用wheelのハッシュは[Windows用サンプルlock](../examples/hello/requirements-windows.lock)へ記録した。これは今回のWindows x64 / CPython 3.14通常GIL版で確認した組合せであり、他OS/CPU/free-threaded版には流用しない。Mac用lockもWindows配布の根拠にしない。framework wheel自体のSHA-256は`64c7f1760f246cf86c6c7ffc246df32c813c54e6ae836b7f7311a54fec1911e1`。wheelの再生成でこの値が常に再現するという保証はしていない。

## 実施したフォルダー構成と検証

```text
payload/
├─ host/pyyaki.exe
├─ runtime/python/       # 公式埋込Python、標準ライブラリ、制御した._pth
├─ runtime/webview2/     # Fixed Versionの展開内容一式
├─ packages/            # pipで配置したPyYakiと15件の依存
└─ app/                 # helloのコピーと実機観測用起動フック
```

開発用Pythonのpipで、取得後のwheelhouseだけから`--no-index --only-binary=:all: --require-hashes --no-compile --target ...`を使って配置した。開発venv全体のコピーやwheelの単純なunzipは行わない。埋込Python内でpipを導入・実行していない。

`python314._pth`は次の内容。site初期化・`.pth`実行は有効にせず、今回のpackagesに`.pth`/sitecustomizeがないことも確認した。workerは従来どおりプロジェクトrootを追加する。

```text
python314.zip
.
../../packages
```

既存ホストへ`run --project <payload/app> --python <payload/runtime/python/python.exe> --smoke-test`を渡し、子の環境だけに`WEBVIEW2_BROWSER_EXECUTABLE_FOLDER=<payload/runtime/webview2>`を指定した。カレントディレクトリは別の空フォルダーとし、PYTHONHOME/PYTHONPATHも存在しない検証用パスへ指定して、隔離状態でそれらへ依存しないことを確認した。グローバルPATHやレジストリは変えていない。

確認結果:

- 既存実GUIスモークのUI/RPC、エラー、並行呼出し、イベント/購読解除、取消が成功。
- 最終確認のホストPIDは18188。終了コード0、watchdogなし、native_smoke.ok=true。詳細JSONと実行ラッパーのメタデータを別名で保存。
- QueryFullProcessImageNameWでWebView2の6プロセスの実行パスを採取し、準備したruntime/webview2配下であることを照合。
- Pythonのexecutable/prefix、全sys.path、PyYakiのimport先、`_pydantic_core.cp314-win_amd64.pyd`のimport先がpayload内。`sys.flags.isolated=1`、site未importを確認。
- worker.logの日本語・絵文字をUTF-8 strictで確認し、観測した9プロセスの確認用ハンドルをすべて取得、残留なし。二重rAF→shell→Python生成→workerの6段階→welcomeを段階ログで確認。

最初の試行はnative_smoke自体は成功したが、検証器の`\\?\`付きWindowsパスの比較で失敗した。比較処理を修正し、元ログを保存して追試した。また実行ラッパーのJSONと詳細JSONの保存名が重複したため、最終保存は`payload-smoke-verified-command.json`（コマンド）と`payload-smoke-verified.json`（詳細）を分けた。これらは検証器の問題で、製品の修正としては扱わない。

検証後のフォルダーは約748 MB、929ファイルだった（bytecode・観測用JSON等を含む標本）。最終exeの圧縮後容量や初回起動時間ではない。OSのDLL探索、クリーン端末でのVC/UCRT不足、Python/WebView2未導入環境、読取専用配置、再配置後の動作は未確認である。

## 取得・配置方式の比較と次の実装

資料参照日: 2026-11-05。既存[ADR-005](architecture.md#8-adr候補一覧)の方式比較を継続し、今回だけで正式採択はしない。

| 候補 | 今回の要件との関係 |
| --- | --- |
| [PyInstaller](https://pyinstaller.org/en/stable/operating-mode.html) | 依存収集とonefile展開を再利用できる。標準のbootloaderはPythonを起動してスクリプトを実行するため、Python非依存GUIを先に維持する統合の検証が必要 |
| [Nuitka](https://nuitka.net/user-documentation/user-manual.html) | standalone/onefileを提供するが、通常の生成にCコンパイラーを要する。アプリごとのコンパイラー不要という今回の生成条件とは、そのままでは一致しない |
| 既存Rustホスト＋固定資材 | 今回のフォルダーでPython・native wheel・Fixed Versionの実接続を確認。ホストの汎用テンプレート再利用と先行描画を維持できる候補だが、単一exe組立・安全な展開・署名対応は未実装 |

次に実装する最小範囲の案は、事前ビルドしたホストへ資材・索引を署名前に付加し、Rustの初回描画後にワーカースレッドで検証・展開して既存UI起動へ合流する経路。設定・payloadを先に展開してから画面を作る入口にはしない。公開API・既存の二重rAFとPython開始順を維持する。

実装と受入に必要な事項:

1. PEの証明書テーブルを考慮して索引を定位し、署名前後で読取・ハッシュ照合を検証。単にファイル末尾を固定長で読む案にしない。
2. 相対パス・サイズ・SHA-256・重複・Windows予約名/ADS・大文字小文字衝突を検査し、reparse pointと展開先脱出、過大展開を拒否。
3. ユーザーキャッシュ内の排他的staging、同時起動、中断復帰、破損、使用中の旧版、容量不足、配置元読取専用、キャッシュと業務データの分離を確認。
4. Fixed Versionと同梱Pythonを絶対的に選び、実行時の取得・インストール・昇格をしない。閉じる操作と自分のワーカー回収を展開中も維持。
5. 最終的にPython/WebView2未導入の標準ユーザー・オフラインのクリーンWindowsで、初回/キャッシュ利用・UI/RPC・終了を確認。現端末の成功で代替しない。

圧縮方式・索引ライブラリの正式選定、署名後の完全性、依存DLL/ライセンス/通知の棚卸し、SBOM、Windows wheelへのネイティブCLI配置は残作業。[ビルド設計](build-system.md)と要件を維持する。今回はこの資材検証までを記録し、単一exeの実装完了とは扱わない。

## 生ログと再開用資材

`test-results/windows-onefile-preflight-20261105/`へ公式ZIP/CAB、取得ページ、署名・版・ハッシュ、wheelhouse、framework wheel、Windows用/全16件のlock、配置ログ、payload、補助スクリプト、実GUIログ、詳細JSON、全ファイルのハッシュ標本を保存した。実行が必要な資材はGitへ入れず、文書とWindows用依存lockだけを共有する。Macへ自動転送されない。

Windows用依存lockは取得済みwheelhouseからネットワークなし・ハッシュ強制で15件すべて再解決した（`windows-lock.log/json`）。今後別端末で準備する場合の、実装済みpip操作の例:

```powershell
.\.venv\Scripts\python.exe -m pip download --only-binary=:all: --require-hashes -r examples\hello\requirements-windows.lock --dest build\windows-inputs\wheels
```

この操作は資材取得だけで、単一exeを生成しない。ホスト・framework wheel・ランタイムは別途同じソースと固定版から準備する。Windows用lockの採用範囲も、この記録の開発端末での同梱候補確認に限る。
