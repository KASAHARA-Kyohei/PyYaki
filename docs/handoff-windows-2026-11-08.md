# Mac作業後のWindows引き継ぎ（2026-11-08）

次の一区切りは、**WindowsでRust/Cargo不要のpip導入とネイティブCLIを実装し、新規venv・実WebView2で確認して結果をdocsへ記録すること**。これはWindowsで実行した結果ではない。現在地の正本は[ロードマップ](roadmap.md#現在地と次の作業)、要件は[requirements.md](requirements.md)、配布設計は[build-system.md](build-system.md#2-pip導入とネイティブcli)。

追記2026-11-09: Mac専用の[強制終了時回収](validation-macos-host-cleanup-2026-11-09.md)を`69acd4b`で追加した。Macのプロセス階層はguard経由となり、Mac診断器の所有確認も更新した。Windows Job/継承コードは変更しておらず、Windows実機・型検査の成功を追加した記録ではない。次回取り込み時は実際のHEADと差分を確認する。

## 最初に確認すること

- ローカルの`AGENTS.md`と`git status`、ブランチ・remoteとの差分を確認して既存変更を保護する。自動stash/reset/強制checkoutをしない。
- `git fetch origin`で最新を確認し、既存変更と分岐がなく安全な場合だけ`git pull --ff-only origin main`を使う。分岐・衝突は内容を確認してから扱う。
- 基点`12ba635`を祖先に含み、この引き継ぎ文書も取得していることを確認する。実際の対象HEADは別途記録する。
- Windows版・CPU・Pythonの絶対パス/版/通常GIL・Rust/MSVC・WebView2を実機で確認する。過去のWindows機は`py` launcherなし、既定Rust 1.84.1を維持して`cargo +1.89.0`を使用した。現在も同じとは仮定せず、既定toolchainを無断で変更しない。
- CPython 3.14通常GIL版、Rustの最低版はCargo.tomlの1.89。既存Windows用資材・lockを確認する。Macの`.venv`、wheelhouse、Mach-Oホスト、SDKパスを流用しない。

最初に読む文書:

1. [開発ガイド](development.md)、[ロードマップ](roadmap.md)、[要件](requirements.md)
2. [ビルド設計のCLI配置](build-system.md#2-pip導入とネイティブcli)、[アーキテクチャのADR-003/022](architecture.md#8-adr候補一覧)
3. [Windowsメンテナー手順](../native/README.md#windowsでの開発用確認)、[起動診断](startup-diagnostics.md)
4. [直近のWindows単一exe結果](validation-windows-onefile-2026-11-07.md)、[パイプ・Job等の境界追試](validation-windows-boundary-followup-2026-11-05.md)
5. [Macホストwheelの手順](macos-wheel.md)と[結果・検証器の失敗](validation-macos-wheel-2026-11-08.md)、[Web自動更新の結果](validation-web-reload-2026-11-08.md)

## Macから引き継ぐ変更

| コミット | 内容 / Windowsでの注意 |
| --- | --- |
| `9e9302a` | Macの初期Space配置対策。二重rAF・10秒期限・Python開始順は維持。Mac専用の対策をWindowsの描画成功証拠にしない |
| `be7e26f` | Mac回帰検証器と共通の起動ログ検査。Mac専用fixture・fullscreen操作をWindowsで実行しない |
| `f8ed948` | Web devのUI自動更新とPython子サーバー再起動。Windowsのspawn/socket・Ctrl+Cは未確認 |
| `62ba686` | Macホスト同梱wheel用の`pyyaki run`。Python CLI初期化後にexecする。WindowsではこのPythonラッパーのrunを明示拒否 |
| `98d3c9f` | Mac wheel組立器と新規venv導入検証器。thin Mach-O・codesign専用であり、Windows wheel生成器ではない |
| `12ba635` | Mac wheelの導入・設計比較・最終結果の文書。Macの成功件数とWindowsの受入を区別 |

Macの直近確認はPython 212成功、Rust 14成功、Mac wheel検証13/13（GUI起動5件）。これはWindowsの期待件数ではなく、スキップ・失敗を含む実際の結果を取り直す。Mac用のGUI/停止検証器はWindowsの所有プロセスの終了処理が未確認のものもあるため、そのまま実行せず、対象OSと回収方法を先に確認する。

Gitで移るのはソース・lock・検証器・docs。`.venv/`、`native/target/`、`test-results/`のwheel・生成exe・ランタイム・生ログは移らない。Windows端末の以前の資材も存在・版・ハッシュを確認する。必要な資材が不足していれば、取得先・条件を調べ、必要な確認を行う。大きいバイナリを通常のGitへ追加しない。

## 今回の作業と完了条件

### 1. WindowsのCLI入口とwheelを実装

- アプリ開発者は`pip install pyyaki`で導入し、Rust/Cargo/MSVCなしで`dev`・`run`・明示資材による`build windows`を使う設計を維持する。ホストを作るメンテナー側のRustビルドとは区別する。
- Windowsの`run`は**Pythonを初期化せずネイティブ入口からGUIを開始**する。venv探索のために先にPythonを実行してアプリをimportしない。UIの二重rAF・shell通知後にPythonを生成する既存順序と10秒期限、localhost待受なしを維持する。
- ADR-003の`.data/scripts/pyyaki.exe`配置案を起点に、既存OSS・標準wheel仕様と実装候補を比較し判断を記録する。同名のPython console scriptとネイティブexeを二重に配置しない。`py3-none-win_amd64`は設計上のタグ候補で、実際の対応条件を検証する。
- 現ホストの`native/src/main.rs`はWindows GUI subsystemで、主に`run`を扱う。CLIとGUIホストの構成、help/version・コンソール出力・終了コード、`dev`/`build`への分岐を検討して試験する。大きい構成判断はADR-003へ残す。
- CLI配置先とvenvから対応Pythonを選び、PATHの別Pythonへ暗黙に切り替えない。Pythonパス・引数は空白/日本語を保ち、シェル経由で連結しない。非venvや`pip --user`を扱えない場合は明示エラーとし、未対応を記録する。
- 同版のホスト・Python/SDK、CPU・タグ・実行ファイル/RECORDの整合性を検査する。既存成果物へ上書きせず、導入時のCargo呼出し・実行ファイルの自動取得・管理者昇格を追加しない。

### 2. Windows実機で検証

- Python共通テスト、Ruff、Rust fmt/test/Clippy・MSVC releaseを[既存手順](../native/README.md#windowsでの開発用確認)で実行する。成功・skip理由・失敗を実件数で記録する。
- 新しい日本語・空白パスのvenvへローカルwheelから導入する。固定lockと`--no-index`等の条件、Cargo/MSVCのないPATHでの実行を記録する。実際の未導入クリーン端末・ネットワーク切断を実施していなければ合格に含めない。
- pip後のネイティブ`pyyaki.exe`を実行し、実WebView2のUI/RPC・イベント/取消、UTF-8ログ・日本語例外、終了フックと自分のworker回収を確認する。通常/import前5秒/起動フック5秒を各3回、段階ログで二重rAF・開始順・期限を照合する。物理的な画面提示をログだけで証明した扱いにしない。
- `dev`の起動・ポート競合・Web更新・再起動/終了、既存`build windows`への分岐が保たれることを確認する。既存単一exeの全重試験を無条件に繰り返さず、変更した境界に応じて回帰範囲を選び、未実行を明記する。
- ホスト不在/破損/版違い/対応外CPU等の明示エラー、同版の再導入/置換、upgrade/uninstall後の残存を確認する。異版upgradeと同版置換を同じ検証に数えない。
- venv launcherのwriter保持による部分closeの制約は既存結果として残す。新CLIによるプロセス階層・Job/ハンドル継承への影響を確認し、Mac成功をWindowsへ流用しない。

検証用アプリ・破損・再配置等の変更は`test-results`配下のコピーだけで行う。終了・障害注入は自分で起動して識別したプロセス/窓に限定し、原ログを保持する。Defender、SmartScreen、企業プロキシ等の保護を無効化しない。

### 3. 記録と区切り

実施日・対象HEAD・実環境、wheelタグ/配置/ハッシュ/署名状態、検査・GUI結果、失敗の停止段階と原ログ、未確認事項を新しい検証記録へ保存する。関連README・API/ビルド設計・roadmapを現状に揃え、変更は目的別にコミットする。公開、ライセンス正式採用、証明書購入・実署名、企業環境・クリーンVMの新規構築はこの一区切りへ自動で広げない。

## Windowsのチャットへ貼り付けるプロンプト

```text
PyYakiの作業をWindowsで再開してください。

まずgit statusとローカルのAGENTS.mdを確認し、既存変更を保護してorigin/mainを安全に取り込んでください。12ba635を祖先に含み、docs/handoff-windows-2026-11-08.mdを取得していることと、実際のHEADを確認してください。

同書と関連文書を読み、今回はWindows向けのRust/Cargo不要pip導入・ネイティブCLIの実装と実機検証を一区切りにしてください。MacのPythonラッパーをWindows runの入口へ流用せず、Python初期化前のネイティブ描画、二重rAF・10秒期限・shell通知後のPython生成、localhost待受なしを維持してください。

既存WindowsのPython/Rust/MSVC/WebView2と資材を確認し、新規venv・日本語空白パス・CargoのないPATHでpip配置、CLIのvenv探索とdev/run/build分岐、実WebView2、通常/遅延反復、UTF-8/例外/終了、導入エラー・再導入/更新/削除を確認してください。Macの件数をWindowsの合格へ流用せず、未確認・skip・失敗を明記してください。

検証用変更はtest-results配下のコピーで行い、終了対象は自分で起動・識別したものだけに限定し、保護機能は無効化しないでください。失敗は元ログを残して調べ、変更は目的ごとにコミットしてください。結果と残課題をdocsへ記録し、最後に環境・対象コミット・確認結果・失敗・未確認事項・コミットID・次の候補を報告してください。
```
