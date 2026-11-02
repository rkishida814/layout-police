# レイアウト警察

スライド・ポスター・文書の「揃えたつもりで、少しだけずれている」ところ（見出しが帯の中央から外れている、段の端がわずかに揃っていない、枠が隣の段にはみ出している など）を、位置の数値だけで見つけるツールです。直しはしません。何がどうずれているかを、画像付きで示します。

## 使い方（ブラウザ）

**https://rkishida814.github.io/layout-police/** を開き、PDF をドラッグします。

- PowerPoint・Word の資料は、「ファイル → エクスポート（名前を付けて保存）→ PDF」で PDF にしてから渡してください。
- 資料はブラウザの中だけで調べ、どこにも送りません。最初に開いたときだけ、調べるための部品（約30MB）を cdn.jsdelivr.net から読み込みます。
- 結果の画像の赤い線がずれている端、青い線が揃える基準の端、橙の枠が比べたものです。

## しくみ

PDF から文字の行・画像・枠・矢印・帯を取り出し、余白や透明部分を除いた「見た目の端」で測ります。差がごく小さければ揃っている、少しだけずれていれば「揃えようとして失敗した」、大きく離れていれば意図的に離したとみなします。しきい値はページ幅の 1/1000 を単位にしているので、スライドでもポスターでも同じ規則で動きます。判定に AI（LLM）は使いません。

| 規則 | 内容 |
|---|---|
| R1 | 帯・枠の中の文字が中央にあるか |
| R2 | 同じ段の左端・右端、横に並んだ要素の上端・下端がほぼ揃って揃っていないもの。段落の行頭の階段状のずれ |
| R3 | 写真のすぐ上下のキャプションが、写真の中央にあるか |
| R4 | 矢印が指す先の中央にあるか |
| R5 | 見出しの記号と文字の間隔、並んだ画像の大きさの一貫性 |
| R6 | 文字が枠からはみ出す・接する、要素が隣の段の始まりを越える |
| R7 | 同じ区画の段の下端 |

## 手元で使う（開発者向け）

```bash
pip install -e .
layout-police 資料.pdf -o 出力フォルダ      # 画像・findings.json・report.md を出す（pptx・docx は PowerPoint・Word で PDF にする）
layout-police-mcp                           # MCP サーバー（ツール check_layout）
layout-police-web                           # 手元のブラウザ画面（http://127.0.0.1:8767）
python build_browser.py index.html          # ブラウザ版（このページ）を作り直す
```

ブラウザ版は、`layout_police/` の extract・structure・rules・report を `layout_police/browser_template.html` に埋め込み、ブラウザの中の Python（[Pyodide](https://pyodide.org/)）で動かしています。

## ライセンス

GNU Affero General Public License v3.0（[LICENSE](LICENSE)）。PDF の読み取りに [PyMuPDF](https://github.com/pymupdf/PyMuPDF)（AGPL-3.0）を使っています。
