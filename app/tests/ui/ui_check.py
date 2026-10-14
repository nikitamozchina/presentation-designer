# UI-проверка в настоящем Chrome (только для разработки, Playwright — не зависимость приложения).
#   python3 -m venv .venv-dev && .venv-dev/bin/pip install playwright
#   .venv-dev/bin/python app/tests/ui/ui_check.py --pack <папка пакета> [--guide <гайд из пакета>] [--shots <папка>]
# Работает на временной копии пакета (гайд без библиотеки + демо), своя Корзина и порт.
# Окна выбора macOS подменяются заглушкой fetch('/api/pick'). Код выхода 1, если хоть одна проверка упала.
import argparse, asyncio, json, os, shutil, subprocess, sys, tempfile, time
from pathlib import Path
from playwright.async_api import async_playwright

APP = Path(__file__).resolve().parents[3]
ap = argparse.ArgumentParser()
ap.add_argument("--pack", required=True)
ap.add_argument("--guide", default=None, help="по умолчанию — первый гайд пакета с эталонами")
ap.add_argument("--shots", default=None)
args = ap.parse_args()
G = args.guide or sorted(p.name for p in (Path(args.pack) / "Гайдлайны").iterdir() if (p / "examples").is_dir())[0]

TMP = Path(tempfile.mkdtemp(prefix="pd-ui-"))
ROOT, TRASH, REF, DEST = TMP / "root", TMP / "trash", TMP / "refs", TMP / "dest"
SHOTS = Path(args.shots) if args.shots else TMP / "shots"
for d in (ROOT / "Экспорт", TRASH, REF / "Папка отчётов", DEST, SHOTS):
    d.mkdir(parents=True, exist_ok=True)
shutil.copytree(Path(args.pack) / "Гайдлайны" / G, ROOT / "Гайдлайны" / G, ignore=lambda d, n: [x for x in n if x in ("library", "_extracted")])
shutil.copytree(Path(args.pack) / "Проекты", ROOT / "Проекты")
for n in ("Отчёт Q3.docx", "Цифры.xlsx", "Лишнее.txt"):
    (REF / n).write_text("x", encoding="utf-8")
(REF / "Папка отчётов" / "a.md").write_text("a", encoding="utf-8")

PORT = "8810"
env = dict(os.environ, PD_ROOT=str(ROOT), PD_PORT=PORT, PD_CONFIG=str(TMP / "config"), PD_TRASH=str(TRASH))
os.environ.update(PD_ROOT=str(ROOT))
sys.path.insert(0, str(APP / "app"))
from core import store  # noqa: E402

items = lambda ps: [{"label": str(p), "name": p.name, "is_dir": p.is_dir()} for p in ps]
files = [REF / "Отчёт Q3.docx", REF / "Цифры.xlsx", REF / "Лишнее.txt"]
PICKS = {"files": {"ok": True, "token": store.save_pick(files, "files"), "items": items(files)},
         "folder": {"ok": True, "token": store.save_pick([REF / "Папка отчётов"], "folder"), "items": items([REF / "Папка отчётов"])}}
EXPORT_PICK = {"ok": True, "token": store.save_pick([DEST], "folder"), "items": [{"label": "~/Папка", "name": "x", "is_dir": True}]}
STUB = """(p) => { const f = window.fetch; window.fetch = (u, o) => {
  if (u === '/api/pick') { const k = JSON.parse(o.body).kind; const r = (k === 'folder' && window.__exportPick) ? window.__exportPick : p[k];
    return Promise.resolve(new Response(JSON.stringify(r), {headers: {'Content-Type': 'application/json'}})); }
  return f(u, o); }; }"""
agent = lambda *a: json.loads(subprocess.run([sys.executable, str(APP / "app/tools/agent.py"), *a], env=env, capture_output=True, text=True).stdout)
failed = []


def check(cond, label):
    print(("OK   " if cond else "FAIL ") + label, flush=True)
    if not cond:
        failed.append(label)


async def main():
    async with async_playwright() as pw:
        b = await pw.chromium.launch(channel="chrome")
        pg = await b.new_page(viewport={"width": 1440, "height": 900})
        errors = []
        pg.on("pageerror", lambda e: errors.append(str(e)))
        pg.set_default_timeout(10000)
        await pg.goto("http://127.0.0.1:%s/" % PORT); await asyncio.sleep(2.5)
        await pg.evaluate(STUB, PICKS)

        # --- новый проект: форма ---
        await pg.click("#newProject"); await asyncio.sleep(0.5)
        check(await pg.inner_text("#pGuide .val") == G, "гайд по умолчанию — готовый")
        await pg.click("#pMode .select-btn"); await pg.click("#pMode li >> text=Дополнить")
        check(await pg.is_visible("#pSourceField"), "«Дополнить» показывает исходную презентацию")
        await pg.click("#pSourceType button[data-type=file]"); await pg.click("#pSave"); await asyncio.sleep(0.2)
        check("файл" in await pg.inner_text("#pError"), "без файла основы — ошибка")
        await pg.focus("#pMode .select-btn")
        for k in ("ArrowDown", "ArrowUp", "Enter"):
            await pg.keyboard.press(k)
        check(await pg.inner_text("#pMode .val") == "Создать" and await pg.is_hidden("#pSourceField"), "выбор с клавиатуры, поле основы скрыто")
        await pg.click("#pSave"); await asyncio.sleep(0.2)
        check("тему" in await pg.inner_text("#pError"), "без темы — ошибка")
        await pg.fill("#pTopic", "Итоги квартала"); await pg.fill("#pDuration", "12")
        check("12 слайдов" in await pg.inner_text("#pDurationHint"), "подсказка длительности")
        await pg.fill("#pName", "Имя Фамилия"); await pg.fill("#pPosition", "Должность")
        await pg.click("#pPresets .btn-link"); await asyncio.sleep(0.4)
        check("Имя Фамилия" in await pg.inner_text("#pPresets"), "пресет автора сохранён")
        await pg.click("#pAddFiles"); await pg.click("#pAddFolder"); await asyncio.sleep(0.2)
        await pg.click("#pRefs li:has-text('Лишнее') button")
        await pg.screenshot(path=str(SHOTS / "1-form.png"))
        await pg.click("#pSave"); await asyncio.sleep(2.5)
        name = await pg.inner_text("#projectTitle")
        check(name.startswith("Итоги квартала — "), "проект создан и открыт")
        req = json.loads((ROOT / "Проекты" / name / "request.json").read_text(encoding="utf-8"))
        check(req["references"] == ["Отчёт Q3.docx", "Цифры.xlsx", "Папка отчётов"], "в заявке только оставленные файлы")
        check(await pg.evaluate("document.getElementById('app').classList.contains('readonly')"), "собираемый проект только для просмотра")

        # --- статусы заявки, которые выставляет агент ---
        agent("task-claim", "project")
        agent("task-state", "project", name, "waiting_user", "--stage", "interview", "--question", "Кто аудитория?", "Ждёт ответа")
        await asyncio.sleep(2.5)
        check("Кто аудитория?" in await pg.inner_text("#statusPane"), "вопрос агента в карточке статуса")
        agent("init-project", name)
        ex, sd = ROOT / "Гайдлайны" / G / "examples", ROOT / "Проекты" / name / "slides"
        shutil.copy(ex / "01-title.html", sd / "01-title.html"); shutil.copy(ex / "06-chart-bars.html", sd / "02-bars.html")
        agent("sync-slides", name)
        agent("task-state", "project", name, "working", "--stage", "generating", "--progress", "2/10", "Делаю слайды")
        await asyncio.sleep(2.5)
        check(await pg.locator("#thumbs .thumb").count() == 2, "слайды видны во время генерации")
        await pg.screenshot(path=str(SHOTS / "2-generating.png"))
        check(agent("task-state", "project", name, "ready", "Готово")["ok"], "ready принят")
        await asyncio.sleep(2.5)
        check(not await pg.evaluate("document.getElementById('app').classList.contains('readonly')"), "после «Готово» правки открыты")

        # --- экспорт в выбранную папку ---
        await pg.evaluate("(p) => { window.__exportPick = p; }", EXPORT_PICK)
        await pg.click("#exportBtn"); await pg.click("#destChange"); await asyncio.sleep(0.4)
        check(await pg.inner_text("#destLabel") == "~/Папка", "«Изменить…» подставляет выбранную папку")
        await pg.click("#exportGo")
        for _ in range(60):
            await asyncio.sleep(1)
            if (await pg.inner_text("#exportMsg")).startswith(("Готово", "Ошибка")):
                break
        check(any(DEST.glob("*.pdf")), "PDF сохранён в выбранную папку")
        await pg.evaluate("() => { window.__exportPick = null; }")
        await pg.click("#destReset") if await pg.is_visible("#destReset") else None
        await pg.click("label.format:has(input[value=pptx])"); await pg.click("#exportGo")
        msg = ""
        for _ in range(90):
            await asyncio.sleep(1)
            msg = await pg.inner_text("#exportMsg")
            if msg.startswith(("Готово", "Ошибка")):
                break
        pdir = ROOT / "Экспорт" / name / "PPTX"
        check(msg.startswith("Готово") and (pdir / (name + ".pptx")).is_file(), "PPTX сохранён в Экспорт/<проект>/PPTX")
        check((pdir / "Шрифты").is_dir() and any((pdir / "Шрифты").glob("*.otf")), "рядом папка «Шрифты» с использованными шрифтами")
        check("«Шрифты»" in msg and "картинкой" not in msg, "сообщение: подсказка про шрифты; столбцы — фигуры, нередактируемого нет")
        await pg.screenshot(path=str(SHOTS / "2b-pptx.png"))
        await pg.click("#exportClose"); await asyncio.sleep(0.3)

        # --- удаление ---
        row = pg.locator("#projectList .side-row").filter(has_text="Итоги квартала")
        check(await row.locator(".side-del").evaluate("e => getComputedStyle(e).opacity") == "0", "корзина скрыта без наведения")
        await row.hover(); await row.locator(".side-del").click(); await asyncio.sleep(0.4)
        check(await pg.evaluate("document.activeElement.id") == "deleteCancel", "фокус в подтверждении — на «Отмена»")
        await pg.screenshot(path=str(SHOTS / "3-delete.png"))
        await pg.keyboard.press("Escape"); await asyncio.sleep(0.3)
        check((ROOT / "Проекты" / name).exists(), "Esc ничего не удаляет")
        await row.hover(); await row.locator(".side-del").click(); await asyncio.sleep(0.3)
        await pg.click("#deleteGo"); await asyncio.sleep(2)
        check((TRASH / name).exists() and not (ROOT / "Проекты" / name).exists(), "проект уехал в Корзину")
        check(name not in await pg.inner_text("#projectList"), "проект исчез из сайдбара")

        check(not errors, "ошибок JS нет %s" % (errors or ""))
        await b.close()


srv = subprocess.Popen([sys.executable, str(APP / "app/server.py")], env=env, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
time.sleep(1.2)
try:
    asyncio.run(main())
finally:
    srv.terminate(); srv.wait()
    if not args.shots:
        shutil.rmtree(TMP, ignore_errors=True)
print("\n%s: %d упало" % ("FAIL" if failed else "OK", len(failed)))
sys.exit(1 if failed else 0)
