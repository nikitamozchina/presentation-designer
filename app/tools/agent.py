#!/usr/bin/env python3
# CLI для ИИ-агента (Claude Code): забрать итерацию правок, найти выделение, отметить результат, закрыть.
# Агент НЕ пишет JSON руками — только через эти команды. Вывод — JSON в stdout.
#
#   agent.py status                               очередь по всем проектам
#   agent.py claim [--takeover]                   забрать следующую итерацию (lock + снапшот)
#   agent.py locate <project> <N> <edit-id>       найти выделенный текст в HTML слайда
#   agent.py begin <project> <N> <edit-id>        ПЕРЕД правкой: снимок файлов (нужен для отката)
#   agent.py item <project> <N> <edit-id> <status> "<комментарий>"
#   agent.py finish <project> <N>                 проверить и закрыть итерацию
#   agent.py heartbeat <edits|guide|project> <имя> "<что делаю>"
#
# Задачи «собрать гайдлайн» / «создать проект» (kind = guide | project):
#   agent.py task-claim <kind> [--resume <имя>]   взять задачу (сначала незавершённые, потом новые)
#   agent.py task-state <kind> <имя> <state> [--stage S] [--progress N/M] [--question "…"] "<сообщение>"
#       state: working | waiting_user | ready | failed;  stage: analysis interview structure generating self_check building
#   agent.py task-log <kind> <имя> <agent|user> "<текст>"   журнал интервью (после КАЖДОГО вопроса и ответа)
#   agent.py copy <kind> <имя> <откуда> <куда>      копирование файла/папки внутри папки задачи (пути относительные)
#   agent.py dedupe guide <имя>                     убрать из _extracted папки «…-медиа», уже целиком скопированные в assets/library (в Корзину)
#   agent.py init-project <проект>                  стили, шрифты, фоны и логотипы гайда → в проект (url() переписываются)
#   agent.py sync-slides <проект>                   обновить список слайдов по папке slides/ + докопировать ассеты
#   agent.py sync-assets <проект>                   докопировать из гайда картинки, на которые ссылаются слайды
import json, os, re, shutil, sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from core import store, tasks, textmatch  # noqa: E402


def out(data, code=0):
    print(json.dumps(data, ensure_ascii=False, indent=2))
    sys.exit(code)


def fail(msg):
    out({"ok": False, "error": msg}, 1)


def cmd_status():
    queue = []
    for p in store.list_projects():
        for n in store.edit_versions(p):
            queue.append({"project": p, "version": n, "status": store.iteration_state(p, n)})
    out({"ok": True, "agent": store.agent_status(), "iterations": queue})


def _job(project, n):
    edits = store.read_json(store.edits_file(project, n))
    pdir = store.project_dir(project)
    meta = store.load_project(project)
    files = {s["id"]: str(pdir / s["file"]) for s in meta["slides"]}
    guide = store.GUIDES / (meta.get("guideline_set_id") or "") / "guide.json"
    return {"project": project, "version": n, "project_dir": str(pdir),
            "guide": str(guide), "styles_css": str(pdir / "styles.css"),
            "items": [dict(it, file=files.get(it["slide_id"])) for it in edits["items"]]}


def cmd_claim(takeover):
    stalled = []
    for p in store.list_projects():
        for n in store.edit_versions(p):
            state = store.iteration_state(p, n)
            if state == "in_progress":
                fail("Итерация %s v%d уже выполняется другим агентом" % (p, n))
            if state == "stalled":
                if not takeover:
                    stalled.append({"project": p, "version": n})
                    continue
                store.lock_file(p, n).unlink()
            if state in ("queued", "stalled"):
                try:
                    fd = os.open(str(store.lock_file(p, n)), os.O_CREAT | os.O_EXCL | os.O_WRONLY)
                except FileExistsError:
                    continue  # кто-то успел раньше
                with os.fdopen(fd, "w", encoding="utf-8") as f:
                    json.dump({"pid": os.getppid(), "claimed_at": store.now()}, f)
                job = _job(p, n)
                slide_ids = sorted({it["slide_id"] for it in job["items"] if it["slide_id"]})
                with_css = any(it["scope"] == "presentation" for it in job["items"])
                saved = store.snapshot(p, n, slide_ids, with_css)
                prev = store.read_json(store.result_file(p, n)) or {}
                done = {r["id"]: r for r in prev.get("items", [])}
                store.write_json(store.result_file(p, n), {
                    "version": n, "status": "in_progress",
                    "started_at": prev.get("started_at") or store.now(), "finished_at": None,
                    "snapshot": saved,
                    "items": [done.get(it["id"], {"id": it["id"], "status": "pending", "note": ""})
                              for it in job["items"]],
                })
                store.set_agent_status(p, "in_progress", "Правки v%d" % n, "%s/v%d" % (p, n))
                job["already_done"] = [i for i, r in done.items() if r.get("status") != "pending"]
                out({"ok": True, "job": job})
    if stalled:
        fail("Есть зависшие итерации (агент пропал посреди работы): %s. "
             "Проверь слайды и запусти claim --takeover" % stalled)
    out({"ok": True, "job": None, "message": "Очередь пуста"})


def _require_owned(project, n):
    if not store.lock_file(project, n).exists():
        fail("Итерация %s v%d не захвачена — сначала claim" % (project, n))


def cmd_locate(project, n, edit_id):
    edits = store.read_json(store.edits_file(project, n)) or {}
    item = next((i for i in edits.get("items", []) if i["id"] == edit_id), None)
    if not item:
        fail("Нет правки %s" % edit_id)
    if not item.get("selection"):
        fail("У правки %s нет выделения — это комментарий ко всему слайду" % edit_id)
    raw = store.slide_path(project, item["slide_id"]).read_text(encoding="utf-8")
    sel = item["selection"]
    hits = textmatch.find(raw, sel["text"], sel.get("prefix", ""), sel.get("suffix", ""))
    verdict = "unique" if len(hits) == 1 else ("not_found" if not hits else "ambiguous")
    out({"ok": True, "verdict": verdict, "matches": hits,
         "hint": {"unique": "Правь ровно этот фрагмент raw (old_string для Edit).",
                  "not_found": "Текст изменился — не ищи похожий, ставь needs_input.",
                  "ambiguous": "Несколько совпадений — ставь needs_input."}[verdict]})


def cmd_begin(project, n, edit_id):
    _require_owned(project, n)
    base = store.begin_item(project, n, edit_id)
    out({"ok": True, "snapshot": str(base)})


def cmd_item(project, n, edit_id, status, note):
    _require_owned(project, n)
    if status not in store.ITEM_STATUSES or status == "pending":
        fail("Статус должен быть одним из: %s" % ", ".join(store.ITEM_STATUSES[1:]))
    changed = {}
    if status in ("done", "partial"):
        changed = store.record_item_change(project, n, edit_id)  # без begin — ошибка
    res = store.read_json(store.result_file(project, n))
    for r in res["items"]:
        if r["id"] == edit_id:
            r.update(status=status, note=note, updated_at=store.now())
            break
    else:
        fail("Нет правки %s" % edit_id)
    store.write_json(store.result_file(project, n), res)
    store.set_agent_status(project, "in_progress", "Правки v%d: %s → %s" % (n, edit_id, status), "%s/v%d" % (project, n))
    out({"ok": True, "changed_files": sorted(changed)})


def cmd_finish(project, n):
    _require_owned(project, n)
    res = store.read_json(store.result_file(project, n))
    pending = [r["id"] for r in res["items"] if r["status"] == "pending"]
    if pending:
        fail("Не отмечены правки: %s" % ", ".join(pending))
    # проверка результата: слайды на месте, разметка слайда цела, текст не пропал
    problems = []
    edits = store.read_json(store.edits_file(project, n))
    for sid in sorted({i["slide_id"] for i in edits["items"] if i["slide_id"]}):
        f = store.slide_path(project, sid)
        raw = f.read_text(encoding="utf-8") if f.exists() else ""
        if 'class="slide' not in raw or not textmatch.visible_text(raw):
            problems.append("%s: слайд пуст или сломан" % sid)
    statuses = [r["status"] for r in res["items"]]
    if problems:
        status = "failed"
    elif "needs_input" in statuses:
        status = "needs_input"
    elif all(s == "done" for s in statuses):
        status = "completed"
    elif any(s in ("done", "partial") for s in statuses):
        status = "completed_with_warnings"
    else:
        status = "failed"
    res.update(status=status, finished_at=store.now(), problems=problems)
    store.write_json(store.result_file(project, n), res)
    store.lock_file(project, n).unlink()
    store.set_agent_status(project, "idle", "Итерация v%d: %s" % (n, status))
    out({"ok": not problems, "status": status, "problems": problems})


# ---------- задачи: гайдлайн / новый проект ----------

def _task_info(kind, name, resumed):
    d = tasks.task_dir(kind, name)
    info = {"kind": kind, "name": name, "dir": str(d), "resumed": resumed,
            "request": tasks.request(kind, name), "lifecycle": tasks.view(kind, name),
            "sources_dir": str(d / "Исходники"), "extracted_dir": str(d / "_extracted"),
            "interview": str(d / "brief" / "interview.md")}
    iv = d / "brief" / "interview.md"
    info["interview_so_far"] = iv.read_text(encoding="utf-8") if iv.exists() else ""
    if kind == "project":
        gid = info["request"].get("guide")
        info["guide_dir"] = str(store.GUIDES / gid) if gid else None
    return info


def cmd_task_claim(kind, resume):
    name, resumed = tasks.claim(kind, resume)
    if not name:
        out({"ok": True, "task": None, "message": "Нет задач, ожидающих агента"})
    if not resumed:
        tasks.set_state(kind, name, "working", stage="analysis", message="Агент изучает исходники")
    else:
        tasks.heartbeat(kind, name)
    out({"ok": True, "task": _task_info(kind, name, resumed)})


def cmd_task_state(kind, name, rest):
    state, stage, progress, question, msg = rest[0], None, None, None, ""
    i = 1
    while i < len(rest):
        if rest[i] == "--stage":
            stage = rest[i + 1]; i += 2
        elif rest[i] == "--progress":
            m = re.fullmatch(r"(\d+)/(\d+)", rest[i + 1])
            if not m:
                fail("--progress в формате N/M")
            progress = {"done": int(m.group(1)), "total": int(m.group(2))}; i += 2
        elif rest[i] == "--question":
            question = rest[i + 1]; i += 2
        else:
            msg = rest[i]; i += 1
    if state not in ("working", "waiting_user", "ready", "failed"):
        fail("state: working | waiting_user | ready | failed")
    if state == "ready":
        problems = _ready_check(kind, name)
        if problems:
            fail("Нельзя ставить ready: " + "; ".join(problems))
    tasks.set_state(kind, name, state, stage=stage, message=msg, question=question, progress=progress)
    out({"ok": True})


def _ready_check(kind, name):
    d = tasks.task_dir(kind, name)
    problems = []
    if kind == "guide":
        g = store.read_json(d / "guide.json")
        if not g:
            return ["нет guide.json"]
        for k in ("id", "name", "version", "base_css"):
            if k not in g:
                problems.append("в guide.json нет поля %s" % k)
        if g.get("base_css") and not (d / g["base_css"]).exists():
            problems.append("нет файла %s" % g["base_css"])
        md = (d / "guide.md").read_text(encoding="utf-8") if (d / "guide.md").exists() else None
        if md is None:
            problems.append("нет guide.md")
        else:
            for sec in ("## Таблицы", "## Графики"):
                if sec not in md:
                    problems.append("в guide.md нет раздела «%s»" % sec[3:])
        css = (d / g["base_css"]).read_text(encoding="utf-8") if g.get("base_css") and (d / g["base_css"]).exists() else ""
        for cls in ("table.data", ".bar-col", "svg.chart", ".legend", ".gantt"):
            if cls not in css:
                problems.append("в стилях нет компонента %s" % cls)
        if (tasks.request(kind, name) or {}).get("options", {}).get("examples") and len(list((d / "examples").glob("*.html"))) < 10:
            problems.append("включены эталонные слайды, а в examples/ меньше 10")
    else:
        meta = store.read_json(d / "project.json") or {}
        if not meta.get("slides"):
            problems.append("в проекте нет слайдов (sync-slides)")
        for s in meta.get("slides", []):
            raw = (d / s["file"]).read_text(encoding="utf-8") if (d / s["file"]).exists() else ""
            if 'class="slide' not in raw or not textmatch.visible_text(raw):
                problems.append("%s пуст или сломан" % s["id"])
    return problems


def cmd_copy(kind, name, src, dst):
    d = tasks.task_dir(kind, name)
    s = store.inside(d, d / src)
    t = store.inside(d, d / dst)
    if not s.exists():
        fail("Нет %s" % src)
    if s.is_dir():
        shutil.copytree(s, t, dirs_exist_ok=True)
    else:
        t.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(s, t)
    out({"ok": True, "copied": str(t)})


def cmd_dedupe(name):
    """Папки _extracted/**/…-медиа, у которых КАЖДЫЙ файл (имя + размер) уже есть в одной папке assets/library/*,
    переносятся в Корзину macOS. Остальное не трогается."""
    import time
    d = tasks.task_dir("guide", name)
    libs = [p for p in (d / "assets" / "library").iterdir() if p.is_dir()] if (d / "assets" / "library").is_dir() else []
    trash = Path.home() / ".Trash"
    moved, kept = [], []
    for media in sorted((d / "_extracted").rglob("*-медиа")):
        if not media.is_dir():
            continue
        files = {f.name: f.stat().st_size for f in media.iterdir() if f.is_file()}
        twin = next((lib for lib in libs if all((lib / n).is_file() and (lib / n).stat().st_size == s for n, s in files.items())), None)
        if not files or twin is None:
            kept.append(str(media.relative_to(d)))
            continue
        target = trash / ("%s %s" % (media.name, time.strftime("%H-%M-%S")))
        shutil.move(str(media), str(target))
        moved.append({"from": str(media.relative_to(d)), "same_as": str(twin.relative_to(d)), "trash": str(target)})
    out({"ok": True, "moved_to_trash": moved, "kept": kept})


def cmd_init_project(name):
    """Гайд → проект: styles.css (url(fonts/…) → url(assets/fonts/…)), fonts/ → assets/fonts/, assets/ → assets/."""
    d = tasks.task_dir("project", name)
    req = tasks.request("project", name)
    g = store.GUIDES / req["guide"]
    guide = store.read_json(g / "guide.json") or {}
    css_src = g / guide.get("base_css", "styles.css")
    if not css_src.exists():
        fail("В гайде нет %s" % css_src.name)
    css = css_src.read_text(encoding="utf-8")
    css = re.sub(r"""url\((['"]?)(?:\./)?fonts/""", r"url(\1assets/fonts/", css)
    (d / "styles.css").write_text(css, encoding="utf-8")
    if (g / "fonts").is_dir():
        shutil.copytree(g / "fonts", d / "assets" / "fonts", dirs_exist_ok=True)
    # библиотека гайда может весить сотни МБ — в проект сразу только база, остальное по ссылкам (sync-assets)
    for sub in ("bg", "logo"):
        if (g / "assets" / sub).is_dir():
            shutil.copytree(g / "assets" / sub, d / "assets" / sub, dirs_exist_ok=True)
    copied, missing = sync_assets(name)
    (d / "slides").mkdir(exist_ok=True)
    out({"ok": True, "styles_css": str(d / "styles.css"), "assets": str(d / "assets"),
         "guide_assets": str(g / "assets"), "note": "Картинки из гайда указывай как ../assets/<путь в гайде> — sync-slides скопирует их сам"})


ASSET_REF = re.compile(r"""(?:src|href)\s*=\s*["'](?:\.\./)?assets/([^"'?#]+)|url\(\s*['"]?(?:\.\./)?assets/([^'")?#]+)""")


def sync_assets(name):
    """Скопировать из гайда в проект картинки, на которые ссылаются слайды и styles.css."""
    from urllib.parse import unquote
    d = tasks.task_dir("project", name) if (store.PROJECTS / name / "request.json").exists() else store.project_dir(name)
    meta = store.read_json(d / "project.json") or {}
    g = store.GUIDES / (meta.get("guideline_set_id") or "") / "assets"
    files = list((d / "slides").glob("*.html")) + [d / "styles.css"]
    copied, missing = [], []
    for f in files:
        if not f.exists():
            continue
        for m in ASSET_REF.finditer(f.read_text(encoding="utf-8")):
            rel = unquote(m.group(1) or m.group(2)).strip()
            dst = store.inside(d / "assets", d / "assets" / rel)
            if dst.exists():
                continue
            src = g / rel
            if src.is_file() and store.inside(g, src):
                dst.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(src, dst)
                copied.append(rel)
            else:
                missing.append("%s → assets/%s" % (f.name, rel))
    return copied, sorted(set(missing))


def cmd_sync_slides(name):
    d = tasks.task_dir("project", name)
    files = sorted((d / "slides").glob("*.html"))

    def mutate(meta):
        old = {s["id"]: s for s in meta.get("slides", [])}
        slides = []
        for i, f in enumerate(files, 1):
            prev = old.get(f.stem, {})
            slides.append({"id": f.stem, "file": "slides/" + f.name, "title": store._slide_title(f),
                           "order": i, "hidden": prev.get("hidden", False)})
        meta["slides"] = slides
    meta = store.update_project(name, mutate)
    copied, missing = sync_assets(name)
    out({"ok": not missing, "slides": len(meta["slides"]), "assets_copied": copied,
         "missing_assets": missing, **({"error": "Битые ссылки на картинки: " + "; ".join(missing[:10])} if missing else {})})


def main(a):
    try:
        if a[:1] == ["status"]:
            cmd_status()
        elif a[:1] == ["claim"]:
            cmd_claim("--takeover" in a)
        elif a[:1] == ["locate"] and len(a) == 4:
            cmd_locate(a[1], int(a[2]), a[3])
        elif a[:1] == ["begin"] and len(a) == 4:
            cmd_begin(a[1], int(a[2]), a[3])
        elif a[:1] == ["item"] and len(a) == 6:
            cmd_item(a[1], int(a[2]), a[3], a[4], a[5])
        elif a[:1] == ["finish"] and len(a) == 3:
            cmd_finish(a[1], int(a[2]))
        elif a[:1] == ["heartbeat"] and len(a) >= 3:
            kind, name, msg = a[1], a[2], (a[3] if len(a) > 3 else "")
            if kind == "edits":
                st = store.read_json(store.agent_status_file(name)) or {}
                store.set_agent_status(name, "in_progress", msg or st.get("message", ""), st.get("current_task_id"))
            else:
                tasks.heartbeat(kind, name, msg or None)
            out({"ok": True})
        elif a[:1] == ["task-claim"] and len(a) >= 2:
            cmd_task_claim(a[1], a[a.index("--resume") + 1] if "--resume" in a else None)
        elif a[:1] == ["task-state"] and len(a) >= 4:
            cmd_task_state(a[1], a[2], a[3:])
        elif a[:1] == ["task-log"] and len(a) == 5:
            f = tasks.log_interview(a[1], a[2], a[3], a[4])
            tasks.heartbeat(a[1], a[2])
            out({"ok": True, "file": str(f)})
        elif a[:1] == ["copy"] and len(a) == 5:
            cmd_copy(a[1], a[2], a[3], a[4])
        elif a[:1] == ["dedupe"] and len(a) == 3 and a[1] == "guide":
            cmd_dedupe(a[2])
        elif a[:1] == ["init-project"] and len(a) == 2:
            cmd_init_project(a[1])
        elif a[:1] == ["sync-assets"] and len(a) == 2:
            c, m = sync_assets(a[1])
            out({"ok": not m, "assets_copied": c, "missing_assets": m})
        elif a[:1] == ["sync-slides"] and len(a) == 2:
            cmd_sync_slides(a[1])
        else:
            fail("Неизвестная команда. См. шапку app/tools/agent.py")
    except (ValueError, KeyError, FileNotFoundError, RuntimeError, TimeoutError) as e:
        fail(str(e))


if __name__ == "__main__":
    main(sys.argv[1:])
