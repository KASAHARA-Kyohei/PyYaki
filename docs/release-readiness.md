# Windows配布前の棚卸しと外部環境での確認

更新: 2026-11-10。実行した内容は[追加検証記録](validation-windows-followup-2026-11-09.md)と[ライセンス反映記録](validation-license-2026-11-10.md)を正とする。本書の手順を用意しただけで、クリーンVM・Mac・実署名・公開の成功にはしない。VM/Macは利用者が所有しているとの回答があり、利用方法は未確認。

## ローカル依存棚卸し

`pyyaki.release_inventory`はメンテナー専用のローカル補助ツール。明示したハッシュ固定wheel、Windows向けCargo解決グラフ、Python/WebView2ディレクトリからCycloneDX 1.6形式の草稿と通知本文を生成する。追加ツール・ネットワーク取得は行わない。プロジェクトのライセンス採用、依存バイナリ再配布、公開は別判断のまま。

```powershell
.venv\Scripts\python.exe tools\check_versions.py
cargo +1.89.0 metadata --manifest-path native/Cargo.toml --locked --offline --format-version 1 --filter-platform x86_64-pc-windows-msvc > test-results\cargo-metadata.json
.venv\Scripts\python.exe -m pyyaki.release_inventory --wheelhouse C:\local\wheelhouse --framework-wheel C:\local\pyyaki-0.1.0.dev0-py3-none-any.whl --requirements examples\hello\requirements-windows.lock --cargo-metadata test-results\cargo-metadata.json --source-commit <40文字の対象コミット> --runtime C:\local\runtime --runtime-version 3.14.10 --webview2 C:\local\webview2 --webview2-version 156.0.4314.58 --output test-results\inventory-new
```

上のパス・コミットは利用時に置換する例。Cargo metadataの対象platformを指定し、wheelhouseへ関係のないwheelを混ぜない。既存出力は拒否する。失敗途中のディレクトリも成功成果物として使わず、別出力でやり直す。

- `bom.cdx.json`: 配布候補wheelのSHA-256、宣言された版・ライセンス名、Cargoの到達可能なsource/build依存グラフ。Cargoグラフは実際のバイナリへのリンク証明ではない。コンポーネントのライセンス名は元の宣言を保持し、OR条件から選択した扱いにしない。
- `licenses/`: 見つかったLICENSE/COPYING/NOTICE/COPYRIGHTを元バイトのまま保存。本文SHA-256で重複をまとめ、元のコンポーネント・ファイル名との対応をinventoryへ残す。
- `inventory.json`と`THIRD-PARTY-NOTICES.draft.md`: 未発見の本文・ランタイムの再配布/内包ライブラリ・CRT確認を列挙し、常に`review_required`とする。PyYaki自体にライセンスを自動設定しない。
- `cpython-files.json` / `webview2-files.json`: 明示ディレクトリ全ファイルのサイズ・ハッシュ。入力版は指定値で、公式配布物との来歴・署名・版の照合は別に必要。reparse pointは拒否する。

これは完全なSCA、脆弱性監査、法的互換性の判定、exe内部の全サブコンポーネント列挙ではない。JSON schema全体の検証、配布exe内から通知を取り出す製品機能、CI来歴・attestationはまだ追加していない。

比較日2026-11-09: [Syft](https://github.com/anchore/syft)はファイルシステム等のSBOM生成、[cargo-about](https://github.com/EmbarkStudios/cargo-about)はCargoライセンス収集を提供する。正式運用では再利用候補とする。今回は既存の明示資材と原文保持・不足一覧の確認に範囲を絞り、追加バイナリを取得せず標準ライブラリの小さい補助ツールを使った。独自のパーサー・スキーマ保守が増えるため、正式公開の採択とは区別する（ADR-025）。形式は[CycloneDX 1.6](https://cyclonedx.org/docs/1.6/json/)、Cargo入力は[公式metadata仕様](https://doc.rust-lang.org/cargo/commands/cargo-metadata.html)を参照する。

## Python不要のWindows確認器

`tools/verify_windows_onefile.ps1`はWindows標準PowerShell/.NETだけを使うメンテナー用確認器。対象exeに`--smoke-test`を渡す。自分で生成したProcessのhandleと、生存中に見つけた子のhandleだけを観測する。120秒watchdog、原ログ、CPU/Working Set/Private Bytesの標本、段階ログを新規test-resultsへ保存する。終了は自分のルートだけで、配下回収はホストのJobが担当する。

```powershell
powershell.exe -NoProfile -File .\verify_windows_onefile.ps1 -Binary .\Hello.exe -Output C:\test-results\fresh-new -Runs 1 -CacheMode fresh -RequireCleanEnvironment
powershell.exe -NoProfile -File .\verify_windows_onefile.ps1 -Binary .\Hello.exe -Output C:\test-results\cached-new -Runs 1 -CacheMode cached -RequireCleanEnvironment
```

ネットワークアダプターの状態・管理者トークン・PATHの開発ツール・Python登録・WebView2 Runtime登録を観測し、既知の条件が揃わなければ`-RequireCleanEnvironment`で実行前に失敗する。検出できないソフトがないことや、試験中ずっと物理的に通信不能だったことまでは保証しない。前後のネットワーク状態、VM設定・標準ユーザー・Runtimeの実際の有無も保存する。署名付き成果物を検証する場合は`-RequireSignature`でAuthenticodeのValidを要求する。署名検査を回避する設定は加えない。

この確認器は`observed`という補助結果を返す。最初のGDI/HTMLの物理的提示やT_native/T_shell/T_interactiveを測定した扱いにせず、性能目標との合否比較はしない。cachedの初回1件はwarmupとして分離する。現試作は起動ごとのWebView2 profileなので、cachedも**アプリ展開cacheあり・profile新規**であり、requirementsのprofile再利用条件と混同しない。30回群・再起動後10回・録画/ETW等の正式計測は[要件の実験手順](requirements.md#53-実験手順)に従って別に実施する。

実行ポリシーや組織の制限でブロックされたら、その拒否を記録して組織で承認された実行方法を確認する。ExecutionPolicy Bypass、Defender等の停止、ホストのネットワークアダプター無効化、管理者昇格を補助ツールへ入れない。

## クリーンVMへ持ち込むもの

内部検証用には、生成exe・そのbuild report/SHA-256・上の確認器・棚卸し草稿を`test-results`の専用フォルダーへ置く。アプリの依存解決・展開後実行を検証するVMにはRustやPythonをインストールしない。既存VMの構成・所有者・スナップショットを確認してから使い、無断で既存VMを再初期化しない。

`tools/windows-offline.wsb.example`は**未起動の設定例**。Windows Sandboxが既に使える場合だけ、HostFolderを自分のtest-results内のinput/outputへ置換する。inputは読取り専用、outputだけ書込み、guest networking/clipboardは無効、ProtectedClientは有効。ホストのネットワークや保護は変更しない。Sandboxの有効化・再起動はこのツールでは行わない。[Microsoftの設定仕様](https://learn.microsoft.com/en-us/windows/security/application-security/application-isolation/windows-sandbox/windows-sandbox-configure-using-wsb-file)を参照。

Sandbox/VMにEvergreen等が既に含まれていれば、未導入端末の受入には使えない。その場合も通常の動作確認は別条件として残し、未導入と記録しない。Sandboxの既定メモリを正式性能の基準環境とも扱わない。

## 署名・公開へ進む前

既存の証明書/署名サービスの方式と署名する最終exeを確認する。今回のCurrentUserストアでは対応する秘密鍵付きコード署名証明書を確認できていない。2026-11-10に利用者から「今は証明書がない」と回答があった。証明書購入・自己署名による代用はしない。署名後は`Get-AuthenticodeSignature`のValid、署名前後のペイロード読取り・全ハッシュ、実起動・更新、組織の実行許可を別に確認する。署名だけでSmartScreen/Defender/企業制限の解除を保証しない。

本体のApache-2.0は採用済みだが、第三者通知の不足解消、依存バイナリの再配布可否、公開先・版・配布経路の判断が済むまで、PyPI/Releaseへの公開を完了扱いにしない。ソースの作業ブランチpushと、wheel/exeの正式公開は別操作である。
