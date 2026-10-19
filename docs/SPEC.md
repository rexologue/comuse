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

Один репозиторий, каждый сервис лежит в своей папке, а общий код (настройки, схемы, БД, клиенты) вынесен в `common/`. Python-пакеты называются через подчёркивание (`agent_loop`), потому что пакет с дефисом не импортируется. В URL дефис остаётся. Стиль кода взят из `virag`: `Settings.from_env()` на dataclass'ах, единый `ApiError`, `lifespan` и роутеры в `api/`.

```
project/
├── main/                  # шлюз: внешний API, реестр, оркестрация, подтверждения, SSE
│   ├── main.py            # app
│   ├── registry.py        # какие сервисы живы и по каким адресам
│   ├── orchestrator.py    # approve → reload, ответ → resume, compile → approval
│   ├── events.py          # шина событий (таблица events + SSE-подписчики)
│   ├── proxy.py, auth.py, views.py
│   └── api/               # internal, gateway, tasks, approvals
├── agent_loop/            # чат с LLM, вызов инструментов, трейс
│   ├── main.py
│   ├── loop.py            # цикл хода, повторы, «пинок» при пустом ответе
│   ├── catalog.py         # отбор инструментов по эмбеддингам
│   ├── meta_tools.py      # task_compile, task_patch, task_approve, approval_answer, action_confirm…
│   ├── history.py, prompts.py
│   └── api/chat.py        # /v1/chat/completions, /sessions
├── compiler/              # трейс чата → Task, правки → JSON Patch
│   ├── main.py, compile.py
│   ├── validate.py        # проверка по каталогу, включая ссылки шаг.output.поле
│   ├── permissions.py     # права из capabilities команд
│   ├── cards.py           # карточки шагов, расписание словами, diff
│   ├── store.py, prompts.py
│   └── api/tasks.py
├── runner/                # исполнение Task по шагам
│   ├── main.py, engine.py
│   └── api/runs.py
├── executor/              # единая точка вызова команд
│   ├── main.py, runtime.py
│   ├── registry.py        # каталог из internal_tools/ → таблица tools
│   ├── paths.py           # песочница путей и права на папки
│   ├── secrets.py         # keyring или зашифрованный файл
│   └── api/               # execute (+ /tools), accounts
├── scheduler/             # cron, папки, почта
│   ├── main.py, jobs.py
│   └── api/jobs.py
├── internal_tools/        # встроенные команды: файл = команда (или пара с общим входом)
│   ├── fs/                # list, transfer (copy/move), write
│   ├── docs/              # read, fill_template, make_template, readers
│   ├── xlsx/              # read_table, write_table, aggregate
│   ├── mail/              # search, read, mark, send, create_draft, adapters/
│   ├── charts/            # charts (bar/line)
│   ├── llm/               # extract, classify, write, inputs
│   └── user/              # ask, notify
├── common/
│   ├── settings.py        # Settings.from_env(), int/bool coercion, load_dotenv
│   ├── errors.py          # ApiError + обработчики
│   ├── service.py         # фабрика сервиса: lifespan, регистрация, internal-токен, /health
│   ├── templating.py      # Jinja-песочница и фильтры дат (compiler + runner)
│   ├── pathrefs.py, utils.py
│   ├── clients/           # llm_client, main_client
│   ├── schemas/           # task, execution
│   ├── storage/           # db, models, queries
│   └── tools/             # @tool, ToolContext, ошибки
├── deploy/                # docker-compose.yaml, .env.example
├── docs/SPEC.md
├── misc/make_demo_data.py # демо-папка для сценария еженедельного отчёта
├── tests/
├── launcher.py            # все сервисы процессами или один по имени
├── Dockerfile
├── .env.example
└── pyproject.toml
```

Отдельного сервиса storage нет: это пакет `common/storage`, который импортирует каждый сервис. Правила записи — в разделе «База данных». Скиллы от агента (`skills/`, Deno) пока не реализованы.

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
| `PUT /tasks/{id}/send_mode {mode}` | compiler | `draft` (по умолчанию): письма ждут одобрения в черновиках; `send`: задача отправляет сама |
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
- **Регистрация команд.** executor при старте сам сканирует `internal_tools/`, обновляет таблицу `tools` и регистрируется в main. main не знает кода инструментов, источник правды один — папка с командами.

### scheduler :8005

Отдельный сервис, который решает только «когда запускать». Runner напрямую не вызывает, только через main, как и все остальные.

- При старте и по `POST /reload {task_id?}` читает активные версии задач и пересобирает свою таблицу `triggers`. `GET /jobs` показывает, что и когда запустится.
- **Типы триггеров:** `cron` (APScheduler) и `manual` (без расписания). Scheduler знает только время запуска: почту, папки и прочее он не читает. «Ответить на новые письма» или «разобрать новые файлы» — это шаги задачи (`mail.search`, `fs.list` и т. д.), запускаемой по cron.
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
| `fs` | list, copy, move, write | read, write | pathlib, только внутри разрешённых папок |
| `docs` | read, fill\_template, make\_template | read, write | python-docx, docxtpl, openpyxl, pdfplumber |
| `xlsx` | read\_table, write\_table, aggregate | read, write | openpyxl; `aggregate` считает суммы, группы и топ-N кодом — числа в отчётах никогда не считает LLM |
| `mail` | search, read, mark, create\_draft, send | read, write, external | stdlib imaplib/smtplib |
| `charts` | bar, line | write | matplotlib → PNG |
| `llm` | extract, classify, write | none | common.llm, structured output |
| `user` | ask, notify | none | approvals и events в main |

**Почта.** Агент видит один набор команд `mail.*`, адаптер выбирается по `accounts.kind`. `imap_smtp` покрывает Gmail, Яндекс, Mail.ru, Outlook.com и корпоративные серверы; адреса серверов лежат в `adapters/presets.py`. `outlook_com` появится позже, в v1 на Windows тоже работаем через IMAP/SMTP. Черновик через IMAP — это `APPEND` в папку «Черновики».

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

Сторонние MCP-серверы будут подключаться тем же механизмом через настройки и по умолчанию выключены.

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
| `trigger` | `cron` (с `expr`) или `manual`: только когда запускать |
| `permissions` | Всё, что задаче можно: папки, аккаунты, адресаты, домены. compiler выводит их из аргументов шагов |
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
| `tasks` | compiler | id, title, status, send\_mode, current\_version\_id, source\_session\_id, created\_at, updated\_at | все |
| `task_versions` | compiler | id, task\_id, version\_no, spec\_json, status, parent\_version\_id, patch\_json, summary, created\_by, created\_at, approved\_at | runner, scheduler, main |
| `triggers` | scheduler | id, task\_id, type, config\_json, next\_fire\_at, enabled | main |
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

1. Шаг `mail.send`, у задачи `send_mode = draft` → executor создаёт черновик и возвращает `needs_approval`.
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
| Письмо ушло не тому | По умолчанию задача кладёт письмо в черновики и ждёт одобрения. Отправлять сразу её переключает только человек (`send_mode = send`), компилятор этого не умеет |

Секреты аккаунтов и скиллов читает только executor, в момент вызова команды. Ключ к LLM, если он есть, задаётся через `LLM_API_KEY` и читается `common/clients/llm_client.py`; нашему серверу ключ не нужен. На Windows секреты лежат в Credential Manager, в Docker — в файле `data/secrets.enc`, зашифрованном Fernet с ключом из `KEYRING_PASSWORD` (scrypt).

## Конфигурация и развёртывание

Все настройки — переменные окружения, их читает `Settings.from_env()` (`common/settings.py`). Неверное значение останавливает запуск с `SettingsError`, а не всплывает позже. Обязательна только `LLM_MODEL`. Для локального запуска `launcher.py` подхватывает `.env` из корня репозитория, в Docker — `deploy/.env` через compose.

### Переменные

| Переменная | Без Docker | В Docker | Зачем |
| --- | --- | --- | --- |
| `MAIN_URL` | `http://127.0.0.1:8000` | `http://main:8000` | Куда сервисы шлют запросы |
| `SERVICE_HOST` | `127.0.0.1` | `0.0.0.0` | На каком адресе слушать |
| `SELF_URL` | `http://127.0.0.1:<порт>` (умолчание) | `http://<имя сервиса>:<порт>` | Адрес, который сервис сообщает main при регистрации |
| `DATA_DIR` | `data` | `/data` (том) | app.db, шаблоны, готовые файлы, secrets.enc |
| `WORKSPACE_DIR` | `workspace` или `C:/Users/<имя>/Documents` | `/workspace` | Корень папок пользователя |
| `LLM_BASE_URL` | `http://llm-host:8000/v1` | то же | Любой OpenAI-совместимый `/v1` с вызовом инструментов |
| `LLM_API_KEY` | пусто | пусто | Пусто — без заголовка Authorization |
| `LLM_NO_PROXY` | `true` | `true` | Не брать прокси из окружения, как `--noproxy` в b.sh |
| `LLM_MODEL` | обязательна | обязательна | Одна модель на всё |
| `LLM_MODEL_CHAT` | необяз. | необяз. | Переопределение: агент в чате и компиляция задач |
| `LLM_MODEL_INSTRUCT` | необяз. | необяз. | Переопределение: `llm.*`-команды, разметка шаблонов |
| `LLM_CHAT_REASONING_EFFORT` | пусто | пусто | `low`/`medium`/`high` для reasoning-моделей; пусто — не отправляется |
| `LLM_MODEL_EMBED` | `bge-m3` | то же | Отбор инструментов; пусто — агент видит все |
| `LLM_EMBED_BASE_URL`, `LLM_EMBED_API_KEY` | пусто | пусто | Отдельный сервер эмбеддингов; по умолчанию `LLM_BASE_URL`, ключ основного сервера не наследуется |
| `API_TOKEN` | пусто | пусто или задан | Пусто — внешний API main без проверки |
| `INTERNAL_TOKEN` | генерирует launcher.py | задаётся в `deploy/.env` | Защита внутренних маршрутов |
| `KEYRING_BACKEND` | `system` | `file` | Где хранить пароли почты |
| `KEYRING_PASSWORD` | не нужен | задаётся | Ключ к `secrets.enc` |
| `AGENT_*`, `COMPILER_*`, `TOOL_TIMEOUT_SEC`, `LLM_TIMEOUT_SEC`… | | | Лимиты и таймауты с разумными умолчаниями |

Порты заданы умолчаниями (8000–8005) и переопределяются `PORT`. Каждый сервис сам сообщает main свой `SELF_URL`, поэтому список адресов поддерживать не нужно.

**Пути в задачах только относительные** — от `WORKSPACE_DIR` (`Отчёты/Продажи`) или `data:templates/…`, `data:outputs/…`. Одна и та же задача работает и на Windows, и в контейнере. Преобразование и проверку делает `executor/paths.py`.

### Без Docker

`python launcher.py` запускает шесть процессов uvicorn (сначала main, потом остальные), собирает логи в одну консоль с префиксом сервиса и перезапускает упавшие. `python launcher.py runner` поднимает один сервис, `--reload` перезапускает при правке кода. Bash-скриптов нет — одинаково на Windows, Linux и macOS.

### В Docker

`Dockerfile`: python:3.12-slim, non-root пользователь, зависимости из `pyproject.toml` через `tomllib`. `deploy/docker-compose.yaml`: один образ, по контейнеру на сервис (`command: python launcher.py <service>`), наружу опубликован только main, остальные в сети compose. Отличия от локального запуска заданы в `environment` compose: адреса, `/data`, `/workspace`, `KEYRING_BACKEND=file`. Шаблон настроек — `deploy/.env.example` (блок DEPLOY SETTINGS + настройки приложения, `TZ` для cron).

Нюансы Docker Desktop на Windows:

- **app.db лежит в именованном томе `agent-data`, а не в папке Windows** — блокировки SQLite в режиме WAL ненадёжны через bind mount из Windows. Папка пользователя монтируется обычно.

## Требования к LLM

Код не привязан к конкретным моделям. Нужен OpenAI-совместимый `/v1/chat/completions` с вызовом инструментов; всё остальное — с фолбэками:

- **Structured output.** `LLMClient.structured` перебирает `json_schema` → `json_object` → обычный текст со схемой в промпте и запоминает, какой режим сработал для модели. Переход между режимами не тратит попыток; попытки (3) тратят только ответы, не прошедшие валидацию, — ошибки возвращаются модели.
- **Эмбеддинги** необязательны: без них агент видит все инструменты.
- **`reasoning_effort`** отправляется, только если задан.
- **Сбои сервера** (500 на кривом вызове инструмента) — ход модели повторяется до 3 раз; пустой ответ без вызовов получает один «пинок» продолжить.
- **Даты.** Модели плохо считают календарь, поэтому прошлая/текущая неделя и прошлый месяц подставляются в системный промпт готовыми.

Наблюдения на сервере команды (октябрь 2026):

| Модель | Как ведёт себя | Где используем |
| --- | --- | --- |
| `gpt-oss:120b` | Доводит многошаговые цепочки до конца; constrained JSON отдаёт пустым — работает через текстовый режим; 20–40 с на шаг | `LLM_MODEL_CHAT`: агент и компиляция |
| `qwen3:30b` | Быстрая, хорошо делает одиночные вызовы и JSON; в многошаговом чате останавливается после первого инструмента | `LLM_MODEL`: `llm.*`-команды |
| `llama3.3:70b` | Путается в путях, останавливается на полпути | не используем |
| `deepseek-r1:70b` | Сервер не поддерживает для неё вызов инструментов | не используем |
| `bge-m3` | Эмбеддинги | `LLM_MODEL_EMBED` |

## Стек

| Задача | Библиотеки |
| --- | --- |
| HTTP-сервисы | FastAPI, uvicorn, httpx, sse-starlette |
| Схемы | pydantic v2 |
| БД | SQLite (WAL), SQLAlchemy 2 (`create_all`, миграций пока нет) |
| LLM | httpx напрямую к OpenAI-совместимому API |
| Шаблоны и правки Task | Jinja2 (sandbox), jsonpatch, jsonschema |
| Расписание | APScheduler 3 |
| Документы | python-docx, docxtpl, openpyxl, pdfplumber, matplotlib |
| Почта | stdlib imaplib/smtplib |
| Секреты | keyring, cryptography (Fernet + scrypt) |
| Запуск | `launcher.py` или docker compose |
| Тесты | pytest, pytest-asyncio |

## Состояние и план

- [x] `common`, main, executor и 22 встроенные команды (включая `data.map_rows`)
- [x] agent-loop: цикл, трейс, мета-инструменты, события прогресса
- [x] compiler: compile с пробным запуском и починкой, validate, patch, diff
- [x] runner (пауза/продолжение) и scheduler (cron, папка, почта)
- [x] launcher, Docker, тесты
- [x] Сквозной сценарий «пример отчёта → такой же отчёт за прошлую неделю» на сервере команды
- [x] Сквозной сценарий целиком на `qwen3.8-27b-fp8` (vLLM): отчёт по примеру → «формируй задачу» (с первой попытки, пробный запуск верный) → одобрение в чате → запуск по кнопке за ~0,1 с без LLM → правка «топ-5, по пятницам» с diff → новая версия в расписании
- [ ] Почта в сценарии (нужен тестовый ящик)
- [ ] Фронтенд
- [ ] Скиллы от агента (Deno + MCP)
- [ ] Alembic-миграции

### Принятые решения

- **Все вызовы между сервисами идут через main**, сервисы знают только `MAIN_URL`.
- **Запуск всегда отдельными процессами**, режима монолита нет.
- **LLM — любая OpenAI-совместимая с вызовом инструментов**, модели задаются только конфигом (роли CHAT и INSTRUCT необязательны).
- **Целевая ОС — Windows, без Outlook COM.** Почта через IMAP/SMTP, пути через pathlib.
- **Токен на внешний API опционален**, internal-токен защищает внутренние маршруты.
- **Docker опционален**, код один и тот же.
- **Числа в отчётах считает код** (`xlsx.aggregate`), LLM только размечает и извлекает текст.
