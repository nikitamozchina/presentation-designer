---
title: "Стек и конвенции"
description: "Технологии, версии, требования к машине, запуск и остановка, правила кода"
category: "architecture"
related_nodes:
  - "docs/architecture/server-api.md"
  - "docs/modules/export.md"
context_tags: ["stack", "python", "macos", "setup"]
---

# Стек

| Слой | Решение |
|---|---|
| Сервер | Python 3.9+, только stdlib (`http.server.ThreadingHTTPServer`) |
| Фронтенд | Ванильный HTML/CSS/JS без сборки, `app/static/` |
| Хранение | JSON + HTML на диске, без БД |
| Экспорт, самопроверка, контактные листы | Google Chrome headless (CLI) + `sips` (macOS) |
| Конвертация pptx/key → PDF и картинки | Keynote через AppleScript (`osascript`), **обязателен** |
| Окна выбора файлов | `osascript` (choose file / choose folder) |
| Агент | Claude Code (`/presentations`) или Codex (`$slides`): глобальная команда из любой папки |
| ОС | Только macOS (Windows — бэклог) |

## Что нужно на машине
1. Python 3.9+: на чистом macOS это Xcode Command Line Tools (`xcode-select --install`) или установщик python.org.
2. Google Chrome в `/Applications` или `~/Applications`.
3. Keynote или Keynote Creator Studio (бесплатно, App Store). При первом вызове macOS спросит «разрешить управлять Keynote» — нужно разрешить; после отказа: `tccutil reset AppleEvents`.
4. Claude Code или Codex с входом в подписку.

pip-зависимостей нет.

## Установка
- Для агента: `INSTALL.md`. `python3 app/tools/install.py --agent claude|codex [--pack <папка>]` — идемпотентно; ставит только под указанного агента (без флага: `CODEX_*` в окружении → codex, иначе claude). `--uninstall` убирает всё, что ставил, для всех агентов.
- Проверяет Python, Chrome и Keynote (пробная `convert.py --check`; без Keynote установка прерывается). `PD_INSTALL_NO_APPS=1` пропускает проверку Chrome и Keynote (тесты), `PD_HOME` подменяет домашнюю папку (тесты).
- Скилл из `app/tools/skill_template.md` (`{{APP}}`, `{{SKILL}}`, `{{CMD}}`). Скрипты в нём вызываются строго `python3 "<APP>/app/tools/<скрипт>.py" …`: под эту форму настроены разрешения обоих агентов.
- Что поставлено, пишется в `<APP>/.state/install.json` (`agents.claude.added_rules`, `agents.codex.{rules,root,created_table}`). Запись прошлых версий из `~/.config/presentation-designer/config.json` переносится и удаляется.
- **Claude Code:** скилл `~/.claude/skills/presentations/SKILL.md`; правила в `~/.claude/settings.json` (бэкап `settings.json.bak-<время>`): `Read(<APP>/**)`, `Edit(<APP>/Проекты/**)`, `Edit(<APP>/Гайдлайны/**)`, `Bash(python3 <APP>/app/tools/{agent,launch,extract,convert,check_slides}.py *)`. В самой папке то же самое задаёт `.claude/settings.json` относительными путями.
- **Codex:** скилл `~/.agents/skills/slides/SKILL.md` (имя `slides`: `presentations` занято встроенным плагином Codex). Песочница Codex по умолчанию не пускает к Keynote, локальному серверу и записи в `<APP>` (проверено `codex sandbox -P :workspace`), поэтому:
  - `~/.codex/rules/presentation-designer.rules` — `prefix_rule(pattern=["python3", "<APP>/app/tools/<скрипт>.py"], decision="allow")` на 5 скриптов: такие команды выполняются вне песочницы без вопросов (проверяется `codex execpolicy check` в `test_install.py`);
  - `<APP>` добавляется в `sandbox_workspace_write.writable_roots` в `~/.codex/config.toml` (бэкап `config.toml.bak-<время>`). Правка текстовая: таблица дописывается в конец или строка `writable_roots` дополняется; dotted-ключи, многострочные массивы и дубли таблицы не трогаются — установщик печатает, что добавить вручную. На Python 3.11+ результат проверяется `tomllib`. Удаление возвращает файл как был.
  - Сам `install.py` Codex запускает вне песочницы, с подтверждением пользователя (`INSTALL.md`); правила подхватываются в новой сессии Codex.
- Интерфейс берёт команды из `install.json` (`store.agent_commands`): `/presentations …` и/или `$slides …`; без записи — вариант Claude Code.
- Команды скилла: без аргумента — старт, `правки`, `гайд`, `создать`, `стоп`, `статус`. Процедуры — `.claude/commands/{process-edits,build-guide,create-presentation}.md`.
- Пакет (`--pack`): `Гайдлайны/*` и `Проекты/*` копируются, если их нет. Шрифты, гайды, демо, `docs/research/` и `app/tools/legacy/` в репозиторий не входят (`.gitignore`).

## Запуск и остановка
- `/presentations` или `$slides` (либо `python3 app/tools/launch.py start|stop|status`) — сервер в фоне, лог `.state/server.out`.
- Напрямую: `python3 app/server.py --open`. Порт — первый свободный начиная с 8765 (переопределяется `PD_PORT`), пишется в `.state/server.json`. Второй экземпляр не стартует, а открывает уже запущенный.
- Остановка: Ctrl+C или `POST /api/shutdown`.
- Лог: `.state/server.log`.

## Конвенции кода
- Совместимость с Python 3.9: без `match`, без `X | Y` в аннотациях, без `tomllib`.
- Тесты: `/usr/bin/python3 -m unittest discover -s app/tests` (именно системный 3.9).
- JSON пишется только через `store.write_json` (атомарно).
- Пути от пользователя/URL проверяются через `store.inside()`.
- `PD_ROOT` в окружении подменяет корень данных, `PD_CONFIG` — папку конфига (пресеты), `PD_PORT` — порт, `PD_TRASH` — Корзину (по умолчанию `~/.Trash`). Используются тестами и UI-проверками.
- Тесты (45): `test_agent_cycle.py` (11), `test_tasks.py` (9), `test_pptx.py` (9), `test_install.py` (8), `test_extract.py` (4), `test_server_projects.py` (4). Dev-проверки в `app/tests/ui/` → `docs/modules/tools.md`.
