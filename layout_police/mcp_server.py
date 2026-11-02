"""MCP サーバー：Claude Code・ai-agents-council からレイアウト警察をツールとして呼ぶ。

起動: python -m layout_police.mcp_server（stdio）
標準出力は MCP の通信に使うので、検出中の表示はすべて標準エラーへ回す。
"""
import contextlib
import sys
import tempfile
from pathlib import Path

try:
    from mcp.server.mcpserver import MCPServer  # mcp 2.x
except ImportError:
    from mcp.server.fastmcp import FastMCP as MCPServer  # mcp 1.x

from .__main__ import run

server = MCPServer(
    "layout-police",
    instructions="研究発表の資料（ポスター・スライド・文書）の、ほぼ揃っているが揃っていない見た目のずれを、"
                 "位置の数値だけで検出する。直しはしない。結果の各指摘には、その箇所を描いた画像の場所が付く。",
)


@server.tool()
def check_layout(path: str, pages: str = "", out_dir: str = "") -> dict:
    """スライド・ポスター・文書のレイアウトのずれを検出する。

    path: 入力ファイル（.pdf / .pptx / .docx）の絶対パス。pptx・docx は PowerPoint・Word で PDF に書き出してから測る。
    pages: 対象ページ（1始まり、カンマ区切り。例 "1,3"）。空なら全ページ。
    out_dir: 画像と findings.json を書くフォルダ。空なら一時フォルダの下に作る。

    返り値: ページごとの指摘。各指摘の title は平易な日本語の説明、size_mm はずれの大きさ、
    image はその指摘だけを描いた画像（赤＝ずれている端、青＝基準にした端、橙＝比べた要素、下に拡大）。
    page_image は全ページに番号付きで描いた画像。説明だけで判断せず、image を見て確かめること。
    """
    src = Path(path).expanduser().resolve()
    if not src.exists():
        return {"error": f"ファイルがありません: {src}"}
    out = Path(out_dir).expanduser().resolve() if out_dir else Path(tempfile.gettempdir()) / "layout_police" / src.stem
    sel = [int(p) - 1 for p in pages.split(",") if p.strip()] if pages else None
    try:
        with contextlib.redirect_stdout(sys.stderr):
            pdf, results, each = run(src, out, sel, judge=False)
    except SystemExit as e:  # 変換できないときなど
        return {"error": str(e)}
    return {
        "source": str(src),
        "pdf": str(pdf),
        "out_dir": str(out),
        "total": sum(len(fs) for _, fs, _, _ in results),
        "pages": [
            {
                "page": pno + 1,
                "page_image": str(img),
                "findings": [
                    {"no": n, "rule": f.rule, "title": f.title, "size_mm": f.to_dict()["delta_mm"],
                     "image": str(each[(pno, n)]),
                     "elements": [{"kind": e.kind, "text": e.label} for e in f.els]}
                    for n, f in enumerate(fs, 1)
                ],
            }
            for pno, fs, img, _ in results
        ],
    }


def main():
    server.run("stdio")


if __name__ == "__main__":
    main()
