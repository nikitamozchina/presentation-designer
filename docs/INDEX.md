---
title: "INDEX — роутер документации"
description: "Точка входа: суть проекта и таблица, какой файл читать под задачу"
category: "index"
related_nodes:
  - "docs/tech-stack.md"
  - "docs/tasks/current.md"
context_tags: ["index", "routing"]
---

# Дизайнер презентаций

Локальное веб-приложение (macOS, 127.0.0.1) для презентаций по корпоративным гайдлайнам. Приложение хранит проекты, HTML-слайды, гайдлайны, комментарии и экспорт. ИИ-агент (Claude Code) по командам `/presentations …` собирает гайдлайны из папок с материалами, создаёт презентации (интервью → структура → слайды → самопроверка) и вносит правки. Процедуры — `.claude/commands/`. Без LLM API и без БД, источник истины — файлы.

Сначала прочитай только этот файл. Дальше открывай 1–2 файла по таблице.

| Задача касается | Читай |
|---|---|
| Стек, версии, установка, /presentations | `docs/tech-stack.md` |
| Где что лежит на диске, project.json, атомарная запись | `docs/architecture/data-layout.md` |
| Правки через агента, edits-vN, статусы, agent.py, /process-edits | `docs/architecture/agent-contract.md` |
| Заявки гайд/проект: request/lifecycle, состояния, task-claim, отмена | `docs/architecture/tasks.md` |
| HTTP API сервера, безопасность | `docs/architecture/server-api.md` |
| Превью, порядок, скрытие слайдов, выделение текста | `docs/modules/slides.md` |
| Комментарии, итерации правок, откат, поиск выделения | `docs/modules/edits.md` |
| Экспорт PDF/JPEG/PPTX: файлы, статусы, надёжность | `docs/modules/export.md` |
| Гайдлайны: сборка агентом, структура, классы таблиц и графиков, эталоны | `docs/modules/guides.md` |
| Новый проект: форма, режимы, генерация, create-presentation.md, ассеты | `docs/modules/projects.md` |
| extract.py, convert.py (Keynote), check_slides.py (самопроверка) | `docs/modules/tools.md` |
| Экспорт в PPTX: сканер, растр, писатель OOXML, шрифты, калибровка, проверки | `docs/modules/pptx-converter.md` |
| Дизайн интерфейса (Apple-стиль) | `docs/modules/ui-design.md` |
| Что делаем сейчас | `docs/tasks/current.md` |
| Что дальше (бэклог) | `docs/tasks/next.md` |

## Не читать без прямой просьбы пользователя
- `docs/tasks/archive/` — закрытые задачи.
- `docs/research/` и `app/tools/legacy/` — бриф, аудиты и скрипты-прототипы; только в локальной копии, в репозиторий не входят.

## Ключевые пути
- Код: `app/` (сервер `app/server.py`, ядро `app/core/`, CLI агента `app/tools/agent.py`, тесты `app/tests/`).
- Данные пользователя: `Проекты/`, `Экспорт/`, `Гайдлайны/` — вверху корня.
- Служебное состояние: `.state/` (порт сервера, логи, токены окон выбора); пульс агента — `agent-status.json` в каждом проекте.
- Процедуры агента: `.claude/commands/process-edits.md`, `build-guide.md`, `create-presentation.md`; глобальный скилл — `app/tools/skill_template.md`.
