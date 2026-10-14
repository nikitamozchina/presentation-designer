# Хранилище: файлы на диске = источник истины. Совместимо с Python 3.9 (системный /usr/bin/python3).
import json, os, re, shutil, time, tempfile
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(os.environ.get("PD_ROOT") or Path(__file__).resolve().parents[2])  # PD_ROOT — для тестов
PROJECTS = ROOT / "Проекты"
EXPORTS = ROOT / "Экспорт"
GUIDES = ROOT / "Гайдлайны"
STATE = ROOT / ".state"
STALE_SECONDS = 15 * 60  # без heartbeat дольше этого агент считается «не отвечает»

FINAL = ("completed", "completed_with_warnings", "needs_input", "failed", "cancelled")
ITEM_STATUSES = ("pending", "done", "partial", "needs_input", "unsupported", "failed")


def now():
    return datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds")


def age_seconds(iso):
    try:
        return time.time() - datetime.fromisoformat(iso).timestamp()
    except (TypeError, ValueError):
        return float("inf")


def read_json(path, default=None):
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    except FileNotFoundError:
        return default


def write_json(path, data):
    """Атомарная запись: временный файл в той же папке, fsync, rename."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(prefix="." + path.name + ".", suffix=".tmp", dir=str(path.parent))
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
            f.write("\n")
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, path)
    except BaseException:
        if os.path.exists(tmp):
            os.unlink(tmp)
        raise


class FileLock:
    """Эксклюзивная блокировка через O_CREAT|O_EXCL; ждёт до timeout секунд."""

    def __init__(self, path, timeout=5.0):
        self.path, self.timeout = Path(path), timeout

    def __enter__(self):
        deadline = time.time() + self.timeout
        while True:
            try:
                fd = os.open(str(self.path), os.O_CREAT | os.O_EXCL | os.O_WRONLY)
                os.write(fd, str(os.getpid()).encode())
                os.close(fd)
                return self
            except FileExistsError:
                if time.time() - self.path.stat().st_mtime > 30:  # брошенный lock упавшего процесса
                    self.path.unlink()
                    continue
                if time.time() > deadline:
                    raise TimeoutError("Блокировка занята: %s" % self.path)
                time.sleep(0.05)

    def __exit__(self, *exc):
        try:
            self.path.unlink()
        except FileNotFoundError:
            pass


def inside(base, path):
    """Защита от path traversal: путь обязан лежать внутри base."""
    base, path = Path(base).resolve(), Path(path).resolve()
    if path != base and base not in path.parents:
        raise ValueError("Путь вне разрешённой папки: %s" % path)
    return path


# ---------- проекты ----------

def project_dir(name):
    if not name or "/" in name or name.startswith("."):
        raise ValueError("Некорректное имя проекта")
    p = inside(PROJECTS, PROJECTS / name)
    if not p.is_dir():
        raise FileNotFoundError("Проект не найден: %s" % name)
    return p


def list_projects():
    return sorted(p.name for p in PROJECTS.iterdir() if p.is_dir() and not p.name.startswith("."))


def _slide_title(path):
    m = re.search(r"<title>(.*?)</title>", path.read_text(encoding="utf-8"), re.S)
    return re.sub(r"^\d+\s*", "", m.group(1)).strip() if m else path.stem


def load_project(name):
    """project.json; если его нет — создаётся по папке slides/ (порядок по имени файла)."""
    pdir = project_dir(name)
    meta = read_json(pdir / "project.json")
    if meta is None and (pdir / "request.json").exists():
        raise FileNotFoundError("Проект ещё создаётся: %s" % name)
    if meta is None:
        slides = sorted((pdir / "slides").glob("*.html"))
        meta = {
            "id": re.sub(r"\W+", "-", name.lower()).strip("-"),
            "name": name,
            "created_at": now(),
            "updated_at": now(),
            "guideline_set_id": "",
            "slides": [
                {"id": s.stem, "file": "slides/" + s.name, "title": _slide_title(s), "order": i + 1, "hidden": False}
                for i, s in enumerate(slides)
            ],
        }
        write_json(pdir / "project.json", meta)
    return meta


def update_project(name, mutate):
    """Чтение-изменение-запись project.json под блокировкой."""
    pdir = project_dir(name)
    with FileLock(pdir / ".project.lock"):
        meta = load_project(name)
        mutate(meta)
        meta["updated_at"] = now()
        write_json(pdir / "project.json", meta)
    return meta


def slide_path(name, slide_id):
    meta = load_project(name)
    for s in meta["slides"]:
        if s["id"] == slide_id:
            return inside(project_dir(name), project_dir(name) / s["file"])
    raise KeyError("Слайд не найден: %s" % slide_id)


# ---------- итерации правок ----------

def edits_dir(name):
    d = project_dir(name) / "edits"
    d.mkdir(exist_ok=True)
    return d


def edit_versions(name):
    return sorted(int(m.group(1)) for f in edits_dir(name).iterdir()
                  for m in [re.fullmatch(r"edits-v(\d+)\.json", f.name)] if m)


def create_edits(name, items):
    """Новая итерация edits-vN.json. N выдаётся под блокировкой. После создания файл не меняется."""
    if not items:
        raise ValueError("Нет правок для сохранения")
    meta = load_project(name)
    known = {s["id"] for s in meta["slides"]}
    clean = []
    for i, it in enumerate(items, 1):
        sid = it.get("slide_id")
        if it.get("scope", "slide") == "slide" and sid not in known:
            raise ValueError("Неизвестный слайд: %s" % sid)
        comment = str(it.get("comment", "")).strip()
        if not comment:
            raise ValueError("Пустой комментарий")
        sel = it.get("selection") or None
        if sel:
            sel = {k: str(sel.get(k, ""))[:2000] for k in ("text", "prefix", "suffix")}
            if not sel["text"].strip():
                sel = None
        clean.append({
            "id": "edit-%03d" % i,
            "slide_id": sid,
            "scope": it.get("scope", "slide") if it.get("scope") in ("slide", "presentation") else "slide",
            "type": "selection" if sel else "comment",
            "comment": comment[:4000],
            "selection": sel,
        })
    d = edits_dir(name)
    with FileLock(d / ".counter.lock"):
        n = (edit_versions(name) or [0])[-1] + 1
        data = {"id": "%s-edits-v%d" % (meta["id"], n), "project": name, "version": n,
                "created_at": now(), "items": clean}
        write_json(d / ("edits-v%d.json" % n), data)
    return data


def edits_file(name, n):
    return edits_dir(name) / ("edits-v%d.json" % n)


def result_file(name, n):
    return edits_dir(name) / ("edits-v%d.result.json" % n)


def lock_file(name, n):
    return edits_dir(name) / ("edits-v%d.lock" % n)


def iteration_state(name, n):
    """Статус итерации выводится из файлов, а не хранится в одном поле."""
    res = read_json(result_file(name, n))
    lock = read_json(lock_file(name, n))
    if res and res.get("status") in FINAL:
        return res["status"]
    if lock:
        hb = (read_json(agent_status_file(name)) or {}).get("updated_at") or lock.get("claimed_at")
        return "stalled" if age_seconds(hb) > STALE_SECONDS else "in_progress"
    return "queued"


def iteration_summary(name, n):
    edits = read_json(edits_file(name, n)) or {}
    res = read_json(result_file(name, n)) or {}
    results = {r["id"]: r for r in res.get("items", [])}
    reverts = read_json(reverts_file(name, n)) or {}
    items = []
    for it in edits.get("items", []):
        r = results.get(it["id"], {})
        ch = read_json(change_file(name, n, it["id"])) or {}
        items.append(dict(it, result=r.get("status", "pending"), note=r.get("note", ""),
                          revertable=bool(ch.get("files")),
                          reverted=bool(reverts.get(it["id"], {}).get("reverted"))))
    return {
        "version": n,
        "created_at": edits.get("created_at"),
        "finished_at": res.get("finished_at"),
        "status": iteration_state(name, n),
        "items": items,
    }


def snapshot(name, n, slide_ids, with_css):
    """Копия затронутых файлов в history/vN/ до правок. Повторно не перезаписывает."""
    pdir = project_dir(name)
    dst = pdir / "history" / ("v%d" % n)
    files = [slide_path(name, sid) for sid in slide_ids]
    if with_css and (pdir / "styles.css").exists():
        files.append(pdir / "styles.css")
    saved = []
    for f in files:
        rel = f.relative_to(pdir)
        target = dst / rel
        if not target.exists():
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(f, target)
        saved.append(str(rel))
    return saved


# ---------- изменения правок: откат и возврат на уровне слайда ----------
# Перед правкой агент вызывает begin → копия файлов в history/vN/<edit>/before/.
# После правки (item done/partial) считаются хунки «было → стало» с одной строкой контекста.
# Откат = в ТЕКУЩЕМ файле заменить «стало» на «было» (поздние правки других мест не трогаются),
# возврат — наоборот. Не нашли фрагмент однозначно → конфликт, файлы не меняются.

def reverts_file(name, n):
    return edits_dir(name) / ("edits-v%d.reverts.json" % n)


def change_file(name, n, edit_id):
    return project_dir(name) / "history" / ("v%d" % n) / (edit_id + ".json")


def _item(name, n, edit_id):
    edits = read_json(edits_file(name, n)) or {}
    it = next((i for i in edits.get("items", []) if i["id"] == edit_id), None)
    if not it:
        raise KeyError("Нет правки %s" % edit_id)
    return it


def item_files(name, it):
    """Файлы, которые правка вправе менять."""
    pdir = project_dir(name)
    if it.get("scope") == "presentation":
        meta = load_project(name)
        files = [pdir / s["file"] for s in meta["slides"]]
        if (pdir / "styles.css").exists():
            files.append(pdir / "styles.css")
        return files
    return [slide_path(name, it["slide_id"])]


def begin_item(name, n, edit_id):
    it = _item(name, n, edit_id)
    pdir = project_dir(name)
    base = pdir / "history" / ("v%d" % n) / edit_id / "before"
    for f in item_files(name, it):
        target = base / f.relative_to(pdir)
        if not target.exists():  # повторный begin (после сбоя) не затирает исходное состояние
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(f, target)
    return base


def _hunks(before, after):
    import difflib
    a, b = before.splitlines(True), after.splitlines(True)
    out = []
    for tag, i1, i2, j1, j2 in difflib.SequenceMatcher(None, a, b, autojunk=False).get_opcodes():
        if tag == "equal":
            continue
        lo_a, hi_a = max(i1 - 1, 0), min(i2 + 1, len(a))
        lo_b, hi_b = max(j1 - 1, 0), min(j2 + 1, len(b))
        whole = lo_a == 0 and hi_a == len(a) and lo_b == 0 and hi_b == len(b)
        out.append({"before": "".join(a[lo_a:hi_a]), "after": "".join(b[lo_b:hi_b]), "whole": whole})
    return out


def record_item_change(name, n, edit_id):
    """Сравнить снимок before с текущими файлами и сохранить хунки правки."""
    pdir = project_dir(name)
    base = pdir / "history" / ("v%d" % n) / edit_id / "before"
    if not base.is_dir():
        raise RuntimeError("Перед правкой %s не вызван begin" % edit_id)
    files = {}
    for f in sorted(base.rglob("*")):
        if f.is_file():
            rel = str(f.relative_to(base))
            cur = pdir / rel
            before = f.read_text(encoding="utf-8")
            after = cur.read_text(encoding="utf-8") if cur.exists() else ""
            if before != after:
                files[rel] = _hunks(before, after)
    write_json(change_file(name, n, edit_id), {"edit_id": edit_id, "recorded_at": now(), "files": files})
    return files


def set_slide_reverted(name, n, slide_id, revert):
    """revert=True — откатить все правки слайда в задании vN, False — вернуть. Атомарно по всем файлам."""
    if any(iteration_state(name, v) in ("in_progress", "stalled") for v in edit_versions(name)):
        raise RuntimeError("Агент вносит правки — дождись окончания")
    if iteration_state(name, n) not in FINAL:
        raise RuntimeError("Задание v%d ещё не выполнено" % n)
    items = [i for i in (read_json(edits_file(name, n)) or {}).get("items", []) if i.get("slide_id") == slide_id]
    changes = {i["id"]: read_json(change_file(name, n, i["id"])) or {} for i in items}
    items = [i for i in items if changes[i["id"]].get("files")]
    if not items:
        raise RuntimeError("У этого слайда нет правок, которые можно откатить")
    pdir = project_dir(name)
    with FileLock(pdir / ".project.lock"):
        reverts = read_json(reverts_file(name, n)) or {}
        todo = [i for i in items if bool(reverts.get(i["id"], {}).get("reverted")) != revert]
        if not todo:
            return
        if revert:  # откатываем с последней правки к первой, возвращаем в прямом порядке
            todo.reverse()
        texts = {}
        for it in todo:
            for rel, hunks in changes[it["id"]]["files"].items():
                path = inside(pdir, pdir / rel)
                text = texts[path] if path in texts else path.read_text(encoding="utf-8")
                for h in hunks:
                    old, new = (h["after"], h["before"]) if revert else (h["before"], h["after"])
                    if h.get("whole"):  # правка заменила файл целиком — меняем, только если он с тех пор не трогался
                        if text != old:
                            raise RuntimeError("Конфликт: %s изменён после этой правки" % rel)
                        text = new
                        continue
                    if old == "":
                        raise RuntimeError("Не удалось однозначно найти место правки в %s" % rel)
                    count = text.count(old)
                    if count != 1:
                        raise RuntimeError("Конфликт: фрагмент в %s уже изменён другой правкой" % rel
                                           if count == 0 else "Фрагмент в %s встречается несколько раз" % rel)
                    text = text.replace(old, new, 1)
                texts[path] = text
        for path, text in texts.items():  # пишем только если все хунки сошлись
            tmp = path.with_name("." + path.name + ".tmp")
            tmp.write_text(text, encoding="utf-8")
            os.replace(tmp, path)
        for it in todo:
            reverts[it["id"]] = {"reverted": revert, "at": now()}
        write_json(reverts_file(name, n), reverts)


# ---------- статус агента ----------
# У каждого проекта свой heartbeat правок: Проекты/<p>/agent-status.json.
# Общий статус для шапки вычисляется по всем задачам (правки + сборка гайдов + создание проектов).

def agent_status_file(name):
    return project_dir(name) / "agent-status.json"


def set_agent_status(name, status, message="", task=None, error=None):
    write_json(agent_status_file(name), {"status": status, "current_task_id": task, "message": message,
                                         "updated_at": now(), "last_error": error})


def agent_status():
    """working > waiting_user > stalled > idle; offline — агент ещё ни разу не запускался."""
    from . import tasks
    seen, working, waiting, stalled = False, [], [], []
    for p in list_projects():
        st = read_json(PROJECTS / p / "agent-status.json")
        if st:
            seen = True
            if st.get("status") == "in_progress":
                (stalled if age_seconds(st.get("updated_at")) > STALE_SECONDS else working).append("Правки: " + p)
    for kind in tasks.KINDS:
        for n in tasks.list_tasks(kind):
            lc = tasks.view(kind, n)
            if lc["state"] != "waiting_agent":
                seen = True
            label = ("Гайд: " if kind == "guide" else "Проект: ") + n
            if lc["state"] == "working":
                working.append(label)
            elif lc["state"] == "waiting_user":
                waiting.append(label)
            elif lc["state"] == "stalled":
                stalled.append(label)
    if working:
        return {"status": "in_progress", "message": "; ".join(working), "tasks": working}
    if waiting:
        return {"status": "waiting_user", "message": "Ждёт ответа в чате: " + "; ".join(waiting), "tasks": waiting}
    if stalled:
        return {"status": "stalled", "message": "Агент не отвечает: " + "; ".join(stalled), "tasks": stalled}
    return {"status": "idle" if seen else "offline", "message": "", "tasks": []}


# ---------- выбор файлов/папок (диалоги macOS) ----------
# Пути живут только на сервере; браузер оперирует токеном. Токены на диске, живут сутки.
PICKS = STATE / "picks.json"
PICK_TTL = 24 * 3600


def move_to_trash(path):
    """Папку — в Корзину macOS (~/.Trash), имя не затирает то, что там уже лежит."""
    path = Path(path)
    trash = Path(os.environ.get("PD_TRASH") or Path.home() / ".Trash")
    trash.mkdir(exist_ok=True)
    target = trash / path.name
    if target.exists():
        target = trash / ("%s %s" % (path.name, time.strftime("%H-%M-%S")))
    i = 2
    while target.exists():
        target = trash / ("%s %s %d" % (path.name, time.strftime("%H-%M-%S"), i))
        i += 1
    shutil.move(str(path), str(target))
    return target


def save_pick(paths, kind):
    import uuid
    STATE.mkdir(exist_ok=True)
    with FileLock(STATE / ".picks.lock"):
        picks = {t: p for t, p in (read_json(PICKS) or {}).items() if age_seconds(p.get("at")) < PICK_TTL}
        token = uuid.uuid4().hex
        picks[token] = {"paths": [str(p) for p in paths], "kind": kind, "at": now()}
        write_json(PICKS, picks)
    return token


def get_pick(token):
    p = (read_json(PICKS) or {}).get(token or "")
    if not p or age_seconds(p.get("at")) > PICK_TTL:
        raise ValueError("Выбор файлов устарел — выбери ещё раз")
    return p
