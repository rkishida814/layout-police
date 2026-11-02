"""使い方: python -m layout_police 入力.pdf|.pptx|.docx [-o 出力フォルダ] [--pages 1,3]"""
import argparse
import json
import sys
from pathlib import Path

import pymupdf

from . import report, rules, structure
from .convert import to_pdf
from .extract import extract


def run(src: Path, out: Path, pages=None, labels=None, judge=True):
    """検出して、画像・findings.json・report.md（judge=True なら judge.html も）を out に書く。

    戻り値: (PDF のパス, [(ページ番号, findings, 全体画像, 区画画像)], {(ページ番号, 何件目): 1件の画像})
    """
    out.mkdir(parents=True, exist_ok=True)
    pdf = to_pdf(src, out)
    doc = pymupdf.open(pdf)
    pages = pages if pages is not None else range(len(doc))
    results, each = [], {}
    for pno in pages:
        pg, els = extract(doc, pno)
        structure.build(pg, els)
        fs = rules.check(pg, els)
        img = out / f"page{pno + 1}.png"
        crops = report.draw(pg, fs, img, out, f"page{pno + 1}")
        for n, p in enumerate(report.draw_each(pg, fs, out / "each", f"page{pno + 1}"), 1):
            each[(pno, n)] = p
        results.append((pno, fs, img, crops))
    report.write(results, out, src.name)
    if judge:
        report.write_judge(results, out, src.name, labels)
    return pdf, results, each


def main(argv=None):
    ap = argparse.ArgumentParser(prog="layout_police", description="スライド・ポスター・文書のレイアウトのずれを検出する")
    ap.add_argument("input")
    ap.add_argument("-o", "--out", help="出力フォルダ（既定: 入力名_layout_police）")
    ap.add_argument("--pages", help="対象ページ（1始まり、カンマ区切り）")
    ap.add_argument("--labels", help="以前の判定（tests/*_labels.json）。判定ページで同じ指摘に前回の判定を入れる")
    a = ap.parse_args(argv)

    src = Path(a.input).resolve()
    out = Path(a.out) if a.out else Path.cwd() / f"{src.stem}_layout_police"
    pages = [int(p) - 1 for p in a.pages.split(",")] if a.pages else None
    labels = json.loads(Path(a.labels).read_text(encoding="utf-8"))["labels"] if a.labels else None
    _, results, _ = run(src, out, pages, labels)
    for pno, fs, _, _ in results:
        print(f"p{pno + 1}: {len(fs)} 件")
        for n, f in enumerate(fs, 1):
            print(f"  {n:2d}. [{f.rule}] {f.title}")
    print(f"出力: {out}")


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")
    main()
