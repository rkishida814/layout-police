"""学生用の Web 画面：資料をアップロードすると、ずれの指摘を画像付きで並べる。

起動: layout-police-web（手元で開く。既定 http://127.0.0.1:8767）
      layout-police-web --host 0.0.0.0（研究室の PC で動かし、学内から使う）
アップロードした資料と結果は、このパソコンの一時フォルダにだけ置き、KEEP_HOURS で消す。外部には送らない。
"""
import argparse
import json
import secrets
import shutil
import tempfile
import threading
import time
import webbrowser
from importlib.resources import files
from pathlib import Path

from starlette.applications import Starlette
from starlette.concurrency import run_in_threadpool
from starlette.requests import Request
from starlette.responses import FileResponse, HTMLResponse, JSONResponse, Response
from starlette.routing import Route

from .__main__ import run

JOBS = Path(tempfile.gettempdir()) / "layout_police_web"
EXTS = (".pdf", ".pptx", ".docx")
MAX_BYTES = 300 * 1024 * 1024
KEEP_HOURS = 24
# PowerPoint・Word での書き出しは同時に1つしかできないので、測るのは1件ずつ
_lock = threading.Lock()


def _cleanup():
    if not JOBS.exists():
        return
    limit = time.time() - KEEP_HOURS * 3600
    for d in JOBS.iterdir():
        if d.is_dir() and d.stat().st_mtime < limit:
            shutil.rmtree(d, ignore_errors=True)


def _measure(src: Path, out: Path) -> dict:
    with _lock:
        _, results, each = run(src, out, judge=False)
    pages = []
    for pno, fs, img, _ in results:
        pages.append({
            "page": pno + 1,
            "image": img.name,
            "findings": [{"title": f.title, "size_mm": f.to_dict()["delta_mm"],
                          "image": each[(pno, n)].relative_to(out).as_posix()}
                         for n, f in enumerate(fs, 1)],
        })
    return {"name": src.name, "total": sum(len(p["findings"]) for p in pages), "pages": pages}


async def index(request: Request):
    html = files("layout_police").joinpath("web_template.html").read_text(encoding="utf-8")
    return HTMLResponse(html.replace("__MAX_MB__", str(MAX_BYTES // 1024 // 1024)))


async def check(request: Request):
    _cleanup()
    form = await request.form(max_files=1, max_part_size=MAX_BYTES)
    up = form.get("file")
    if up is None or not getattr(up, "filename", ""):
        return JSONResponse({"error": "ファイルが選ばれていません"}, status_code=400)
    name = Path(up.filename).name
    if Path(name).suffix.lower() not in EXTS:
        return JSONResponse({"error": "測れるのは PDF・pptx・docx です"}, status_code=400)
    job = secrets.token_hex(8)
    out = JOBS / job
    out.mkdir(parents=True)
    src = out / ("input" + Path(name).suffix.lower())
    with src.open("wb") as f:
        shutil.copyfileobj(up.file, f)
    await up.close()
    try:
        data = await run_in_threadpool(_measure, src, out)
    except SystemExit as e:  # PDF に変換できないときなど
        shutil.rmtree(out, ignore_errors=True)
        return JSONResponse({"error": str(e)}, status_code=422)
    except Exception as e:
        shutil.rmtree(out, ignore_errors=True)
        return JSONResponse({"error": f"測れませんでした（{type(e).__name__}: {e}）"}, status_code=500)
    data["name"] = name
    data["job"] = job
    (out / "result.json").write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
    return JSONResponse(data)


async def job_file(request: Request):
    job, rel = request.path_params["job"], request.path_params["path"]
    if not job.isalnum():
        return Response(status_code=404)
    base = (JOBS / job).resolve()
    p = (base / rel).resolve()
    if not p.is_relative_to(base) or p.suffix.lower() != ".png" or not p.is_file():
        return Response(status_code=404)
    return FileResponse(p)


app = Starlette(routes=[
    Route("/", index),
    Route("/check", check, methods=["POST"]),
    Route("/jobs/{job}/{path:path}", job_file),
])


def main(argv=None):
    import uvicorn
    ap = argparse.ArgumentParser(prog="layout-police-web", description="レイアウト警察の Web 画面")
    ap.add_argument("--host", default="127.0.0.1", help="学内から使うときは 0.0.0.0")
    ap.add_argument("--port", type=int, default=8767)
    ap.add_argument("--no-browser", action="store_true", help="起動時にブラウザを開かない")
    a = ap.parse_args(argv)
    url = f"http://{'127.0.0.1' if a.host == '0.0.0.0' else a.host}:{a.port}"
    print(f"レイアウト警察: {url} （止めるときは Ctrl+C）")
    if a.host == "0.0.0.0":
        print("学内の他の PC からは、このパソコンの IP アドレスの :{} で開けます".format(a.port))
    if not a.no_browser:
        threading.Timer(1.0, webbrowser.open, [url]).start()
    uvicorn.run(app, host=a.host, port=a.port, log_level="warning")


if __name__ == "__main__":
    main()
