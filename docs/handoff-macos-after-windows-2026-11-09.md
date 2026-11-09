# Windows CLI作業後のMac回帰引き継ぎ

更新: 2026-11-09。このWindows端末ではMac実機にアクセスできていないため、以下は準備した再確認手順であり成功記録ではない。以前のMac件数を更新版の合格にしない。

## 安全な取り込み

ローカルAGENTS.md、git status、現在のHEADと差分を先に確認する。既存変更をstash/resetで隠したり取り消したりしない。Windows作業は`codex/windows-native-cli`へコミットする。push状態は[追加検証記録](validation-windows-followup-2026-11-09.md)に残す。

```console
git status --short
git fetch origin
git log --oneline HEAD..origin/codex/windows-native-cli
git log --oneline origin/codex/windows-native-cli..HEAD
```

既存変更・分岐がなく安全な場合だけ、そのブランチをff-onlyで取り込む。実際の対象HEADとPython/Rust/SDK版を記録する。Windowsのvenv・exe・wheel・test-resultsをMac用資材として流用しない。

## 確認する変更

- host::runの引数受渡しは共通部分。Macのguard・Space配置復元・二重rAF・10秒期限は維持したが、Mac実機で再確認する。
- worker bootstrapにCPython 3.14通常GILの検査を追加した。UTF-8と通常ファイルのencodingを変えないこと、例外・終了を確認する。
- WindowsのWeb reload用selector/内容ハッシュ、native CLI/JobはWindows条件付き。Macの既存loopやguardへ移植していない。
- nativeに2つ目のbinを追加したため、従来のcargo runが曖昧になる問題を`default-run = "pyyaki"`で修正した。
- source版スタンプを`tools/check_versions.py`で確認できる。SDKだけ旧版という資材生成ミスを防ぐ。アプリはimportしない。

## Macで実行する

Macの対応venv・Rust/SDK・Playwrightを既存手順で準備する。SDKROOT指定はその端末で必要性と存在を確認してから使う。

```console
.venv/bin/python tools/check_versions.py
.venv/bin/python -m pytest -q -rs -W error::DeprecationWarning --basetemp test-results/mac-followup-pytest-new
.venv/bin/ruff check src examples tests tools/check_versions.py native/worker_bootstrap.py
.venv/bin/ruff format --check src examples tests tools/check_versions.py native/worker_bootstrap.py
cargo fmt --manifest-path native/Cargo.toml -- --check
cargo test --manifest-path native/Cargo.toml --locked
cargo clippy --manifest-path native/Cargo.toml --locked --all-targets -- -D warnings
cargo build --manifest-path native/Cargo.toml --release --locked
cargo run --manifest-path native/Cargo.toml --release --locked -- --version
.venv/bin/python tests/macos_regression.py --output test-results/mac-after-windows-new
.venv/bin/python tests/macos_host_cleanup.py --binary native/target/release/pyyaki --output test-results/mac-cleanup-after-windows-new
.venv/bin/python tests/browser_dev_reload.py --output test-results/mac-web-after-windows-new
```

自分の検証窓・子だけを終了し、故障注入はtest-resultsのコピーで行う。失敗の段階・元ログ・スキップ理由を残す。新ホストでMac wheel/.appを別出力へ再生成し、[Mac wheel](macos-wheel.md)・[.app](macos-build.md)の署名/全manifest・日本語パス再配置を確認する。元の生成物は置き換えない。

fullscreen/Space、物理的提示・実クリック、長時間試験を実行していなければ未確認とする。結果・実際のHEAD・資材ハッシュをMac側の新記録へ残し、目的別コミットで返す。Windowsの合格へMacの成功を足さない。
