---
title: "Контракт с ИИ-агентом"
description: "Как итерации правок передаются агенту (Claude Code или Codex) и как выводятся их статусы"
category: "architecture"
related_nodes:
  - "docs/modules/edits.md"
  - "docs/architecture/data-layout.md"
  - "docs/architecture/tasks.md"
context_tags: ["agent", "claude-code", "edits", "statuses", "queue"]
---

# Контракт с агентом

Здесь описан цикл **правок**. Заявки «собрать гайдлайн» и «создать презентацию» (`/presentations гайд|создать`, `agent.py task-*`) описаны в `docs/architecture/tasks.md`.

## Запуск обработки
- Ручной, автозапуска нет: UI пишет «Задание vN ждёт агента» и предлагает скопировать команду правок установленного агента (`edit_commands` в `/api/state`): `/presentations правки` или `$slides правки`.
- `/presentations правки` / `$slides правки` — глобальный скилл (ставит `app/tools/install.py`), работает из любой папки по абсолютным путям и выполняет `.claude/commands/process-edits.md`.
- Внутри папки приложения — также `/process-edits` (project-команда, `.claude/settings.json`).
- Разрешения: глобальные правила install.py (Read приложения, Edit `Проекты/**` и `Гайдлайны/**`, Bash agent.py/launch.py/extract.py/convert.py/check_slides.py) → `docs/tech-stack.md`.

## Файлы итерации (`Проекты/<p>/edits/`)
| Файл | Владелец | Изменяемость |
|---|---|---|
| edits-vN.json | приложение | неизменяем после создания |
| edits-vN.lock | agent.py claim | существует, пока итерация в работе |
| edits-vN.result.json | agent.py | статус итерации и каждой правки |
| edits-vN.reverts.json | приложение | откачена ли правка `{edit-id: {reverted, at}}` |
| history/vN/<edit-id>/before/ | agent.py begin | файлы до правки (не перезаписываются) |
| history/vN/<edit-id>.json | agent.py item | хунки «было → стало» по файлам |

edits-vN.json: `{id, project, version, created_at, items[]}`, item: `{id, slide_id, scope: slide|presentation, type: comment|selection, comment, selection: {text, prefix, suffix} | null}`.

## CLI `python3 app/tools/agent.py`
| Команда | Действие |
|---|---|
| status | очередь и статус агента |
| claim [--takeover] | захват следующей queued (O_EXCL lock), снапшот в history/vN, result=in_progress |
| locate P N edit-id | поиск выделения: unique / not_found / ambiguous + точный `raw` HTML |
| begin P N edit-id | перед правкой: снимок файлов правки (scope slide — слайд, presentation — все слайды + css) |
| item P N edit-id status "note" | статус правки: done, partial, needs_input, unsupported, failed; для done/partial без begin — ошибка, считаются хунки |
| finish P N | проверка (все отмечены, слайды целы), итоговый статус, снятие lock |
| heartbeat edits P "msg" | пульс агента в `Проекты/P/agent-status.json` (для заявок — `heartbeat guide\|project <имя>`) |

## Статусы итерации (выводятся из файлов)
| Статус | Условие |
|---|---|
| queued | есть edits-vN.json, нет lock и финального result |
| in_progress | lock + heartbeat свежее 15 мин |
| stalled | lock + heartbeat старше 15 мин («агент не отвечает») |
| completed | все правки done |
| completed_with_warnings | есть done/partial, остальные unsupported/failed |
| needs_input | хотя бы одна needs_input |
| failed | ничего не сделано или слайд сломан проверкой finish |

Статус агента в шапке вычисляется по всем `agent-status.json` проектов и заявкам: in_progress > waiting_user > stalled > idle; offline — агент ещё ни разу не работал.

## Правила агента
- Одна правка — одно изменение (иначе откат одной правки заберёт чужое).
- Выделение правится только при `unique`; иначе needs_input.
- `scope: slide` — только свой файл; `scope: presentation` — можно styles.css и все слайды.
- Добавление/удаление/перестановка слайдов → unsupported (структура меняется только в приложении).
- После `stalled` продолжение — только `claim --takeover`, уже отмеченные правки сохраняются (`already_done`).
- Цвет текста — только классами гайда. После каждой правки, до `item`, — `check_slides.py <project>`: найденное исправляется в той же правке, иначе не попадёт в откат слайда.
- Откат — по слайду внутри задания (`edits-vN.reverts.json` хранит состояние по каждой правке) → `docs/modules/edits.md`.
