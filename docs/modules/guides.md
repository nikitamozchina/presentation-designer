---
title: "Гайдлайны"
description: "Сборка гайдлайна агентом из папки, структура гайда, обязательный контракт классов таблиц и графиков, эталоны, пакет гайдлайнов"
category: "module"
related_nodes:
  - "docs/architecture/tasks.md"
  - "docs/modules/projects.md"
  - "docs/modules/tools.md"
context_tags: ["guidelines", "brand", "fonts", "examples", "charts", "tables"]
---

# Гайдлайны

## Новый гайдлайн
1. Форма «Новый гайдлайн»: название, папка с файлами (родное окно macOS), тумблер «Сделать эталонные слайды» (по умолчанию вкл.). Отправка — `POST /api/guides`.
2. Папка копируется в `Гайдлайны/<название>/Исходники/`, оригинал не трогается. Дальше заявка `guide` → `docs/architecture/tasks.md`.
3. `/presentations гайд` запускает агента по `.claude/commands/build-guide.md`:
   - разбор через `extract.py --sheets`;
   - бюджет контекста: индекс, контактные листы, не больше 12 картинок слайдов, PDF по 20 страниц и не больше 60 всего;
   - если не хватает критичного (шрифт, главный цвет, логотип), агент спрашивает в чате по одному вопросу с вариантами, а мелочи записывает в «Допущения».
4. Агент собирает гайд, затем `agent.py dedupe guide <имя>` переносит в Корзину папки `_extracted/…-медиа`, которые целиком (имя + размер) уже лежат в `assets/library/`.
5. Если заказаны эталоны: `check_slides.py --guide`, исправления до 3 кругов, потом `task-state ready` с проверкой готовности.

Клик по гайду в сайдбаре открывает его эталоны только для просмотра. Пока гайд собирается, справа видна карточка статуса.

## Структура гайда
```
Гайдлайны/<имя>/
  guide.json      обязательно: id, name, version, base_css; дальше свободно (canvas, colors, fonts,
                  type_scale_px, min_font_px, backgrounds, components, assets, typography_rules, layout_rules)
  guide.md        правила словами: рецепты слайдов, ## Таблицы, ## Графики, библиотека, можно/нельзя, Допущения
  styles.css      @font-face url("fonts/…"), картинки url("assets/…"), без внешних URL и @import
  fonts/          otf/ttf/woff2
  assets/         bg/, logo/, icons*/, img/, library/<презентация>/ (+ по-слайдам.md, media.md) — всё, что пригодится
  examples/       01-title … 10-timeline.html (если заказаны)
  Исходники/, _extracted/, request.json, lifecycle.json — служебное, по /g/ не отдаётся
```
- Гайд сознательно сделан универсальной библиотекой (решение автора): иконки, 3D, маскоты, мокапы и фото переносятся все, даже если весят много. В проект попадает только то, на что ссылаются слайды → `docs/modules/projects.md`.

## Обязательный контракт классов
На эти классы опирается создание презентаций. Проверка готовности гайда без них не пропустит.
- Таблицы: `table.data` (`.l`, `.total`, `tr.hl`, `tr.sum`, `.up/.down`), `table.agenda` (`tr.hl`, `tr.final`).
- Столбцы: `.bars > .bar-col (.v .bar .x)`, `.key`, `.bar.dim`. Высота столбца в px = значение / максимум × 400.
- SVG-графики: `svg.chart` (`.grid .area .line .dot .lbl .val`, кольцо `.track .seg .s2 .s3 .s4`), `.legend > .legend-item .sw`, `.delta.up/.down`.
- Ганта: `.gantt (.l) > .gantt-head + .gantt-row (.name .track-line .gbar)`, `--cols`, `--label`, `.gbar.alt/.light`.

Эталоны: 01 титул, 02 раздел, 03 текст + иконки, 04 сравнение, 05 цифры, 06 столбцы, 07 линия, 08 кольцо, 09 таблица, 10 Ганта.

## Пакет гайдлайнов
Гайды распространяются отдельным пакетом `<пакет>/Гайдлайны/` (ставится `install.py --pack`). В пакет не кладутся исходники, служебные файлы заявки и тяжёлые черновики. Из `_extracted/` берутся только превью, на которые ссылается guide.md, без `index.md` (в нём локальные пути):
```bash
rsync -a --delete --exclude /Исходники --exclude /request.json --exclude /lifecycle.json --exclude /agent-status.json \
  --exclude /.claim.lock --exclude /.check --exclude /brief --exclude .DS_Store \
  --include /_extracted/ --include '/_extracted/картинки-*/' --include '/_extracted/картинки-*/сетка-*.png' \
  --include '/_extracted/*/' --include '/_extracted/*/*.pptx.md' --include '/_extracted/*/*-листы/' --include '/_extracted/*/*-листы/лист-*.png' \
  --exclude '/_extracted/**' "Гайдлайны/<гайд>/" "<пакет>/Гайдлайны/<гайд>/"
```
Гайд без `lifecycle.json` приложение считает готовым. После упаковки проверь `PD_ROOT=<пакет> check_slides.py --guide <гайд>` и удали из пакета `.state/` и `.check/`. Описание конкретных гайдов пакета — в его `README.md` (пакет не публикуется).

## Тесты
`test_tasks.py`: `test_guide_ready_check`, `test_assets_pulled_from_guide_on_sync`.
