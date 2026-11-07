# PyYaki ビルド・配布設計

状態: 初期設計案 + Web用wheel・Mac .app / Windows単一exeのローカル試作 / 技術調査日: 2026-10-30 / 更新: 2026-11-07 / 正式配布・実署名・未導入クリーン環境は未完了

Pythonパッケージ`0.1.0.dev0`のwheelにはPythonワーカーとSDKを含むが、ネイティブexe・ランタイム資材は含まない。Windowsは事前ビルド済みホストと明示した資材から単一exeを組み立てるローカル試作を追加した。`native/Cargo.lock`はRust依存を固定し、`examples/hello/requirements-windows.lock`は今回のWindows x64 / CPython 3.14の組合せを固定する。ネイティブwheel配置は未実装。Macの`requirements-dev.lock`をWindows配布の互換性証明に使わない。

初期試作の`pyyaki`はPython console scriptで、`dev`と`build macos`/`build windows`のローカル試作を実装。Python CLIの`run`は未実装で、以下に設計するWindowsネイティブCLIとは区別する。生成したWindows exeはPythonに依存せずRust側から起動する。wheelはRustホスト・Pythonランタイム・WebView2を含まず、公開も行っていない。

Windowsの開発用確認はRust版`pyyaki.exe run --project <path> --python <venv-python.exe>`を直接使う。[メンテナー向け手順](../native/README.md)では事前導入したRust/MSVCでWindows上でビルドし、開発venvと既存WebView2で実行する。Macで行ったWindowsターゲットの型検査は、exeの生成や起動成功ではない。2026-11-03にこの手順でWindows 11 x64のMSVCリンク・実WebView2を確認した。[実機記録](validation-windows.md)を参照。アプリ利用者にRustを要求する正式配布へ方針変更したものではない。

Mac向けは開発機でCargoビルドしたホストを直接実行する段階。開発時のSDK再ビルド・変更反映は[開発ガイド](development.md#変更する場所と反映方法)、診断releaseビルドの実行結果は[Mac初期計測](performance-macos.md)を参照する。依存は同じCargo.lockで固定し、Pythonは事前導入したvenvを使用する。Python同梱.appのローカル生成を追加した。準備済み資材・ハッシュ固定wheelからオフラインで組み立て、アドホック署名と再配置を検証する。[入力・生成手順・限界](macos-build.md)を参照。Developer ID署名、notarization、第三者への正式配布は未対応。Windows向け単一exe要件は維持する。

起動の観測と障害表示は[切り分け手順](startup-diagnostics.md)を参照。2026-11-04に内部起動通知を追加したため、ホストとPythonワーカーは同じソースから準備する。既存の.appや将来の配布exeへソース変更は自動反映されず、再生成が必要。

2026-11-05の[フォルダーでの資材確認](windows-onefile-prototype.md)に続き、[Windows単一exeローカル試作](windows-onefile.md)を実装した。非圧縮blob、PE証明書テーブルを考慮した索引、全ハッシュ検証、私有キャッシュ、別GUI子ホスト/Jobを使う。以下は製品化に向けた設計案であり、自動取得・実署名・未導入クリーン環境等の全工程を実装済みにはしない。

Macのローカル生成は、staging内でレポートを書き終えてから新規hard linkと排他的renameで公開する。通常の失敗ではstagingと自分のレポートを回収し、既存の成果物へ置換しない。[失敗注入と実資材の追試](macos-build.md#失敗時処理の追試2026-11-04)を参照する。2ファイルの原子的公開や電源断復旧を保証するものではない。

2026-11-04にC案の既定アイコンを追加した。Mac用ICNSはwheelに同梱し、.appのResourcesへ署名前に配置する。Mac開発ホストのDock用PNGとWindowsホストのICOはRustへ埋め込む。変換はメンテナー作業であり、ビルド/実行時のPillow依存を増やさない。Windowsのリソースコンパイラーは開発ホストのビルド時だけ必要。単一exe試作はテンプレートのC案を維持し、独自アイコン設定は未実装。[手順と範囲](../native/README.md#アイコン)を参照。

## 1. 配布単位と成立条件

**Windowsで配布先へ渡すアプリは依存ファイルを含む単一exeとする。** 利用者回答により必須であり、exeとフォルダーをまとめたZIPを最終成果物の代替にしない。

推奨案は、ビルド済みRustホストへPython、アプリ、UI、依存パッケージ、Fixed Version WebView2を格納し、起動時にユーザー専用キャッシュへ展開する方式。Python DLL、`.pyd`、WebView2の実行ファイル等をディスクへ配置する設計であり、単一ファイルのまま完全インメモリで動作する構想とは区別する。

展開前にRustの起動画面を描き、展開後にHTMLを表示してからPythonを起動する。初回の展開負担と容量は避けられない可能性があり、[requirements.mdの計測仕様](requirements.md#5-起動速度の測定仕様)で検証する。PyInstallerもone-fileでは展開を伴いone-folderより起動が遅くなると説明している。これは方式の参考であって、PyYakiの性能実測ではない。[PyInstallerのone-file動作](https://pyinstaller.org/en/stable/operating-mode.html)

| 場面 | 必要なもの | ネットワーク/権限 |
| --- | --- | --- |
| OSS開発者がホストを開発・公開 | Rust/Cargo、Windowsのビルド環境、対応Python | CI/メンテナー環境だけで使用 |
| 利用者が`pip install pyyaki` | 対応Pythonとpip、公開済みwheel | 通常は取得時に接続。事前配布したwheelhouseでオフライン導入可。venvなら管理者不要 |
| 利用者が`pyyaki dev` | Python環境、ブラウザ | loopback HTTP/WebSocket。外部サービスを使わなければオフライン可 |
| 利用者が`pyyaki run` | Windows、開発venv、利用可能なWebView2 | Rust不要。既存Evergreenまたは事前取得したFixed Version。実行時の自動取得・昇格はしない |
| 利用者が`pyyaki build windows` | 対応Windows/Python、ホストと対象依存wheel、ランタイム資材 | Rust/Cargo/MSVC/Node不要。未取得資材の明示取得時だけ接続 |
| 配布exeを使う人 | 対応Windows、ローカルの配置先と書込み可能なユーザー領域 | Python/WebView2導入・ネット接続・管理者権限不要。ただし組織の実行許可は必要 |

本プロジェクトの主な開発機はMacで、手元のWindowsで後から動作確認する体制。Macで共通Python処理・UI・Web経路を開発できる範囲と、Windowsホスト・wheel・単一exeのビルドや実行の検証を区別する。Windows向けビルドはWindowsで行う設計を維持し、Macからのクロスビルドを前提にしない。手元のWindowsのOS・CPUは検証開始前に確認する。

## 2. pip導入とネイティブCLI

### 2.1 パッケージ案

- `pyyaki`: 共通Pythonディスパッチャー、Webアダプター、JS SDK、ビルド機能。
- Windows x64の`pyyaki` wheelには、同じ版のビルド済みネイティブ`pyyaki.exe`と汎用GUIホストの資材を同梱する。
- 非Windowsのpure Python wheelはWeb開発用Python CLIを提供し、`run`/`build windows`は未対応と明確にエラーにする。Macでは明示した事前取得資材を使う`build macos`試作も提供するが、ホスト資材はまだwheelへ同梱しない。
- Python、WebView2、アプリ固有依存の大きなアーカイブは基本wheelへすべて詰めず、バージョン固定の資材キャッシュからビルド時に取得する案。`pip install`時に任意URLから実行ファイルを取得・実行しない。
- Windows wheelはPython ABIへ直接リンクしない実行ファイルを含むため、`py3-none-win_amd64`をタグ候補とする。`Requires-Python`とCPython判定は別途設定する。タグと実際の対応Pythonの一致をG1で検証する。

### 2.2 Pythonより先に起動するCLI

Windowsで`[project.scripts]`からPythonラッパーを生成する方式は`run`に使わない。wheelの`{distribution}-{version}.data/scripts/pyyaki.exe`からvenvの`Scripts`へネイティブexeを配置する案とする。同名のconsole scriptを同時に生成しない。

wheel仕様には`.data/scripts`をインストール先へ配置する仕組みがある。実際のpip配置、上書き・アンインストール、対応バージョン、venvのPython探索は未検証であり、リリース前に確認する。[wheel配布仕様](https://packaging.python.org/en/latest/specifications/binary-distribution-format/)、[互換性タグ仕様](https://packaging.python.org/en/latest/specifications/platform-compatibility-tags/)

CLIの分岐案:

| コマンド | Windowsでの処理 |
| --- | --- |
| `pyyaki run` | ネイティブコマンドからGUIホストを起動。設定読取と画面描画の後に開発venvのPythonを起動 |
| `pyyaki dev` | 同じvenvのPythonで開発サーバーを起動。ブラウザを開く案 |
| `pyyaki build windows` | 同じvenvのPythonで依存検証と単一exe組立を行う |

最初はvenvへの導入を推奨する。CLIは自分の配置場所と`pyvenv.cfg`から関連Pythonを特定し、PATHの先頭のPythonへ暗黙に切り替えない。例外環境向けに`run --python <absolute-path>`を候補とし、指定したインタープリターが同じフレームワーク版を持つかワーカー接続時に確認する。

`pip --user`や通常インストール環境でのネイティブコマンドとPythonの対応付けはG1で確認する。未対応なら明示エラーとvenv導入手順を提供し、別のPythonで勝手に起動しない。`python -m pyyaki run`はPython初期化済みになるため、先行描画を保証する入口としては提供しない。

## 3. ビルドパイプライン案

1. **入力検証**: `pyproject.toml`から静的設定と対象を読む。対応OS/CPU/Python、依存lock、include資産、出力先を検査。アプリのimportによって設定を取得しない。
2. **資材解決**: 同じPyYaki版のホスト、固定CPythonパッチ版、固定WebView2版、対象依存wheelを解決し、取得元・ハッシュ・ライセンス・来歴を記録。
3. **依存の配置**: 対象Windows/Pythonに合うwheelを、開発環境から隔離したstagingへ配置。開発venv全体をコピーしない。
4. **ランタイム準備**: Embeddable Packageを展開し、制御した`._pth`とPyYakiワーカーを配置。パッケージのDLL・データを保持する。
5. **資産収集**: アプリの明示includeを収集し、UIとSDKを配置。秘密情報や不要な開発ファイルが入っていないか一覧を確認する。
6. **検証実行**: ターゲットランタイムでワーカー起動、アプリimport、native拡張のimport、最小RPCを確認する。ビルド元Windowsでも成功するだけでは配布検証の代わりにしない。
7. **マニフェスト生成**: 全ファイルの相対パス、サイズ、SHA-256、版、ソースコミット、Python ABI、WebView2版、公開プロトコル版、ライセンス情報を記録。
8. **単一exe組立**: 汎用ホストの未署名テンプレートにペイロードと索引を付加。アプリ識別子等はデータで渡し、Rustを再コンパイルしない。
9. **署名と検証**: 配布者が署名する場合は組立・メタデータ設定後の最終exeへAuthenticode署名とタイムスタンプを付与し、再検証。元テンプレートの署名をアプリの署名として流用しない。
10. **クリーン環境確認**: Python/WebView2未導入、標準ユーザー、オフラインのWindows VMで初回/再起動、UI・RPC・終了を確認する。

成果物案は`dist/<name>.exe`。SBOM、マニフェスト、ハッシュ一覧、NOTICE、計測結果などは開発者向けの別レポートとして生成してよいが、実行に必要なファイルはexe内に含める。ビルド用の中間フォルダーを利用者が持ち運ぶ必要はない。

### 3.1 ペイロード形式の提案

保守可能な圧縮形式と独自の小さい索引を組み合わせる。圧縮方式はADR-005で決定する。ZIPを初期比較対象とし、圧縮率、初回時間、ライブラリの攻撃面を評価する。

- PEファイル本体、ペイロード、索引/フッター、署名の順に構成する案。**署名後にアーカイブを追記しない。**
- 署名済みPEには証明書テーブルがあるため、単純に「ファイル末尾Nバイト」を読む方式にしない。PEの範囲と証明書テーブルを考慮して索引を定位する。
- 各オフセット、圧縮サイズ、展開サイズ、合計サイズに上限を設け、範囲外参照・整数overflow・ZIP bombを拒否する。
- PEのバージョン情報・アイコンの個別変更はMVPの必須にはしない。追加する場合もコンパイラ不要のデータ更新として署名前に完了する。
- アーカイブ付加とAuthenticodeの両立は**未検証**。G1で署名前後の読取・改ざん検出を確認し、不成立なら単一exeの別形式を検討する。

## 4. Pythonランタイムと依存

### 4.1 Embeddable Package

公式のWindows Embeddable Packageを最初の候補とする。通常の開発Pythonと同じものではなく、pipやTcl/Tk等を含まない。埋込環境で配布後にpipを実行して依存を更新する設計にはしない。[CPython Windows埋込配布](https://docs.python.org/3.14/using/windows.html#the-embeddable-package)

配置案（exe内部と展開後の論理構造）:

```text
payload/
├─ manifest.json
├─ runtime/python/          # python.exe、python3.dll、python314.dll、標準ライブラリ等
├─ runtime/webview2/        # 同じCPU向けFixed Version
├─ framework/pyyaki/      # ワーカーと共通ディスパッチャー
├─ packages/                # lockから解決した依存wheelの内容
├─ app/                     # 利用者のPythonコードとデータ
├─ ui/                      # HTML/CSS/JS、画像、フォント
└─ licenses/                # OSSおよび再配布ランタイムの通知・条件
```

`python314._pth`等で標準ライブラリ、必要なランタイムディレクトリ、framework、packages、appを相対的に許可する。`import site`は既定で追加せず、ユーザーsite、環境変数、レジストリ由来の検索パスを使わない。ワーカーは隔離オプションを用いて起動し、実際のsys.pathと`.pth`処理を試験する。[CPythonの探索パス制御](https://docs.python.org/3.14/using/windows.html#finding-modules)

開発時`run`は利用者venvを使い、配布時は同梱Pythonへ切り替わる。その違いを隠さず、build時に同梱環境でimport検証する。`PYTHONPATH`/`PYTHONHOME`を消去または制御し、カレントディレクトリから偶然依存が見つかることを避ける。

### 4.2 依存wheelとネイティブ拡張

- `requirements-windows.lock`は**推移依存を含む全件の固定版と許可ハッシュ**を要求する案。単なる`pip freeze`だけを安全なlockと見なさない。
- ビルド用Python側のpipで`--only-binary=:all:`と`--require-hashes`を用い、対象に合うwheelを取得・stagingへ配置する。埋込Python内でpipをブートストラップしない。
- `.data`、`.dist-info`、ライセンス、パッケージのdata filesを含むwheelインストール規則に従う。ZIPを展開するだけでwheelの配置が完了したとは扱わない。
- 対応する`win_amd64`、`cp314`等とABI互換を確認し、`abi3`はそのwheelに記載された範囲だけを信頼する。free-threaded ABIを混在させない。
- `.pyd`、付属DLL、`*.libs`ディレクトリ、importlib.resourcesで読むデータを保存する。不要そうという理由で自動削除しない。
- DLL探索は同梱ディレクトリの許可リストを使い、必要に応じて`os.add_dll_directory`等で明示する。グローバルPATHへの追加やCWD依存は避ける。
- `.pth`の実行、sitecustomize、独自DLLローダーに依存するwheelは自動対応としない。G3で調査し、信頼した依存の限定フックまたは非対応という結果を記録する。
- データやDLLの追加が必要なら、宣言したcollect設定を導入する案。ビルド時フックの実行は任意コード実行なので、未信頼パッケージを安全に解析できる保証をしない。
- 対応wheelがない依存、コンパイラ、OSサービス、GPUドライバー等が必要な依存は明示的に失敗させる。利用者へRust/Cargoの自動インストールを要求しない。

代表試験はpure Python依存、Pydantic関連native拡張、NumPyのようなDLL同梱依存、データファイル依存を候補とする。具体的な版の対応可否は未検証。MVPの公式対応表には成功した組合せだけを載せる。

pipのハッシュ強制とbinary限定は公式に提供されているが、ハッシュは依存コードの無害性を証明しない。[pipの安全な導入](https://pip.pypa.io/en/stable/topics/secure-installs/)

### 4.3 VC runtime等

Python、WebView2、Rustホスト、native wheelが必要とするVC/UCRT/OpenSSL等を別々に棚卸しする。RustホストのCRTを静的リンクできても、すべての依存DLLが不要になるわけではない。

再配布が必要なランタイムは、正規配布物・条件を確認してapp-localで配置できる範囲を検証する。開発PCのSystem32から無断でDLLを収集しない。クリーンVMで欠落が見つかったらG3を不合格とし、管理者インストールを前提にすり替えない。

## 5. WebView2

| 方式 | 利点 | 要件との関係 |
| --- | --- | --- |
| 既存Evergreen | exeにランタイムを詰めず更新をOS側へ委ねられる | 開発`run`の候補。配布先に必ずあるとは仮定しない |
| Bootstrapper | 初期配布が小さい | ダウンロードが必要なので単一exeの初回オフライン要件を満たさない |
| Evergreen offline installer | ネット接続なしで導入できる | インストールを伴い、無導入の単一exe実行の既定案にはしない |
| Fixed Version同梱 | 特定版をアプリ内で選べる | 配布exeの推奨。容量増大、更新責任、配置条件を引き受ける |

配布ホストは同梱Fixed Versionを明示選択し、ユーザーの環境変数で意図せず別版へ切り替わらないことを確認する。Loaderを静的リンクするか、対応CPUの`WebView2Loader.dll`を同梱するかはホスト依存ツリーで確認する。Microsoft Edgeブラウザ本体を本番WebView2 Runtimeの代わりに使わない。[Microsoftの配布方式とLoader要件](https://learn.microsoft.com/en-us/microsoft-edge/webview2/concepts/distribution)

Fixed Versionは数百MB規模になり得る。実際の圧縮後exe容量・展開後容量は版ごとに測り、固定の容量保証をしない。Windows 10固有ACLとUNC制限があるため、MVPではWindows 11 x64のローカルディスクを最初の検証対象とする。

WebView2のUser Data Folderはアプリの展開物とは分離し、書込み可能な`%LOCALAPPDATA%\PyYaki\apps\<app-id>\profiles\<instance-id>`を候補とする。多重起動時に同じプロファイルを競合させない。アプリが保存する業務データにはこの一時的なプロファイルを使わない。[WebView2 User Data Folder](https://learn.microsoft.com/en-us/microsoft-edge/webview2/concepts/user-data-folder)

## 6. 安全な展開とキャッシュ

提案パスは`%LOCALAPPDATA%\PyYaki\apps\<app-id>\versions\<payload-sha256>`。アプリデータは別の`data`、ログは`logs`、プロファイルは`profiles`へ置く。配布exeの隣へ書き込まず、既定の一時フォルダーから実行する方式も避ける。組織によってはLocalAppDataからの実行自体が禁止されるため、その制限を回避するものではない。

1. 起動画面を描画後、資材マニフェストとアーカイブ範囲を検証する。対象のapp-idはパスに使う前に検証する。
2. 同じapp-idとペイロードハッシュの展開はOSロックで直列化する。別版は別ディレクトリにし、起動中の版を上書きしない。
3. 私有ACLを持つ同一ボリュームのランダムなstagingへ展開する。リンク・reparse point・パストラバーサル・重複名・大小文字衝突・ADSを拒否する。
4. 全ファイルのサイズ/ハッシュを検証してからatomic renameする。不完全なstagingを正規キャッシュとして実行しない。検証エラーはGUIに表示する。
5. 再起動でも実行ファイルとロード対象資産をマニフェストに照合する。最初は全ファイル検証を基準とし、完了マーカーやmtimeだけで検証を省略しない。
6. 終了後の古いstaging/版の清掃はロック・使用状態を確認し、他プロセスが使用中のDLLを削除しない。容量不足時は必要容量と解決方法を表示する。

フルハッシュ検証の起動コストはG1/G3で計測し、性能のために黙って弱めない。署名のある配布exeと私有ACLを信頼の起点にするが、同じユーザー権限で動く攻撃者がすべてのローカルファイルを変更できる状況に対し、完全な改ざん防御を保証しない。

この方式の「ポータブル」はインストーラーなしでexeを持ち運べる意味であり、ユーザー領域に痕跡を残さない意味ではない。キャッシュ・ログ・プロファイルの削除手順を提供し、業務データの削除は別操作にする。

## 7. ランチャーの配布と更新

- メンテナーのWindows CIで固定Rust toolchain/Cargo.lockからホストをビルドし、コミット、ターゲット、機能、依存、ハッシュ、SBOM、署名情報を発行する。
- wheelのホスト資材とPython/JSの版を一致させる。利用者のbuildは同じ版を再利用し、毎回「最新ホスト」を取得しない。
- 外部ランタイム資材は版付きURLとSHA-256を資材マニフェストで固定する。変更可能なlatest URLだけを信頼しない。信頼する署名鍵/CI identityとマニフェストの対応も検証する。
- ホスト更新は`pip install --upgrade pyyaki`と**アプリの再ビルド・再配布**で反映する。pip更新だけで既に配ったexeが更新されることはない。
- 配布exeは起動時に更新サーバーへ問い合わせない。MVPは署名された新しいexeを配布者が提供し、利用者が終了後に置き換える方式。
- オフライン資材セットにはwheelhouse、ホスト、Python ZIP、WebView2、マニフェスト、検証用公開鍵/来歴と通知を含める。取得手順とオフラインビルド手順を用意する。
- 資材不足の`build --offline`は不足項目を列挙して終了する案。ネットワークへ自動フォールバックしない。未準備状態の`pip install`/buildまでオフライン可能と説明しない。

Fixed Versionに重大な脆弱性が出た場合、メンテナーが資材版・依存情報・注意事項を更新し、アプリ配布者が再ビルドして再配布する。オフライン利用者への更新経路は組織の配布手段に従う。自動更新はMVP対象外でも、脆弱性対応方針は公開前に必要。

## 8. Defender・署名・企業制限

- ホスト単体の署名と、利用者が組み立てたアプリexeの署名を区別する。アプリ配布者が自分の最終exeに署名する。フレームワークの証明書で任意の利用者コードを署名するサービスは提供しない。
- 個別の`.exe`/`.dll`/`.pyd`について、元配布物の署名、再配布条件、企業ポリシーの要求を確認する。外側exeの署名だけで内部実行コードがすべて許可されるとは説明しない。
- ビルドは署名証明書なしでも可能とする案。未署名と署名済みの成果物を明示し、企業配布はその組織の許可条件で判定する。
- UPXや難読化、実行制限を避ける無断コピー、証明書検査の無効化を既定にしない。Defenderを有効にして試験する。
- 署名、バージョン情報、安定した発行者情報、SBOM、ハッシュ、展開先、子プロセス一覧を企業の許可申請資料にする。フォルダーからのスクリプト/DLL実行を禁じる環境では動かない可能性を明示する。
- MOTW付きの実際の配布経路を試験する。SmartScreenの評価とDefenderのマルウェア検知、AppLocker/WDAC/SACの許可判定を別々に記録する。

Microsoftは署名済みの新しいバイナリでもSmartScreen警告が出る場合があると説明している。署名すれば警告・誤検知・企業ブロックが必ず解消するという保証はしない。[SmartScreenの開発者向け説明](https://learn.microsoft.com/en-us/windows/apps/package-and-deploy/smartscreen-reputation)、[Windowsのアプリ制御](https://learn.microsoft.com/en-us/windows/security/book/application-security-application-and-driver-control)

## 9. OSSライセンスとサプライチェーン

### 9.1 ライセンス方針案

PyYaki本体はApache-2.0を第一候補、MITを代替候補とし、権利者がADR-010で選択する。現段階では正式採用しておらず、LICENSEファイルを追加したり「Apache-2.0で公開済み」と表示したりしない。

| 対象 | 取り扱い |
| --- | --- |
| Rust/JS/Pythonの依存OSS | 固定した実際の版のSPDX識別子とLICENSE/NOTICEを収集。推移依存も含む |
| CPython | PSF等の複数の履歴・同梱ライブラリの条件を確認し、必要な通知を保持する |
| WebView2 | OSS本体とは異なるMicrosoftの再配布条件を確認。Chromium関連通知も保持する |
| VC runtime等 | app-local配布の可否と再配布権を確認。任意のOSファイルを同梱しない |
| 利用者のアプリ/依存 | 利用者のライセンス選択を尊重。フレームワークのライセンスだけで全バンドルを再許諾しない |

公式のライセンス本文は確認材料にするが、依存バージョン未固定の現段階で全体の法的互換性を確定したとは扱わない。[CPythonのライセンス](https://docs.python.org/3/license.html)、[wryのMIT本文](https://github.com/tauri-apps/wry/blob/dev/LICENSE-MIT)、[PyTauriのライセンス表示](https://github.com/pytauri/pytauri)

### 9.2 公開前に必要な管理

1. Cargo.lock、Pythonの固定依存・ハッシュ、JS SDK資産、Python/WebView2資材版を固定する。
2. CIアクションはコミットSHA等で固定し、最小権限で実行。外部PRへ署名秘密鍵や公開トークンを渡さない。
3. wheel/ホストは信頼したCIから公開し、Trusted Publishingと来歴attestationを候補にする。ハッシュと署名/来歴は別の検証として記録する。
4. ホストwheelとアプリexeの両方に、実際の同梱内容からSBOMを生成。ライセンス通知をexe内から参照/取得できるようにする。
5. 依存脆弱性を監査し、重大な問題と再配布条件未確認を公開ゲートで止める。正当な例外が必要なら期限・根拠・影響を記録する。
6. 署名・タイムスタンプ前の内容を再現可能性の比較対象にし、署名後のバイト単位一致は別扱いにする。再現可能ビルドは未達成の目標と明記する。
7. 脆弱性の連絡先、修正版提供方針、サポートする版の範囲、資材の廃止手順を公開前に決める。

PEP 740はPyPI等のattestationと来歴を扱うが、pipが常にそれを強制検証する保証ではない。ビルド資材の検証方針はPyYaki側でも明示する。[PEP 740](https://peps.python.org/pep-0740/)

## 10. ビルド失敗条件と検証

以下は明示的なエラーとする: 対応外OS/ABI、未固定依存、ハッシュ不一致、対応wheel不在、必要資材不在、ランタイム版不一致、パストラバーサル、app-id不正、include外の必要資産、空き容量不足、署名検証不合格。

アプリが必要な動的importや外部データを完全自動で検出することはできない。配布者は明示include/collectとクリーンVM試験で確認する。最低限、初回・キャッシュあり・破損キャッシュ・多重起動・中断後再起動・日本語パス・読取専用exe配置・オフライン・標準権限・無効プロキシ・Defender有効・企業ポリシー環境を試験する。

これらは[roadmap.md](roadmap.md)のG1/G3/G4で行う予定であり、本設計作業で成功を確認した事項ではない。
