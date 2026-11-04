# Mac用.app生成のローカル試作

状態: 2026-10-31にApple Siliconの開発機で実装・確認。Pythonを同梱した`.app`（macOSのアプリ用ディレクトリ構造）を生成する。Developer ID署名・公証・他のMacへの正式配布は未対応。Windowsの単一exeは別のG1/G3で検証する。

## 生成するもの

`pyyaki build macos`は、同じMacのCPU向けに用意したRustホスト、standalone CPython 3.14通常GIL版、ハッシュ固定した依存wheel、PyYaki wheel、明示したアプリ資産から`.app`を組み立てる。開発venv全体のコピーや、Pythonを先に起動するシェルランチャーは使わない。

起動時は`Contents/MacOS/PyYaki`が自分のbundleから同梱プロジェクトとPythonを探し、従来と同じHTMLマーカー後にワーカーを生成する。アプリのRPC/UI配信にHTTP/WebSocket待受は使わない。ワーカーは`-I -B`で起動し、環境変数のPython探索パスとbundle内へのbytecode書込みを避ける。

## 資材を準備する

以下はリポジトリのルート、開発用venvを準備済みのApple Silicon Macでの例。現在はメンテナーがホストとwheelを用意する。生成処理自体はCargoを呼ばず、指定した資材から組み立てるが、Appleのcodesign/otool/lipo（Command Line Tools）も必要。これらは配布先でのアプリ実行には使わない。公開wheelへのホスト同梱・資材の自動取得は今後の作業。

```console
cargo build --manifest-path native/Cargo.toml --release --locked
.venv/bin/python -m build --wheel --no-isolation --outdir build/macos-inputs/wheels
.venv/bin/python -m pip download --only-binary=:all: --require-hashes -r examples/hello/requirements-macos.lock --dest build/macos-inputs/wheels
```

この開発機ではCargoに`SDKROOT=/Library/Developer/CommandLineTools/SDKs/MacOSX26.5.sdk`を指定した。別のMacにこのSDKが存在する前提にはしない。サンプルのlockはMac用で、Windows配布のlockに流用しない。

Pythonランタイムは[python-build-standaloneの公式配布](https://github.com/astral-sh/python-build-standalone/releases/tag/20260211)を使って確認した。採用比較は[ADR-014](architecture.md#8-adr候補一覧)を参照する。確認した資材は次のとおり。

- ファイル: `cpython-3.14.3+20260211-aarch64-apple-darwin-install_only_stripped.tar.gz`
- SHA-256: `348647e4c13b662f7b0d218ccf472688038679815fb1a429ca664b7dce324237`
- [公式ダウンロード](https://github.com/astral-sh/python-build-standalone/releases/download/20260211/cpython-3.14.3%2B20260211-aarch64-apple-darwin-install_only_stripped.tar.gz)

取得後にSHA-256を照合し、次の例のように安全なfilterで新規ディレクトリへ展開する。展開先の`python/`が`--runtime`の入力になる。HomebrewのPythonやvenvは再配置可能な配布用ランタイムとして扱わない。

```python
import hashlib
import tarfile
from pathlib import Path

archive = Path("build/macos-inputs/python.tar.gz")
with archive.open("rb") as stream:
    actual = hashlib.file_digest(stream, "sha256").hexdigest()
if actual != "348647e4c13b662f7b0d218ccf472688038679815fb1a429ca664b7dce324237":
    raise ValueError("Runtime hash mismatch")
target = Path("build/macos-inputs/runtime")
target.mkdir(parents=True, exist_ok=False)
with tarfile.open(archive) as stream:
    stream.extractall(target, filter="data")
```

## 生成・起動する

```console
.venv/bin/pyyaki build macos \
  --project examples/hello \
  --host native/target/release/pyyaki \
  --runtime build/macos-inputs/runtime/python \
  --wheelhouse build/macos-inputs/wheels \
  --framework-wheel build/macos-inputs/wheels/pyyaki-0.1.0.dev0-py3-none-any.whl \
  --requirements examples/hello/requirements-macos.lock \
  --output "dist/Hello PyYaki.app"
```

`.app`をFinderから開くか、`Contents/MacOS/PyYaki`を引数なしで実行する。同梱Pythonを使うため`--project`や`--python`の指定は不要。helloサンプルだけを自動確認する場合は次を使う。

```console
"dist/Hello PyYaki.app/Contents/MacOS/PyYaki" --smoke-test
```

隣に生成する`Hello PyYaki.build.json`へ、ホスト/wheel/lockのハッシュと生成ファイルのSHA-256・サイズ・内部symlinkを記録する。これは実行時に読む入力ではなく、ビルドの確認用レポート。既存の`.app`やレポート（壊れたsymlinkも含む）は上書きしない。ビルド中のstagingは通常の例外やKeyboardInterruptで破棄する。完成したレポートをstagingに書き、同一ボリュームのhard linkで新規公開してから、Darwinの`renamex_np(RENAME_EXCL)`で完成した.appを上書きなしで公開する。appの公開に失敗したら、自分が作ったレポートだけをinodeで照合して取り除く。

起動通知の内部契約を変更した場合（2026-11-04の計測追加等）は、Rustホストとframework wheelの両方を同じソースから再生成する。既存.app内のホスト・ワーカーは自動更新されない。

## 設定と制限

- `tool.pyyaki.name`、reverse-DNS形式の`app-id`、3つの数値からなる`version`をbundleメタデータに使う。window.title未指定時はnameを使う。開発用ホストがnameを直接読む動作とは区別する。
- 既定アイコンは[選択したC案](../assets/branding/README.md)。wheel内の`static/pyyaki.icns`を`Contents/Resources/PyYaki.icns`へ署名前に配置し、`CFBundleIconFile`で参照する。通常ファイルとしてレポートへハッシュを記録する。変換済み資産を使うため、生成時のPillowやiconutilは不要。独自アイコン指定の設定は未実装。
- `tool.pyyaki.build.macos.include`はプロジェクト相対globの配列。既定はentryのPythonファイルとUI配下。domainやデータ等が必要なら明示し、pyproject.toml自体はincludeしない。実行用設定はentry/UI/windowだけから生成する。
- `.env`等の隠しファイル、venv・Git・生成物・秘密鍵拡張子を除外し、アプリ資産のsymlinkやパス脱出を拒否する。Pythonコードに直接書かれた秘密情報の自動判定は行わない。
- lockは推移依存を含む`name==version`とSHA-256だけを受け付ける。URL・pipオプション・環境マーカー・extrasはこの試作では未対応。wheelhouseは通常の`.whl`ファイルだけを置く。資材解決の自動ネット取得は行わない。
- 同梱環境でのアプリimport確認はアプリのPythonコードを実行する。ビルドは未信頼コードのsandboxではない。`.pth`/sitecustomize依存は拒否し、独自ローダーや全native wheelへの対応は保証しない。
- Mach-OのCPU・絶対リンク先、runtimeの外へ出るsymlinkを検査し、アドホック署名後にcodesign検証する。ランタイムとwheelに含まれるライセンスファイルは保持する。全依存の法的な再配布確認やPyYaki自身のライセンス正式採用を完了した意味ではない。

## 確認した範囲

macOS 27.0.1 / M2 / CPython 3.14.3で、同梱Pydantic native拡張を含むhelloの生成・実WKWebViewのRPC/イベント/取消・正常終了を確認した。`.app`を移動し、資材元のruntimeとプロジェクトのコピーを一時的に隠して、PATHをシステム用だけにし、無効なPYTHONHOME/PYTHONPATHを指定した状態でも成功した。起動後に署名とmanifestの全ファイルハッシュも再確認した。

これはPython未導入のクリーンMacや別CPUでの試験ではない。Developer ID署名、公証、quarantine付き配布、最低OS版、Intel、DMG、独自アイコン設定・更新、第三者への正式配布は後続検証とする。5分の性能報告は開発用ホストの別測定であり、このbundleの性能保証には使わない。

## 失敗時処理の追試（2026-11-04）

`tests/test_build_macos.py`で、資材欠落、runtimeコピーの途中失敗・再配置失敗、依存とframeworkの導入失敗、アプリimport、署名・検証失敗、レポート書込時の容量不足、公開権限エラー、KeyboardInterruptを注入した。stagingと自分の不完全な成果物が残らず、既存成果物・途中で出力先へ置かれた別成果物・別のレポートを保護することを確認した。外部Python/pip/署名コマンドの失敗注入はstubによる単体試験で、実際にOS設定やディスク容量を変更していない。Darwinの排他的renameは実APIで空の既存ディレクトリへの置換拒否も確認した。

別途、実runtimeとオフラインwheelhouseから`test-results/macos-publication-proof/Hello PyYaki.app`を生成し、実codesignの検証・実WKWebViewスモークに成功した。隣の`.build.json`の通常ファイル2,505件をSHA-256で照合し、stagingが残っていないことを確認。元の`dist/Hello PyYaki.app`は変更していない。

## 既定アイコンの組み込み検証（2026-11-04）

`cf3c2c6`のホストとICNS入りのwheel、既存のオフライン資材から、別の`test-results/icon-proof-final/Hello PyYaki.app`を生成した。`CFBundleIconFile=PyYaki.icns`と資産の一致、AppleのiconutilによるICNSの読み戻し、通常ファイル2,507件のレポートハッシュ、実codesignの検証を確認。Pillowは同梱Pythonに入っていない。確認値は同じディレクトリの`verification.json`に保存した。既存のdistや以前の検証.appは上書きしていない。

実WKWebViewのスモークにも成功し、結果は`bundle-smoke.json`と`bundle-smoke.log`に保存した。起動後の署名も再確認した。Dockの画面取得はCUAツールがタイムアウトしたため、Dock/Finderの実表示を目視確認したとは扱わない。独自アイコン設定、他Macや正式配布の検証は未完了。

出力先には同一ボリュームでhard linkと排他的renameを使えることが必要で、未対応なら暗黙の上書きへフォールバックせず失敗する。appとレポートの2つをまとめた原子的トランザクションではない。SIGKILL・電源断・公開の極小区間での中断や、同権限の別プロセスによる任意の改変に対する完全な復旧は保証しない。レポートだけ残った場合は対応する.appの有無と内容を確認してから、不要なものだけ手動で整理する。G3全体の競合・破損・中断試験の完了とは扱わない。
