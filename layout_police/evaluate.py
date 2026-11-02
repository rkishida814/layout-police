"""本人の判定（ラベル）と照らして、規則を変えたあとの検出を採点する。

使い方: python -m layout_police.evaluate tests/AAPposter_labels.json ポスター.pdf
同じ規則で、関係する要素の枠が1つ以上一致する検出を「同じ指摘」とみなす。
"""
import json
import sys
from collections import Counter
from pathlib import Path

import pymupdf

from . import rules, structure
from .extract import extract


def _same(a, b, tol=1.5):
    return all(abs(x - y) <= tol for x, y in zip(a, b))


def match_label(f, page, labels, used=None):
    """検出 f と同じ指摘の判定を labels から探す（同じ規則で、要素の枠が1つ以上一致）。"""
    for l in labels:
        if l["page"] == page and l["rule"] == f.rule and (used is None or id(l) not in used) \
                and any(_same(e.box(), b) for e in f.els for b in l["boxes"]):
            return l
    return None


def main(argv=None):
    sys.stdout.reconfigure(encoding="utf-8")
    argv = argv or sys.argv[1:]
    labels = json.loads(Path(argv[0]).read_text(encoding="utf-8"))["labels"]
    doc = pymupdf.open(argv[1])
    found = {}
    for pno in sorted({l["page"] for l in labels}):
        pg, els = extract(doc, pno - 1)
        structure.build(pg, els)
        found[pno] = rules.check(pg, els)

    used, score = set(), Counter()
    for l in labels:
        hit = next((f for f in found[l["page"]] if f.rule == l["rule"] and id(f) not in used
                    and any(_same(e.box(), b) for e in f.els for b in l["boxes"])), None)
        if hit:
            used.add(id(hit))
        score[(l["verdict"], "残った" if hit else "消えた")] += 1
        if l["verdict"] == "hmm" and hit is None:
            print(f"  - 「どちらとも」が消えた: [{l['rule']}] {l['title'][:60]}")
        elif (l["verdict"] == "yes") != bool(hit) and l["verdict"] != "hmm":
            print(f"  {'× 正解を見失った' if hit is None else '× 不要な指摘が残った'}: [{l['rule']}] {l['title'][:60]}")
    new = [f for fs in found.values() for f in fs if id(f) not in used]
    print()
    print(f"直すべきずれ（yes）: {score[('yes', '残った')]}/{score[('yes', '残った')] + score[('yes', '消えた')]} を検出")
    print(f"ずれではない（no）: {score[('no', '消えた')]}/{score[('no', '残った')] + score[('no', '消えた')]} を除外")
    print(f"どちらとも（hmm）: 残った {score[('hmm', '残った')]}・消えた {score[('hmm', '消えた')]}")
    print(f"ラベルの無い新しい指摘: {len(new)} 件")
    for f in new:
        print(f"  + [{f.rule}] {f.title[:70]}")


if __name__ == "__main__":
    main()
