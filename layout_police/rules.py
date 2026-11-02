"""ずれの検出規則。どれも「ほぼ揃っているが揃っていない」を位置の数値だけで判定する。

しきい値は u（ページ幅の 1/1000）で決めるので、スライドでもポスターでも同じ規則が使える。
  EPS  … これ以下の差は揃っているとみなす
  NEAR … これ未満の差は「揃えようとして失敗した」とみなす。これ以上は意図的に離したとみなす
"""
import re
from collections import defaultdict

from .structure import body_size

PT2MM = 25.4 / 72


class Finding:
    def __init__(self, rule, title, delta, marks, els):
        self.rule, self.title, self.delta = rule, title, delta
        self.marks = marks  # (axis 'v'|'h', 位置, 始点, 終点, 'ref'|'bad')
        self.els = els
        self.context = None  # 画像に必ず含める範囲（帯の全幅など）
        self.dims = []  # 寸法線 (x0, x1, y, 文字)：左右の余白などを示す

    def sort_key(self):
        bad = [m for m in self.marks if m[4] == "bad"] or self.marks
        m = bad[0]
        return (getattr(self.els[0], "sec", 0) or 0, m[2] if m[0] == "v" else m[1], m[1] if m[0] == "v" else m[2])

    def to_dict(self):
        return dict(rule=self.rule, title=self.title, delta_pt=round(self.delta, 1),
                    delta_mm=round(self.delta * PT2MM, 1),
                    elements=[dict(kind=e.kind, label=e.label, box=[round(v, 1) for v in e.box()]) for e in self.els],
                    marks=[dict(axis=a, pos=round(p, 1), start=round(s, 1), end=round(t, 1), role=r)
                           for a, p, s, t, r in self.marks])


def mm(pt):
    return f"{abs(pt) * PT2MM:.1f} mm"


def _name(e):
    if e.kind == "text":
        return f"「{e.label[:24]}」"
    if e.kind == "vtext":
        return f"縦の文字「{e.label[:24]}」"
    return {"image": "画像", "frame": "枠", "panel": "塗りの枠", "arrow": "矢印"}.get(e.kind, e.kind)


def classify_align(t, eps):
    ls = t.lines
    if len(ls) == 1:
        return "single"
    tol = max(eps, 0.06 * t.size)
    spread = lambda vals: max(vals) - min(vals)
    lefts = [l["x0"] for l in ls]
    centers = [(l["x0"] + l["x1"]) / 2 for l in ls]
    rights = [l["x1"] for l in ls]
    if spread(lefts) <= tol:
        return "left"
    # PowerPoint は行末の空白も含めて中央に置くので、インクの中心は半角空白の半分ほど揺れる
    if spread(centers) <= 0.25 * t.size and spread(centers) < 0.35 * spread(lefts):
        return "center"
    if spread(rights) <= tol:
        return "right"
    return "indented"


class _UF:
    def __init__(self):
        self.p = {}

    def find(self, a):
        self.p.setdefault(a, a)
        while self.p[a] != a:
            self.p[a] = self.p[self.p[a]]
            a = self.p[a]
        return a

    def union(self, a, b):
        self.p[self.find(a)] = self.find(b)


def _contains(a, b):
    return a.x0 <= b.cx <= a.x1 and a.y0 <= b.cy <= a.y1


def _vgap(a, b):
    return max(a.y0, b.y0) - min(a.y1, b.y1)


def _hgap(a, b):
    return max(a.x0, b.x0) - min(a.x1, b.x1)


def _tol(e, eps):
    """文字は字形ごとに左右の余白が違うので、大きさに応じて許容を広げる。"""
    return max(eps, 0.08 * e.size) if e.kind == "text" else eps


def _ctol(t, eps):
    """中央揃えの文字の中心の許容。行末の空白の分（半角空白の半分ほど）は揺れとみなす。"""
    return max(1.5 * eps, 0.15 * t.size)


def _split(comp, near):
    """つながった組の幅が near を超えるときは、いちばん大きな隙間で割る。"""
    comp = sorted(comp, key=lambda x: x[1])
    if comp[-1][1] - comp[0][1] < near or len(comp) < 2:
        return [comp]
    k = max(range(1, len(comp)), key=lambda i: comp[i][1] - comp[i - 1][1])
    return _split(comp[:k], near) + _split(comp[k:], near)


def _edge_groups(items, linked, near, eps):
    """items: (要素, 値)。linked(a, b) が真で値の差が near 未満の組をつなぎ、ばらつく組を返す。"""
    uf = _UF()
    for i in range(len(items)):
        uf.find(i)
        for j in range(i + 1, len(items)):
            (a, va), (b, vb) = items[i], items[j]
            if a is not b and abs(va - vb) < near and linked(a, b):
                uf.union(i, j)
    comps = defaultdict(list)
    for i in range(len(items)):
        comps[uf.find(i)].append(items[i])
    out = []
    for whole in comps.values():
        for comp in _split(whole, near):
            if len(comp) < 2:
                continue
            # 値を許容の範囲でまとめ、要素の多い位置（同数なら大きい要素の位置）を基準とする
            subs = []
            for e, v in comp:
                if subs and v - subs[-1][-1][1] <= max(_tol(e, eps), _tol(subs[-1][-1][0], eps)):
                    subs[-1].append((e, v))
                else:
                    subs.append([(e, v)])
            if len(subs) < 2:
                continue
            ref = max(subs, key=lambda s: (len(s), sum(e.w * e.h for e, _ in s)))
            out.append((comp, subs, ref))
    return out


def check(pg, els):
    u, W = pg.u, pg.W
    EPS, NEAR, VNEAR = max(1.0, 0.6 * u), 12 * u, 100 * u
    F = []
    for e in els:
        # 小さな画像（図の部品・アイコン・小さな矢印の絵）は、配置を揃える対象にしない。見出しの記号は別に扱う
        if e.kind == "image" and getattr(e, "role", "") != "marker" and max(e.w, e.h) < 0.04 * W:
            e.kind = "part"
    imgs = [e for e in els if e.kind == "image" and getattr(e, "role", "") != "marker"]
    # 端・大きさを揃える対象の画像（切り抜きのイラスト・矢印の絵は除く。キャプションの判定には使う）
    photos = [e for e in imgs if not getattr(e, "cutout", False)]
    bsize = body_size(els)
    texts = []
    for t in [e for e in els if e.kind == "text" and not e.band_title]:
        t.align = classify_align(t, EPS)
        # 図の上に重ねた文字（軸の目盛りなど）と、記号だけの短い文字は図の一部として扱う
        s = "".join(l["text"] for l in t.lines)
        t.minor = (sum(not c.isspace() for c in s) < 3 or re.fullmatch(r"[\d\s.,%±+*()\-–]+", s) is not None
                   or any(_contains(im, t) for im in imgs))
        if not t.minor:
            texts.append(t)
    boxes = [e for e in els if e.kind in ("frame", "panel")]

    # R1 区切りの帯の見出しが、帯の中央にあるか
    for t in [e for e in els if e.kind == "text" and e.band_title]:
        d = t.cx - W / 2
        if EPS < abs(d) < 80 * u:
            f = Finding("R1", f"帯の中の文字{_name(t)}が、左右の中央から{'右' if d > 0 else '左'}へ {mm(d)} ずれている"
                              f"（文字の見た目の端から、左の余白 {mm(t.x0)}・右の余白 {mm(W - t.x1)}）",
                        abs(d), [("v", W / 2, t.y0 - 4 * u, t.y1 + 4 * u, "ref"),
                                 ("v", t.cx, t.y0 - 4 * u, t.y1 + 4 * u, "bad")], [t])
            f.context = (0, t.y0 - 6 * u, W, t.y1 + 6 * u)
            f.dims = [(0, t.x0, t.cy, f"左 {mm(t.x0)}"), (t.x1, W, t.cy, f"右 {mm(W - t.x1)}")]
            F.append(f)

    # R1/R6 枠・塗りの中の文字：中央揃えの中心、左右の余白、はみ出し
    for b in boxes:
        inside = [t for t in texts if all(_contains(b, _P(l)) for l in t.lines)]
        if not inside:
            continue
        ix0, iy0, ix1, iy1 = b.inner
        for t in inside:
            if t.align in ("center", "single") and getattr(t, "role", "") != "heading":
                d = t.cx - (ix0 + ix1) / 2
                if _ctol(t, EPS) < abs(d) < NEAR and len(inside) == 1:
                    F.append(Finding("R1", f"{_name(t)}が枠の中央から {mm(d)} ずれている", abs(d),
                                     [("v", (ix0 + ix1) / 2, b.y0, b.y1, "ref"), ("v", t.cx, t.y0, t.y1, "bad")], [t, b]))
        lpad = min(t.x0 for t in inside) - ix0
        rpad = ix1 - max(t.x1 for t in inside)
        far_r = max((l for t in inside for l in t.lines), key=lambda l: l["x1"])
        far_l = min((l for t in inside for l in t.lines), key=lambda l: l["x0"])
        for side, pad, other, x in (("右", rpad, lpad, ix1), ("左", lpad, rpad, ix0)):
            l = far_r if side == "右" else far_l
            edge = _Edge(l["x1"] if side == "右" else l["x0"], l["y0"], l["y1"])
            if pad < -EPS:
                F.append(Finding("R6", f"文字が枠の{side}からはみ出している（{mm(pad)}）", -pad,
                                 [("v", x, b.y0, b.y1, "ref"), ("v", x - pad if side == "右" else x + pad, b.y0, b.y1, "bad")], [b]))
            elif pad < max(EPS, 1.5 * u) and other > 2 * max(pad, 0.5):
                F.append(Finding("R6", f"文字が枠の{side}の線にほぼ接している（余白 {mm(max(pad, 0))}、反対側は {mm(other)}）",
                                 other - pad, [("v", x, b.y0, b.y1, "ref"), ("v", edge.x, edge.y0, edge.y1, "bad")], [b]))

    # R3 キャプション：写真のすぐ上・下の短い文字は、写真（横に並んだ写真の組）の中央に置く
    for t in texts:
        if (len(t.lines) > 3 or t.align not in ("center", "single") or getattr(t, "role", "") == "heading"
                or t.size >= 1.15 * bsize):  # 本文より大きい文字は見出しで、キャプションではない
            continue
        cands = []
        for im in imgs:
            if im.sec != t.sec or not (im.x0 <= t.cx <= im.x1):
                continue
            # 画像を貼った範囲に食い込み、同じ高さに別の文字も並ぶもの（0Ag 0.01Ag …）は、グラフの目盛りで、キャプションではない
            px0, py0, px1, py1 = getattr(im, "placed", im.box())
            inside = lambda o: px0 <= o.cx <= px1 and min(o.y1, py1) - max(o.y0, py0) >= 0.3 * o.h
            if inside(t) and sum(1 for o in texts if abs(o.cy - t.cy) < 0.5 * t.size and inside(o)) >= 2:
                continue
            below, above = t.y0 - im.y1, im.y0 - t.y1
            if -0.3 * t.size <= below <= 1.2 * t.size:
                cands.append((below, im, "below"))
            elif -0.3 * t.size <= above <= 1.2 * t.size:
                cands.append((above, im, "above"))
        if not cands:
            continue
        _, im, where = min(cands, key=lambda c: c[0])
        key = (lambda e: e.y1) if where == "below" else (lambda e: e.y0)
        row = [o for o in imgs if o.sec == t.sec and abs(key(o) - key(im)) < NEAR
               and min(o.x1, t.x1) - max(o.x0, t.x0) > 0.1 * t.w]
        if im not in row:
            row.append(im)
        x0, x1 = min(o.x0 for o in row), max(o.x1 for o in row)
        t.caption = True
        d = t.cx - (x0 + x1) / 2
        if _ctol(t, EPS) < abs(d) < 25 * u:
            what = "並んだ写真の組" if len(row) > 1 else "写真"
            F.append(Finding("R3", f"キャプション{_name(t)}が{what}の中央から{'右' if d > 0 else '左'}へ {mm(d)} ずれている",
                             abs(d), [("v", (x0 + x1) / 2, min(o.y0 for o in row), max(o.y1 for o in row), "ref"),
                                      ("v", t.cx, t.y0, t.y1, "bad")], [t] + row))

    # R4 矢印：指す先の文字（または要素）の中央に置く
    for a in [e for e in els if e.kind == "arrow"]:
        # すぐ右に文字が続く小さな図形は、凡例の記号であって矢印ではない
        if any(0 <= l["x0"] - a.x1 <= 10 * u and l["y0"] <= a.cy <= l["y1"]
               for t in els if t.kind == "text" for l in t.lines):
            continue
        if a.dir in ("down", "up"):
            if getattr(a, "round", False):  # 丸は下の文字を指さない（模式図の粒など）
                continue
            if a.dir == "down":
                cs = [t for t in texts if t.y0 >= a.y0 and t.y0 - a.y1 <= 2 * t.size and t.x0 <= a.cx <= t.x1]
                tgt = min(cs, key=lambda t: t.y0, default=None)
            else:
                cs = [t for t in texts if t.y1 <= a.y1 and a.y0 - t.y1 <= 2 * t.size and t.x0 <= a.cx <= t.x1]
                tgt = max(cs, key=lambda t: t.y1, default=None)
            if tgt is None or tgt.align not in ("center", "single"):
                continue
            d = a.cx - tgt.cx
            if _ctol(tgt, EPS) < abs(d) < 40 * u:
                F.append(Finding("R4", f"矢印が指す先{_name(tgt)}の中央から{'右' if d > 0 else '左'}へ {mm(d)} ずれている",
                                 abs(d), [("v", tgt.cx, tgt.y0, tgt.y1, "ref"), ("v", a.cx, a.y0 - 2 * u, a.y1 + 2 * u, "bad")], [a, tgt]))
        else:
            for o in [e for e in els if e.kind in ("image", "text") and e.sec == a.sec]:
                gap = o.x0 - a.x1 if a.dir == "right" else a.x0 - o.x1
                if 0 <= gap <= 30 * u and o.y0 <= a.cy <= o.y1:
                    d = a.cy - o.cy
                    if 2 * EPS < abs(d) < 30 * u:
                        F.append(Finding("R4", f"矢印の高さが{_name(o)}の中央から {mm(d)} ずれている", abs(d),
                                         [("h", o.cy, o.x0, o.x1, "ref"), ("h", a.cy, a.x0 - 2 * u, a.x1 + 2 * u, "bad")], [a, o]))
                    break

    # R2 同じ段の中で、左端・右端がほぼ揃っているが揃っていない
    def colmates(a, b):
        # 見出しの記号は記号どうしでだけ比べる（本文や写真を記号から字下げするのはよくある意図的な配置）
        ra, rb = getattr(a, "role", ""), getattr(b, "role", "")
        if (ra == "marker") != (rb == "marker"):
            return False
        # 記号どうし・見出しどうしは、同じ段の中なら離れていても比べる
        far_ok = ra == rb and ra in ("marker", "heading")
        # 段の主な縦の線（3つ以上の要素が乗る線）に乗る要素とは、離れていても比べる
        far_ok = far_ok or (id(a) in on_main) != (id(b) in on_main)
        return (a.sec == b.sec and a.col == b.col and (far_ok or _vgap(a, b) <= VNEAR)
                and not _contains(a, b) and not _contains(b, a))

    def main_lines(items):
        """段ごとに、いちばん多くの要素が乗る縦の線（3つ以上）の要素を返す。"""
        on = set()
        bycol = defaultdict(list)
        for e, v in items:
            if getattr(e, "role", "") != "marker":
                bycol[(e.sec, e.col)].append((e, v))
        for its in bycol.values():
            best = []
            for e, v in its:
                grp = [x for x in its if abs(x[1] - v) <= EPS]
                if len(grp) > len(best):
                    best = grp
            if len(best) >= 3:
                on.update(id(e) for e, _ in best)
        return on

    def inset_only(subs, side):
        """枠・塗り（表のセルなど）の端と、その中に置いた文字の端が、文字の標準の余白（約 2.5 mm）だけ離れている組。
        外側の線に枠が乗り、内側の線には文字だけが揃って乗っていれば、枠の中に置いた文字として揃っている。"""
        if len(subs) != 2:
            return False
        outer, inner = subs if side == "左端" else subs[::-1]
        boxes_ = [v for e, v in outer if e.kind in ("frame", "panel")]
        if not boxes_ or not all(e.kind == "text" for e, _ in inner):
            return False
        tv = [v for _, v in inner]
        if max(tv) - min(tv) > EPS:
            return False
        d = min(abs(sum(tv) / len(tv) - v) for v in boxes_)
        return 1.5 / PT2MM <= d <= 3.6 / PT2MM

    left_items, right_items = [], []
    for e in els:
        if e.col is None or getattr(e, "band_title", False):
            continue
        if e.kind == "image" and getattr(e, "cutout", False):
            continue
        if e.kind in ("image", "frame", "panel"):
            left_items.append((e, e.x0))
            if getattr(e, "role", "") != "marker":
                right_items.append((e, e.x1))
        elif e.kind == "vtext":
            left_items.append((e, e.x0))
            right_items.append((e, e.x1))
        elif e.kind == "text" and (e.minor or (len(e.lines) == 1 and e.lines[0]["cont"] is not None)):
            continue  # 枠の中の1行の文字は枠の中で置かれるもので、揃えるのは枠のほう
        elif e.kind == "text" and not getattr(e, "caption", False) and e.align in ("left", "single", "indented"):
            left_items.append((e, e.x0))
        elif e.kind == "text" and e.align == "right":
            right_items.append((e, e.x1))
    for items, side in ((left_items, "左端"), (right_items, "右端")):
        on_main = main_lines(items)
        for comp, subs, ref in _edge_groups(items, colmates, NEAR, EPS):
            if inset_only(subs, side):
                continue
            vals = [v for _, v in comp]
            marks = [("v", v, e.y0, e.y1, "ref" if s is ref else "bad") for s in subs for e, v in s]
            F.append(Finding("R2", f"{side}がわずかにずれている（{mm(max(vals) - min(vals))}）："
                             + "、".join(_name(e) for e, _ in comp), max(vals) - min(vals), marks, [e for e, _ in comp]))

    # R2 横に並んだ要素の上端・下端
    def rowmates(a, b):
        ov = min(a.y1, b.y1) - max(a.y0, b.y0)
        if a.sec != b.sec or ov < 0.5 * min(a.h, b.h) or _hgap(a, b) > 40 * u or _contains(a, b) or _contains(b, a):
            return False
        if a.kind == "text" or b.kind == "text":
            return (a.kind == b.kind == "text" and not a.minor and not b.minor and abs(a.size - b.size) < 0.6
                    and a.align in ("single", "left") and b.align in ("single", "left"))
        # 大きさの違うもの（写真と矢印の図など）の上端・下端を揃える意味はない
        return a.kind == b.kind and min(a.h, b.h) >= 0.75 * max(a.h, b.h)
    row_items = [e for e in els if e.kind in ("image", "frame", "panel", "text") and not getattr(e, "band_title", False)
                 and getattr(e, "role", "") != "marker" and not getattr(e, "cutout", False)]
    for side, key in (("上端", lambda e: e.y0 if e.kind != "text" else e.lines[0]["y0"]), ("下端", lambda e: e.y1)):
        its = [(e, key(e)) for e in row_items if not (side == "下端" and e.kind == "text")]
        for comp, subs, ref in _edge_groups(its, rowmates, NEAR, EPS):
            vals = [v for _, v in comp]
            marks = [("h", v, e.x0, e.x1, "ref" if s is ref else "bad") for s in subs for e, v in s]
            F.append(Finding("R2", f"横に並んだ要素の{side}がわずかにずれている（{mm(max(vals) - min(vals))}）："
                             + "、".join(_name(e) for e, _ in comp), max(vals) - min(vals), marks, [e for e, _ in comp]))

    # R2 段落の中の行頭（字下げ）が階段状になっていないか
    for t in texts:
        if len(t.lines) < 2 or t.align in ("center", "right"):
            continue
        tol = _tol(t, EPS)
        # 1文字の3割以上の差は意図した字下げとみなす（全角1字下げ、箇条書きのぶら下げなど）
        near = min(NEAR, 0.3 * t.size)
        # 箇条書きの点は点どうし、本文は本文（点の後ろの文字と、折り返した行の行頭）どうしで比べる
        pts = defaultdict(list)
        for l in t.lines:
            if l.get("body_x0") is not None:
                pts["点"].append((l["x0"], l))
                pts["本文"].append((l["body_x0"], l))
            else:
                pts["本文"].append((l["x0"], l))
        for kind, its in pts.items():
            groups = []
            for x, l in sorted(its, key=lambda p: p[0]):
                if groups and x - groups[-1][-1][0] < near:
                    groups[-1].append((x, l))
                else:
                    groups.append([(x, l)])
            for g in groups:
                v = [x for x, _ in g]
                if len(g) > 1 and max(v) - min(v) > tol:
                    what = "箇条書きの点の位置" if kind == "点" else "行頭"
                    F.append(Finding("R2i", f"段落{_name(t)}の{what}がわずかにずれている（{len(g)} 行、{mm(max(v) - min(v))}）",
                                     max(v) - min(v), [("v", x, l["y0"], l["y1"], "bad") for x, l in g], [t]))

    # R5 繰り返す部品の一貫性：見出しの記号と文字の間隔、並んだ画像の大きさ
    gaps = [(m, h, h.lines[0]["x0"] - m.x1) for m, h in pg.marker_pairs]
    if len(gaps) >= 2:
        subs = []
        for m, h, g in sorted(gaps, key=lambda x: x[2]):
            if subs and g - subs[-1][-1][2] <= _tol(h, EPS):
                subs[-1].append((m, h, g))
            else:
                subs.append([(m, h, g)])
        if len(subs) > 1:
            ref = max(subs, key=len)
            vals = [g for _, _, g in gaps]
            F.append(Finding("R5", f"［参考］見出しの記号（■）と文字の間隔が揃っていない（{mm(min(vals))}〜{mm(max(vals))}）："
                             + "、".join(f"{_name(h)} {mm(g)}" for _, h, g in sorted(gaps, key=lambda x: (x[0].sec, x[0].y0))),
                             max(vals) - min(vals),
                             [("h", m.cy, m.x1, h.lines[0]["x0"], "ref" if s is ref else "bad") for s in subs for m, h, _ in s],
                             [m for m, _, _ in gaps]))
    for i, a in enumerate(photos):
        for b in photos[i + 1:]:
            if not (a.sec == b.sec and min(a.y1, b.y1) - max(a.y0, b.y0) > 0.5 * min(a.h, b.h) and _hgap(a, b) <= 40 * u):
                continue
            for dim, va, vb in (("幅", a.w, b.w), ("高さ", a.h, b.h)):
                if EPS < abs(va - vb) < 0.05 * max(va, vb):
                    f = Finding("R5", f"並んだ画像の{dim}がわずかに違う（{mm(va)} と {mm(vb)}）", abs(va - vb),
                                [("h", a.y1 + 2 * u, a.x0, a.x1, "bad"), ("h", b.y1 + 2 * u, b.x0, b.x1, "bad")]
                                if dim == "幅" else [("v", a.x1 + 2 * u, a.y0, a.y1, "bad"), ("v", b.x1 + 2 * u, b.y0, b.y1, "bad")],
                                [a, b])
                    f.no_inset = True  # 大きさの比較なので、端の拡大は意味がない（横に描いた長さの棒で見る）
                    F.append(f)

    # R6 隣の段の始まりの線を越えている要素
    for si, cols in pg.columns.items():
        for ci in range(len(cols) - 1):
            x = cols[ci + 1][2]
            nxt = [e for e in els if e.sec == si and e.col == ci + 1]
            for e in els:
                if e.sec == si and e.col == ci and e.kind != "bar" and e.x0 < x - 15 * u and x + EPS < e.x1 < x + 30 * u:
                    F.append(Finding("R6", f"{_name(e)}が隣の段の始まりの線を {mm(e.x1 - x)} 越えている", e.x1 - x,
                                     [("v", x, min(o.y0 for o in nxt + [e]), max(o.y1 for o in nxt + [e]), "ref"),
                                      ("v", e.x1, e.y0, e.y1, "bad")], [e]))

    # R7 同じ区画の段の下端
    for si, cols in pg.columns.items():
        if len(cols) < 2:
            continue
        # 段をまたぐ横長の行（下に並ぶ組織像など）がある区画では、段の下端は比べられない
        if any(e.sec == si and e.kind != "bar" and e.col is not None and e.col + 1 < len(cols)
               and e.x1 > cols[e.col + 1][2] + 30 * u for e in els):
            continue
        # 段の間の空白を斜めの線・曲線（流れ図の区切り、吹き出しの点線など）が横切る区画は、段ではなく図
        sy0, sy1 = pg.sections[si]
        if any(s0 < x - u and x + u < s1 and t0 < sy1 and t1 > sy0
               for s0, t0, s1, t1 in _strokes(pg) for _, _, x in cols[1:]):
            continue
        bottoms = []
        for ci in range(len(cols)):
            mine = [e for e in els if e.sec == si and e.col == ci and e.kind != "bar"]
            if mine:
                bottoms.append(max(mine, key=lambda e: e.y1))
        vals = [e.y1 for e in bottoms]
        if len(vals) >= 2 and EPS < max(vals) - min(vals) < 20 * u:
            ref = sorted(vals)[len(vals) // 2]
            F.append(Finding("R7", f"左右の段の下端がわずかにずれている（{mm(max(vals) - min(vals))}）", max(vals) - min(vals),
                             [("h", e.y1, e.x0, e.x1, "ref" if abs(e.y1 - ref) <= EPS else "bad") for e in bottoms], bottoms))

    F.sort(key=Finding.sort_key)
    return F


def _strokes(pg):
    """線で描いた斜めの線・曲線の範囲（高さ 20u 以上）。枠や水平・垂直の線は含めない。"""
    if not hasattr(pg, "strokes"):
        pg.strokes = []
        for dr in pg.page.get_drawings():
            r = dr["rect"]
            if "s" not in (dr.get("type") or "") or r.height < 20 * pg.u:
                continue
            slanted = any(it[0] == "l" and abs(it[1].x - it[2].x) > pg.u and abs(it[1].y - it[2].y) > pg.u
                          for it in dr["items"])
            if slanted or any(it[0] == "c" for it in dr["items"]):
                pg.strokes.append((r.x0, r.y0, r.x1, r.y1))
    return pg.strokes


class _Edge:
    """枠にいちばん近い文字の行の端。"""

    def __init__(self, x, y0, y1):
        self.x, self.y0, self.y1 = x, y0, y1


class _P:
    """行の中心を点として _contains に渡すための入れ物。"""

    def __init__(self, l):
        self.cx, self.cy = (l["x0"] + l["x1"]) / 2, (l["y0"] + l["y1"]) / 2
