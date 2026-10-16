# ТЗ: Офисный агент (хакатон)

Проект fintech2026 · 16.10.2026

## Цель и рамки

Локальный бэкенд на Python и FastAPI. Офисный сотрудник объясняет задачу в обычном чате, а система превращает разговор в замороженный сценарий (Task) и исполняет его по расписанию или по команде. Фронтенд в это ТЗ не входит: всё доступно через HTTP API сервиса main.

**Принципы**

- **Чат — это запись.** Агент пробует и показывает результат, пользователь поправляет.
- **Task — замороженный план.** При запуске ничего не планируется заново, LLM работает только внутри шагов `llm.*`.
- **Любое действие на компьютере идёт через executor.** Там проверяются права, подставляются секреты и пишется лог.
- **Секреты не попадают в LLM.** Агент и задачи знают только имя аккаунта.
- **Изменения только через версии.** Каждая правка задачи создаёт новую версию, а активной она становится только после подтверждения.

**Вне рамок v1:** фронтенд, несколько пользователей, OAuth-интеграции (MS Graph, Gmail API), управление GUI (computer use), облачный деплой.

## Архитектура

Шесть FastAPI-сервисов, main в центре: клиент и сервисы общаются только через него. storage — не сервис, а общий пакет над одним файлом SQLite.

```
                 Клиент: UI, curl или OpenAI-клиент
                                │
                 ┌──────────────▼───────────────┐
                 │          main :8000          │
                 │ шлюз · реестр · оркестрация  │
                 │   подтверждения · SSE        │
                 └──────────────┬───────────────┘
                                │  вызовы между сервисами тоже через main
   ┌─────────────┬──────────────┼──────────────┬──────────────┐
   ▼             ▼              ▼              ▼              ▼
agent-loop    compiler        runner        executor      scheduler
  :8001         :8002          :8003          :8004          :8005
чат и трейс  чат → Task    исполняет Task  права, секреты  cron, события
   │             │              ┆              │
   ▼             ▼              ┆              ▼
 LLM-провайдер (OpenAI API)     ┆        internal_tools, skills (Deno + MCP)
                                ┆
        SQLite · data/app.db · common.storage (у каждой таблицы один владелец)
```

scheduler решает, когда запускать, runner — в каком порядке, executor — чем; main связывает их и хранит подтверждения. Каждый сервис всегда работает отдельным процессом: локально через launcher.py, в Docker через compose.

## Структура репозитория

Один репозиторий, каждый сервис лежит в своей папке, а общий код (схемы, БД, клиент LLM) вынесен в `common/`. Python-пакеты называем через подчёркивание (`agent_loop`), потому что пакет с дефисом не импортируется. В URL дефис остаётся.

```
project/
├── main/                  # шлюз: внешний API, реестр сервисов, оркестрация, SSE
│   ├── app.py
│   ├── registry.py        # какие сервисы живы и по каким адресам
│   ├── orchestrator.py    # многошаговые сценарии: approve → reload и т.п.
│   ├── approvals.py       # подтверждения и вопросы пользователю
│   └── routes/            # agent, compiler, tasks, runs, tools, skills, accounts, events
├── agent_loop/            # чат с LLM, вызов инструментов, трейс
│   ├── app.py             # /v1/chat/completions
│   ├── loop.py
│   ├── tool_selector.py   # отбирает 5–10 релевантных инструментов (эмбеддинги bge-m3)
│   ├── meta_tools.py      # task.compile, task.patch, task.run_now, task.list, skill.create
│   └── prompts/
├── compiler/              # трейс чата → Task, правки → JSON Patch
│   ├── app.py
│   ├── compile.py
│   ├── patch.py
│   ├── validate.py
│   └── prompts/
├── runner/                # исполнение Task по шагам
│   ├── app.py
│   ├── engine.py
│   └── templating.py      # Jinja2 SandboxedEnvironment
├── executor/              # единая точка вызова инструментов
│   ├── app.py
│   ├── registry.py        # каталог: internal_tools + skills + внешние MCP
│   ├── permissions.py
│   ├── paths.py           # относительные пути задач → абсолютные внутри WORKSPACE_DIR
│   ├── secrets.py         # keyring
│   └── mcp/               # MCP-клиент, запуск Deno-скиллов
├── scheduler/             # триггеры: cron, папка, почта
│   ├── app.py
│   ├── jobs.py
│   └── watchers/          # files.py, mail.py
├── internal_tools/        # встроенные команды, одна команда = один файл
│   ├── fs/                # list.py, read.py, copy.py, move.py, write.py
│   ├── docs/              # read.py, fill_template.py, make_template.py
│   ├── xlsx/              # read_table.py, write_table.py
│   ├── mail/              # search.py, read.py, create_draft.py, move.py, send.py
│   │   └── adapters/      # imap_smtp.py, presets.yaml
│   ├── charts/            # bar.py, line.py
│   ├── llm/               # extract.py, classify.py, write.py
│   └── user/              # ask.py, notify.py
├── skills/                # скиллы, которые написал агент
│   └── <name>/            # manifest.yaml, skill.ts
├── common/                # общий пакет, его импортируют все сервисы
│   ├── schemas/           # pydantic: Task, Step, ToolSpec, Run, Event
│   ├── storage/           # SQLAlchemy-модели, репозитории, миграции Alembic
│   ├── llm/               # клиент к провайдеру (OpenAI-совместимый)
│   ├── tools/             # декоратор @tool, ToolContext
│   ├── http.py            # клиент к main
│   └── config.py          # pydantic-settings: env → config.yaml → умолчания
├── deploy/
│   └── docker.env         # то, чем Docker отличается от локального запуска
├── data/                  # app.db, templates/, outputs/ (в Docker — том)
├── tests/
├── launcher.py            # поднимает все сервисы или один по имени
├── config.yaml            # несекретные настройки, общие для всех окружений
├── .env.example           # шаблон .env: LLM, токены, папки
├── Dockerfile             # один образ на все сервисы
├── docker-compose.yml
└── pyproject.toml
```

Отдельного сервиса storage нет: это пакет `common/storage`, который импортирует каждый сервис. Правила записи в разделе «База данных».

## Сервисы

У каждого сервиса одна зона ответственности: main связывает, agent-loop разговаривает, compiler собирает задачи, scheduler решает «когда», runner — «в каком порядке», executor — «чем». Локально все слушают 127.0.0.1, в Docker наружу публикуется только порт main.

### main :8000

Единственная точка входа и для клиента, и для сервисов между собой. Внутри нет бизнес-логики задач и инструментов, только маршрутизация, оркестрация и подтверждения.

- **Реестр сервисов.** Каждый сервис при старте шлёт `POST /internal/register {name, url}`, main периодически проверяет `GET /health`. Так сервисы не знают адресов друг друга, только адрес main.
- **Оркестрация.** Сценарии, где участвуют несколько сервисов (подтвердить версию, затем перезагрузить триггеры), живут в `main/orchestrator.py`.
- **Подтверждения и события.** Сервисы создают их через `POST /internal/approvals` и `POST /internal/events`, main хранит и отдаёт клиенту через SSE.

Внешний API (для будущего фронта и curl):

| Метод и путь | Куда идёт | Что делает |
| --- | --- | --- |
| `POST /agent/v1/chat/completions` | agent-loop | Чат в OpenAI-совместимом формате, `session_id` в `metadata` |
| `GET /agent/sessions`, `GET /agent/sessions/{id}` | agent-loop | Список сессий, история и трейс |
| `POST /compiler/compile` | compiler | `{session_id}` → черновик задачи |
| `POST /compiler/patch` | compiler | `{task_id, instruction}` → черновик новой версии и diff |
| `GET /tasks`, `GET /tasks/{id}` | storage | Задачи, текущая версия, карточки шагов |
| `GET /tasks/{id}/versions/{v}/diff` | compiler | Разница с предыдущей версией |
| `POST /tasks/{id}/versions/{v}/approve` | compiler, затем scheduler | Сделать версию активной |
| `POST /tasks/{id}/pause`, `/resume`, `/archive` | compiler, затем scheduler | Управление задачей |
| `POST /runs` | runner | `{task_id, dry_run?}` → запуск вручную |
| `GET /runs/{id}`, `GET /tasks/{id}/runs` | storage | Статус запуска и его шаги |
| `GET /approvals`, `POST /approvals/{id}/answer` | main, затем runner | Вопросы и подтверждения; ответ продолжает запуск |
| `GET /tools`, `GET /tools/{name}` | executor | Каталог инструментов со схемами |
| `GET /skills`, `POST /skills/{id}/approve` | executor | Скиллы от агента и их права |
| `GET /accounts`, `POST /accounts` | executor | Аккаунты почты и LLM; секрет сразу уходит в keyring |
| `GET /settings`, `PUT /settings` | main | Провайдер LLM, модель, разрешённые папки |
| `GET /events` | main | Поток событий (SSE) |

Внутренние маршруты (нужен internal-токен): `POST /internal/register`, `POST /internal/events`, `POST /internal/approvals`, `POST /executor/execute` (вызывают agent-loop и runner), `POST /runner/runs` (вызывает scheduler).

В исходной схеме был путь `/agent/v1/chat/compatible`. Лучше `/chat/completions`: тогда любой OpenAI-клиент работает с нами без настроек, достаточно указать `base_url`.

### agent-loop :8001

Ведёт диалог: собирает контекст, отбирает инструменты, вызывает LLM, исполняет tool calls через executor и пишет трейс.

- **API:** `POST /v1/chat/completions` (со стримингом), `GET /sessions`, `GET /sessions/{id}`.
- **Цикл:** история сессии → отбор 5–10 инструментов → LLM → tool calls → `POST main/executor/execute` с `context.mode = "chat"` → результат в историю и в `trace_events` → снова LLM. Остановка, когда ответ пришёл без tool calls, или после 10 итераций.
- **Мета-инструменты** (`task.compile`, `task.patch`, `task.run_now`, `task.list`, `skill.create`) управляют самой системой, а не компьютером. Их обрабатывает agent-loop через main, в executor они не идут.
- **Внешние действия в чате** (`side_effect: external`) executor возвращает со статусом `needs_approval`, и агент спрашивает пользователя прямо в диалоге.

### compiler :8002

Превращает чат в Task и единственный пишет в `tasks` и `task_versions`.

- `POST /compile {session_id}`: читает `chat_messages` и `trace_events`, просит LLM собрать Task JSON, валидирует, сохраняет версию со статусом `draft`, делает dry run. Ответ: `{task_id, version_id, summary, cards, dry_run_result}`.
- `POST /patch {task_id, instruction}`: LLM выдаёт JSON Patch к текущей версии → новая версия `draft` и diff.
- `POST /validate {spec}`: схема pydantic, все инструменты есть в каталоге, аргументы проходят input-схемы, права покрывают все шаги.
- `POST /tasks/{id}/versions/{v}/approve`, `/pause`, `/resume`, `/archive`: меняет статусы.
- Если LLM вернул невалидный JSON, ошибки валидации уходят ему обратно, до 3 попыток.

### runner :8003

Исполняет активную версию задачи по шагам, сам ничего не планирует.

- `POST /runs {task_id или task_version_id, trigger, dry_run}`: создаёт запись в `runs`, исполняет в фоне и сразу отвечает `run_id`.
- `POST /runs/{id}/resume {approval_id}` продолжает после ответа пользователя, `POST /runs/{id}/cancel` отменяет.
- **Движок:** берёт `current_version_id` → считает `variables` → для каждого шага рендерит `args` (Jinja2, StrictUndefined) → `POST main/executor/execute` → пишет `step_runs` → обрабатывает `on_error`.
- Шаг `user.ask` или ответ `needs_approval` от executor: runner создаёт approval в main, запуск переходит в `waiting`. После ответа main вызывает `resume`.
- Один запуск задачи за раз: если предыдущий ещё идёт, новый пропускается с записью в лог.

### executor :8004

Единственное место, где выполняются инструменты. И чат, и runner зовут его одним форматом.

- `POST /execute {tool, args, context}` → `{status, output, artifacts}`, где status: `ok`, `error` или `needs_approval`. В `context`: режим (`chat` или `run`), `session_id` или `run_id` + `step_id`, permissions задачи, `dry_run`.
- `GET /tools`, `GET /tools/{name}`, `POST /tools/reload`; `POST /skills/{id}/install`; `POST /accounts`, `DELETE /accounts/{id}`.
- **Конвейер вызова:** найти инструмент → проверить args по input-схеме → проверить права (capabilities инструмента против permissions задачи) → применить политику для внешних действий (dry run, черновик) → подставить секреты из keyring → вызвать с таймаутом → проверить output → записать в `tool_calls` без секретов.
- **Регистрация команд.** В исходной схеме команды в executor регистрировал main. Предлагаю наоборот: executor при старте сам сканирует `internal_tools/` и одобренные `skills/`, обновляет таблицу `tools` и регистрируется в main. Тогда main не знает кода инструментов, а источник правды один: папка с командами.

### scheduler :8005

Отдельный сервис, который решает только «когда запускать». Runner напрямую не вызывает, только через main, как и все остальные.

- При старте и по `POST /reload {task_id?}` читает активные версии задач и пересобирает свою таблицу `triggers`. `GET /jobs` показывает, что и когда запустится.
- **Типы триггеров:** `cron` (APScheduler), `file_appeared` (watchdog на папку, с задержкой на докачку файла), `new_email` (опрос через `mail.search` в executor, курсор в `triggers.state_json`), `manual` (без триггера).
- Срабатывание → `POST main/runner/runs {task_id, trigger}`.

## Инструменты (команды)

Одна команда = один Python-файл с декоратором `@tool`. Схемы, описание для LLM, текст для карточки и нужные права лежат в этом же файле, отдельной регистрации нет. Посмотреть все команды: `python -m executor.tools list` или `GET /tools`.

```python
# internal_tools/mail/send.py
from pydantic import BaseModel, EmailStr
from common.tools import tool, ToolContext

class Input(BaseModel):
    account: str                  # имя аккаунта, не пароль
    to: list[EmailStr]
    cc: list[EmailStr] = []
    subject: str
    body: str
    attachments: list[str] = []   # пути внутри разрешённых папок

class Output(BaseModel):
    message_id: str
    mode: str                     # "sent" или "draft"

@tool(
    name="mail.send",
    title="Отправить письмо",
    card="Отправить письмо {to} с темой «{subject}»",
    description="Send an email from a configured account.",
    side_effect="external",
    capabilities=["mail.send:{to}", "mail.send:{cc}", "fs.read:{attachments}"],
    secrets=["account"],
    dry_run="draft",
    timeout=30,
)
async def send(inp: Input, ctx: ToolContext) -> Output:
    adapter = ctx.mail_adapter(inp.account)   # IMAP/SMTP или Outlook COM
    if ctx.mode == "draft":
        return Output(message_id=await adapter.create_draft(inp), mode="draft")
    return Output(message_id=await adapter.send(inp), mode="sent")
```

| Поле | Зачем |
| --- | --- |
| `name` | `группа.действие`, по нему на команду ссылаются шаги Task |
| `title`, `card` | Название и шаблон фразы для карточки шага |
| `description` | Описание для LLM |
| `Input`, `Output` | Pydantic-модели, из них строятся JSON-схемы для LLM и для валидации |
| `side_effect` | `none`, `read`, `write` или `external`; по нему решается, нужно ли подтверждение |
| `capabilities` | Нужные права; `{to}` берётся из аргументов и сверяется с permissions задачи |
| `secrets` | Какие аргументы ссылаются на аккаунты; executor подставит секрет сам |
| `dry_run` | Что делать в пробном запуске: `skip`, `draft` или `real` |
| `timeout` | Секунды до отмены |

### Стартовый набор

| Группа | Команды | side\_effect | На чём |
| --- | --- | --- | --- |
| `fs` | list, read, copy, move, write | read, write | pathlib, только внутри разрешённых папок |
| `docs` | read, fill\_template, make\_template | read, write | markitdown, docxtpl, python-docx |
| `xlsx` | read\_table, write\_table | read, write | openpyxl, pandas |
| `mail` | search, read, create\_draft, move, send | read, write, external | адаптеры ниже |
| `charts` | bar, line | write | matplotlib → PNG |
| `llm` | extract, classify, write | none | common.llm, structured output |
| `user` | ask, notify | none | approvals и events в main |

**Почта.** Агент видит один набор команд `mail.*`, адаптер выбирается по `accounts.kind`. `imap_smtp` покрывает Gmail, Яндекс, Mail.ru, Outlook.com и корпоративные серверы; адреса серверов лежат в `presets.yaml`. `outlook_com` появится позже, в v1 на Windows тоже работаем через IMAP/SMTP. Черновик через IMAP — это `APPEND` в папку «Черновики».

**Мета-инструменты** агента (`task.*`, `skill.create`) не лежат в `internal_tools`: они управляют системой и живут в `agent_loop/meta_tools.py`.

### Скиллы, которые пишет агент

1. Агент вызывает `skill.create` с кодом и манифестом.
2. executor сохраняет `skills/<name>/` со статусом `draft`.
3. Пользователь видит права простыми словами («будет ходить на mycompany.bitrix24.ru») и одобряет: `POST /skills/{id}/approve`.
4. executor запускает `deno run --allow-net=<домены> --allow-read=<папки> skill.ts` как MCP-сервер (stdio) и регистрирует его команды как `skill.<name>.<tool>`.

```yaml
# skills/bitrix_deals/manifest.yaml
name: bitrix_deals
title: Сделки из Битрикс24
runtime: deno
entry: skill.ts
permissions:
  net: [mycompany.bitrix24.ru]
  read: []
  write: []
secrets: [bitrix_token]      # executor передаёт через переменную окружения
tools:
  - name: list_deals
    card: "Выгрузить сделки за {date_from}–{date_to}"
    side_effect: read
```

Сторонние MCP-серверы подключаются тем же механизмом через `config.yaml` и по умолчанию выключены.

## Модель задачи

Задача и её версии хранятся отдельно. `tasks` — это имя, статус и ссылка на активную версию; `task_versions` — неизменяемые снимки сценария в JSON. Схема описана в `common/schemas/task.py` (pydantic), её же compiler отдаёт LLM как JSON-схему.

```json
{
  "schema_version": 1,
  "title": "Еженедельный отчёт по продажам",
  "trigger": { "type": "cron", "expr": "0 10 * * MON" },
  "permissions": {
    "read_folders": ["Отчёты/Продажи"],
    "write_folders": ["Отчёты/Готовые"],
    "mail_accounts": ["work_mail"],
    "mail_send_to": ["boss@company.ru", "vasya@company.ru"]
  },
  "approval": { "mode": "draft_first_n", "n": 3 },
  "variables": {
    "week_start": "{{ today | start_of_prev_week }}",
    "week_end": "{{ today | end_of_prev_week }}"
  },
  "steps": [
    { "id": "files", "tool": "fs.list",
      "args": { "folder": "Отчёты/Продажи", "pattern": "*.xlsx",
                "modified_between": ["{{ week_start }}", "{{ week_end }}"] },
      "on_empty": "ask_user" },
    { "id": "data", "tool": "llm.extract",
      "args": { "inputs": "{{ files.output }}", "schema_ref": "sales_summary" } },
    { "id": "chart", "tool": "charts.bar",
      "args": { "data": "{{ data.output.top_clients }}" } },
    { "id": "doc", "tool": "docs.fill_template",
      "args": { "template": "templates/weekly.docx", "context": "{{ data.output }}",
                "images": { "chart": "{{ chart.output.path }}" } } },
    { "id": "send", "tool": "mail.send",
      "args": { "account": "work_mail", "to": ["boss@company.ru"], "cc": ["vasya@company.ru"],
                "subject": "Отчёт за {{ week_start }}–{{ week_end }}",
                "body": "Отчёт во вложении.", "attachments": ["{{ doc.output.path }}"] },
      "on_error": "retry", "retries": 2 }
  ]
}
```

| Поле | Что это |
| --- | --- |
| `schema_version` | Версия формата, сейчас 1 |
| `trigger` | `cron`, `file_appeared`, `new_email` или `manual` с параметрами |
| `permissions` | Всё, что задаче можно: папки, аккаунты, адресаты, домены. compiler выводит их из аргументов шагов |
| `approval` | `always`, `draft_first_n` или `never`: когда спрашивать перед внешними действиями |
| `variables` | Считаются один раз в начале запуска (даты, имена файлов) |
| `steps[].tool`, `args` | Команда из каталога и её аргументы; `{{ step_id.output }}` ссылается на результат прошлого шага |
| `steps[].when` | Условие выполнения шага |
| `steps[].foreach` | Повторить шаг для каждого элемента списка; нужно для сортировки документов |
| `steps[].on_error`, `on_empty` | `fail`, `retry`, `skip` или `ask_user` |

Сортировка документов с `foreach` выглядит так:

```json
{ "id": "sort", "foreach": "{{ files.output }}", "as": "file", "steps": [
    { "id": "kind", "tool": "llm.classify",
      "args": { "input": "{{ file.path }}", "labels": ["договор", "счёт", "акт", "другое"] } },
    { "id": "move", "tool": "fs.move",
      "args": { "src": "{{ file.path }}", "dst": "Документы/{{ kind.output.label }}/" } }
] }
```

### Статусы

| Объект | Статус | Что значит |
| --- | --- | --- |
| Задача | `draft` | Ещё нет одобренной версии, scheduler её не видит |
| Задача | `active` | Есть `current_version_id`, триггеры работают |
| Задача | `paused` | Триггеры выключены, ручной запуск возможен |
| Задача | `archived` | Скрыта, история сохранена |
| Версия | `draft` | Собрана compiler'ом, ждёт подтверждения |
| Версия | `approved` | Стала `current_version_id`; предыдущая стала `superseded` |
| Версия | `rejected` | Пользователь отказался |

«Пометка как используемый» из исходной схемы — это approve версии. Runner всегда исполняет `current_version_id` и записывает id версии в `runs`, поэтому история запусков показывает, какой именно сценарий отработал. По желанию активную версию можно дублировать в `data/tasks/<id>.yaml`, чтобы читать глазами и хранить в git.

## База данных

Один файл `data/app.db` (SQLite), доступ через `common/storage` на SQLAlchemy 2, миграции через Alembic. Включаем `journal_mode=WAL` и `busy_timeout=5000`, чтобы несколько процессов читали параллельно.

**Главное правило: у каждой таблицы ровно один сервис-владелец.** Только он пишет в таблицу, остальные читают напрямую или просят владельца через API. Так нет конфликтов записи и понятно, кто отвечает за данные.

| Таблица | Владелец | Основные поля | Кто читает |
| --- | --- | --- | --- |
| `settings` | main | key, value\_json | все |
| `approvals` | main | id, kind, run\_id, task\_version\_id, payload\_json, status, answer\_json, created\_at, resolved\_at | runner, compiler, agent-loop |
| `events` | main | id, type, payload\_json, created\_at, read\_at | — |
| `chat_sessions` | agent-loop | id, title, status, created\_at | compiler, main |
| `chat_messages` | agent-loop | id, session\_id, role, content, tool\_call\_id, created\_at | compiler |
| `trace_events` | agent-loop | id, session\_id, message\_id, tool, args\_json, result\_json, status, user\_feedback, created\_at | compiler |
| `tasks` | compiler | id, title, status, current\_version\_id, source\_session\_id, created\_at, updated\_at | все |
| `task_versions` | compiler | id, task\_id, version\_no, spec\_json, status, parent\_version\_id, patch\_json, summary, created\_by, created\_at, approved\_at | runner, scheduler, main |
| `triggers` | scheduler | id, task\_id, type, config\_json, state\_json, next\_fire\_at, enabled | main |
| `runs` | runner | id, task\_id, task\_version\_id, trigger, status, dry\_run, started\_at, finished\_at, error | main, agent-loop |
| `step_runs` | runner | id, run\_id, step\_id, iteration, status, input\_json, output\_json, error, started\_at, finished\_at | main |
| `tools` | executor | name, source, version, manifest\_json, enabled | agent-loop, compiler |
| `skills` | executor | id, name, status, manifest\_json, path, source\_session\_id, approved\_at | main |
| `accounts` | executor | id, kind, name, config\_json, secret\_ref, created\_at | main (без секретов) |
| `tool_calls` | executor | id, tool, session\_id, run\_id, step\_run\_id, args\_redacted\_json, status, duration\_ms, error, created\_at | main |
| `artifacts` | executor | id, run\_id, path, mime, created\_at | main |

Значения перечислений:

- `runs.status`: queued, running, waiting, succeeded, failed, cancelled.
- `approvals.kind`: ask\_user, confirm\_action, version\_approve, skill\_permissions.
- `tools.source`: internal, skill, mcp.
- `accounts.kind`: imap\_smtp, outlook\_com, llm.

`trace_events.user_feedback` — правки пользователя к результату вызова («нет, возьми другую колонку»). Именно они подсказывают compiler'у, какие вызовы попадут в итоговую задачу, а какие были неудачными попытками. Если конкуренция за SQLite станет проблемой, storage можно вынести в отдельный сервис или перейти на Postgres; для хакатона это не нужно.

## Ключевые сценарии

Пять сценариев покрывают демо целиком. Везде «main/…» означает вызов через main, который проксирует запрос в нужный сервис.

### 1. Чат с инструментами

1. Клиент → `POST main/agent/v1/chat/completions` → agent-loop.
2. agent-loop → LLM, получает tool call `docs.read`.
3. agent-loop → `POST main/executor/execute` (`mode: chat`) → executor выполняет и возвращает результат.
4. agent-loop пишет `trace_events`, снова идёт в LLM и отвечает клиенту.

### 2. «Формируй задачу раз в неделю»

1. LLM вызывает мета-инструмент `task.compile`.
2. agent-loop → `POST main/compiler/compile {session_id}`.
3. compiler читает трейс, собирает Task, валидирует, сохраняет версию `draft` и делает dry run через `main/runner/runs`.
4. main создаёт approval `version_approve` и возвращает agent-loop карточки шагов и итог dry run.
5. Агент показывает пользователю: «Вот задача, вот что получилось на пробном запуске. Сохранить?»
6. «Да» → `POST main/tasks/{id}/versions/{v}/approve` → main вызывает compiler (статусы), затем scheduler `/reload`.

### 3. Правка через чат

1. «Добавь Васю в копию» → мета-инструмент `task.patch {task_id, instruction}`.
2. compiler строит JSON Patch, сохраняет новую версию `draft` и считает diff по карточкам.
3. Агент показывает «было → стало», дальше как шаг 6 предыдущего сценария.

### 4. Запуск по расписанию

1. scheduler: сработал cron → `POST main/runner/runs {task_id, trigger: "cron"}`.
2. runner создаёт run и по шагам вызывает `main/executor/execute`.
3. executor проверяет права, подставляет секреты, выполняет и пишет `tool_calls`.
4. runner пишет `step_runs`, в конце шлёт событие `run.finished` в main, тот отдаёт его клиенту по SSE.

### 5. Вопрос пользователю и подтверждение отправки

1. Шаг `mail.send`, политика `draft_first_n` ещё действует → executor создаёт черновик и возвращает `needs_approval`.
2. runner → `POST main/internal/approvals`, run переходит в `waiting`.
3. Пользователь отвечает в чате или в UI → `POST main/approvals/{id}/answer`.
4. main → `runner/runs/{id}/resume`: шаг выполняется по-настоящему или пропускается.

Так же работает `on_empty: ask_user`: не нашлось файлов за неделю → вопрос в чат → ответ продолжает или отменяет запуск.

## Безопасность и секреты

Все проверки делает код, а не LLM: даже если модель уговорили через письмо или документ, executor не даст выйти за права задачи.

| Риск | Мера |
| --- | --- |
| Секрет утёк в лог, промпт или git | Секреты только в keyring (Windows Credential Manager, macOS Keychain, Secret Service). В БД лежит `secret_ref`, в `tool_calls` аргументы с вырезанными секретами |
| Prompt injection из письма или документа | Права проверяет executor: адресат не из `mail_send_to` или папка не из `read_folders` — отказ |
| Агент написал опасный код | Скиллы запускаются только в Deno с явными `--allow-*` и только после одобрения прав пользователем |
| Доступ к файлам вне задачи | Команды `fs.*` работают только в разрешённых папках; пути нормализуются, `..` и симлинки наружу отклоняются |
| Чужой процесс дёргает API | Наружу открыт только main. Токен на внешний API опционален (пустой API\_TOKEN — без проверки), внутренние маршруты закрыты INTERNAL\_TOKEN |
| Шаблон в Task выполняет произвольный код | Jinja2 SandboxedEnvironment и StrictUndefined |
| Письмо ушло не тому в первых запусках | Политика `draft_first_n`: первые n запусков создают черновики и спрашивают подтверждение |

Секреты аккаунтов и скиллов читает только executor, в момент вызова команды. Ключ к LLM, если он есть, задаётся через `LLM_API_KEY` и читается `common.llm`; нашему серверу ключ не нужен. На Windows секреты лежат в Credential Manager, в Docker — в зашифрованном файле `keyrings.alt` в томе с данными, пароль берётся из `KEYRING_PASSWORD`.

## Конфигурация и развёртывание

Один код и один `.env` работают на Windows без Docker и в Docker; разница вынесена в маленький файл `deploy/docker.env`. Все настройки читает `common/config.py` на pydantic-settings в таком порядке: переменные окружения → `config.yaml` → умолчания в коде.

- **`.env`** — то, что меняется от машины к машине: LLM, токены, папка с документами. В git только `.env.example`.
- **`config.yaml`** — общее для всех окружений: лимиты агента, таймауты, пресеты почты, порты по умолчанию.
- **`deploy/docker.env`** — только отличия Docker: адреса сервисов, пути внутри контейнера, бэкенд keyring.

### Переменные

| Переменная | Без Docker | В Docker | Зачем |
| --- | --- | --- | --- |
| `MAIN_URL` | `http://127.0.0.1:8000` | `http://main:8000` | Куда сервисы шлют запросы |
| `SERVICE_HOST` | `127.0.0.1` | `0.0.0.0` | На каком адресе слушать |
| `SELF_URL` | `http://127.0.0.1:<порт>` (умолчание) | `http://<имя сервиса>:<порт>` | Адрес, который сервис сообщает main при регистрации |
| `DATA_DIR` | `./data` | `/data` (том) | app.db, шаблоны, готовые файлы |
| `WORKSPACE_DIR` | `C:/Users/<имя>/Documents` | `/workspace` | Корень папок, с которыми работают задачи |
| `LLM_BASE_URL` | `http://llm-host:8000/v1` | то же | OpenAI-совместимый endpoint |
| `LLM_API_KEY` | пусто | пусто | Пусто — без заголовка Authorization |
| `LLM_NO_PROXY` | `true` | `true` | Не брать прокси из окружения (`httpx trust_env=False`), как `--noproxy` в b.sh |
| `LLM_MODEL_AGENT`, `LLM_MODEL_COMPILER` | `qwen3:30b` | то же | Модели по ролям, меняются независимо |
| `LLM_MODEL_EMBED` | `bge-m3` | то же | Эмбеддинги для отбора инструментов |
| `API_TOKEN` | пусто | пусто или задан | Пусто — внешний API main без проверки |
| `INTERNAL_TOKEN` | генерирует launcher.py | задаётся в `.env` | Защита маршрутов `/internal/*` |
| `KEYRING_BACKEND` | `system` (Credential Manager) | `file` | Где хранить пароли почты |
| `KEYRING_PASSWORD` | не нужен | задаётся в `.env` | Пароль к файлу секретов в `/data` |

Порты заданы умолчаниями (8000–8005) и переопределяются переменной `PORT`, но трогать их обычно не нужно. Поскольку каждый сервис сам сообщает main свой `SELF_URL`, никакого списка адресов поддерживать не надо.

**Пути в задачах только относительные** — от `WORKSPACE_DIR` (`Отчёты/Продажи`). Тогда одна и та же задача работает и на Windows (`C:/Users/.../Documents/Отчёты/Продажи`), и в контейнере (`/workspace/Отчёты/Продажи`). Преобразование делает `executor/paths.py`, он же не даёт выйти за корень.

### Без Docker (Windows)

`python launcher.py` запускает шесть процессов uvicorn через `subprocess`, собирает их логи в одну консоль с префиксом сервиса и перезапускает упавшие. `python launcher.py runner` поднимает один сервис, `--reload` включает перезагрузку при изменении кода. Никаких bash-скриптов, поэтому одинаково работает на Windows, Linux и macOS. Deno ставится отдельно и нужен только для скиллов.

### В Docker

Один образ на все сервисы (python:3.12-slim, зависимости, Deno), сервис выбирается командой. Наружу публикуется только порт main.

```yaml
# docker-compose.yml
x-svc: &svc
  build: .
  env_file: [.env, deploy/docker.env]   # второй файл перекрывает первый
  volumes:
    - app-data:/data
    - ${HOST_WORKSPACE:-./workspace}:/workspace
  restart: unless-stopped

services:
  main:
    <<: *svc
    command: python launcher.py main
    environment: { SELF_URL: "http://main:8000" }
    ports: ["127.0.0.1:8000:8000"]
    healthcheck:
      test: ["CMD", "python", "-c", "import urllib.request; urllib.request.urlopen('http://localhost:8000/health')"]
  agent-loop:
    <<: *svc
    command: python launcher.py agent_loop
    environment: { SELF_URL: "http://agent-loop:8001" }
    depends_on: { main: { condition: service_healthy } }
  # compiler, runner, executor, scheduler — так же, порты 8002–8005

volumes:
  app-data:
```

```bash
# deploy/docker.env
MAIN_URL=http://main:8000
SERVICE_HOST=0.0.0.0
DATA_DIR=/data
WORKSPACE_DIR=/workspace
KEYRING_BACKEND=file
WATCH_POLLING=true
```

```bash
# .env.example
LLM_BASE_URL=http://llm-host:8000/v1
LLM_API_KEY=
LLM_NO_PROXY=true
LLM_MODEL_AGENT=qwen3:30b
LLM_MODEL_COMPILER=qwen3:30b
LLM_MODEL_EMBED=bge-m3
API_TOKEN=
INTERNAL_TOKEN=
KEYRING_PASSWORD=
WORKSPACE_DIR=C:/Users/<имя>/Documents   # без Docker
HOST_WORKSPACE=C:/Users/<имя>/Documents    # что монтировать в /workspace
```

Два нюанса Docker Desktop на Windows:

- **app.db держим в именованном томе `app-data`, а не в папке Windows.** Блокировки SQLite в режиме WAL ненадёжно работают через bind mount из Windows. Папка с документами монтируется обычно.
- **События файловой системы из смонтированной папки Windows могут не доходить до контейнера.** Поэтому триггер `file_appeared` в Docker работает через `PollingObserver` watchdog (флаг `WATCH_POLLING=true` в `docker.env`).

### Модели на сервере команды

Проверено 16.10.2026 одним запросом на модель; время может включать загрузку модели в память.

| Модель | Tool calling | Structured output | Время (tools / JSON) | Роль |
| --- | --- | --- | --- | --- |
| `qwen3:30b` | да | да | 7 с / 2 с | По умолчанию: agent-loop, compiler, `llm.*` |
| `llama3.3:70b` | да | не проверяли | 11 с / — | Запасная для agent-loop |
| `gpt-oss:120b` | да | пустой ответ | 29 с / 17 с | Не для compiler и `llm.extract` |
| `deepseek-r1:70b` | нет (сервер отвечает ошибкой) | — | — | Только текст в `llm.write`, если нужен |
| `bge-m3` | — | — | — | Эмбеддинги для `tool_selector` |

Из таблицы следует требование к `common.llm`: если модель вернула пустой или невалидный JSON, клиент повторяет запрос со схемой в промпте и валидирует ответ сам. Теги `<think>` от reasoning-моделей вырезаются до разбора.

## Стек, план и открытые вопросы

| Задача | Библиотеки |
| --- | --- |
| HTTP-сервисы | FastAPI, uvicorn, httpx, sse-starlette |
| Схемы | pydantic v2 |
| БД | SQLite (WAL), SQLAlchemy 2, Alembic |
| LLM | openai SDK с `base_url` (облако, Ollama, LM Studio, vLLM) или litellm |
| Шаблоны и правки Task | Jinja2 (sandbox), jsonpatch |
| Расписание и события | APScheduler, watchdog |
| Документы | markitdown, python-docx, docxtpl, openpyxl, pandas, pdfplumber, matplotlib |
| Почта | imapclient, aiosmtplib, pywin32 (Outlook на Windows) |
| Секреты | keyring |
| Скиллы | Deno, mcp (Python SDK) |
| Запуск | `launcher.py` (процесс на каждый сервис) или docker compose |

**Запуск.** Каждый сервис всегда работает отдельным процессом: локально через `launcher.py`, в контейнерах через `docker compose`. Подробности в разделе «Конфигурация и развёртывание».

### План работ

- [ ] `common`: схемы Task и ToolSpec, storage и миграции, клиент к main. Делается первым, остальные от него зависят
- [ ] main и executor с пятью командами: `fs.list`, `docs.read`, `docs.fill_template`, `llm.extract`, `mail.send`
- [ ] agent-loop: цикл, трейс, мета-инструменты
- [ ] compiler: compile, validate, patch
- [ ] runner и scheduler (сначала только cron)
- [ ] Сквозной демо-сценарий: пример отчёта → задача → запуск → письмо на почте
- [ ] Бонус: скилл в Deno, самовосстановление шага после ошибки

После `common` четыре следующих пункта можно делать параллельно, по человеку на пункт: сервисы связаны только схемами и HTTP-контрактами.

### Принятые решения

- **Запуск всегда отдельными процессами.** Режима монолита нет.
- **Все вызовы между сервисами идут через main**, включая runner → executor. Прямых вызовов нет: сервисы знают только `MAIN_URL`.
- **LLM — сервер команды, без ключа.** Модель по умолчанию `qwen3:30b`, см. проверку моделей в разделе «Конфигурация и развёртывание».
- **Целевая ОС — Windows, без Outlook COM.** Почта только через IMAP/SMTP; код без bash-скриптов, пути только через pathlib.
- **Токен опционален.** Пустой `API_TOKEN` — внешний API без проверки; пустой `LLM_API_KEY` — заголовок Authorization не отправляется.
- **Docker опционален.** Один и тот же код и `.env` работают и с ним, и без него.
