# Windows検証後のMac作業引き継ぎ（2026-11-08）

目的は、Windows側の変更を取り込み、**Macの共通コード・実WKWebView・Python同梱.appの回帰確認を行い、結果と残課題をdocsへ記録すること**。次に機能を増やす前の一区切りとして、この順序を推奨する。これはMacで実行した結果ではない。現在地の正本は[ロードマップ](roadmap.md#現在地と次の作業)。

## 最初に読む

1. [AGENTS.md](../AGENTS.md)、[開発ガイド](development.md)、[ロードマップ](roadmap.md)
2. [起動の切り分け](startup-diagnostics.md)、[ネイティブホスト手順](../native/README.md)
3. [Mac .app生成](macos-build.md)、[Macの計測記録](performance-macos.md)
4. [Windows UTF-8修正](validation-worker-logs-2026-11-05.md)、[境界追試](validation-windows-boundary-followup-2026-11-05.md)
5. [Windows単一exeの手順](windows-onefile.md)、[最終実機記録](validation-windows-onefile-2026-11-07.md)

## 取り込む変更と現在地

同期先は`origin/main`（`https://github.com/KASAHARA-Kyohei/PyYaki`）。基点として、次のコミットを含むことを確認する。引き継ぎ文書自身も含め、実際に検証するHEADを別途記録する。

| コミット | 内容 / Macでの注意 |
| --- | --- |
| `8986fb5` | 固定bootstrapでPythonパッケージimport前にstdout/stderrをUTF-8化。Macもこのbootstrapを使う。通常ファイルの既定encodingと隔離モードを維持 |
| `755630e` | 共通host.rsに有限の遷移・新規窓・download・IPC拒否ログ。Windowsで追試済み、Macでの回帰は未確認 |
| `f40bdc1` | Windows単一exeの未署名・非圧縮ローカル試作。Windows専用Rustモジュール、Pythonの生成CLI・形式試験を追加。Mac向けビルドCLIの回帰も確認 |
| `430240c` | Windows検証器の到達点。対象SHA、taoメイン窓の識別、実展開中の取消/強制終了を確認するモードまで追加 |
| `dd9c66f` | Windows単一exeの最終実機結果と関連文書の現在地 |

Windows最終結果は、単一exeの採用14ケース、既存ホストの起動反復9/9、Python 125成功/20スキップ、Rust 16成功/1子entry用ignore、MSVC release・Clippy成功。未署名exeは746,925,088 bytes。以前の不適切な窓close検証、検証器の例外、外側プロセス中断も元ログとともに記録している。**これをMacの成功件数やFR-08/G1/G3全体の合格にはしない。**

Windowsではvenv launcherがwriterを保持するため、workerだけの部分closeは即時EOFにならない制約が残る。全writer切断は埋込Pythonで成功した。この差とMacのSpace問題を同じ原因と決めつけない。

## Gitと環境の準備

- 最初に`git status --short`、現在ブランチ、ローカルとremoteの差分を確認し、利用者の変更を保護する。自動stash、reset、強制checkoutで消さない。
- `git fetch origin`で確認する。クリーンなmainでfast-forwardできる場合に限り`git pull --ff-only origin main`を使う。分岐や既存変更があれば差分を調べ、保護した作業場所で進める。既存コミットを無断で書き換えない。
- `git rev-parse HEAD`、macOS版、CPU、Python/Rust版、通常GIL、SDKを記録する。Windowsで使った版やMacの過去のM2/macOS 27.0.1という値を今回の実測値に代入しない。
- Mac側の既存`.venv`、Rust toolchain、Command Line Tools、standalone Python、Mac用wheelhouseを確認して再利用する。CPython 3.14通常GIL版、RustはCargo.tomlの最低版1.89以上。既定toolchainを不用意に変えない。
- `SDKROOT=/Library/Developer/CommandLineTools/SDKs/MacOSX26.5.sdk`は過去のMacの条件。現在のSDKを確認してから必要な場合にだけ使う。

## 今回進める作業

### 1. 共通コードとMacホストを検査

[開発ガイド](development.md#検証する)の手順を使い、成功・スキップ理由・失敗を実際の件数で記録する。下記はリポジトリルートからの実装済みコマンド。GUI試験とビルド/重い試験は重ねない。

```console
.venv/bin/python -m pytest -q -rs -W error::DeprecationWarning
.venv/bin/python -m ruff check src examples tests native/worker_bootstrap.py
.venv/bin/python -m ruff format --check src examples tests native/worker_bootstrap.py
cargo fmt --manifest-path native/Cargo.toml -- --check
cargo test --manifest-path native/Cargo.toml --locked
cargo clippy --manifest-path native/Cargo.toml --locked --all-targets -- -D warnings
cargo build --manifest-path native/Cargo.toml --release --locked
```

新しいWindows用の形式試験や、`tests/test_onefile_probe.py`→Windows検証器のimportがMacのテスト収集を壊していないかも確認する。失敗したら原因を調べ、Windows関連という理由だけで共通試験を一括skipしない。Mac上のWindows GNU型検査を行う場合も、MSVCリンク・Windows実機の結果と区別する。

### 2. 実WKWebViewと起動・ログを確認

```console
./native/target/release/pyyaki run --project examples/hello --python .venv/bin/python --smoke-test
.venv/bin/python tests/startup_probe.py --binary native/target/release/pyyaki --runs 3 --delay 5
```

- まず通常のウィンドウ表示で確認する。通常/import前5秒/起動フック5秒を各3回。各結果の対象SHA、段階ログ、失敗の停止地点を保存する。
- 二重rAFのscheduled/frame1/frame2→shell→Python生成→worker6段階→welcomeと、既存10秒期限を維持する。描画ログを物理的な画面提示の証明にしない。
- 検証用アプリの変更は`test-results/`配下のコピーで行い、日本語・絵文字のUTF-8ログ、意図したimport例外とログ案内、通常終了時の自分のworker回収を確認する。スモークではUI/RPC・イベント・解除・取消も確認する。
- 新しい有限拒否ログは必要な拒否操作で確認する。全originや全WebKit条件まで確認したことにしない。強制終了等を行う場合も、自分で起動して識別したプロセス/窓だけを対象にする。
- Space移動/フルスクリーンで失敗したら、既知のADR-017と照合する。frame2欠落、DOM visibility、active Space、window occlusionとPython開始の有無を分ける。元ログを残し、成功した再試行で失敗を上書きしない。本対応や期限の変更は、この回帰確認とは別の作業として整理する。

### 3. 同じソースから.appを再生成・再配置

[Mac .app生成](macos-build.md)に従い、更新ホストとframework wheelの両方を再生成する。Mac側の既存入力を使い、`test-results/`内の新規出力へ生成する。既存dist/.app/レポートを上書きしない。

同梱Python・ネイティブ拡張・探索パス、UI/RPC・イベント・取消、通常終了、アドホック署名、manifestの全ファイルハッシュ、再配置を確認する。再配置の負対照は自分の検証用コピーだけを使う。C案アイコンのDock/Finder実表示は観察できた場合だけ記録する。Developer ID署名・公証・他Macへの正式配布を検証済みにしない。

Windowsの`build windows`はWindows上のローカル試作で、Macクロスビルドは未対応。Windows用lockや埋込Python、Fixed WebView2をMacの.appへ流用しない。

## Gitで移るもの・移らないもの

ソース、lock、検証器、docsはGitに入っている。以下はWindows端末のGit管理外で、pullしてもMacへ届かない。

- `test-results/windows-onefile-20261105/`：生成exe・改変コピー・生ログ・補助スクリプト・staging
- `test-results/windows-onefile-preflight-20261105/`：公式Python ZIP/Fixed WebView2 CAB・wheelhouse等
- `.venv/`、`native/target/`、以前の.appやMac用ローカル資材

Windows最終exeのSHA-256は`e5269f35a68623ded54276905521804fe0a33d1fe5adeb8105ec856a57a87aec`。このexeは未署名で、Macでは実行できない。Windowsでの再検証に必要になった場合は、資材と元ログの別途転送を相談する。大きなバイナリを通常のGitへ追加しない。

## 完了条件とその後

Macの実施日・対象コミット・環境・成功/スキップ/失敗・停止段階・原ログ保存先・修正とコミット・未確認事項を、新しい日付の検証記録へまとめる。必要ならroadmapを更新する。修正は目的ごとにコミットし、今回の依頼外の公開・配布・新機能へ自動で広げない。

回帰確認後の候補は、MacのSpace問題（ADR-017）の対応、Windows単一exeのクリーンWindows VM・実Authenticode署名・圧縮/性能評価、公開前のLICENSE/NOTICE/SBOM等。Windowsの受入はWindows環境で別途確認する。今回は署名証明書の購入、正式ライセンス採用、公証/公開、Windows VMの新規構築を依頼した記録ではない。

## Macのチャットへ貼り付けるプロンプト

```text
PyYakiの作業をMacで再開してください。

まずgit statusとローカルのAGENTS.mdを確認して既存変更を保護し、origin/mainの最新変更を安全に取り込んでください。dd9c66f、f40bdc1を含むことと、実際に検証するHEADを確認してください。

AGENTS.mdとdocs/handoff-macos-2026-11-08.mdを読み、同書で指定した関連文書を確認してください。

今回はWindows側の共通コード変更を受けたMac回帰確認を一区切りにします。既存のPython 3.14通常GIL版・Rust・SDK・Mac用資材を確認し、Python/Rustテスト・Clippy、実WKWebViewスモーク、通常/import前5秒/起動フック5秒を各3回、UTF-8ログ・例外表示・通常終了、同じソースからのPython同梱.app再生成・署名/ハッシュ・再配置を確認してください。

既存の二重rAF、10秒期限、Python開始順を維持し、MacのSpace問題とWindowsの失敗を同因と決めつけないでください。失敗は元ログを残して調べ、未確認項目は未確認と記録してください。検証用変更はtest-results配下のコピーで行い、終了対象は自分で起動・識別したものだけにしてください。セキュリティ機能は無効化しないでください。

Windowsのexe・資材・生ログはGit管理外なのでMacへ移っていません。Windows実機の成功や過去のMacの件数を今回の合格に流用しないでください。

結果と残課題を実施日付きのdocsへ記録し、修正は目的ごとにコミットしてください。最後に対象コミット・環境・確認結果・失敗の停止段階・未確認項目・コミットID・次の作業候補を報告してください。
```
