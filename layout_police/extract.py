"""PDF の1ページから要素を取り出し、見た目の端（インクの端）で位置を測る。

座標はすべて PDF の pt（左上原点）。u はページ幅の 1/1000 で、しきい値の単位に使う。
"""
import math
from collections import Counter

import numpy as np
import pymupdf

RENDER_WIDTH_PX = 4000  # 計測用にページを描画するときの横幅


class El:
    """ページ上の1要素。x0,y0,x1,y1 は見た目の端。"""

    def __init__(self, kind, box, label="", **kw):
        self.kind = kind
        self.x0, self.y0, self.x1, self.y1 = [float(v) for v in box]
        self.label = label
        self.__dict__.update(kw)

    w = property(lambda s: s.x1 - s.x0)
    h = property(lambda s: s.y1 - s.y0)
    cx = property(lambda s: (s.x0 + s.x1) / 2)
    cy = property(lambda s: (s.y0 + s.y1) / 2)

    def box(self):
        return (self.x0, self.y0, self.x1, self.y1)

    def __repr__(self):
        return f"{self.kind}({self.x0:.1f},{self.y0:.1f},{self.x1:.1f},{self.y1:.1f} {self.label!r})"


class Page:
    def __init__(self, page):
        self.page = page
        self.W, self.H = page.rect.width, page.rect.height
        self.u = self.W / 1000
        self.z = RENDER_WIDTH_PX / self.W
        pix = page.get_pixmap(matrix=pymupdf.Matrix(self.z, self.z), alpha=False)
        self.img = np.frombuffer(pix.samples, np.uint8).reshape(pix.h, pix.w, 3)

    def _px(self, r):
        H, W, _ = self.img.shape
        z = self.z
        return (max(0, int(np.floor(r[0] * z))), max(0, int(np.floor(r[1] * z))),
                min(W, int(np.ceil(r[2] * z))), min(H, int(np.ceil(r[3] * z))))

    def trim_white(self, r, thr=235):
        """r の範囲から、白い余白を除いた見た目の外枠を返す。"""
        x0, y0, x1, y1 = self._px(r)
        sub = self.img[y0:y1, x0:x1]
        if sub.size == 0:
            return None
        ext = _ink_extent(sub.min(axis=2) < thr)
        if ext is None:
            return None
        z = self.z
        return ((x0 + ext[0]) / z, (y0 + ext[1]) / z, (x0 + ext[2]) / z, (y0 + ext[3]) / z)

    def fill_ratio(self, r, thr=235):
        """r の範囲のうち、白でない画素の割合。写真は 1 に近く、斜めの棒などの絵は小さい。"""
        x0, y0, x1, y1 = self._px(r)
        sub = self.img[y0:y1, x0:x1]
        return float((sub.min(axis=2) < thr).mean()) if sub.size else 0.0

    def ink_x(self, box):
        """文字の行の左右のインクの端。行の上下の端は少し削り、隣の行の混入を避ける。"""
        x0, y0, x1, y1 = box
        h = y1 - y0
        px0, py0, px1, py1 = self._px((x0 - 1, y0 + 0.12 * h, x1 + 1, y0 + 0.88 * h))
        sub = self.img[py0:py1, px0:px1].astype(int)
        if sub.size == 0:
            return x0, x1
        q = sub >> 4
        key = q[..., 0] * 256 + q[..., 1] * 16 + q[..., 2]
        vals, cnt = np.unique(key, return_counts=True)
        k = vals[cnt.argmax()]
        bg = np.array([(k >> 8) & 15, (k >> 4) & 15, k & 15]) * 16 + 8
        cols = np.nonzero((np.abs(sub - bg).sum(axis=2) > 90).any(axis=0))[0]
        if len(cols) == 0:
            return x0, x1
        return (px0 + cols.min()) / self.z, (px0 + cols.max() + 1) / self.z


def _raw_lines(pg, boxes):
    """文字の行を取り出す。行末の空白は除き、最初と最後の文字の枠で範囲を決める。"""
    lines = []
    for b in pg.page.get_text("rawdict")["blocks"]:
        if b["type"] != 0:
            continue
        for l in b["lines"]:
            if abs(l["dir"][0] - 1) > 1e-3:  # 縦書き・回転した文字（グラフの軸名など）は対象外
                continue
            chars = [(c, s) for s in l["spans"] for c in s["chars"]]
            vis = [(c, s) for c, s in chars if c["c"].strip()]
            if not vis:
                continue
            first = vis[0][0]
            # 行頭の記号（箇条書きの点など）の後ろ、本文の始まり
            body = next((c for c, _ in vis[1:] if c["c"] not in BULLETS), None) if first["c"] in BULLETS else None
            lines.append(dict(
                box=[first["bbox"][0], min(s["bbox"][1] for _, s in vis),
                     vis[-1][0]["bbox"][2], max(s["bbox"][3] for _, s in vis)],
                size=max(s["size"] for _, s in vis),
                text=_tidy("".join(c["c"] for c, _ in chars)),
                color=vis[0][1]["color"],
                block=b["number"],
                wide_start=_is_wide(first["c"]),
                body_x0=body["bbox"][0] if body else None))
    # 同じ行が別々のテキストボックスに分かれているもの（見出しの継ぎ足しなど）をつなぐ
    lines.sort(key=lambda l: (round(l["box"][1]), l["box"][0]))
    out = []
    for l in lines:
        for o in out:
            tol = 0.15 * o["size"]
            gap = l["box"][0] - o["box"][2]
            if (abs(l["box"][1] - o["box"][1]) < tol and abs(l["box"][3] - o["box"][3]) < tol
                    and -1 <= gap < 0.6 * o["size"]):
                o["box"][2] = max(o["box"][2], l["box"][2])
                o["text"] += " " + l["text"]
                break
        else:
            out.append(l)
    for l in out:
        l["x0"], l["x1"] = l["box"][0], l["box"][2]
        l["y0"], l["y1"] = l["box"][1], l["box"][3]
        l["cont"] = _container(l, boxes)
        b = l["box"]
        if l["cont"] is not None:  # 枠線の画素を文字のインクと取り違えないよう、枠の内側だけを見る
            inner = boxes[l["cont"]].inner
            b = [max(b[0], inner[0] + 0.5), b[1], min(b[2], inner[2] - 0.5), b[3]]
        ink0, l["x1"] = pg.ink_x(b)
        # 和文（全角）で始まる行は、字形ごとにインクの位置が違う（「 と 研 など）ので、文字の枠の左端で揃いを見る
        l["x0"] = l["box"][0] if l["wide_start"] else ink0
    return out


def _tidy(s):
    """和文の文字の間に入る余計な空白（Mac の PDF に多い）を除く。表示用。"""
    import re
    return re.sub(r"(?<=[^\x00-\x7f]) +(?=[^\x00-\x7f]|[,.])", "", s).strip()


BULLETS = set("•●○◦・▪■□◆◇▶►‣-–—※☑✓✔*→⇒")


def _is_wide(ch):
    import unicodedata
    return unicodedata.east_asian_width(ch) in ("W", "F")


def _container(e, boxes):
    """行を含む最小の枠・塗りの番号（boxes の添字）。"""
    inside = [b for b in boxes if b.x0 <= e["x0"] + 1 and e["x1"] - 1 <= b.x1 and b.y0 <= (e["y0"] + e["y1"]) / 2 <= b.y1]
    return boxes.index(min(inside, key=lambda b: b.w * b.h)) if inside else None


def _paragraphs(lines, boxes):
    """行を段落にまとめる。同じ大きさ・色・入れ物で、上下に詰まって並ぶ行を1つにする。"""
    paras = []
    for l in sorted(lines, key=lambda l: l["y0"]):
        best = None
        for p in paras:
            q = p[-1]
            gap = l["y0"] - q["y1"]
            # 同じテキストボックス（PDF の文字ブロック）の行は、行間を詰めて重なっていても続きとみなす
            lo = -0.6 if l["block"] == q["block"] else -0.35
            if (abs(l["size"] - q["size"]) < 0.6 and l["color"] == q["color"] and l["cont"] == q["cont"]
                    and lo * q["size"] <= gap <= 0.3 * q["size"]
                    and min(l["x1"], q["x1"]) - max(l["x0"], q["x0"]) > 0):
                best = p
        if best is None:
            paras.append([l])
        else:
            best.append(l)
    return [El("text", (min(l["x0"] for l in ls), min(l["y0"] for l in ls),
                        max(l["x1"] for l in ls), max(l["y1"] for l in ls)),
               ls[0]["text"][:40], lines=ls, size=max(l["size"] for l in ls)) for ls in paras]


def _content_frac(doc, xref, thr=235):
    """画像の中で、白でも透明でもない部分の範囲を、画像の縦横に対する割合で返す。
    あわせて、画像の中の透明な画素の割合を返す（切り抜きのイラスト・矢印の絵は大きい）。"""
    try:
        pm = pymupdf.Pixmap(doc, xref)
        if pm.colorspace is None or pm.colorspace.n != 3:
            pm = pymupdf.Pixmap(pymupdf.csRGB, pm)
        if pm.alpha:
            pm = pymupdf.Pixmap(pm, 0)
    except Exception:
        return (0, 0, 1, 1), 0.0, None
    a = np.frombuffer(pm.samples, np.uint8).reshape(pm.h, pm.w, 3)
    m = a.min(axis=2) < thr
    clear = 0.0
    typ, val = doc.xref_get_key(xref, "SMask")
    if typ == "xref":
        sm = pymupdf.Pixmap(doc, int(val.split()[0]))
        if (sm.w, sm.h) == (pm.w, pm.h):
            al = np.frombuffer(sm.samples, np.uint8).reshape(sm.h, sm.w, sm.n)[..., 0]
            m = m & (al > 30)
            clear = float((al <= 30).mean())
    ext = _ink_extent(m)
    if ext is None:
        return None, clear, None
    axis = _axis_row(m)
    return ((ext[0] / pm.w, ext[1] / pm.h, ext[2] / pm.w, ext[3] / pm.h), clear,
            None if axis is None else axis / pm.h)


def _axis_row(m):
    """グラフの横軸：画像の下半分で、幅の 3 割以上に続く細い横線のうち、いちばん下のものの中心の行。
    写真のように上下の行もずっと色が続くものは線ではない。"""
    h, w = m.shape
    hl = np.nonzero(m.sum(axis=1) >= 0.3 * w)[0]
    runs = np.split(hl, np.nonzero(np.diff(hl) > 1)[0] + 1) if len(hl) else []
    for r in reversed(runs):
        if r[0] < 0.5 * h:
            break
        if len(r) <= max(3, 0.02 * h):
            return (r[0] + r[-1] + 1) / 2
    return None


def _ink_extent(m):
    """白でない画素 m の範囲（画素で x0, y0, x1, y1）。
    細い罫線や点のゴミは見た目の端にしない：列（行）の 3% 以上が白でないところを中身とする。"""
    h, w = m.shape
    cols = np.nonzero(m.sum(axis=0) >= max(2, 0.03 * h))[0]
    rows = np.nonzero(m.sum(axis=1) >= max(2, 0.03 * w))[0]
    if len(cols) == 0 or len(rows) == 0:
        return None
    return cols.min(), rows.min(), cols.max() + 1, rows.max() + 1


def _images(pg, doc):
    out = []
    for info in pg.page.get_image_info(xrefs=True):
        r = pymupdf.Rect(info["bbox"]) & pg.page.rect
        if r.is_empty:
            continue
        vis = tuple(r)
        xref, t = info.get("xref", 0), info["transform"]
        clear, axis = 0.0, None
        if xref and abs(t[1]) < 1e-6 and abs(t[2]) < 1e-6:
            # 画像そのものの画素で中身の範囲を測る（重なった枠や文字を画像の端と取り違えない）
            frac, clear, ax = _content_frac(doc, xref)
            if frac is None:
                continue  # 透明または真っ白な画像
            fx, fy = np.array(frac[0::2]), np.array(frac[1::2])
            X, Y = t[4] + fx * t[0], t[5] + fy * t[3]
            vis = (max(r.x0, X.min()), max(r.y0, Y.min()), min(r.x1, X.max()), min(r.y1, Y.max()))
            if vis[0] >= vis[2] or vis[1] >= vis[3]:
                continue
            if ax is not None and r.y0 <= t[5] + ax * t[3] <= r.y1:
                axis = t[5] + ax * t[3]
        trimmed = pg.trim_white(vis)
        if trimmed is None:
            continue
        # ページの描画でさらに白い余白を削る（トリミングされて見えない部分を除くため）
        vis = trimmed
        # 斜めに回した画像は図の部品（模式図の棒など）で、配置を揃える対象ではない
        ang = math.degrees(math.atan2(t[1], t[0])) % 90
        kind = "image" if min(ang, 90 - ang) < 2 else "part"
        # 背景を透明にした切り抜きのイラスト・矢印の絵は、写真や図のように端を揃える対象ではない
        out.append(El(kind, vis, f"画像@({vis[0]:.0f},{vis[1]:.0f})", fill=pg.fill_ratio(vis), cutout=clear >= 0.4,
                      placed=tuple(r), axis=axis))
    return out


def _drawings(pg):
    out, u = [], pg.u
    drawings = pg.page.get_drawings()
    # 同じ形・大きさの小さな図形（10u 未満）がたくさんあるもの（粉末の粒などの模様）は、矢印ではない
    shape = lambda dr: (tuple(it[0] for it in dr["items"]), round(dr["rect"].width), round(dr["rect"].height))
    count = Counter(shape(dr) for dr in drawings)
    for dr in drawings:
        r = dr["rect"]
        kinds = [it[0] for it in dr["items"]]
        typ = dr.get("type") or ""
        if r.width > 0.95 * pg.W and r.height > 0.95 * pg.H:
            continue
        if r.width > 0.85 * pg.W and r.height < 0.06 * pg.H:
            out.append(El("bar", tuple(r)))
        elif kinds == ["re"] and r.width > 10 * u and r.height > 10 * u:
            sw = (dr.get("width") or 0) if "s" in typ else 0
            if sw:
                out.append(El("frame", (r.x0 - sw / 2, r.y0 - sw / 2, r.x1 + sw / 2, r.y1 + sw / 2),
                              f"枠@({r.x0:.0f},{r.y0:.0f})",
                              inner=(r.x0 + sw / 2, r.y0 + sw / 2, r.x1 - sw / 2, r.y1 - sw / 2)))
            elif dr.get("fill") and min(dr["fill"]) < 0.97:
                out.append(El("panel", tuple(r), f"塗り@({r.x0:.0f},{r.y0:.0f})", inner=tuple(r)))
        elif ("f" in typ and set(kinds) <= {"l", "c"} and 3 <= len(kinds) <= 12
              and 2 * u < max(r.width, r.height) < 40 * u
              and (count[shape(dr)] < 10 or max(r.width, r.height) >= 10 * u)):
            pts = [p for it in dr["items"] for p in it[1:] if isinstance(p, pymupdf.Point)]
            my, mx = sum(p.y for p in pts) / len(pts), sum(p.x for p in pts) / len(pts)
            if r.width >= r.height:
                d = "down" if my < (r.y0 + r.y1) / 2 else "up"
            else:
                d = "right" if mx < (r.x0 + r.x1) / 2 else "left"
            # 曲線だけの図形（円・楕円）は尖った先が無く、向きを持たない
            out.append(El("arrow", tuple(r), f"矢印@({r.x0:.0f},{r.y0:.0f})", dir=d, round="l" not in kinds))
    return out


def _covered_by_text(e, texts):
    """画像のうち、ほぼ全体が文字に重なっているもの（文字の影・チェックボックス記号など）。"""
    area = e.w * e.h
    if area <= 0:
        return True
    cov = 0.0
    for t in texts:
        for l in t.lines:
            ix = min(e.x1, l["box"][2] + 0.3 * l["size"]) - max(e.x0, l["box"][0] - 0.3 * l["size"])
            iy = min(e.y1, l["y1"]) - max(e.y0, l["y0"])
            if ix > 0 and iy > 0:
                cov += ix * iy
    return cov >= 0.8 * area


def _rotated_text(pg):
    """縦に回した文字（グラフの縦軸名など）。左右の端の比較にだけ使う。"""
    out = []
    for b in pg.page.get_text("dict")["blocks"]:
        for l in b.get("lines", []):
            if abs(l["dir"][0]) < 1e-3 and "".join(s["text"] for s in l["spans"]).strip():
                vis = pg.trim_white(l["bbox"])
                if vis:
                    t = "".join(s["text"] for s in l["spans"]).strip()
                    out.append(El("vtext", vis, t[:40]))
    return out


def extract(doc, pno):
    pg = Page(doc[pno])
    drawings = _drawings(pg)
    boxes = [d for d in drawings if d.kind in ("frame", "panel")]
    texts = _paragraphs(_raw_lines(pg, boxes), boxes)
    els = list(texts) + _rotated_text(pg)
    for e in _images(pg, doc) + drawings:
        if e.kind == "image":
            if min(e.w, e.h) < 1.5 * pg.u or (e.w > 0.9 * pg.W and e.h > 0.9 * pg.H):
                continue
            if e.w > 0.85 * pg.W and e.h < 0.06 * pg.H:
                e.kind = "bar"
            elif min(e.w, e.h) < 4 * pg.u:
                e.kind = "line"  # 有意差の括弧などの細い線。比較の対象にしない
            elif _covered_by_text(e, texts):
                continue
        els.append(e)
    # 文字を含まない小さな塗りは、グラフの棒などなので除く
    for e in [e for e in els if e.kind == "panel"]:
        has_text = any(e.x0 <= t.cx <= e.x1 and e.y0 <= t.cy <= e.y1 for t in texts)
        if not has_text and not (e.w > 60 * pg.u and e.h > 60 * pg.u):
            els.remove(e)
    els = _merge_illustrations(els)
    for i, e in enumerate(els):
        e.id = i
    return pg, els


def _merge_illustrations(els):
    """重なり合う3つ以上の画像・図形（結晶の模式図など、部品を組み合わせた図）を、1つの図として扱う。

    大きな画像の内側にほぼ収まる画像（背景の上に置いた写真など）は、その画像の一部として除く。
    """
    def inside(a, b):
        ix = min(a.x1, b.x1) - max(a.x0, b.x0)
        iy = min(a.y1, b.y1) - max(a.y0, b.y0)
        return ix > 0 and iy > 0 and ix * iy >= 0.9 * a.w * a.h and b.w * b.h > a.w * a.h
    texts = [e for e in els if e.kind == "text"]
    # 入れ物になるもの：画像と、文字を含まない塗り（写真の背景の灰色の四角など）
    holders = [e for e in els if e.kind == "image" or (
        e.kind == "panel" and not any(e.x0 <= t.cx <= e.x1 and e.y0 <= t.cy <= e.y1 for t in texts))]
    els = [e for e in els if not (e.kind == "image" and any(inside(e, o) for o in holders if o is not e))]
    parts = [e for e in els if e.kind in ("image", "arrow")]
    parent = {id(e): e for e in parts}

    def find(e):
        while parent[id(e)] is not e:
            e = parent[id(e)]
        return e
    for i, a in enumerate(parts):
        for b in parts[i + 1:]:
            if min(a.x1, b.x1) > max(a.x0, b.x0) and min(a.y1, b.y1) > max(a.y0, b.y0):
                parent[id(find(a))] = find(b)
    groups = {}
    for e in parts:
        groups.setdefault(id(find(e)), []).append(e)
    out = [e for e in els if e.kind not in ("image", "arrow")]
    for g in groups.values():
        if len(g) >= 3:
            box = (min(e.x0 for e in g), min(e.y0 for e in g), max(e.x1 for e in g), max(e.y1 for e in g))
            out.append(El("image", box, f"図（部品 {len(g)} 個）@({box[0]:.0f},{box[1]:.0f})",
                          cutout=2 * sum(getattr(e, "cutout", False) for e in g) >= len(g)))
        else:
            out.extend(g)
    return out
