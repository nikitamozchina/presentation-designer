---
title: "Слайды: превью, порядок, скрытие, выделение"
description: "Как слайды показываются и управляются в интерфейсе"
category: "module"
related_nodes:
  - "docs/architecture/data-layout.md"
  - "docs/modules/edits.md"
  - "docs/architecture/server-api.md"
context_tags: ["slides", "preview", "selection", "hidden"]
---

# Слайды

## Формат слайда
- Отдельный HTML, `<div class="slide">` 1920x1080, стили `../styles.css`, ресурсы `../assets/`.
- Без `<script>` и внешних URL.

## Превью
- `<iframe sandbox="allow-same-origin" src="/p/{p}/slides/{file}">` размером 1920x1080, уменьшается через `transform: scale`.
- Родитель читает выделение: `iframe.contentDocument.getSelection()`.
- Обновление: опрос `/api/projects/{p}` раз в 1–2 с; iframe перезагружается при смене `mtime`, но не во время активного ввода комментария.

## Скрытие и порядок
- Скрыть/показать: `POST .../slides/{id}/hidden`. HTML остаётся; карточка приглушается; в экспорт не попадает.
- Порядок: `POST .../order` с полным списком id.

## Выделение текста (сохраняется в правку)
- `text` — `selection.toString()`.
- `prefix` / `suffix` — до 40 символов видимого текста слайда до и после выделения.
- Экранные координаты не хранятся.

## Проверено
- Выделение настоящей мышью внутри sandbox-iframe ловится родителем (слушатели mouseup/keyup на contentDocument).
- Playwright не может внедрять скрипты в такие iframe (CSP) — в UI-тестах координаты брать из родителя.
