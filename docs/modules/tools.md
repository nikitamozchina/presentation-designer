---
title: "Инструменты агента: извлечение, Keynote, самопроверка"
description: "extract.py, convert.py (Keynote), check_slides.py и контактные листы — аргументы, вывод, пороги"
category: "module"
related_nodes:
  - "docs/modules/guides.md"
  - "docs/modules/projects.md"
  - "docs/architecture/server-api.md"
context_tags: ["extract", "keynote", "self-check", "contact-sheets", "tools"]
---

# Инструменты (`app/tools/`)

Все работают на stdlib (Python 3.9+), вывод — JSON в stdout. Глобальные разрешения на них ставит install.py → `docs/tech-stack.md`.

## extract.py — разбор исходников
`python3 app/tools/extract.py <папка|файл> <куда> [--sheets]`
| Формат | Что достаётся |
|---|---|
| pptx | порядок слайдов (`presentation.xml`), тексты (`a:br` → пробел), заметки, макеты, графики; размер слайда; цвета и шрифты темы + **частые** цвета, кегли и шрифты (тема часто врёт). Все картинки → `…-медиа/` с каталогами `media.md` (файл, КБ, слайды, макеты) и `по-слайдам.md` |
| docx | заголовки, списки, таблицы, ссылки, `sdt`, вставки `ins`; удалённое `del` пропускается |
| xlsx | листы, sharedStrings, даты |
| otf/ttf | семейство и начертание из таблицы `name` |
| pdf | число страниц (читает агент сам, частями) |
| md/txt/csv | как есть |

- Итог — `index.md` + `.md` на каждый файл.
- `--sheets` добавляет контактные листы слайдов (через `convert.py --images`, 4×4 на лист) и сетки картинок для папок от 4 картинок (`core/sheets.py`, рендер через Chrome).

## convert.py — Keynote
`python3 app/tools/convert.py <файл> <папка> [--pdf] [--images] [--pptx]`, `--check` — проверка.
- Работает **только через Keynote**, он обязателен. PowerPoint неинтерактивно зависает на окне «Предоставить доступ».
- Поиск Keynote: `Keynote.app` или `Keynote Creator Studio.app` в `/Applications` и `~/Applications`, иначе Spotlight по идентификатору. Идентификатор берётся из `Info.plist` найденного приложения: у старого Keynote `com.apple.iWork.Keynote`, у Creator Studio `com.apple.Keynote`. AppleScript обращается `tell application id "<он>"`.
- Keynote запускается `open -g -a <путь> --args -ApplePersistenceIgnoreState YES`. Скрипт закрывает только свой документ и выходит из Keynote, только если сам его запустил.
- Коды ошибок -600, -1743, -1712 переводятся в понятный текст. -1743 — нет разрешения «управлять Keynote», вернуть: `tccutil reset AppleEvents`.
- Шаблон на 17 слайдов превращается в PDF и 17 картинок примерно за 9 с.
- Если шрифта презентации нет в системе, Keynote молча подставляет замену и не зависает: проверено на шаблоне, где фирменный шрифт заменён несуществующим (17 слайдов, 12 с).

## check_slides.py — самопроверка
`python3 app/tools/check_slides.py <проект>` или `--guide <гайд>` (эталоны).
- Скрипт сам поднимает сервер, если тот не запущен. Затем открывает `/check/(project|guide)/<имя>?min=` в Chrome headless (`--dump-dom`, обёртка с kill). Страница по очереди грузит слайды в sandbox-iframe и меряет строки текста через `Range.getClientRects`.
- Результат кладётся в `<папка>/.check/`: скриншоты `NN-<id>.png`, контактный лист `лист-NN.png`, `report.json`, `report.md`.

| Проверка | Порог |
|---|---|
| out_of_canvas — текст за краем слайда | допуск 8 px |
| text_cut / clipped_by_box — текст обрезан блоком или вылез из плашки | 8 px |
| overlap — строки разных блоков наезжают | >15% меньшей площади и >150 px² |
| small_text — мелкий шрифт | < `min_font_px` гайда × 0.85 |
| low_contrast — текст плохо читается | < 3:1 на сплошном фоне; под картинками и градиентами не меряется; одиночные знаки без букв и цифр не в счёт |
| empty_bottom — пустой низ слайда | содержание кончается выше 70% высоты (логотип и `.note-src` не считаются) |
| broken_image, font_failed, empty | — |

Порог контраста 3:1, а не 4.5:1, взят сознательно: серый `--muted` основного гайда на белом даёт ~3.4 и для подписей допустим, а на голубой подложке `--light` даёт 2.8 и уже ловится.

Чего скрипт не видит (это смотрит агент глазами на контактном листе): повторы текста, баланс и выравнивание блоков, сходство с эталонами.

## Разработка
- UI-проверка: `app/tests/ui/ui_check.py` — 26 проверок в настоящем Chrome на временной копии пакета (своя Корзина и порт, окна выбора подменены). Форма проекта, статусы заявки, генерация, экспорт в выбранную папку, удаление. Playwright — только для разработки:
  `python3 -m venv .venv-dev && .venv-dev/bin/pip install playwright`, затем `.venv-dev/bin/python app/tests/ui/ui_check.py --pack <пакет> [--guide <гайд>] [--shots <папка>]`. Код выхода 1 при любой ошибке. Проверено: ловит регрессию «Изменить…» в экспорте.
- В Playwright — только async API с таймаутами: синхронный зависает на sandbox-iframe.
- PPTX (dev, нужны lxml / pillow / pymupdf в том же venv) → `docs/modules/pptx-converter.md`:
  - `app/tests/ui/pptx_xsd.py --xsd <папка XSD> file.pptx…` — проверка по схемам ECMA-376 Transitional. Схемы: ecma-international.org → ECMA-376 5th edition Part 4 → `OfficeOpenXML-XMLSchema-Transitional.zip`;
  - `app/tests/ui/pptx_compare.py --root <данные> --out <отчёт> Проект…` — рендер PowerPoint vs HTML, пороги по строкам и растру.
- Тесты инструментов: `app/tests/test_extract.py` (4) — порядок слайдов pptx и переносы, docx, xlsx, имена шрифтов.
