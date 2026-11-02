"""pptx / docx を PDF にする。描画の正しさのため、作ったアプリ（PowerPoint・Word）での書き出しを優先する。"""
import platform
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

_PS = {
    ".pptx": "$a=New-Object -ComObject PowerPoint.Application; $p=$a.Presentations.Open('{src}',$true,$false,$false); "
             "$p.SaveAs('{dst}',32); $p.Close(); $a.Quit()",
    ".docx": "$a=New-Object -ComObject Word.Application; $d=$a.Documents.Open('{src}',$false,$true); "
             "$d.SaveAs([ref]'{dst}',[ref]17); $d.Close(); $a.Quit()",
}

# Mac の Office はサンドボックスで動くため、アプリのコンテナの中で開いて書き出し、結果を移す
# （外のフォルダを直接指すと、ファイルへのアクセス許可の窓が出て止まる）。
# 使用中の作業を閉じないよう、アプリは終了させない。
_MAC = {
    ".pptx": ("com.microsoft.Powerpoint",
              'tell application "Microsoft PowerPoint"\n'
              '  open POSIX file "{src}"\n'
              '  set p to active presentation\n'
              '  save p in POSIX file "{dst}" as save as PDF\n'
              '  close p saving no\n'
              'end tell'),
    ".docx": ("com.microsoft.Word",
              'tell application "Microsoft Word"\n'
              '  open POSIX file "{src}"\n'
              '  set d to active document\n'
              '  save as d file name "{dst}" file format format PDF\n'
              '  close d saving no\n'
              'end tell'),
}


def _mac_office(src: Path, dst: Path) -> bool:
    bundle, script = _MAC[src.suffix.lower()]
    container = Path.home() / "Library/Containers" / bundle / "Data"
    if not container.exists():
        return False
    with tempfile.TemporaryDirectory(dir=container) as tmp:
        s = Path(tmp) / ("src" + src.suffix.lower())
        d = Path(tmp) / "out.pdf"
        shutil.copy2(src, s)
        q = lambda p: str(p).replace("\\", "\\\\").replace('"', '\\"')
        r = subprocess.run(["osascript", "-e", script.format(src=q(s), dst=q(d))],
                           capture_output=True, text=True, timeout=300)
        if r.returncode != 0 or not d.exists():
            print(f"注意: Office での変換に失敗しました: {r.stderr.strip()}", file=sys.stderr)
            return False
        shutil.move(str(d), dst)
    return True


def to_pdf(src: Path, out_dir: Path) -> Path:
    ext = src.suffix.lower()
    if ext == ".pdf":
        return src
    if ext not in _PS:
        raise SystemExit(f"対応していない形式です: {ext}（PDF・pptx・docx）")
    dst = out_dir / (src.stem + ".pdf")
    if platform.system() == "Windows":
        cmd = _PS[ext].format(src=str(src).replace("'", "''"), dst=str(dst).replace("'", "''"))
        r = subprocess.run(["powershell", "-NoProfile", "-Command", cmd], capture_output=True, text=True)
        if r.returncode == 0 and dst.exists():
            return dst
    if platform.system() == "Darwin" and _mac_office(src, dst):
        return dst
    soffice = shutil.which("soffice") or shutil.which("libreoffice")
    if soffice:
        print("注意: LibreOffice で変換します。フォントが置き換わり、位置が実物と変わることがあります。"
              "正確に測るには、PowerPoint・Word で PDF に書き出したものを渡してください。", file=sys.stderr)
        subprocess.run([soffice, "--headless", "--convert-to", "pdf", "--outdir", str(out_dir), str(src)], check=True,
                       stdout=subprocess.DEVNULL)
        if dst.exists():
            return dst
    raise SystemExit("PDF に変換できませんでした。PowerPoint・Word で PDF に書き出してから渡してください。")
