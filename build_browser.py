"""ブラウザだけで動くレイアウト警察（1つの HTML）を作る。

使い方: python build_browser.py [出力先]（既定: dist/layout_police.html）
layout_police の検出部分（extract・structure・rules・report）を HTML に埋め込み、ブラウザの中の
Python（Pyodide）で動かす。資料はブラウザの外に出ない。規則を直したら作り直す。
"""
import json
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
PKG = HERE / "layout_police"
MODULES = ["__init__.py", "extract.py", "structure.py", "rules.py", "report.py"]

# ブラウザの中で動かす入口。/tmp/in.pdf を調べ、ページごとの指摘と画像のパスを JSON で返す
DRIVER = '''
import json, shutil, sys
from pathlib import Path
sys.path.insert(0, "/home/pyodide")
import pymupdf
from layout_police import extract as _ex, structure, rules, report

def measure(progress):
    out = Path("/tmp/out")
    shutil.rmtree(out, ignore_errors=True)
    out.mkdir()
    doc = pymupdf.open("/tmp/in.pdf")
    pages = []
    for pno in range(len(doc)):
        progress(pno + 1, len(doc))
        pg, els = _ex.extract(doc, pno)
        structure.build(pg, els)
        fs = rules.check(pg, els)
        if not fs:
            pages.append({"page": pno + 1, "image": None, "findings": []})
            continue
        img = out / f"page{pno + 1}.png"
        report.draw(pg, fs, img)
        each = report.draw_each(pg, fs, out / "each", f"page{pno + 1}", head=False)
        pages.append({"page": pno + 1, "image": str(img),
                      "findings": [{"title": f.title, "size_mm": f.to_dict()["delta_mm"], "image": str(p)}
                                   for f, p in zip(fs, each)]})
    return json.dumps(pages, ensure_ascii=False)
'''


def main():
    out = Path(sys.argv[1]) if len(sys.argv) > 1 else HERE / "dist" / "layout_police.html"
    sources = {m: (PKG / m).read_text(encoding="utf-8") for m in MODULES}
    sources["__driver__.py"] = DRIVER
    # </script> が JSON の中にあっても script 要素が閉じないようにする
    blob = json.dumps(sources, ensure_ascii=False).replace("</", "<\\/")
    html = (PKG / "browser_template.html").read_text(encoding="utf-8").replace("__PY_SOURCES__", blob)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(html, encoding="utf-8")
    print(f"作成: {out}（{out.stat().st_size // 1024} KB）")


if __name__ == "__main__":
    main()
