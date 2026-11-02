"""検出結果を、線と番号を描いた画像・JSON・Markdown にする。"""
import json
import re

import pymupdf
from PIL import Image, ImageDraw, ImageFont

OUT_WIDTH_PX = 2800
COLORS = {"ref": (0, 90, 230), "bad": (230, 0, 0)}


# Mac では日本語のヒラギノを先に試す（"Hiragino Sans GB" は中国語の字形）
_JA_FONTS = ("meiryo.ttc", "YuGothM.ttc", "msgothic.ttc",
             "/System/Library/Fonts/ヒラギノ角ゴシック W4.ttc", "Hiragino Sans GB.ttc")
_has_ja = None


def _font(size):
    for name in _JA_FONTS + ("DejaVuSans.ttf",):
        try:
            return ImageFont.truetype(name, size)
        except OSError:
            continue
    return ImageFont.load_default(size)


def _ja(text):
    """日本語のフォントが無い環境（ブラウザ版）では、画像に描く文字から日本語を除く（数値だけ残す）。"""
    global _has_ja
    if _has_ja is None:
        _has_ja = False
        for name in _JA_FONTS:
            try:
                ImageFont.truetype(name, 10)
                _has_ja = True
                break
            except OSError:
                continue
    return text if _has_ja else re.sub(r"[^\x00-\x7f]+\s*", "", text).strip()


def draw(pg, findings, path_full, crop_dir=None, stem="page"):
    z = OUT_WIDTH_PX / pg.W
    pix = pg.page.get_pixmap(matrix=pymupdf.Matrix(z, z), alpha=False)
    im = Image.frombytes("RGB", (pix.w, pix.h), pix.samples)
    # 元の絵を薄くして、線と番号を目立たせる
    im = Image.blend(im, Image.new("RGB", im.size, (255, 255, 255)), 0.35)
    d = ImageDraw.Draw(im)
    lw = max(2, int(OUT_WIDTH_PX / 900))
    f = _font(max(14, int(OUT_WIDTH_PX / 110)))
    r = max(10, int(OUT_WIDTH_PX / 150))
    for n, fd in enumerate(findings, 1):
        for axis, pos, a, b, role in fd.marks:
            c = COLORS[role]
            if axis == "v":
                d.line([pos * z, a * z, pos * z, b * z], fill=c, width=lw)
            else:
                d.line([a * z, pos * z, b * z, pos * z], fill=c, width=lw)
        bad = [m for m in fd.marks if m[4] == "bad"] or fd.marks
        axis, pos, a, b, _ = bad[0]
        x, y = (pos * z + r + 2, a * z) if axis == "v" else (a * z, pos * z - r - 2)
        d.ellipse([x - r, y - r, x + r, y + r], fill=(230, 0, 0), outline="white", width=2)
        d.text((x, y), str(n), fill="white", font=f, anchor="mm")
    im.save(path_full)
    crops = []
    if crop_dir is not None:
        for si, (y0, y1) in enumerate(pg.sections):
            if not any(getattr(fd.els[0], "sec", None) == si for fd in findings):
                continue
            box = (0, max(0, int((y0 - 10) * z)), im.width, min(im.height, int((y1 + 10) * z)))
            if box[3] - box[1] < 20:
                continue
            p = crop_dir / f"{stem}_section{si}.png"
            im.crop(box).save(p)
            crops.append(p)
    return crops


def _dashed(d, p0, p1, fill, width=2, dash=8):
    (x0, y0), (x1, y1) = p0, p1
    length = max(abs(x1 - x0), abs(y1 - y0))
    for s in range(0, int(length), dash * 2):
        t0, t1 = s / length, min(1, (s + dash) / length)
        d.line([x0 + (x1 - x0) * t0, y0 + (y1 - y0) * t0, x0 + (x1 - x0) * t1, y0 + (y1 - y0) * t1], fill=fill, width=width)


def _pairs(fd):
    """ずれている端ごとに、比べる相手の端（同じ向きでいちばん近い基準、無ければ別のずれた端）を選ぶ。"""
    out = []
    bads = [m for m in fd.marks if m[4] == "bad"]
    for b in bads[:3]:
        others = [m for m in fd.marks if m[0] == b[0] and m is not b and abs(m[1] - b[1]) > 1e-6]
        refs = [m for m in others if m[4] == "ref"] or others
        out.append((b, min(refs, key=lambda m: abs(m[1] - b[1])) if refs else None))
    return out


def _render(pg, clip, z):
    pix = pg.page.get_pixmap(matrix=pymupdf.Matrix(z, z), clip=clip, alpha=False)
    return Image.blend(Image.frombytes("RGB", (pix.w, pix.h), pix.samples),
                       Image.new("RGB", (pix.w, pix.h), (255, 255, 255)), 0.25)


def _inset(pg, bad, ref, px=300):
    """ずれた端の近くを拡大し、基準の端を延長した破線と並べて、差を mm で書く。"""
    axis, p, a, b, _ = bad
    if ref is None:  # 比べる相手の端が無いときは、その端の中ほどを拡大する
        ref = (axis, p, a, b, "bad")
    q, ra, rb = ref[1], ref[2], ref[3]
    delta = abs(p - q)
    span = max(6 * delta, 12 * pg.u)
    # 基準の要素に近いほうの端の近くを見る
    if ra >= b:
        t = b  # 基準は下にある：ずれた要素の下端を見る
    elif rb <= a:
        t = a  # 基準は上にある：上端を見る
    else:
        t = (max(a, ra) + min(b, rb)) / 2  # 並んでいる：重なる範囲の中ほどを見る
    mid = (p + q) / 2
    if axis == "v":
        clip = pymupdf.Rect(mid - span / 2, t - span * 0.4, mid + span / 2, t + span * 0.4)
    else:
        clip = pymupdf.Rect(t - span * 0.5, mid - span * 0.4, t + span * 0.5, mid + span * 0.4)
    clip = clip & pg.page.rect
    if clip.is_empty:
        return None
    z = px / max(clip.width, clip.height)
    im = _render(pg, clip, z)
    d = ImageDraw.Draw(im)
    X = lambda x: (x - clip.x0) * z
    Y = lambda y: (y - clip.y0) * z
    if axis == "v":
        if delta:
            _dashed(d, (X(q), 0), (X(q), im.height), COLORS[ref[4]], 3)
        d.line([X(p), max(0, Y(a)), X(p), min(im.height, Y(b))], fill=COLORS["bad"], width=3)
    else:
        if delta:
            _dashed(d, (0, Y(q)), (im.width, Y(q)), COLORS[ref[4]], 3)
        d.line([max(0, X(a)), Y(p), min(im.width, X(b)), Y(p)], fill=COLORS["bad"], width=3)
    lab = _ja(f"差 {delta * 25.4 / 72:.1f} mm" if delta else "拡大")
    f = _font(20)
    if lab:
        d.rectangle([4, 4, 14 + d.textlength(lab, font=f), 32], fill="white", outline=(120, 120, 120))
        d.text((9, 7), lab, fill="black", font=f)
    return im


def draw_each(pg, findings, out_dir, stem="page", max_px=900, head=True):
    """1件ずつ、その指摘だけを描いた画像を作る。

    上に指摘の文、中ほどに周辺（比べた要素を橙の枠、基準の端を延長した青の破線）、
    下にずれた端の拡大（差を mm で表示）を並べる。
    """
    out_dir.mkdir(exist_ok=True)
    u, paths = pg.u, []
    for n, fd in enumerate(findings, 1):
        xs = [v for e in fd.els for v in (e.x0, e.x1)]
        ys = [v for e in fd.els for v in (e.y0, e.y1)]
        for axis, pos, a, b, _ in fd.marks:
            xs += [pos] if axis == "v" else [a, b]
            ys += [a, b] if axis == "v" else [pos]
        if fd.context:
            xs += [fd.context[0], fd.context[2]]
            ys += [fd.context[1], fd.context[3]]
        pad = 25 * u
        clip = pymupdf.Rect(min(xs) - pad, min(ys) - pad, max(xs) + pad, max(ys) + pad) & pg.page.rect
        z = min(max_px / clip.width, max_px / clip.height, 4)
        im = _render(pg, clip, z)
        d = ImageDraw.Draw(im)
        X = lambda x: (x - clip.x0) * z
        Y = lambda y: (y - clip.y0) * z
        for e in fd.els:
            d.rectangle([X(e.x0) - 2, Y(e.y0) - 2, X(e.x1) + 2, Y(e.y1) + 2], outline=(255, 150, 0), width=2)
        pairs = _pairs(fd)
        for bad, ref in pairs:  # 基準の端を、ずれた要素のところまで破線で延ばす
            if ref is None or getattr(fd, "no_inset", False):
                continue
            lo, hi = min(bad[2], ref[2]), max(bad[3], ref[3])
            if bad[0] == "v":
                _dashed(d, (X(ref[1]), Y(lo)), (X(ref[1]), Y(hi)), COLORS[ref[4]])
            else:
                _dashed(d, (X(lo), Y(ref[1])), (X(hi), Y(ref[1])), COLORS[ref[4]])
        for axis, pos, a, b, role in fd.marks:
            if axis == "v":
                d.line([X(pos), Y(a) - 6, X(pos), Y(b) + 6], fill=COLORS[role], width=3)
            else:
                d.line([X(a) - 6, Y(pos), X(b) + 6, Y(pos)], fill=COLORS[role], width=3)
        fs = _font(18)
        for x0, x1, y, lab in fd.dims:
            d.line([X(x0), Y(y), X(x1), Y(y)], fill=(0, 140, 70), width=2)
            for x in (x0, x1):
                d.line([X(x), Y(y) - 8, X(x), Y(y) + 8], fill=(0, 140, 70), width=2)
            lab = _ja(lab)
            tw = d.textlength(lab, font=fs)
            cx = (X(x0) + X(x1)) / 2
            d.rectangle([cx - tw / 2 - 4, Y(y) - 26, cx + tw / 2 + 4, Y(y) - 4], fill="white")
            d.text((cx - tw / 2, Y(y) - 25), lab, fill=(0, 110, 50), font=fs)
        insets = [] if getattr(fd, "no_inset", False) else \
            [i for i in (_inset(pg, b, r) for b, r in pairs) if i is not None]
        out = _compose(f"#{n} {fd.title}" if head else None, im, insets)
        p = out_dir / f"{stem}_{n:03d}.png"
        out.save(p)
        paths.append(p)
    return paths


def _compose(title, main, insets, width=900):
    """指摘の文・周辺の図・拡大図を縦に並べた1枚にする。title が None なら文と凡例を描かない（画面側に出すとき）。"""
    f = _font(20)
    tmp = ImageDraw.Draw(Image.new("RGB", (1, 1)))
    lines, cur = [], ""
    for ch in title or "":  # 日本語なので1文字ずつ折り返す
        if tmp.textlength(cur + ch, font=f) > width - 24:
            lines.append(cur)
            cur = ""
        cur += ch
    lines.append(cur)
    head = 14 + 26 * len(lines) + 30 if title else 8
    row_h = max((i.height for i in insets), default=0)
    W = max(width, main.width)
    H = head + main.height + (row_h + 16 if insets else 0) + 8
    out = Image.new("RGB", (W, H), "white")
    d = ImageDraw.Draw(out)
    if title:
        for k, l in enumerate(lines):
            d.text((12, 10 + 26 * k), l, fill="black", font=f)
        leg = "赤＝ずれている端　青＝基準の端（破線は延長）　橙＝比べた要素　下＝拡大"
        d.text((12, 12 + 26 * len(lines)), leg, fill=(110, 110, 110), font=_font(15))
    out.paste(main, (0, head))
    x = 8
    for i in insets:
        out.paste(i, (x, head + main.height + 8))
        d.rectangle([x - 1, head + main.height + 7, x + i.width, head + main.height + 8 + i.height], outline=(150, 150, 150))
        x += i.width + 12
    return out


def write_judge(results, out_dir, src_name, labels=None):
    """1件ずつ「ずれ／ずれではない」を付けてもらうための、手元で開くページ。

    labels（以前の判定）があれば、同じ指摘には前回の判定を入れておき、新しい指摘だけを見せる。
    """
    import base64
    import hashlib
    import io
    from importlib.resources import files
    from .evaluate import match_label
    items = []
    for p, fs, _, _ in results:
        for n, f in enumerate(fs, 1):
            prev = match_label(f, p + 1, labels) if labels else None
            if prev and prev["verdict"] not in ("yes", "no"):
                prev = None  # 「どちらとも」は画像を直したので、もう一度見てもらう
            # 1ファイルで開けるよう、画像は JPEG にして埋め込む
            buf = io.BytesIO()
            Image.open(out_dir / "each" / f"page{p + 1}_{n:03d}.png").save(buf, "JPEG", quality=85)
            items.append(dict(id=f"p{p + 1}-{n}", page=p + 1, no=n, rule=f.rule, title=f.title,
                              delta_mm=round(f.delta * 25.4 / 72, 1),
                              boxes=[[round(v, 1) for v in e.box()] for e in f.els],
                              prev=prev["verdict"] if prev else None, prev_note=prev["note"] if prev else "",
                              img="data:image/jpeg;base64," + base64.b64encode(buf.getvalue()).decode()))
    # 検出の中身が変われば別の保存先にし、古い番号の判定を取り違えないようにする
    ver = hashlib.sha1("".join(i["title"] for i in items).encode()).hexdigest()[:10]
    tpl = files("layout_police").joinpath("judge_template.html").read_text(encoding="utf-8")
    html = (tpl.replace("__SOURCE__", json.dumps(src_name, ensure_ascii=False))
               .replace("__VERSION__", json.dumps(ver))
               .replace("__ITEMS__", json.dumps(items, ensure_ascii=False)))
    (out_dir / "judge.html").write_text(html, encoding="utf-8")


def write(results, out_dir, src_name):
    """results: [(ページ番号, findings, 画像のパス, 切り出しのパス)]"""
    data = dict(source=src_name, pages=[dict(page=p + 1, image=str(img.name), findings=[f.to_dict() for f in fs])
                                        for p, fs, img, _ in results])
    (out_dir / "findings.json").write_text(json.dumps(data, ensure_ascii=False, indent=1), encoding="utf-8")
    lines = [f"# レイアウト警察：{src_name}", ""]
    total = sum(len(fs) for _, fs, _, _ in results)
    lines.append(f"検出 {total} 件。赤い線がずれている端、青い線が基準にした端。番号は画像の番号と対応する。")
    for p, fs, img, crops in results:
        lines += ["", f"## {p + 1} ページ（{len(fs)} 件）", "", f"![]({img.name})", ""]
        for n, f in enumerate(fs, 1):
            lines.append(f"{n}. [{f.rule}] {f.title}")
        for c in crops:
            lines += ["", f"![]({c.name})"]
    (out_dir / "report.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
