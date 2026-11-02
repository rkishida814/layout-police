"""要素から、区画（帯で区切られた範囲）・段・見出しの印を推定する。意味は読まず、位置だけで決める。"""
from statistics import median


def _cluster(values, tol):
    """1次元の値を、隣との差が tol 以下ならつなげてまとめる。"""
    groups = []
    for v in sorted(values):
        if groups and v - groups[-1][-1] <= tol:
            groups[-1].append(v)
        else:
            groups.append([v])
    return groups


def body_size(els):
    sizes = [l["size"] for e in els if e.kind == "text" for l in e.lines for _ in range(len(l["text"]))]
    return median(sizes) if sizes else 0


def find_markers(els, u):
    """見出しの左に置かれた小さな記号（■など）と見出しの組。"""
    bs = body_size(els)
    pairs = []
    for m in els:
        if m.kind != "image" or not (5 * u <= m.w <= 25 * u and 5 * u <= m.h <= 25 * u and 0.6 < m.w / m.h < 1.6):
            continue
        best = None
        for t in els:
            if t.kind != "text" or t.size < bs:
                continue
            l = t.lines[0]
            gap = l["x0"] - m.x1
            if l["y0"] <= m.cy <= l["y1"] and -1 <= gap <= 20 * u and (best is None or gap < best[1]):
                best = (t, gap)
        if best:
            m.role = "marker"
            best[0].role = "heading"
            pairs.append((m, best[0]))
    return pairs


def build(pg, els):
    u, W = pg.u, pg.W
    bars = sorted([e for e in els if e.kind == "bar"], key=lambda e: e.y0)
    bands = []
    for b in bars:
        if bands and b.y0 <= bands[-1][1] + 2 * u:
            bands[-1][1] = max(bands[-1][1], b.y1)
        else:
            bands.append([b.y0, b.y1])
    edges = [0] + [y for band in bands for y in band] + [pg.H]
    sections = [(edges[i], edges[i + 1]) for i in range(0, len(edges), 2)]

    pairs = find_markers(els, u)
    for e in els:
        e.band_title = e.kind == "text" and any(b0 <= e.cy <= b1 for b0, b1 in bands)
        e.sec = next((i for i, (a, b) in enumerate(sections) if a <= e.cy <= b), None)
    pg.bands, pg.sections, pg.marker_pairs = bands, sections, pairs

    # 段：区画ごとに、見出しの印の左端を段の始まりとする。印が無ければ、縦の空白で分ける
    pg.columns = {}
    for si, (a, b) in enumerate(sections):
        mine = [e for e in els if e.sec == si and e.kind != "bar" and not e.band_title]
        if not mine:
            continue
        anchors = [g[0] for g in _cluster([m.x0 for m, _ in pairs if m.sec == si], 40 * u)]
        if len(anchors) < 2:
            anchors = _gutters(mine, u, W)
        cols = []
        for i, x in enumerate(anchors):
            start = 0 if i == 0 else x - 15 * u
            end = W if i == len(anchors) - 1 else anchors[i + 1] - 15 * u
            cols.append((start, end, x))
        pg.columns[si] = cols
        for e in mine:
            e.col = next((ci for ci, (s, t, _) in enumerate(cols) if s <= e.cx < t), len(cols) - 1)
    for e in els:
        if not hasattr(e, "col"):
            e.col = None


def _gutters(mine, u, W):
    """要素の横の重なりを数え、どの要素もない縦の帯（幅 8u 以上）で段を分ける。"""
    span = [e for e in mine if e.w < 0.6 * W]
    if not span:
        return [min(e.x0 for e in mine)]
    lo, hi = min(e.x0 for e in span), max(e.x1 for e in span)
    step = u
    n = int((hi - lo) / step) + 1
    cov = [0] * n
    for e in span:
        for k in range(int((e.x0 - lo) / step), min(n, int((e.x1 - lo) / step) + 1)):
            cov[k] += 1
    anchors, k = [lo], 0
    while k < n:
        if cov[k] == 0:
            j = k
            while j < n and cov[j] == 0:
                j += 1
            if (j - k) * step >= 8 * u and j < n:
                anchors.append(lo + j * step)
            k = j
        else:
            k += 1
    return anchors
