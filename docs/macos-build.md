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

隣に生成する`Hello PyYaki.build.json`へ、ホスト/wheel/lockのハッシュと生成ファイルのSHA-256・サイズ・内部symlinkを記録する。これは実行時に読む入力ではなく、ビルドの確認用レポート。既存の`.app`やレポートは上書きしない。ビルド中のstagingは失敗時にも破棄する。

起動通知の内部契約を変更した場合（2026-11-04の計測追加等）は、Rustホストとframework wheelの両方を同じソースから再生成する。既存.app内のホスト・ワーカーは自動更新されない。

## 設定と制限

- `tool.pyyaki.name`、reverse-DNS形式の`app-id`、3つの数値からなる`version`をbundleメタデータに使う。window.title未指定時はnameを使う。開発用ホストがnameを直接読む動作とは区別する。
- `tool.pyyaki.build.macos.include`はプロジェクト相対globの配列。既定はentryのPythonファイルとUI配下。domainやデータ等が必要なら明示し、pyproject.toml自体はincludeしない。実行用設定はentry/UI/windowだけから生成する。
- `.env`等の隠しファイル、venv・Git・生成物・秘密鍵拡張子を除外し、アプリ資産のsymlinkやパス脱出を拒否する。Pythonコードに直接書かれた秘密情報の自動判定は行わない。
- lockは推移依存を含む`name==version`とSHA-256だけを受け付ける。URL・pipオプション・環境マーカー・extrasはこの試作では未対応。wheelhouseは通常の`.whl`ファイルだけを置く。資材解決の自動ネット取得は行わない。
- 同梱環境でのアプリimport確認はアプリのPythonコードを実行する。ビルドは未信頼コードのsandboxではない。`.pth`/sitecustomize依存は拒否し、独自ローダーや全native wheelへの対応は保証しない。
- Mach-OのCPU・絶対リンク先、runtimeの外へ出るsymlinkを検査し、アドホック署名後にcodesign検証する。ランタイムとwheelに含まれるライセンスファイルは保持する。全依存の法的な再配布確認やPyYaki自身のライセンス正式採用を完了した意味ではない。

## 確認した範囲

macOS 27.0.1 / M2 / CPython 3.14.3で、同梱Pydantic native拡張を含むhelloの生成・実WKWebViewのRPC/イベント/取消・正常終了を確認した。`.app`を移動し、資材元のruntimeとプロジェクトのコピーを一時的に隠して、PATHをシステム用だけにし、無効なPYTHONHOME/PYTHONPATHを指定した状態でも成功した。起動後に署名とmanifestの全ファイルハッシュも再確認した。

これはPython未導入のクリーンMacや別CPUでの試験ではない。Developer ID署名、公証、quarantine付き配布、最低OS版、Intel、DMG、アイコン・更新、第三者への正式配布は後続検証とする。5分の性能報告は開発用ホストの別測定であり、このbundleの性能保証には使わない。
