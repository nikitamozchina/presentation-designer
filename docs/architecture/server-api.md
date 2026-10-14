---
title: "HTTP API и безопасность"
description: "Эндпоинты app/server.py и меры защиты локального сервера"
category: "architecture"
related_nodes:
  - "docs/tech-stack.md"
  - "docs/modules/export.md"
context_tags: ["api", "server", "security"]
---

# API (`app/server.py`)

| Метод | Путь | Ответ / действие |
|---|---|---|
| GET | / | `app/static/index.html` |
| GET | /static/* | файлы интерфейса |
| GET | /api/state | проекты, `project_states`, гайды (`state`, эталоны, `used_by` — сколько проектов на гайде), статус агента, найден ли Chrome |
| GET | /api/guides/{g} | гайд: состояние заявки, эталоны |
| GET | /g/{g}/{path} | файлы гайда (эталоны, styles.css, assets); Исходники/, _extracted/, brief/, *.json — 403 |
| GET | /check/(project\|guide)/{name}?min= | служебная страница самопроверки для check_slides.py |
| GET | /api/presets | пресеты автора |
| GET | /api/projects/{p} | слайды (+mtime), гайд, итерации со статусами правок, статус экспорта, `lifecycle`, `editable`, `request` |
| GET | /p/{p}/{path} | файлы проекта (слайды, styles.css, assets); edits/, history/, *.json — 403 |
| GET | /print/{p} | страница печати видимых слайдов |
| GET | /pptx-scan/{p} | служебная страница экспорта PPTX: геометрия видимых слайдов (результат в `<pre id="result">`) |
| GET | /pptx-raster/{p}/{slide_id} | служебная страница: растровый слой слайда на прозрачном фоне |
| POST | /api/projects/{p}/slides/{id}/hidden | `{hidden}` |
| POST | /api/projects/{p}/order | `{ids: [...]}` — полный список |
| POST | /api/projects/{p}/edits | `{items: [...]}` → `{version}` |
| POST | /api/projects/{p}/edits/{n}/slides/{slide_id}/revert | `{revert: bool}` — откат/возврат всех правок слайда в задании |
| POST | /api/projects/{p}/export | `{format: pdf|jpeg|pptx, target: токен|null}`, фоновая задача |
| POST | /api/pick | `{kind: folder\|files\|file, prompt}` → родное окно macOS → `{token, items[{label, name, is_dir}]}` или `{cancelled}`; одно окно за раз |
| POST | /api/guides | `{name, token, examples}` → заявка гайда |
| POST | /api/projects | форма нового проекта → `docs/modules/projects.md` |
| POST | /api/tasks/(guide\|project)/{name}/cancel | отмена незавершённой заявки |
| POST | /api/presets | `{action: save\|delete, name, position}` |
| POST | /api/(projects\|guides)/{name}/delete | папка → Корзина macOS (`store.move_to_trash`, `PD_TRASH` для тестов) → `{trash_name}`; 409, пока агент собирает или правит, идёт экспорт. `Экспорт/<проект>` не трогается |
| POST | /api/projects/{p}/reveal | `{format}` → последний результат в Finder |
| POST | /api/shutdown | остановка сервера |

Ошибки: `{ok: false, error}`; 400/404 — данные, 409 — конфликт (экспорт уже идёт, откат при работе агента или конфликт фрагмента, проект ещё собирается), 500 — с записью в `.state/server.log`.

Пока проект из формы не `ready`, `hidden`, `order`, `edits` и `export` отвечают 409 (`require_editable`).

## Безопасность
- Слушает только 127.0.0.1.
- Host только `127.0.0.1:<port>` / `localhost:<port>` (DNS rebinding).
- POST: `Content-Type: application/json` и Origin своего сервера или отсутствует (CSRF).
- Все пути через `store.inside()` (path traversal).
- Пути из окон выбора не попадают в браузер: только токены (`.state/picks.json`, живут 24 ч). Скрипт AppleScript передаётся через stdin, prompt очищен от кавычек.
- HTML слайдов отдаётся с CSP `script-src 'none'`; в UI — `<iframe sandbox="allow-same-origin">` без `allow-scripts`. `allow-scripts` вместе с `allow-same-origin` запрещено.
- Запрос не больше 2 МБ.
