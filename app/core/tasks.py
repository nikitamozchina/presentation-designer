# Задачи агента «собрать гайдлайн» и «создать проект»: заявка → захват → этапы → готово.
# Папка задачи: Гайдлайны/<имя>/ или Проекты/<имя>/.
#   request.json    — заявка (пишет приложение, после создания не меняется)
#   lifecycle.json  — состояние (пишет только agent.py; приложение — только отмену)
#   .claim.lock     — захват задачи агентом (O_EXCL)
#   brief/interview.md — журнал вопросов и ответов (дописывается после каждого ответа)
import os, re, shutil, threading, uuid
from datetime import date
from pathlib import Path

from . import store

KINDS = ("guide", "project")
# working — агент работает (stage: analysis / interview / structure / generating / self_check / building)
STATES = ("waiting_agent", "working", "waiting_user", "ready", "failed", "cancelled")
FINAL_STATES = ("ready", "failed", "cancelled")
STAGES = ("analysis", "interview", "structure", "generating", "self_check", "building", "copying")
BAD_CHARS = re.compile(r'[\\/:*?"<>|\x00-\x1f]')


def base(kind):
    return {"guide": store.GUIDES, "project": store.PROJECTS}[kind]


def task_dir(kind, name):
    if kind not in KINDS:
        raise ValueError("Неизвестный тип задачи: %s" % kind)
    if not name or "/" in name or name.startswith("."):
        raise ValueError("Некорректное имя")
    p = store.inside(base(kind), base(kind) / name)
    if not p.is_dir():
        raise FileNotFoundError("Не найдено: %s" % name)
    return p


def clean_name(text, limit=80):
    """Имя папки: без / \\ : * ? " < > |, без ведущих точек, не длиннее limit."""
    name = BAD_CHARS.sub(" ", str(text or ""))
    name = re.sub(r"\s+", " ", name).strip().lstrip(".").strip()
    return name[:limit].rstrip()


def unique_name(kind, title, with_date):
    """«Тема — 2026-10-12», при совпадении — «Тема — 2026-10-12 2», «… 3»."""
    stem = clean_name(title, 64)
    if not stem:
        raise ValueError("Название не может быть пустым")
    if with_date:
        stem = "%s — %s" % (stem, date.today().isoformat())
    root = base(kind)
    root.mkdir(parents=True, exist_ok=True)
    taken = {p.name.lower() for p in root.iterdir()}
    if stem.lower() not in taken:
        return stem
    i = 2
    while ("%s %d" % (stem, i)).lower() in taken:
        i += 1
    return "%s %d" % (stem, i)


def create(kind, title, request, with_date, make_extra=None):
    """Атомарно: всё собирается во временной папке и появляется одним rename."""
    root = base(kind)
    root.mkdir(parents=True, exist_ok=True)
    with store.FileLock(root / ".create.lock"):
        name = unique_name(kind, title, with_date)
        tmp = root / (".creating-" + uuid.uuid4().hex[:8])
        tmp.mkdir()
        try:
            req = dict(request, kind=kind, name=name, created_at=store.now())
            store.write_json(tmp / "request.json", req)
            store.write_json(tmp / "lifecycle.json", {
                "state": "waiting_agent", "stage": None, "message": "Ждёт агента",
                "question": None, "progress": None, "updated_at": store.now(),
                "sources_copied": not request.get("sources"), "history": []})
            if make_extra:
                make_extra(tmp, name)
            os.rename(tmp, root / name)
        except BaseException:
            shutil.rmtree(tmp, ignore_errors=True)
            raise
    return name


def request(kind, name):
    return store.read_json(task_dir(kind, name) / "request.json")


def lifecycle(kind, name):
    return store.read_json(task_dir(kind, name) / "lifecycle.json")


def view(kind, name):
    """Состояние для UI: working без свежего heartbeat → stalled."""
    lc = lifecycle(kind, name)
    if lc is None:
        return None
    lc = dict(lc)
    lc.pop("history", None)
    if lc["state"] == "working" and store.age_seconds(lc.get("updated_at")) > store.STALE_SECONDS:
        lc["state"] = "stalled"
        lc["message"] = "Агент не отвечает — отправь команду ещё раз, он продолжит"
    lc["commands"] = store.agent_commands({"guide": "гайд", "project": "создать"}[kind])
    return lc


def set_state(kind, name, state, stage=None, message="", question=None, progress=None, **extra):
    """Переход состояния. Пишет agent.py (и приложение — только для отмены/копирования)."""
    if state not in STATES:
        raise ValueError("Неизвестное состояние: %s" % state)
    if stage is not None and stage not in STAGES:
        raise ValueError("Неизвестный этап: %s" % stage)
    d = task_dir(kind, name)
    with store.FileLock(d / ".lifecycle.lock"):
        lc = store.read_json(d / "lifecycle.json") or {}
        if lc.get("state") == "cancelled" and state != "cancelled":
            raise RuntimeError("Задача отменена пользователем")
        lc.setdefault("history", []).append({"at": store.now(), "state": state, "stage": stage, "message": message})
        lc["history"] = lc["history"][-200:]
        lc.update(state=state, stage=stage, message=message, question=question,
                  progress=progress, updated_at=store.now(), **extra)
        store.write_json(d / "lifecycle.json", lc)
    if state in FINAL_STATES:
        try:
            (d / ".claim.lock").unlink()
        except FileNotFoundError:
            pass
    return lc


def heartbeat(kind, name, message=None):
    d = task_dir(kind, name)
    with store.FileLock(d / ".lifecycle.lock"):
        lc = store.read_json(d / "lifecycle.json") or {}
        if lc.get("state") == "cancelled":
            raise RuntimeError("Задача отменена пользователем")
        lc["updated_at"] = store.now()
        if message:
            lc["message"] = message
        store.write_json(d / "lifecycle.json", lc)


def list_tasks(kind):
    out = []
    root = base(kind)
    if not root.is_dir():
        return out
    for p in sorted(root.iterdir()):
        if p.is_dir() and not p.name.startswith(".") and (p / "lifecycle.json").exists():
            out.append(p.name)
    return out


def claim(kind, resume=None):
    """Сначала незавершённые (захваченные, но брошенные/ждущие ответа), потом самые старые новые."""
    names = list_tasks(kind)
    if resume:
        names = [n for n in names if n == resume]
        if not names:
            raise FileNotFoundError("Задача не найдена: %s" % resume)
    unfinished, fresh = [], []
    for n in names:
        lc = view(kind, n)
        if lc["state"] in ("stalled", "waiting_user") or (lc["state"] == "working" and resume):
            unfinished.append(n)
        elif lc["state"] == "waiting_agent":
            if not lc.get("sources_copied", True):
                continue  # исходники ещё копируются
            fresh.append((request(kind, n).get("created_at") or "", n))
        elif lc["state"] == "working" and not resume:
            continue  # над ней работает другая сессия
    for n in unfinished:
        return n, True
    for _, n in sorted(fresh):
        lock = task_dir(kind, n) / ".claim.lock"
        try:
            fd = os.open(str(lock), os.O_CREAT | os.O_EXCL | os.O_WRONLY)
        except FileExistsError:
            continue
        os.write(fd, store.now().encode())
        os.close(fd)
        return n, False
    return None, False


def cancel(kind, name):
    lc = lifecycle(kind, name) or {}
    if lc.get("state") in FINAL_STATES:
        raise RuntimeError("Задача уже завершена")
    return set_state(kind, name, "cancelled", message="Отменено пользователем")


def log_interview(kind, name, who, text):
    """Журнал интервью: дописывается сразу, чтобы ответы не потерялись при обрыве сессии."""
    f = task_dir(kind, name) / "brief" / "interview.md"
    f.parent.mkdir(exist_ok=True)
    with open(f, "a", encoding="utf-8") as fh:
        fh.write("\n**%s** (%s):\n%s\n" % ({"agent": "Агент", "user": "Пользователь"}.get(who, who),
                                          store.now(), text.strip()))
    return f


# ---------- копирование исходников ----------

_copying = set()
_copy_lock = threading.Lock()


def _copy_one(src, dst):
    src, dst = Path(src), Path(dst)
    if src.is_dir():
        for f in src.rglob("*"):
            if f.is_file() and not any(part.startswith(".") for part in f.relative_to(src).parts):
                _copy_one(f, dst / f.relative_to(src))
        return
    if dst.exists() and dst.stat().st_size == src.stat().st_size:
        return  # уже скопирован (повтор после перезапуска)
    dst.parent.mkdir(parents=True, exist_ok=True)
    tmp = dst.with_name("." + dst.name + ".part")
    shutil.copy2(src, tmp)
    os.replace(tmp, dst)


def copy_sources(kind, name, log=print):
    """Копирует request.sources в <задача>/Исходники/. Идемпотентно, можно перезапускать."""
    key = (kind, name)
    with _copy_lock:
        if key in _copying:
            return
        _copying.add(key)
    try:
        d = task_dir(kind, name)
        req = request(kind, name)
        dst_root = d / "Исходники"
        sources = req.get("sources") or []
        for i, s in enumerate(sources, 1):
            src = Path(s["path"])
            if not src.exists():
                raise FileNotFoundError("Исходник не найден: %s" % src)
            set_state(kind, name, "waiting_agent", stage="copying",
                      message="Копирую исходники: %d из %d" % (i, len(sources)),
                      progress={"done": i - 1, "total": len(sources)}, sources_copied=False)
            if s.get("only"):  # из проекта-основы берём только слайды и оформление
                for part in s["only"]:
                    if (src / part).exists():
                        _copy_one(src / part, dst_root / s["as"] / part)
            else:
                _copy_one(src, dst_root / s["as"])
        set_state(kind, name, "waiting_agent", message="Ждёт агента", sources_copied=True)
    except RuntimeError:
        pass  # отменено пользователем
    except Exception as e:
        log("copy_sources %s/%s: %r" % (kind, name, e))
        try:
            set_state(kind, name, "failed", message="Не удалось скопировать исходники: %s" % e)
        except RuntimeError:
            pass
    finally:
        with _copy_lock:
            _copying.discard(key)


def pending_copies():
    out = []
    for kind in KINDS:
        for n in list_tasks(kind):
            lc = lifecycle(kind, n) or {}
            if lc.get("state") == "waiting_agent" and not lc.get("sources_copied", True):
                out.append((kind, n))
    return out


def source_entries(paths):
    """Пути из диалога → [{path, as}] с уникальными именами внутри «Исходники/»."""
    entries, used = [], set()
    for p in paths:
        p = Path(p)
        nm = clean_name(p.name, 120) or "файл"
        cand, i = nm, 2
        while cand.lower() in used:
            stem, suf = (p.stem, p.suffix) if p.is_file() else (nm, "")
            cand = "%s %d%s" % (clean_name(stem, 110), i, suf)
            i += 1
        used.add(cand.lower())
        entries.append({"path": str(p), "as": cand, "is_dir": p.is_dir()})
    return entries
