# Качество экспорта PPTX: HTML-слайды vs рендер PowerPoint (только для разработки).
#   .venv-dev/bin/pip install pillow pymupdf
#   .venv-dev/bin/python app/tests/ui/pptx_compare.py --root <папка с Проекты/ и Гайдлайны/> --out <отчёт> Проект [Проект…]
# Критерии (план v2, п. 7): у ≥95% текстовых блоков и у 100% заголовков число строк в PowerPoint = числу строк в HTML;
# каждому текстовому блоку сканера — текстбокс в pptx. Отчёт: report.md + листы «HTML | PowerPoint» с красными рамками расхождений.
import argparse, json, os, re, shutil, subprocess, sys, time, zipfile
from pathlib import Path
from urllib.parse import quote

import pymupdf
from PIL import Image, ImageDraw

APP = Path(__file__).resolve().parents[3]
ap = argparse.ArgumentParser()
ap.add_argument("--root", required=True)
ap.add_argument("--out", required=True)
ap.add_argument("--port", default="8830")
ap.add_argument("projects", nargs="+")
args = ap.parse_args()
ROOT, OUT = Path(args.root).resolve(), Path(args.out).resolve()
OUT.mkdir(parents=True, exist_ok=True)
os.environ["PD_ROOT"] = str(ROOT)
sys.path.insert(0, str(APP / "app"))
from core import exporter, pptx_export  # noqa: E402

BASE = "http://127.0.0.1:%s" % args.port
PPT_DATA = Path.home() / "Library/Containers/com.microsoft.Powerpoint/Data/pd-render"
HEAD = re.compile(r"\b(h|title-xl|section-xl|title-sub|num)\b")
APPLESCRIPT = '''on run argv
  with timeout of 180 seconds
    tell application "Microsoft PowerPoint"
      open (POSIX file (item 1 of argv))
      delay 2
      set p to active presentation
      save p in (POSIX file (item 2 of argv)) as save as PDF
      close p saving no
    end tell
  end timeout
end run'''


def powerpoint_pngs(pptx, dst):
    """pptx → PDF через PowerPoint (файл в его sandbox-контейнере) → PNG 1920×1080."""
    PPT_DATA.mkdir(parents=True, exist_ok=True)
    src, pdf = PPT_DATA / "in.pptx", PPT_DATA / "out.pdf"
    shutil.copy(pptx, src)
    if pdf.exists():
        pdf.unlink()
    subprocess.run(["osascript", "-e", APPLESCRIPT, str(src), str(pdf)], check=True, timeout=240, capture_output=True)
    dst.mkdir(parents=True, exist_ok=True)
    out = []
    with pymupdf.open(str(pdf)) as doc:
        for i, page in enumerate(doc, 1):
            z = 1920 / page.rect.width
            pix = page.get_pixmap(matrix=pymupdf.Matrix(z, z), alpha=False)
            p = dst / ("%02d.png" % i)
            pix.save(str(p))
            out.append(p)
    return out


def html_pngs(project, dst):
    dst.mkdir(parents=True, exist_ok=True)
    out = []
    for i, s in enumerate(exporter.visible_slides(project), 1):
        p = dst / ("%02d.png" % i)
        exporter._run_chrome(["--window-size=1920,1080", "--virtual-time-budget=5000", "--screenshot=" + str(p),
                              "%s/p/%s/%s" % (BASE, quote(project), quote(s["file"]))], p, 60, print)
        out.append(p)
    return out


def lines_in(img, box, fs, lh, others=()):
    """Число строк текста в рамке: строки пикселей, заметно отличных от фона, слитые в полосы ≥ 0.35 кегля;
    слипшаяся полоса считается по высоте (≈ межстрочный на строку). Соседние тексты (others) закрашиваются фоном."""
    x0, y0, x1, y1 = [int(round(v)) for v in box]
    x0, y0, x1, y1 = max(x0, 0), max(y0, 0), min(x1, img.width), min(y1, img.height)
    if x1 - x0 < 4 or y1 - y0 < 4:
        return 0
    reg = img.crop((x0, y0, x1, y1)).convert("RGB")
    w, h = reg.size
    px = reg.load()
    counts = {}
    for yy in range(0, h, 2):
        for xx in range(0, w, 3):
            c = tuple(v // 16 for v in px[xx, yy])
            counts[c] = counts.get(c, 0) + 1
    bg = [v * 16 + 8 for v in max(counts, key=counts.get)]
    if others:
        d = ImageDraw.Draw(reg)
        for ox0, oy0, ox1, oy1 in others:
            d.rectangle((ox0 - x0 - 1, oy0 - y0 - 1, ox1 - x0 + 1, oy1 - y0 + 1), fill=tuple(bg))
        px = reg.load()
    ink = []
    for yy in range(h):
        n = 0
        for xx in range(0, w, 2):
            r, g, b = px[xx, yy]
            if abs(r - bg[0]) + abs(g - bg[1]) + abs(b - bg[2]) > 90:
                n += 1
                if n >= 2:
                    break
        ink.append(n >= 2)
    bands, start, gap = [], None, 0
    for yy, on in enumerate(ink + [False] * int(fs)):
        if on:
            start = yy if start is None else start
            gap = 0
        elif start is not None:
            gap += 1
            if gap > max(2, fs * 0.18):
                bands.append((start, yy - gap))
                start, gap = None, 0
    return sum(max(1, int(round((b - a + lh - fs) / lh))) for a, b in bands if b - a >= fs * 0.35)


def text_of(it):
    return "".join(r.get("t", "") for r in it["runs"]).strip()


def main():
    srv = subprocess.Popen([sys.executable, str(APP / "app/server.py")], env=dict(os.environ, PD_PORT=args.port),
                           stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    time.sleep(1.5)
    md, ok_all = ["# Сравнение PPTX (PowerPoint) с HTML\n"], True
    try:
        for project in args.projects:
            t0 = time.time()
            pptx = exporter.export(project, "pptx", BASE, log=lambda m: None)
            t_export = time.time() - t0
            scan = pptx_export.parse_dump(exporter.dump_dom("%s/pptx-scan/%s" % (BASE, quote(project))))
            hp = html_pngs(project, OUT / project / "html")
            pp = powerpoint_pngs(pptx, OUT / project / "powerpoint")
            total = same = 0
            head_bad, rows, missing, raster_bad = [], [], [], []
            with zipfile.ZipFile(pptx) as z:
                boxes = [z.read("ppt/slides/slide%d.xml" % i).decode().count('txBox="1"') for i in range(1, len(scan) + 1)]
            for n, (sd, a, b) in enumerate(zip(scan, hp, pp), 1):
                ia, ib = Image.open(a), Image.open(b)
                draw, bad_here = ImageDraw.Draw(ib), 0
                texts = [it for it in sd["items"] if it["k"] == "text" and text_of(it)]
                if boxes[n - 1] < len(texts):
                    missing.append("слайд %d: текстов %d, текстбоксов %d" % (n, len(texts), boxes[n - 1]))
                rect = lambda t: (t["x"], t["y"], t["x"] + t["w"], t["y"] + t["h"])
                for it in texts:
                    fs = max([r["size"] for r in it["runs"] if r.get("size")] or [it["fs"]])
                    lh = it.get("lh") or fs * 1.2
                    box = (it["x"] - 2, it["y"] - 2, it["x"] + it["w"] * 1.06 + 8, it["y"] + it["h"] + 1.1 * lh)
                    others = [rect(o) for o in texts if o is not it]
                    la, lb = lines_in(ia, box, fs, lh, others), lines_in(ib, box, fs, lh, others)
                    total += 1
                    head = bool(HEAD.search(it.get("cls") or "")) or it.get("tag") in ("h1", "h2", "h3")
                    if la == lb:
                        same += 1
                        continue
                    bad_here += 1
                    draw.rectangle(box, outline=(255, 0, 0), width=4)
                    rows.append("| %d | %s | %d | %d |%s" % (n, text_of(it)[:50].replace("|", "/"), la, lb, " **заголовок**" if head else ""))
                    if head:
                        head_bad.append(n)
                for it in sd["items"]:  # растровые вставки (графики, баннеры): пиксели должны совпасть с HTML
                    if it["k"] != "raster":
                        continue
                    box = tuple(int(v) for v in (it["x"], it["y"], it["x"] + it["w"], it["y"] + it["h"]))
                    a_, b_ = ia.convert("RGB").crop(box), Image.open(b).convert("RGB").crop(box)
                    diff = sum(abs(x - y) for pa, pb in zip(a_.getdata(), b_.getdata()) for x, y in zip(pa, pb)) / max(1, 3 * a_.width * a_.height)
                    if diff > 12:
                        raster_bad.append("слайд %d (%s): среднее отличие %.0f" % (n, it.get("why"), diff))
                        draw.rectangle(box, outline=(255, 120, 0), width=6); bad_here += 1
                if bad_here:
                    ib.save(OUT / project / "powerpoint" / ("%02d-diff.png" % n))
            pct = 100.0 * same / max(total, 1)
            ok = pct >= 95 and not head_bad and not missing and not raster_bad
            ok_all &= ok
            md += ["## %s — %s" % (project, "OK" if ok else "НЕ ПРОШЁЛ"),
                   "- Экспорт: %.0f с, слайдов %d, картинками: %s" % (t_export, len(scan), _raster(project)),
                   "- Строки совпали у %d из %d текстовых блоков (%.1f%%), порог 95%%" % (same, total, pct),
                   "- Заголовки с расхождением: %s" % (", ".join(map(str, sorted(set(head_bad)))) or "нет"),
                   "- Потерянные тексты: %s" % ("; ".join(missing) or "нет"),
                   "- Растровые вставки не совпали: %s" % ("; ".join(raster_bad) or "нет"), ""]
            if rows:
                md += ["| Слайд | Текст | строк HTML | строк PowerPoint |", "|---|---|---|---|"] + rows + [""]
            pairs = []
            for a, b in zip(hp, pp):
                d = b.with_name(b.stem + "-diff.png")
                pairs += [a, d if d.exists() else b]
            sheet(pairs, OUT / project / "листы")
    finally:
        srv.terminate(); srv.wait()
    (OUT / "report.md").write_text("\n".join(md), encoding="utf-8")
    print("\n".join(md))
    sys.exit(0 if ok_all else 1)


def _raster(project):
    from core import store
    st = store.read_json(store.project_dir(project) / "export-status.json") or {}
    return ", ".join("слайд %d" % n for n in st.get("raster") or []) or "нет"


def sheet(images, dst, per=8, tw=640):
    dst.mkdir(parents=True, exist_ok=True)
    th = tw * 9 // 16
    for k in range(0, len(images), per):
        chunk = images[k:k + per]
        rows = (len(chunk) + 1) // 2
        im = Image.new("RGB", (tw * 2 + 30, rows * (th + 40) + 10), (230, 230, 235))
        d = ImageDraw.Draw(im)
        for i, p in enumerate(chunk):
            x, y = 10 + (i % 2) * (tw + 10), 10 + (i // 2) * (th + 40)
            im.paste(Image.open(p).convert("RGB").resize((tw, th)), (x, y))
            d.text((x, y + th + 6), "%d %s" % ((k + i) // 2 + 1, "HTML" if i % 2 == 0 else "PowerPoint"), fill=(0, 0, 0))
        im.save(dst / ("лист-%02d.png" % (k // per + 1)))


if __name__ == "__main__":
    main()
