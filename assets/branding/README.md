# PyYaki アイコン

作成・更新: 2026-11-04。鯛焼きをモチーフに、Codex組み込みimage_genで3案を個別生成した。利用者の訂正により、選択は**C（赤茶とクリーム色のシンボル）**。

## 選択したC案

[pyyaki-icon.png](pyyaki-icon.png)は、[C案の元画像](concepts/c-symbol.png)を変更せずに保存したもの。赤茶の背景とクリーム色の鯛焼き、少ない線の形を使う。1,254×1,254pxのPNG。

[プレビュー画像](preview.png)と[HTMLプレビュー](preview.html)で、C案の256/64/32/16px表示を確認できる。生成に使った[プロンプト全文](concepts/prompts.md)の`c-symbol`を参照。選択の訂正に伴う画像の再生成・編集は行っていない。

配布用の`.ico`・`.icns`への変換とアプリへの組み込みは今後の作業。現段階では画像・比較プレビュー・記録だけを追加しており、アプリの動作は変更していない。

Bの検討資料は[仕上げ版](concepts/b-refined.png)・[比較画像](concepts/b-refined-preview.png)・[HTML](concepts/b-refined-preview.html)・[編集プロンプト](concepts/b-refinement-prompt.md)へ保存している。

## 初期候補

| 案 | 方向 | 元画像 |
| --- | --- | --- |
| A | 濃い青緑と焼き色、輪郭の強いフラット | [a-flat.png](concepts/a-flat.png) |
| B | アイボリー背景、焼き菓子らしい柔らかな立体感 | [b-soft.png](concepts/b-soft.png) |
| C | 赤茶とクリーム色、少ない線のシンボル | [c-symbol.png](concepts/c-symbol.png) |

元画像はいずれも1,254×1,254pxのPNG。生成時には1,024pxの正方形を指定したが、実際の出力寸法は上記のとおり。原本を保持している。

[比較画像](concepts/comparison.png)と[HTMLプレビュー](concepts/preview.html)で、256pxと64/32/16px、明背景・暗背景の表示を確認できる。角丸はCSSによる比較表示。生成したPNG自体の角を切り抜いたものではない。プレビューは画像をCSSで縮小し、HTMLをChromiumで描画して保存した。16pxでは細部が減るため、採用案の調整時に小サイズも確認する。

生成に使った[プロンプト全文](concepts/prompts.md)を保存している。CLI/APIキーを使うフォールバックは使用していない。この候補追加ではアプリの動作を変更していない。
