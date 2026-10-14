# Контактные листы: много слайдов на одной картинке, чтобы агент видел презентацию целиком,
# не тратя контекст на каждую страницу. Рендер — Chrome headless (обёртка из exporter).
import html
from pathlib import Path

from . import exporter

COLS, PER_SHEET, TILE_W, TILE_H, LABEL = 4, 16, 480, 270, 30


def make_sheets(images, out_dir, prefix="sheet", labels=None, log=print):
    """images: список путей к PNG/JPG. Возвращает пути к листам NN.png (по PER_SHEET картинок)."""
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    images = [Path(p).resolve() for p in images]
    labels = labels or [str(i) for i in range(1, len(images) + 1)]
    sheets = []
    for start in range(0, len(images), PER_SHEET):
        chunk = list(zip(images[start:start + PER_SHEET], labels[start:start + PER_SHEET]))
        rows = (len(chunk) + COLS - 1) // COLS
        w, h = COLS * (TILE_W + 10) + 10, rows * (TILE_H + LABEL + 10) + 10
        tiles = "".join(
            '<figure><img src="%s"><figcaption>%s</figcaption></figure>' % (p.as_uri(), html.escape(lbl))
            for p, lbl in chunk)
        page = out_dir / (".%s-%02d.html" % (prefix, len(sheets) + 1))
        page.write_text(
            "<!doctype html><meta charset='utf-8'><style>"
            "html,body{margin:0;background:#e5e5ea;font:600 18px -apple-system,Helvetica,sans-serif}"
            "main{display:grid;grid-template-columns:repeat(%d,%dpx);gap:10px;padding:10px}"
            "figure{margin:0;background:#fff}img{display:block;width:%dpx;height:%dpx;object-fit:contain;background:#fff}"
            "figcaption{height:%dpx;line-height:%dpx;padding:0 8px;color:#1d1d1f}"
            "</style><main>%s</main>" % (COLS, TILE_W, TILE_W, TILE_H, LABEL, LABEL, tiles), encoding="utf-8")
        png = out_dir / ("%s-%02d.png" % (prefix, len(sheets) + 1))
        if png.exists():
            png.unlink()
        exporter._run_chrome(["--window-size=%d,%d" % (w, h), "--allow-file-access-from-files",
                              "--screenshot=" + str(png), page.as_uri()], png, 60, log)
        page.unlink()
        sheets.append(png)
    return sheets
