#!/usr/bin/env python3
# Установка «Дизайнера презентаций» для Claude Code: глобальная команда /presentations.
#   python3 app/tools/install.py [--pack <папка>]   установить / обновить (повторный запуск безопасен)
#   python3 app/tools/install.py --uninstall         убрать команду, разрешения и конфиг (данные не трогает)
# Пакет (--pack) — отдельно распространяемая папка с подпапками «Гайдлайны/» и, по желанию, «Проекты/».
import json, os, shutil, stat, sys, time
from pathlib import Path

APP = Path(__file__).resolve().parents[2]
HOME = Path.home()
CLAUDE = HOME / ".claude"
SKILL_DIR = CLAUDE / "skills" / "presentations"
SETTINGS = CLAUDE / "settings.json"
CONFIG = HOME / ".config" / "presentation-designer" / "config.json"

sys.path.insert(0, str(APP / "app"))
from core import exporter, store  # noqa: E402


def rules():
    a = str(APP)
    return [
        "Read(/%s/**)" % a,            # «//абсолютный путь» в синтаксисе Claude Code
        "Edit(/%s/Проекты/**)" % a,
        "Edit(/%s/Гайдлайны/**)" % a,
        "Bash(python3 %s/app/tools/agent.py *)" % a,
        "Bash(python3 %s/app/tools/launch.py *)" % a,
        "Bash(python3 %s/app/tools/extract.py *)" % a,
        "Bash(python3 %s/app/tools/convert.py *)" % a,
        "Bash(python3 %s/app/tools/check_slides.py *)" % a,
    ]


def say(icon, text):
    print("%s %s" % (icon, text))


def load_settings():
    if not SETTINGS.exists():
        return {}
    try:
        return json.loads(SETTINGS.read_text(encoding="utf-8"))
    except ValueError:
        sys.exit("✗ %s повреждён (не JSON) — исправьте его и запустите установку снова" % SETTINGS)


def save_settings(data):
    if SETTINGS.exists():
        backup = SETTINGS.with_name("settings.json.bak-%s" % time.strftime("%Y%m%d-%H%M%S"))
        shutil.copy2(SETTINGS, backup)
        say("•", "Бэкап настроек Claude Code: %s" % backup)
    store.write_json(SETTINGS, data)


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


def install(pack):
    if sys.version_info < (3, 9):
        sys.exit("✗ Нужен Python 3.9+, сейчас %s" % sys.version.split()[0])
    say("✓", "Python %s" % sys.version.split()[0])
    if exporter.find_chrome():
        say("✓", "Google Chrome найден")
    else:
        say("!", "Google Chrome не найден — экспорт PDF/JPEG не заработает, пока его не установить")
    import subprocess
    r = subprocess.run([sys.executable, str(APP / "app" / "tools" / "convert.py"), "--check"], capture_output=True, text=True)
    try:
        kn = json.loads(r.stdout)
    except ValueError:
        kn = {"ok": False, "error": r.stderr.strip() or "нет ответа"}
    if not kn.get("ok"):
        sys.exit("✗ Keynote обязателен: %s" % kn.get("error"))
    say("✓", "Keynote отвечает (разрешение на управление есть)")
    if not CLAUDE.is_dir():
        say("!", "Папка ~/.claude не найдена — установите Claude Code и запустите установку снова")

    for d in ("Проекты", "Экспорт", "Гайдлайны"):
        (APP / d).mkdir(exist_ok=True)
    if pack:
        copy_pack(pack)
    if not any((APP / "Гайдлайны").glob("*/guide.json")):
        say("!", "Гайдлайнов нет — первый собери в приложении («Новый гайдлайн») или поставь пакет: --pack <папка>")

    for f in ("start.command", "stop.command"):
        p = APP / f
        if p.exists():
            p.chmod(p.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)

    SKILL_DIR.mkdir(parents=True, exist_ok=True)
    tpl = (APP / "app" / "tools" / "skill_template.md").read_text(encoding="utf-8")
    (SKILL_DIR / "SKILL.md").write_text(tpl.replace("{{APP}}", str(APP)), encoding="utf-8")
    say("✓", "Команда /presentations: %s" % (SKILL_DIR / "SKILL.md"))

    prev = store.read_json(CONFIG) or {}
    settings = load_settings()
    allow = settings.setdefault("permissions", {}).setdefault("allow", [])
    stale = [r for r in prev.get("added_rules", []) if r not in rules()]  # путь установки сменился
    added = [r for r in rules() if r not in allow]
    if added or stale:
        settings["permissions"]["allow"] = [r for r in allow if r not in stale] + added
        save_settings(settings)
        say("✓", "Разрешения для папки приложения добавлены в %s" % SETTINGS)
    else:
        say("•", "Разрешения уже настроены")

    owned = sorted(set(r for r in prev.get("added_rules", []) if r in rules()) | set(added))
    store.write_json(CONFIG, {"app_dir": str(APP), "python": sys.executable,
                              "installed_at": store.now(), "added_rules": owned})
    say("✓", "Готово. В Claude Code (из любой папки): /presentations — открыть приложение, "
             "/presentations правки — внести правки, /presentations стоп — остановить")


def uninstall():
    cfg = store.read_json(CONFIG) or {}
    if SKILL_DIR.exists():
        shutil.rmtree(SKILL_DIR)
        say("✓", "Команда /presentations удалена")
    owned = cfg.get("added_rules", [])
    if owned and SETTINGS.exists():
        settings = load_settings()
        allow = settings.get("permissions", {}).get("allow", [])
        if any(r in allow for r in owned):
            settings["permissions"]["allow"] = [r for r in allow if r not in owned]
            save_settings(settings)
            say("✓", "Разрешения приложения убраны из %s" % SETTINGS)
    if CONFIG.exists():
        CONFIG.unlink()
    say("•", "Проекты, гайдлайны и экспорт в %s не тронуты" % APP)


if __name__ == "__main__":
    args = sys.argv[1:]
    if "--uninstall" in args:
        uninstall()
    else:
        pack = args[args.index("--pack") + 1] if "--pack" in args and len(args) > args.index("--pack") + 1 else None
        install(pack)
