#!/usr/bin/env python3
# Самопроверка слайдов после генерации: что вылезло за холст, обрезалось, наехало, не загрузилось.
#   check_slides.py <проект>            слайды проекта
#   check_slides.py --guide <гайд>      эталонные слайды гайда (examples/)
#   флаги: --no-shots (без скриншотов)
# Пишет <папка>/.check/report.json, report.md, NN-<id>.png и контактные листы лист-NN.png.
# Сервер приложения поднимается сам, если не запущен. Вывод — JSON со сводкой.
import json, os, re, subprocess, sys, time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from urllib.parse import quote

APP = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(APP))
from core import exporter, sheets, store, tasks  # noqa: E402

sys.path.insert(0, str(APP / "tools"))
import launch  # noqa: E402

LABELS = {"out_of_canvas": "текст за краем слайда", "text_cut": "текст обрезан блоком",
          "clipped_by_box": "текст выходит за свою плашку", "overlap": "блоки текста наезжают друг на друга",
          "small_text": "слишком мелкий текст", "broken_image": "картинка не загрузилась",
          "font_failed": "шрифт не загрузился", "empty": "пустой слайд", "error": "ошибка проверки",
          "low_contrast": "текст плохо читается на фоне (контраст ниже 3:1)",
          "empty_bottom": "нижняя часть слайда пустая — растянуть блоки или добавить содержание"}


def out(data, code=0):
    print(json.dumps(data, ensure_ascii=False))
    sys.exit(code)


def ensure_server():
    info = launch.running()
    if info:
        return info["url"].rstrip("/")
    store.STATE.mkdir(exist_ok=True)
    log = open(store.STATE / "server.out", "a")
    subprocess.Popen([sys.executable, str(APP / "server.py")], cwd=str(APP.parent), stdout=log, stderr=log,
                     stdin=subprocess.DEVNULL, start_new_session=True)
    for _ in range(50):
        time.sleep(0.2)
        info = launch.running()
        if info:
            return info["url"].rstrip("/")
    out({"ok": False, "error": "Не удалось запустить сервер приложения (.state/server.out)"}, 1)


def dump_dom(url, timeout=120):
    try:
        text = exporter.dump_dom(url, timeout=timeout)
    except exporter.ExportError as e:
        out({"ok": False, "error": str(e)}, 1)
    m = re.search(r'id="result">__RESULT__(.*?)__END__', text, re.S)
    if not m:
        out({"ok": False, "error": "Проверка не успела выполниться за %d с" % timeout}, 1)
    import html as h
    return json.loads(h.unescape(m.group(1)))


def main(a):
    guide = "--guide" in a
    name = a[a.index("--guide") + 1] if guide else next((x for x in a if not x.startswith("--")), None)
    if not name:
        out({"ok": False, "error": "check_slides.py <проект> | --guide <гайд>"}, 1)
    kind = "guide" if guide else "project"
    d = tasks.task_dir(kind, name) if guide else store.project_dir(name)
    if not guide and not (d / "request.json").exists():
        store.load_project(name)  # старый проект без заявки: создать project.json по папке slides/
    gdir = d if guide else store.GUIDES / ((store.read_json(d / "project.json") or {}).get("guideline_set_id") or "")
    g = store.read_json(gdir / "guide.json") or {}
    min_px = g.get("min_font_px") or (g.get("type_scale_px") or {}).get("min_readable") or 20
    base = ensure_server()
    report = dump_dom("%s/check/%s/%s?min=%d" % (base, kind, quote(name), int(min_px)))
    check = d / ".check"
    check.mkdir(exist_ok=True)
    for old in check.glob("*.png"):
        old.unlink()
    shots = []
    if "--no-shots" not in a:
        if guide:
            urls = [(r["slide"], "%s/g/%s/examples/%s.html" % (base, quote(name), quote(r["slide"]))) for r in report]
        else:
            meta = store.read_json(d / "project.json")
            files = {s["id"]: s["file"] for s in meta["slides"]}
            urls = [(r["slide"], "%s/p/%s/%s" % (base, quote(name), quote(files[r["slide"]]))) for r in report]

        def shot(pair):
            i, (sid, url) = pair
            png = check / ("%02d-%s.png" % (i, sid))
            exporter._run_chrome(["--window-size=1920,1080", "--screenshot=" + str(png), url], png, 45, lambda m: None)
            return png
        with ThreadPoolExecutor(4) as pool:
            shots = list(pool.map(shot, enumerate(urls, 1)))
        sheets.make_sheets(shots, check, prefix="лист", labels=[p.stem for p in shots], log=lambda m: None)
    lines = ["# Самопроверка: %s\n" % name]
    total = 0
    for r in report:
        if not r["issues"]:
            continue
        lines.append("## %s" % r["slide"])
        for it in r["issues"]:
            total += 1
            extra = ", ".join("%s=%s" % (k, v) for k, v in it.items() if k not in ("type",))
            lines.append("- %s: %s" % (LABELS.get(it["type"], it["type"]), extra))
        lines.append("")
    if not total:
        lines.append("Проблем не найдено.")
    store.write_json(check / "report.json", report)
    (check / "report.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
    out({"ok": True, "slides": len(report), "issues": total,
         "slides_with_issues": [r["slide"] for r in report if r["issues"]],
         "report": str(check / "report.md"), "shots_dir": str(check),
         "sheets": [str(p) for p in sorted(check.glob("лист-*.png"))]})


if __name__ == "__main__":
    main(sys.argv[1:])
