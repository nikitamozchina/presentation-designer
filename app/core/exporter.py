# Экспорт PDF/JPEG через установленный Google Chrome (headless) + sips (встроен в macOS).
# Chrome с отдельным профилем иногда не завершается после записи файла, поэтому ждём не выход
# процесса, а появления файла со стабильным размером; по таймауту убиваем всю группу процессов.
import os, re, shutil, signal, subprocess, tempfile, threading, time, uuid
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from urllib.parse import quote

from . import pptx_export, store

CHROME_CANDIDATES = [
    "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
    os.path.expanduser("~/Applications/Google Chrome.app/Contents/MacOS/Google Chrome"),
    "/Applications/Chromium.app/Contents/MacOS/Chromium",
]
PDF_TIMEOUT = 180
JPEG_TIMEOUT = 45
SCAN_TIMEOUT = 180
PARALLEL = 4
_status_lock = threading.Lock()


class ExportError(Exception):
    pass


def find_chrome():
    for c in CHROME_CANDIDATES:
        if os.access(c, os.X_OK):
            return c
    return None


def _run_chrome(args, out_file, timeout, log):
    chrome = find_chrome()
    if not chrome:
        raise ExportError("Нужен Google Chrome: google.com/chrome")
    profile = tempfile.mkdtemp(prefix="pd-chrome-")
    cmd = [chrome, "--headless=new", "--disable-gpu", "--no-first-run", "--no-default-browser-check",
           "--disable-extensions", "--hide-scrollbars", "--user-data-dir=" + profile] + args
    proc = subprocess.Popen(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE, start_new_session=True)
    deadline, last_size, stable_since = time.time() + timeout, -1, None
    try:
        while time.time() < deadline:
            exited = proc.poll() is not None
            size = out_file.stat().st_size if out_file.exists() else -1
            if size > 0 and size == last_size:
                stable_since = stable_since or time.time()
                if exited or time.time() - stable_since >= 1.0:
                    return
            else:
                stable_since = None
            last_size = size
            if exited and size <= 0:
                err = proc.stderr.read().decode("utf-8", "replace")[-2000:]
                log("chrome exit %s без файла: %s" % (proc.returncode, err))
                raise ExportError("Chrome завершился, не создав файл")
            time.sleep(0.2)
        raise ExportError("Chrome не уложился в %d с" % timeout)
    finally:
        if proc.poll() is None:
            try:
                os.killpg(proc.pid, signal.SIGKILL)
            except (ProcessLookupError, PermissionError):  # группа уже распалась или помощник Chrome недоступен
                proc.kill()
        proc.wait()
        if proc.stderr:
            proc.stderr.close()
        shutil.rmtree(profile, ignore_errors=True)


def dump_dom(url, marker='id="result">__RESULT__', timeout=120, log=print):
    """Страница со своим скриптом → её DOM после выполнения. Chrome --dump-dom не всегда завершается сам —
    ждём маркер результата и убиваем группу процессов."""
    chrome = find_chrome()
    if not chrome:
        raise ExportError("Нужен Google Chrome: google.com/chrome")
    profile = tempfile.mkdtemp(prefix="pd-chrome-")
    fd, tmp = tempfile.mkstemp(suffix=".html")
    text = ""
    with os.fdopen(fd, "wb") as fh:
        proc = subprocess.Popen([chrome, "--headless=new", "--disable-gpu", "--no-first-run", "--hide-scrollbars",
                                 "--user-data-dir=" + profile, "--virtual-time-budget=20000",
                                 "--window-size=1920,1080", "--dump-dom", url],
                                stdout=fh, stderr=subprocess.DEVNULL, start_new_session=True)
        deadline = time.time() + timeout
        try:
            while time.time() < deadline:
                text = Path(tmp).read_text(encoding="utf-8", errors="replace")
                if marker in text or proc.poll() is not None:
                    break
                time.sleep(0.3)
        finally:
            if proc.poll() is None:
                try:
                    os.killpg(proc.pid, signal.SIGKILL)
                except (ProcessLookupError, PermissionError):  # группа уже распалась или помощник Chrome недоступен
                    proc.kill()
            proc.wait()
            shutil.rmtree(profile, ignore_errors=True)
            os.unlink(tmp)
    return text


def visible_slides(project):
    meta = store.load_project(project)
    return [s for s in sorted(meta["slides"], key=lambda s: s["order"]) if not s.get("hidden")]


def print_page(project, base):
    """HTML для печати: видимые слайды по порядку, по одному на страницу 1920x1080."""
    frames = "\n".join(
        '<iframe sandbox="allow-same-origin" src="%s/p/%s/%s"></iframe>' % (base, quote(project), quote(s["file"]))
        for s in visible_slides(project))
    return ("<!doctype html><html><head><meta charset='utf-8'><style>"
            "@page{size:1920px 1080px;margin:0}html,body{margin:0;padding:0;background:#fff}"
            "iframe{display:block;width:1920px;height:1080px;border:0;break-after:page;page-break-after:always}"
            "iframe:last-child{break-after:auto;page-break-after:auto}"
            "</style></head><body>%s</body></html>" % frames)


def _status(project, **kw):
    path = store.project_dir(project) / "export-status.json"
    st = store.read_json(path) or {}
    st.update(kw, updated_at=store.now())
    store.write_json(path, st)
    return st


def _replace(tmp, final):
    """Атомарная подмена результата: старый удаляется только после готовности нового."""
    old = None
    if final.exists():
        old = final.with_name("." + final.name + ".old-" + uuid.uuid4().hex[:6])
        os.replace(final, old)
    os.replace(tmp, final)
    if old:
        shutil.rmtree(old, ignore_errors=True) if old.is_dir() else old.unlink()


def _unique(path):
    """В чужой папке ничего не перезаписываем: «Имя.pdf» → «Имя 2.pdf»."""
    if not path.exists():
        return path
    stem, suf = (path.stem, path.suffix) if path.suffix == ".pdf" else (path.name, "")
    i = 2
    while (path.parent / ("%s %d%s" % (stem, i, suf))).exists():
        i += 1
    return path.parent / ("%s %d%s" % (stem, i, suf))


def _export_pptx(project, slides, base, out_root, custom, log):
    """Скан геометрии → растровые слои (только где они нужны) → pptx + «Шрифты» (использованные файлы) рядом."""
    _status(project, stage="scan")
    scan = pptx_export.parse_dump(dump_dom("%s/pptx-scan/%s" % (base, quote(project)), timeout=SCAN_TIMEOUT, log=log))
    if scan is None:
        raise ExportError("Chrome не успел прочитать слайды за %d с" % SCAN_TIMEOUT)
    bad = [s["slide"] for s in scan if s.get("error")]
    if bad:
        raise ExportError("Не удалось прочитать слайды: %s" % ", ".join(bad))
    out_root.mkdir(parents=True, exist_ok=True)
    tmp = out_root / (".PPTX.tmp-" + uuid.uuid4().hex[:6])
    tmp.mkdir()
    try:
        need = [s["slide"] for s in scan if any(it["k"] == "raster" for it in s["items"])]
        _status(project, stage="raster", progress=0, total=len(need))
        layers, done = {}, [0]

        def shot(sid):
            png = tmp / ("layer-%s.png" % re.sub(r"\W+", "_", sid))
            _run_chrome(["--window-size=1920,1080", "--force-device-scale-factor=%d" % pptx_export.SCALE,
                         "--default-background-color=00000000", "--virtual-time-budget=15000", "--screenshot=" + str(png),
                         "%s/pptx-raster/%s/%s" % (base, quote(project), quote(sid))], png, JPEG_TIMEOUT, log)
            layers[sid] = png.read_bytes()
            png.unlink()
            with _status_lock:
                done[0] += 1
                _status(project, progress=done[0])

        with ThreadPoolExecutor(PARALLEL) as pool:
            list(pool.map(shot, need))
        _status(project, stage="build")
        name = project + ".pptx"
        res = pptx_export.build(project, scan, layers, tmp / name, warn=lambda m: log("pptx %s: %s" % (project, m)))
        if res["fonts"]:
            (tmp / "Шрифты").mkdir()
            for f in res["fonts"]:
                shutil.copy2(f, tmp / "Шрифты" / f.name)
        if custom:
            final_dir = _unique(out_root / (project + " — PPTX"))
            os.replace(tmp, final_dir)
        else:
            final_dir = out_root / "PPTX"
            _replace(tmp, final_dir)
        _status(project, stage=None, progress=len(slides), total=len(slides),
                raster=sorted({r["slide"] for r in res["raster"]}))
        return final_dir / name
    finally:
        if tmp.exists():
            shutil.rmtree(tmp, ignore_errors=True)


def export(project, fmt, base, log=print, dest_dir=None):
    """fmt: 'pdf' | 'jpeg' | 'pptx'. base: http://127.0.0.1:<port>.
    dest_dir=None — «Экспорт/<Проект>/PDF|JPEG» (результат заменяется);
    иначе папка, выбранная пользователем (имя делается уникальным, ничего не затирается)."""
    slides = visible_slides(project)
    if not slides:
        raise ExportError("Нет видимых слайдов для экспорта")
    custom = dest_dir is not None
    if custom:
        dest_dir = Path(dest_dir)
        if not dest_dir.is_dir() or not os.access(str(dest_dir), os.W_OK):
            raise ExportError("Нет доступа на запись в папку %s" % dest_dir)
    out_root = dest_dir if custom else store.inside(store.EXPORTS, store.EXPORTS / project)
    _status(project, format=fmt, status="in_progress", progress=0, total=len(slides), stage=None, raster=None,
            path=None, error=None, started_at=store.now(), finished_at=None)
    try:
        if fmt == "pdf":
            dst_dir = out_root if custom else out_root / "PDF"
            dst_dir.mkdir(parents=True, exist_ok=True)
            final = dst_dir / (project + ".pdf")
            tmp = dst_dir / (".%s.%s.tmp.pdf" % (project, uuid.uuid4().hex[:6]))
            try:
                _run_chrome(["--no-pdf-header-footer", "--print-to-pdf=" + str(tmp),
                             "%s/print/%s" % (base, quote(project))], tmp, PDF_TIMEOUT, log)
                if not tmp.read_bytes()[:5] == b"%PDF-":
                    raise ExportError("Получен повреждённый PDF")
                if custom:
                    final = _unique(final)
                    os.replace(tmp, final)
                else:
                    _replace(tmp, final)
            finally:
                if tmp.exists():
                    tmp.unlink()
            _status(project, progress=len(slides))
        elif fmt == "jpeg":
            out_root.mkdir(parents=True, exist_ok=True)
            final = out_root / (project + " — JPEG") if custom else out_root / "JPEG"
            tmp = out_root / (".JPEG.tmp-" + uuid.uuid4().hex[:6])
            tmp.mkdir()
            done = [0]

            def shot(pair):
                i, s = pair
                stem = "%02d-%s" % (i, s["id"].split("-", 1)[-1] if s["id"][:2].isdigit() else s["id"])
                png = tmp / (stem + ".png")
                _run_chrome(["--window-size=1920,1080", "--virtual-time-budget=5000",  # без него текст иногда снимается до загрузки шрифтов
                             "--screenshot=" + str(png),
                             "%s/p/%s/%s" % (base, quote(project), quote(s["file"]))], png, JPEG_TIMEOUT, log)
                r = subprocess.run(["sips", "-s", "format", "jpeg", "-s", "formatOptions", "92",
                                    str(png), "--out", str(tmp / (stem + ".jpg"))], capture_output=True)
                if r.returncode != 0:
                    raise ExportError("sips не смог сохранить JPEG: %s" % stem)
                png.unlink()
                with _status_lock:
                    done[0] += 1
                    _status(project, progress=done[0])

            try:
                with ThreadPoolExecutor(PARALLEL) as pool:
                    list(pool.map(shot, enumerate(slides, 1)))
                if len(list(tmp.glob("*.jpg"))) != len(slides):
                    raise ExportError("Сохранены не все слайды")
            except BaseException:
                shutil.rmtree(tmp, ignore_errors=True)
                raise
            if custom:
                final = _unique(final)
                os.replace(tmp, final)
            else:
                _replace(tmp, final)
        elif fmt == "pptx":
            final = _export_pptx(project, slides, base, out_root, custom, log)
        else:
            raise ExportError("Неизвестный формат: %s" % fmt)
    except Exception as e:
        _status(project, status="failed", error=str(e), finished_at=store.now())
        log("export %s %s failed: %s" % (project, fmt, e))
        raise
    st = store.read_json(store.project_dir(project) / "export-status.json") or {}
    last = st.get("last") or {}
    last[fmt] = {"path": str(final), "finished_at": store.now(), "slides": len(slides)}
    _status(project, status="completed", path=str(final), finished_at=store.now(), last=last)
    return final
