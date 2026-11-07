# Windows単一exeのローカル試作

更新: 2026-11-07。`pyyaki build windows`へ明示した事前取得資材から、Python・依存・UI・Fixed Version WebView2を含む単一exeを生成する**未署名のローカル試作**を追加した。正式配布、FR-08/G1全体の合格、起動速度の達成を意味しない。[前段の資材確認](windows-onefile-prototype.md)と区別する。

## 入力と生成

最初の対象はWindows 11 x64、CPython 3.14通常GIL版。ビルド用Python/pipと、同じframework版・onefile format 1に対応する**未署名の事前ビルド済みホスト**が必要。アプリごとにCargoを実行しない。ネイティブホストのwheelへの自動配置、ランタイムの自動取得・更新、クロスビルドは未対応。

資材の準備は[前段記録](windows-onefile-prototype.md)を参照する。`--runtime`は公式埋込Pythonを展開したフォルダー、`--webview2`はx64 Fixed Versionの`msedgewebview2.exe`があるフォルダー、wheelhouseはハッシュ固定lockに対応するローカルwheelのみとする。信頼した入力を使うこと。生成中に、配置したPythonでアプリをimportしてentryを検査する。

実装済みCLIの例（パスは準備した資材へ読み替える）:

```powershell
.\.venv\Scripts\python.exe -m pyyaki.cli build windows --project examples\hello --host native\target\release\pyyaki.exe --runtime build\inputs\python --webview2 build\inputs\webview2 --wheelhouse build\inputs\wheels --framework-wheel build\inputs\pyyaki-0.1.0.dev0-py3-none-any.whl --requirements examples\hello\requirements-windows.lock --output dist\Hello.exe
```

既存のexeと`.build.json`は置き換えない。生成物はexeと開発者向けレポート。実行にレポートや元プロジェクトを持ち運ぶ必要はない。レポートにはホスト/framework wheel/lock/wheel群のSHA-256、全同梱ファイルのサイズ/位置/SHA-256、exeのSHA-256を残す。ビルド中に依存の取得・sdistビルド・埋込Pythonへのpip導入はしない。

設定は既存の`tool.pyyaki`の`app-id`、`name`、entry/UI/windowを使う。試作の`name`と同梱相対パスはASCIIに制限する（配置元・出力先・キャッシュの絶対パスの日本語とは区別）。追加のアプリファイルは、実装済みの次の設定で明示する。既定はentryファイルとUI配下。秘密鍵、隠しファイル、venv等は既存の収集規則で除外する。

```toml
[tool.pyyaki.build.windows]
include = ["app.py", "ui/**/*", "data/*.json"]
```

`pyproject.toml`はentry/UI/windowだけを生成し、開発用の他設定をコピーしない。`._pth`は`python314.zip`、`.`、`../../packages`だけとし、siteを有効にしない。`.pth`/sitecustomize依存は拒否する。Windowsワーカーへ`-B`を加え、開発用runも含めてbytecodeの書込みをしない。同梱アプリがキャッシュ内へ生成・書換えを行うと次回の完全性確認が失敗する。業務データの保存先APIは追加していない。

## 起動とプロセス

1. 同梱exeを引数なしで起動すると、Python/WebView2に依存しないRust/GDIの準備画面を作る。
2. 最初の描画呼出しの後、別スレッドで索引・同梱ファイルの検証とキャッシュ展開を行う。準備画面は閉じられる。描画ログはGDIの実提示・性能の証明ではない。
3. 完了したキャッシュだけを使い、**同じexeをGUI用の子ホストとして起動**する。子だけの環境でWebView2の環境変数を取り除き、同梱Fixed Versionへの絶対パスを指定する。既存GUI経路の二重rAF→shell→Python開始、各10秒期限を維持する。
4. GUI側の通常終了を待ち、その終了コードを返す。ランチャーはKILL_ON_JOB_CLOSEのJobを保持し、子ホストを停止状態で割り当ててから再開する。ランチャー強制終了では、子ホストとそのPython Jobを含む自分のツリーを回収する。

準備画面からGUIへ移る2段階の窓・2ホスト構成は今回の試作上の選択。1ウィンドウ内での連続した展開/UI合流や初回性能最適化は未実装。`--smoke-test`と`--diagnostics`は既存の検証モードを子ホストへ渡す。製品の公開RPCは増やさない。開発用`run --project ... --python ...`は引き続き明示したvenvを使う。

## 形式とキャッシュの制約

format 1はPE本体→非圧縮blob群→JSON索引→64-byteフッター。追加の圧縮ライブラリは使わず、圧縮方式の正式選定を保留した。PE32+の証明書テーブルの**ファイルオフセット**を考慮して、署名前ペイロードの末尾を定位する。構造上の人工テストは実署名の検証と区別し、現時点の出力は未署名とする。ハッシュの一致は作成者の正当性を証明しない。署名する場合も組立後に行い、実署名後の読取・完全性の検証が別途必要。

上限はexe全体2GiB、1ファイル512MiB、索引8MiB、通常ファイル10,000件、検証するディレクトリ20,000件、同梱相対パス220 ASCII bytes。blobを連続させ、整数overflow、範囲外、重複、大小文字衝突、ファイル/ディレクトリ衝突、絶対/親パス、ADS、予約デバイス名、リンク/reparse pointを拒否する。圧縮による過大展開はない。

試作の展開先は`%LOCALAPPDATA%\PyYakiOnefile\<app-id>\<index-sha256>`。既存の開発用領域のACLを変えないため、設計案の`PyYaki\apps`と区別した専用領域を使う。ルート/appフォルダーはownerとSYSTEMだけの保護ACLを新規作成し、既存フォルダーは同じACLを要求する。ACLの自動修復、所有権取得、セキュリティ機能の無効化はしない。同じユーザー権限の攻撃者に対する完全な防御ではない。

索引ハッシュごとのOSファイルロックで展開を直列化し、同一ボリュームのランダムなstagingへ新規作成する。全ファイルを検証してから既存先を置換しないMoveFileで確定する。再起動時もexeのblobとキャッシュ全件を照合し、完了マーカー/mtimeだけで省略しない。破損は上書き修復せず停止し、ログへ記録する。キャンセル/強制終了/失敗のstagingは診断用に残し、再利用・自動実行しない。古い版・stagingの安全な自動清掃とディスク予算は未実装。

ログは`%LOCALAPPDATA%\PyYaki\prototype\<instance>\launcher.log`と、別instanceの既存`host.log`/`worker.log`。launcherにGUI子PIDと採用キャッシュを残す。WebView2プロファイルはGUI instance内で、実行用キャッシュから分離する。アプリデータへは使わない。

## 検証と残るゲート

メンテナー用の`tests/onefile_windows.py`で、生成exe、検証結果の新しい保存先、必要なら再利用する検証用LOCALAPPDATAを明示する。強制終了は自分のPopenハンドルだけ、通常closeは観測した自分の窓/PIDだけに送る。

```powershell
.\.venv\Scripts\python.exe tests\onefile_windows.py --binary dist\Hello.exe --output test-results\onefile-first
.\.venv\Scripts\python.exe tests\onefile_windows.py --binary dist\Hello.exe --output test-results\onefile-cached --local test-results\onefile-first\local --case cached
```

実機結果は後続の実施記録を参照する。未導入・オフラインのクリーンVM、実署名と署名後改ざん、DLL依存棚卸し、正式なLICENSE/NOTICE/SBOM、企業PAC/実行制限、MOTW、容量不足/電源断、使用中旧版の清掃、30回以上の性能測定は残る。現端末の成功や環境変数の負対照で代替しない。
