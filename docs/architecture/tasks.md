---
title: "Заявки: гайдлайн и проект"
description: "Жизненный цикл заявок «собрать гайдлайн» и «создать презентацию»: файлы, состояния, этапы, захват агентом, отмена"
category: "architecture"
related_nodes:
  - "docs/architecture/agent-contract.md"
  - "docs/modules/guides.md"
  - "docs/modules/projects.md"
context_tags: ["tasks", "lifecycle", "claim", "statuses", "agent"]
---

# Заявки (`app/core/tasks.py`)

Есть два вида заявок (`kind`):
- `guide` — папка `Гайдлайны/<имя>/`;
- `project` — папка `Проекты/<имя>/`.

Заявку создаёт приложение по форме. Выполняет её агент по процедуре:
- `guide` — по `.claude/commands/build-guide.md`, команда `/presentations гайд`;
- `project` — по `.claude/commands/create-presentation.md`, команда `/presentations создать`.

## Файлы в папке заявки
| Файл | Пишет | Содержимое |
|---|---|---|
| request.json | приложение, один раз | данные формы и список исходников `sources[] {path, as, is_dir, only?}` |
| lifecycle.json | `tasks.py` (через сервер и agent.py) | `state, stage, message, question, progress {done,total}, sources_copied, updated_at, history[]` |
| agent-status.json | agent.py heartbeat | пульс агента по этой заявке |
| brief/interview.md | `agent.py task-log` | журнал вопросов и ответов, дописывается сразу |
| brief/brief.md | агент | бриф презентации (только для project) |
| .claim.lock | `task-claim` | заявку держит сессия агента, снимается на ready/failed/cancelled |
| Исходники/ | приложение, фоном | копии выбранных файлов, оригиналы не трогаются |
| _extracted/ | extract.py | тексты, картинки, контактные листы → `docs/modules/tools.md` |

## Состояния
```
waiting_agent ──task-claim──► working ◄──► waiting_user
      │                          │
      │                          ├──► ready     (проверки готовности пройдены)
      │                          └──► failed
      └──────────── cancel ────────────► cancelled   (из любого незавершённого)
stalled = working, а пульс старше 15 мин (вычисляется, на диск не пишется)
```

- `waiting_user` — агент задал вопрос в чате. Свежесть пульса не проверяется: ответа можно ждать сколько угодно.
- Этапы (`stage`):
  - `guide`: analysis, building;
  - `project`: analysis, interview, structure, generating, self_check.
- Пока исходники копируются, сохраняется `waiting_agent` с `sources_copied: false`. UI показывает этот этап как «Копирование файлов».

## Создание (`tasks.create`)
1. Имя — `clean_name` (убирает `/\:*?"<>|`, ведущие точки, длина до 80). У проектов к имени добавляется « — ГГГГ-ММ-ДД», при совпадении — « 2», « 3»…
2. Всё собирается в `.creating-*`, затем папка переименовывается. Полусозданных папок не бывает.
3. Копирование исходников идёт фоном в потоке сервера (`copy_sources`). Оно идемпотентно, `only` ограничивает подпапки. В конце ставится флаг `sources_copied`. После рестарта сервера `recover()` докопирует недостающее.

## Захват (`task-claim`)
- Сначала незавершённые заявки: `waiting_user` или `stalled` (`resumed: true`). Потом новые `waiting_agent`, по времени создания, с захватом `.claim.lock` через O_EXCL.
- Заявку в `working` со свежим пульсом не берут: над ней работает другая сессия.
- Пока `sources_copied` не поставлен, заявку не берут.
- `--resume <имя>` — взять конкретную, в том числе в `working`. Ответ содержит `request`, `lifecycle`, пути, `interview_so_far`, а для проекта ещё `guide_dir`.

## Проверки перед ready (`agent.py _ready_check`)
- **guide:**
  - в `guide.json` есть `id, name, version, base_css`;
  - в `guide.md` есть разделы `## Таблицы` и `## Графики`;
  - в CSS есть классы `table.data`, `.bar-col`, `svg.chart`, `.legend`, `.gantt`;
  - если заказаны эталоны — не меньше 10 штук в `examples/`.
- **project:** в `project.json` есть слайды, файлы на месте и не пустые.

## В интерфейсе
- В сайдбаре у заявки бейдж: «ждёт агента», «в работе», «нужен ответ», «не отвечает», «ошибка».
- Справа карточка статуса: этапы, вопрос агента, прогресс «N из M», команда для копирования, «Отменить».
- Пока проект не `ready`, он только для просмотра: комментарии, скрытие, порядок и экспорт заблокированы (`require_editable` на сервере). Генерация и правки в **разных** проектах идут параллельно.
- Проекты и гайды без `lifecycle.json` (демо и гайды из пакета) считаются готовыми.

## Тесты
- `app/tests/test_tasks.py` (9): имена с датой и дублями, атомарность, порядок захвата и resume, stalled и отмена, идемпотентное копирование, CLI-цикл, ассеты из гайда, копирование только внутри заявки, проверка готовности гайда.
- `app/tests/test_server_projects.py` (4): создание проекта через API с частью фактуры, валидация формы, пресеты автора, удаление проекта и гайда в Корзину (запрет при работе агента, без затирания имён, без traversal).
