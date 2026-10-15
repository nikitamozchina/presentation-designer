#!/usr/bin/env python3
# Установка «Дизайнера презентаций» для ИИ-агента: Claude Code (/presentations) или Codex ($slides).
#   python3 app/tools/install.py --agent claude|codex [--pack <папка>]   установить / обновить (повторный запуск безопасен)
#   python3 app/tools/install.py --uninstall                              убрать команды, разрешения и запись об установке (данные не трогает)
# Без --agent агент угадывается по окружению (CODEX_* → codex), иначе claude.
# Пакет (--pack) — отдельно распространяемая папка с подпапками «Гайдлайны/» и, по желанию, «Проекты/».
import json, os, re, shutil, stat, sys, time
from pathlib import Path

APP = Path(__file__).resolve().parents[2]
HOME = Path(os.environ.get("PD_HOME") or Path.home())  # PD_HOME — для тестов
INSTALL = APP / ".state" / "install.json"                # что и куда поставлено (для обновления и удаления)
LEGACY_CONFIG = HOME / ".config" / "presentation-designer" / "config.json"
SCRIPTS = ("agent", "launch", "extract", "convert", "check_slides")

CLAUDE = HOME / ".claude"
CLAUDE_SKILL = CLAUDE / "skills" / "presentations"
SETTINGS = CLAUDE / "settings.json"

CODEX = HOME / ".codex"
CODEX_SKILL = HOME / ".agents" / "skills" / "slides"
CODEX_RULES = CODEX / "rules" / "presentation-designer.rules"
CODEX_CONFIG = CODEX / "config.toml"

AGENTS = {"claude": {"name": "Claude Code", "cmd": "/presentations", "skill": "presentations", "dir": CLAUDE_SKILL},
          "codex": {"name": "Codex", "cmd": "$slides", "skill": "slides", "dir": CODEX_SKILL}}

sys.path.insert(0, str(APP / "app"))
from core import exporter, store  # noqa: E402


def say(icon, text):
    print("%s %s" % (icon, text))


def backup(path):
    if path.exists():
        dst = path.with_name("%s.bak-%s" % (path.name, time.strftime("%Y%m%d-%H%M%S")))
        shutil.copy2(path, dst)
        say("•", "Бэкап: %s" % dst)


def detect_agent(args):
    if "--agent" in args:
        i = args.index("--agent")
        agent = args[i + 1] if i + 1 < len(args) else ""
        if agent not in AGENTS:
            sys.exit("✗ --agent: claude или codex")
        return agent
    return "codex" if any(k.startswith("CODEX_") for k in os.environ) else "claude"


# ---------- Claude Code: разрешения в ~/.claude/settings.json ----------
def claude_rules():
    a = str(APP)
    return ["Read(/%s/**)" % a,            # «//абсолютный путь» в синтаксисе Claude Code
            "Edit(/%s/Проекты/**)" % a,
            "Edit(/%s/Гайдлайны/**)" % a] + ["Bash(python3 %s/app/tools/%s.py *)" % (a, s) for s in SCRIPTS]


def load_settings():
    if not SETTINGS.exists():
        return {}
    try:
        return json.loads(SETTINGS.read_text(encoding="utf-8"))
    except ValueError:
        sys.exit("✗ %s повреждён (не JSON) — исправь его и запусти установку снова" % SETTINGS)


def claude_permissions(prev):
    settings = load_settings()
    allow = settings.setdefault("permissions", {}).setdefault("allow", [])
    stale = [r for r in prev.get("added_rules", []) if r not in claude_rules()]  # путь установки сменился
    added = [r for r in claude_rules() if r not in allow]
    if added or stale:
        settings["permissions"]["allow"] = [r for r in allow if r not in stale] + added
        backup(SETTINGS)
        store.write_json(SETTINGS, settings)
        say("✓", "Разрешения для папки приложения добавлены в %s" % SETTINGS)
    else:
        say("•", "Разрешения уже настроены")
    return {"added_rules": sorted(set(r for r in prev.get("added_rules", []) if r in claude_rules()) | set(added))}


def claude_remove(rec):
    owned = rec.get("added_rules", [])
    if owned and SETTINGS.exists():
        settings = load_settings()
        allow = settings.get("permissions", {}).get("allow", [])
        if any(r in allow for r in owned):
            settings["permissions"]["allow"] = [r for r in allow if r not in owned]
            backup(SETTINGS)
            store.write_json(SETTINGS, settings)
            say("✓", "Разрешения приложения убраны из %s" % SETTINGS)


# ---------- Codex: правила (~/.codex/rules) и запись в папку приложения (config.toml) ----------
def codex_rules_text():
    rules = ["# Дизайнер презентаций: служебные скрипты выполняются вне песочницы без вопросов. Создано install.py."]
    for s in SCRIPTS:
        rules.append('prefix_rule(\n    pattern = ["python3", %s],\n    decision = "allow",\n    justification = "Дизайнер презентаций: %s.py",\n)'
                     % (json.dumps("%s/app/tools/%s.py" % (APP, s), ensure_ascii=False), s))
    return "\n".join(rules) + "\n"


TABLE = "[sandbox_workspace_write]"


def toml_add_root(text, root):
    """Добавить root в sandbox_workspace_write.writable_roots. → (новый текст, создана ли таблица) или None, если структура непонятна."""
    lines = text.splitlines()
    heads = [i for i, l in enumerate(lines) if l.strip() == TABLE]
    if len(heads) > 1 or any("sandbox_workspace_write" in l for i, l in enumerate(lines) if i not in heads):
        return None  # dotted-ключи или дубли таблицы — руками надёжнее
    if not heads:
        tail = "" if not text or text.endswith("\n") else "\n"
        return text + tail + "\n%s\nwritable_roots = %s\n" % (TABLE, json.dumps([root], ensure_ascii=False)), True
    start = heads[0] + 1
    end = next((i for i in range(start, len(lines)) if lines[i].lstrip().startswith("[")), len(lines))
    for i in range(start, end):
        m = re.match(r"\s*writable_roots\s*=\s*(.*)$", lines[i])
        if m:
            try:
                roots = json.loads(m.group(1))
            except ValueError:
                return None  # многострочный массив или литеральные строки
            if root not in roots:
                lines[i] = "writable_roots = %s" % json.dumps(roots + [root], ensure_ascii=False)
            return "\n".join(lines) + "\n", False
    lines.insert(start, "writable_roots = %s" % json.dumps([root], ensure_ascii=False))
    return "\n".join(lines) + "\n", False


def toml_remove_root(text, root, created):
    lines = text.splitlines()
    out, i = [], 0
    while i < len(lines):
        l = lines[i]
        m = re.match(r"\s*writable_roots\s*=\s*(.*)$", l)
        if m:
            try:
                roots = json.loads(m.group(1))
            except ValueError:
                roots = None
            if roots is not None and root in roots:
                roots = [r for r in roots if r != root]
                if not roots and created and out and out[-1].strip() == TABLE:
                    out.pop()  # таблицу создавали мы, и в ней больше ничего — убираем целиком
                    while out and not out[-1].strip():
                        out.pop()
                    i += 1
                    continue
                l = "writable_roots = %s" % json.dumps(roots, ensure_ascii=False)
        out.append(l)
        i += 1
    return "\n".join(out) + "\n"


def toml_valid(text, root):
    try:
        import tomllib  # Python 3.11+; на 3.9 проверка пропускается
    except ImportError:
        return True
    try:
        roots = tomllib.loads(text).get("sandbox_workspace_write", {}).get("writable_roots", [])
    except Exception:  # noqa: BLE001 — любой сбой разбора = не пишем
        return False
    return root is None or root in roots


def codex_permissions(prev):
    CODEX_RULES.parent.mkdir(parents=True, exist_ok=True)
    CODEX_RULES.write_text(codex_rules_text(), encoding="utf-8")
    say("✓", "Правила для скриптов приложения: %s" % CODEX_RULES)
    rec = {"rules": str(CODEX_RULES), "root": prev.get("root"), "created_table": prev.get("created_table", False)}
    root = str(APP)
    text = CODEX_CONFIG.read_text(encoding="utf-8") if CODEX_CONFIG.exists() else ""
    if prev.get("root") and prev["root"] != root:  # приложение переехало — старый путь убираем
        text = toml_remove_root(text, prev["root"], False)
    res = toml_add_root(text, root)
    manual = "Добавь в %s вручную:\n  %s\n  writable_roots = [\"%s\"]" % (CODEX_CONFIG, TABLE, root)
    if res is None or not toml_valid(res[0], root):
        say("!", "Не смог безопасно поправить config.toml — без этого Codex будет спрашивать разрешение на правки слайдов. " + manual)
        return rec
    new, created = res
    if new != (CODEX_CONFIG.read_text(encoding="utf-8") if CODEX_CONFIG.exists() else ""):
        backup(CODEX_CONFIG)
        CODEX_CONFIG.parent.mkdir(parents=True, exist_ok=True)
        CODEX_CONFIG.write_text(new, encoding="utf-8")
        say("✓", "Папка приложения разрешена для записи: %s" % CODEX_CONFIG)
    else:
        say("•", "Запись в папку приложения уже разрешена")
    rec.update(root=root, created_table=created or rec["created_table"])
    return rec


def codex_remove(rec):
    if CODEX_RULES.exists():
        CODEX_RULES.unlink()
        say("✓", "Правила Codex удалены")
    if rec.get("root") and CODEX_CONFIG.exists():
        text = CODEX_CONFIG.read_text(encoding="utf-8")
        new = toml_remove_root(text, rec["root"], rec.get("created_table"))
        if new != text and toml_valid(new, None):
            backup(CODEX_CONFIG)
            CODEX_CONFIG.write_text(new, encoding="utf-8")
            say("✓", "Папка приложения убрана из %s" % CODEX_CONFIG)


# ---------- общее ----------
def copy_pack(pack):
    pack = Path(pack).expanduser().resolve()
    if not pack.is_dir():
        sys.exit("✗ Папка пакета не найдена: %s" % pack)
    copied = 0
    for sub in ("Гайдлайны", "Проекты"):
        src = pack / sub
        if not src.is_dir():
            continue
        (APP / sub).mkdir(exist_ok=True)
        for item in sorted(src.iterdir()):
            if item.is_dir() and not item.name.startswith("."):
                dst = APP / sub / item.name
                if dst.exists():
                    say("•", "%s/%s уже есть — пропускаю" % (sub, item.name))
                else:
                    shutil.copytree(item, dst)
                    say("✓", "Скопировано: %s/%s" % (sub, item.name))
                    copied += 1
    if not copied and not any((APP / "Гайдлайны").glob("*/guide.json")):
        sys.exit("✗ В пакете нет папки «Гайдлайны» с guide.json")


def load_install():
    data = store.read_json(INSTALL)
    if data is None:  # установка до версии с Codex: запись лежала в ~/.config
        old = store.read_json(LEGACY_CONFIG) or {}
        data = {"agents": {"claude": {"added_rules": old["added_rules"]}}} if old.get("app_dir") == str(APP) else {}
    data.setdefault("agents", {})
    return data


def checks():
    if sys.version_info < (3, 9):
        sys.exit("✗ Нужен Python 3.9+, сейчас %s" % sys.version.split()[0])
    say("✓", "Python %s" % sys.version.split()[0])
    if os.environ.get("PD_INSTALL_NO_APPS"):  # тесты: без Chrome и Keynote
        return
    if exporter.find_chrome():
        say("✓", "Google Chrome найден")
    else:
        say("!", "Google Chrome не найден — экспорт не заработает, пока его не установить")
    import subprocess
    r = subprocess.run([sys.executable, str(APP / "app" / "tools" / "convert.py"), "--check"], capture_output=True, text=True)
    try:
        kn = json.loads(r.stdout)
    except ValueError:
        kn = {"ok": False, "error": r.stderr.strip() or "нет ответа"}
    if not kn.get("ok"):
        sys.exit("✗ Keynote обязателен: %s" % kn.get("error"))
    say("✓", "Keynote отвечает (разрешение на управление есть)")


def install(agent, pack):
    a = AGENTS[agent]
    say("•", "Агент: %s" % a["name"])
    checks()
    home = CLAUDE if agent == "claude" else CODEX
    if not home.is_dir():
        say("!", "Папка %s не найдена — проверь, что %s установлен" % (home, a["name"]))

    for d in ("Проекты", "Экспорт", "Гайдлайны", ".state"):
        (APP / d).mkdir(exist_ok=True)
    if pack:
        copy_pack(pack)
    if not any((APP / "Гайдлайны").glob("*/guide.json")):
        say("!", "Гайдлайнов нет — первый собери в приложении («Новый гайдлайн») или поставь пакет: --pack <папка>")
    for f in ("start.command", "stop.command"):
        p = APP / f
        if p.exists():
            p.chmod(p.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)

    tpl = (APP / "app" / "tools" / "skill_template.md").read_text(encoding="utf-8")
    a["dir"].mkdir(parents=True, exist_ok=True)
    (a["dir"] / "SKILL.md").write_text(tpl.replace("{{APP}}", str(APP)).replace("{{SKILL}}", a["skill"])
                                       .replace("{{CMD}}", a["cmd"]), encoding="utf-8")
    say("✓", "Команда %s: %s" % (a["cmd"], a["dir"] / "SKILL.md"))

    data = load_install()
    prev = data["agents"].get(agent, {})
    data["agents"][agent] = claude_permissions(prev) if agent == "claude" else codex_permissions(prev)
    data.update(app_dir=str(APP), python=sys.executable, installed_at=store.now())
    store.write_json(INSTALL, data)
    if LEGACY_CONFIG.exists() and (store.read_json(LEGACY_CONFIG) or {}).get("app_dir") == str(APP):
        LEGACY_CONFIG.unlink()  # запись переехала в .state/install.json
    say("✓", "Готово. В %s (из любой папки): %s — открыть приложение, %s правки — внести правки, %s стоп — остановить"
        % (a["name"], a["cmd"], a["cmd"], a["cmd"]))
    if agent == "claude":
        say("•", "Если команда не появилась — перезапусти Claude Code")
    else:
        say("•", "Если команда не появилась — начни новую сессию Codex")


def uninstall():
    data = load_install()
    for agent, rec in data["agents"].items():
        d = AGENTS[agent]["dir"]
        if d.exists():
            shutil.rmtree(d)
            say("✓", "Команда %s удалена" % AGENTS[agent]["cmd"])
        claude_remove(rec) if agent == "claude" else codex_remove(rec)
    if INSTALL.exists():
        INSTALL.unlink()
    if LEGACY_CONFIG.exists() and (store.read_json(LEGACY_CONFIG) or {}).get("app_dir") == str(APP):
        LEGACY_CONFIG.unlink()
    say("•", "Проекты, гайдлайны и экспорт в %s не тронуты" % APP)


if __name__ == "__main__":
    args = sys.argv[1:]
    if "--uninstall" in args:
        uninstall()
    else:
        pack = args[args.index("--pack") + 1] if "--pack" in args and len(args) > args.index("--pack") + 1 else None
        install(detect_agent(args), pack)
