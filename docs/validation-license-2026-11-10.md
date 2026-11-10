# Apache-2.0採用とWindowsパッケージ再確認

実施日: 2026-11-10（JST）。基点は`fe722adf6806a81a721365251ef3d599d0143d00`、ブランチは`codex/windows-native-cli`。作業開始時のgit statusはクリーン。再fetchした`origin/main`は`0949e8a3273b2d773e13e4494ab06ab03b58183a`で、基点の祖先に含まれる。取り込む新規mainコミットはなかった。

## 承認と変更

- 利用者が本体のApache-2.0採用を明示選択した。ADR-010、README、要件、ビルド方針、roadmapを更新した。
- [Apache公式本文](https://www.apache.org/licenses/LICENSE-2.0.txt)を改変せず、rootとnativeのLICENSEへ配置した。SHA-256は`cfc7749b96f63bd31c3c42b5c471bf756814053e847c10f3eb003417bc523d30`。native配下のコピーはパッケージ外ファイルを読まない既存棚卸しの境界を維持するためで、rootと同一バイトを確認した。`.gitattributes`で両方のLFを指定し、Windowsでの自動改行変換を防ぐ。
- PythonへSPDX式と同梱ファイルを設定し、RustにもApache-2.0を宣言した。[setuptools公式説明](https://setuptools.pypa.io/en/latest/userguide/pyproject_config.html)でPEP 639対応が77以降であることを確認し、ビルド・dev要件の下限を77へ揃えた。既存lockの84.2.1は変更不要だった（参照日2026-11-10）。
- 権利者名は推測していない。第三者通知草稿、依存バイナリ再配布、PyPI/Release公開の判断は別に残す。

ネイティブ起動順・IPC・Pythonワーカーのコード変更はない。Python前の描画、二重rAF、10秒期限、shell通知後のPython生成、runでlocalhost待受なしという設計を維持する。今回のメタデータ変更で11-09のGUI検証・性能を新たに合格へ数え直さない。

## 実行結果

Windows 11 x64 build 26200、CPython 3.14.10通常GIL、setuptools 84.2.1。rootのソースを`test-results/license-20261110/source`へコピーして生成し、既存資材は上書きしていない。

| 確認 | 結果・範囲 |
| --- | --- |
| 共通pytest | 207 passed / 26 skipped、DeprecationWarningをerror。skipはWindowsのsymlink権限、Darwin/POSIX専用検査。Mac実機成功とは数えない |
| 版スタンプ | Python/Rust/SDK/pyproject/Cargoの版一致 |
| pure wheel / sdist | `--no-isolation`でローカル生成成功。公式LICENSEの同一バイト、License-Expression、wheelのLicense-File/全RECORDを照合 |
| Windows native wheel | 既存releaseのGUI/CLIを使用して新規生成。同じLICENSEとLicense-Expression/License-File/全RECORDを照合。実署名は未実施 |
| 新規venv | 日本語・空白パス、PATHは当該ScriptsとWindows System32のみ。`--no-index --no-deps`導入後のライセンス本文/メタデータ、native help/version、uninstallとCLI/package消失に成功。依存一式の再導入・GUI再試験ではない |

| 内部検証成果物 | SHA-256 |
| --- | --- |
| pure wheel | `3e4bc32ff9cd1c4870f1ac9d8fefcf857bbd339de4434692b1b065d230dbbf4d` |
| Windows native wheel | `2b8d35cefd4bf0f5bd39f8d29fdecbfefe21cdfb2e4f2262cd9c275481a86839` |
| sdist | `10361bcc7a341564568f0f93e922f29cf57eed324d3ca3ebbcf1424be5b0dfe2` |

これらはローカルの作業コピーから生成した内部確認物で、公開リリースの来歴保証ではない。ログ・wheel・sdistはGit管理外のまま保持した。

## 失敗・修正と原ログ

原ログは`test-results/license-20261110`配下。

- 最初の公式本文取得はsandbox内でDNS失敗。同じ公式URLを許可されたネットワーク実行で取得した。`download-initial-failure.txt`に内容、原ツール出力は作業チャットに残る。
- native wheelの最初の原子的配置はハードリンク作成時のWinError 5。`native-wheel.log`を保持し、保護を変更せず許可された実行で別の`native-wheel-retry`出力へ生成した。
- 内容照合の初稿はpure専用readerへnative wheelを渡したため拒否された（`artifacts.log`）。native側を全RECORDの直接照合へ修正し、`artifacts-corrected.log`で成功。製品readerの制約は緩めていない。
- 導入検査の初稿は`os.environ.copy()`の通常dictで環境変数キーの大小文字を取り違えた（`install.log`）。検査コードだけ修正し、`install-corrected.log`で成功。初稿も保存した。
- sandbox内の初回pytestは約322秒で無出力のまま手動停止した。PID 17480/19432、完全コマンド、起動時刻を識別し、保持した当該process handleだけを終了した。時刻の完全一致では精度差により最初の停止条件を満たさず、再照合して終了した。`pytest.json`、空の`pytest.log`、`stalled-pytest-*.json`を保持する。停止原因の詳細は未特定で、テスト成功・製品失敗のどちらにも数えない。通常権限の新規出力`pytest-retry.log`は約49秒で完了した。

## 外部環境・署名・公開の残り

利用者はクリーンWindows VMとMacを所有していると回答したが、この端末から利用する方法は未確認。接続先の質問は別環境の実機試験を実行するためのもので、パスワード等の送付は求めない。これらの実機試験を合格にせず、準備した[手順](release-readiness.md)と[Mac引き継ぎ](handoff-macos-after-windows-2026-11-09.md)を残す。

利用者から署名証明書は「今はない」と回答があった。実署名・署名後の起動/更新は未確認。物理提示の正式計測、クリーン環境での未導入・標準ユーザー・物理オフライン、第三者本文/再配布条件、CI来歴・脆弱性監査、公開先/版は引き続き残る。venvの部分close制約も解消していない。Macの以前の成功件数をWindowsへ流用していない。

ソース変更については`origin`（https://github.com/KASAHARA-Kyohei/PyYaki.git）の`codex/windows-native-cli`への通常pushが明示承認された。以前の自動レビュー拒否を迂回せず、この追加承認に基づいて実行する。mainの変更、force push、バイナリ・原ログ送信、GitHub Release/PyPI公開は含まれない。
