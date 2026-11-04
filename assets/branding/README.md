# PyYaki アイコン

作成・更新: 2026-11-04。鯛焼きをモチーフに、Codex組み込みimage_genで3案を個別生成した。利用者の訂正により、選択は**C（赤茶とクリーム色のシンボル）**。

## 選択したC案

[pyyaki-icon.png](pyyaki-icon.png)は、[C案の元画像](concepts/c-symbol.png)を変更せずに保存したもの。赤茶の背景とクリーム色の鯛焼き、少ない線の形を使う。1,254×1,254pxのPNG。

[プレビュー画像](preview.png)と[HTMLプレビュー](preview.html)で、C案の256/64/32/16px表示を確認できる。生成に使った[プロンプト全文](concepts/prompts.md)の`c-symbol`を参照。選択の訂正に伴う画像の再生成・編集は行っていない。

原本を変更せず、[変換スクリプト](export_icons.py)で次の組み込み用資産を出力する。

| 資産 | 用途 |
| --- | --- |
| [pyyaki.icns](../../src/pyyaki/static/pyyaki.icns) | Mac .app用。32〜1,024pxの表現を含み、wheelにも同梱 |
| [pyyaki-dock.png](../../native/icons/pyyaki-dock.png) | Mac開発用ホストのDock用。512px |
| [pyyaki.ico](../../native/icons/pyyaki.ico) | Windowsホストのexe・ウィンドウ・タスクバー用。16/24/32/48/64/128/256pxのRGBA PNGを収録 |

```console
.venv/bin/python -m pip install 'Pillow>=12,<13'
.venv/bin/python assets/branding/export_icons.py
```

2026-11-04はPillow 12.3.0で変換した。色・形の再生成、角の切り抜き、背景の透過化は行わず、不透明な原本をRGBAへ変換して縮小する。Pillowは開発用extraの依存であり、生成済みの資産を使うRustビルド・Mac .app生成・配布後実行では不要。再変換後はRustホスト・wheel・.appを再生成する。独自アイコンを指定する公開設定やWebのfaviconは未実装。実装範囲と検証の限界は[ネイティブ手順](../../native/README.md#アイコン)を参照する。

Bの検討資料は[仕上げ版](concepts/b-refined.png)・[比較画像](concepts/b-refined-preview.png)・[HTML](concepts/b-refined-preview.html)・[編集プロンプト](concepts/b-refinement-prompt.md)へ保存している。

## 初期候補

| 案 | 方向 | 元画像 |
| --- | --- | --- |
| A | 濃い青緑と焼き色、輪郭の強いフラット | [a-flat.png](concepts/a-flat.png) |
| B | アイボリー背景、焼き菓子らしい柔らかな立体感 | [b-soft.png](concepts/b-soft.png) |
| C | 赤茶とクリーム色、少ない線のシンボル | [c-symbol.png](concepts/c-symbol.png) |

元画像はいずれも1,254×1,254pxのPNG。生成時には1,024pxの正方形を指定したが、実際の出力寸法は上記のとおり。原本を保持している。

[比較画像](concepts/comparison.png)と[HTMLプレビュー](concepts/preview.html)で、256pxと64/32/16px、明背景・暗背景の表示を確認できる。角丸はCSSによる比較表示。生成したPNG自体の角を切り抜いたものではない。プレビューは画像をCSSで縮小し、HTMLをChromiumで描画して保存した。16pxでは細部が減るため、採用案の調整時に小サイズも確認する。

生成に使った[プロンプト全文](concepts/prompts.md)を保存している。CLI/APIキーを使うフォールバックは使用していない。候補の生成時点ではアプリの動作を変更せず、上記の形式変換を後から追加した。
