---
title: "Данные на диске"
description: "Структура папок, project.json, правила записи и владельцы файлов"
category: "architecture"
related_nodes:
  - "docs/architecture/agent-contract.md"
  - "docs/architecture/tasks.md"
  - "docs/modules/slides.md"
context_tags: ["data", "filesystem", "json"]
---

# Структура

```
Экспорт/<Проект>/PDF/<Проект>.pdf
Экспорт/<Проект>/JPEG/NN-<slide>.jpg
Проекты/<Проект>/
  project.json            метаданные и порядок слайдов
  slides/*.html           по слайду на файл, 1920x1080
  styles.css, assets/     общие стили и ресурсы (пути ../styles.css, ../assets/)
  edits/                  итерации правок → agent-contract.md
  history/vN/             снапшоты до правок итерации N; <edit-id>/before/ и <edit-id>.json — для отката правки
  export-status.json      статус и last.{pdf,jpeg} — последний экспорт каждого формата
  agent-status.json       пульс агента по этому проекту (правки и генерация)
  .check/                 самопроверка: скриншоты, лист-NN.png, report.md/json → modules/tools.md
  — только у проектов, созданных из формы (→ architecture/tasks.md):
  request.json, lifecycle.json, .claim.lock
  Исходники/              копии фактуры и основы; _extracted/ — их разбор
  brief/                  interview.md (журнал вопросов), brief.md
Гайдлайны/<имя>/          → modules/guides.md (guide.json, guide.md, styles.css, fonts/, assets/, examples/)
.state/                   server.json, server.log, server.out, picks.json (токены окон выбора, 24 ч)
~/.config/presentation-designer/  presets.json (авторы)
```

## project.json
| Поле | Тип | Смысл |
|---|---|---|
| id | str | slug имени |
| name | str | = имя папки |
| guideline_set_id | str | папка в `Гайдлайны/` |
| slides[] | list | `{id, file, title, order, hidden}`; `id` = имя файла без `.html` |
| created_at, updated_at | ISO-8601 | |

Если `project.json` нет — создаётся из `slides/*.html` (порядок по имени, title из `<title>` без номера). Исключение: папка с `request.json` (проект из формы) — там `project.json` пишет приложение при создании, и `load_project` без него падает с FileNotFoundError, а не выдумывает пустой проект.

## Владельцы файлов
| Файл | Пишет |
|---|---|
| project.json, edits-vN.json, export-status.json | приложение |
| slides/*.html, styles.css | агент: по итерации правок или при генерации проекта |
| edits-vN.result.json, edits-vN.lock, agent-status.json | агент через `app/tools/agent.py` |
| request.json | приложение, один раз при создании |
| lifecycle.json, brief/interview.md | `app/core/tasks.py` через сервер и `agent.py task-*` |
| project.json проекта из формы | приложение при создании; список слайдов — `agent.py sync-slides` |
| history/vN/ | `agent.py claim` (снапшот), читается при откате |

## Правила записи
- JSON — атомарно: временный файл в той же папке → fsync → `os.replace`.
- Изменение project.json — под `FileLock` (`.project.lock`).
- Номер итерации выдаётся под `edits/.counter.lock`.
- Скрытие слайда не удаляет HTML: только `hidden: true`.
