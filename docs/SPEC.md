# ТЗ: Офисный агент (хакатон)

Проект fintech2026 · 16.10.2026

## Цель и рамки

Локальный бэкенд на Python и FastAPI. Офисный сотрудник объясняет задачу в обычном чате, а система превращает разговор в замороженный сценарий (Task) и исполняет его по расписанию или по команде. Фронтенд в это ТЗ не входит: всё доступно через HTTP API сервиса main.

**Принципы**

- **Чат — это запись.** Агент пробует и показывает результат, пользователь поправляет.
- **Task — замороженный план.** При запуске ничего не планируется заново, LLM работает только внутри шагов `llm.*`.
- **Любое действие на компьютере идёт через executor.** Там проверяются аргументы, подставляются секреты и пишется лог.
- **Права задачи — это её одобренные шаги.** Отдельного списка прав нет: пользователь одобряет конкретные аргументы шагов. Команды простые (`mail.send` отправляет тем, кому сказали), а отбор того, с чем задача работает, делает команда, которая ищет: `mail.search.sender_in: ["*@company.ru"]` отдаёт только письма от этих адресов.
- **Секреты не попадают в LLM.** Агент и задачи знают только имя аккаунта.
- **Изменения только через версии.** Каждая правка задачи создаёт новую версию, а активной она становится только после подтверждения.
- **Одно одобрение — задачи целиком.** Пользователь одобряет собранную задачу (шаги с их аргументами, пробный запуск). Запуски на действиях не останавливаются; как ведёт себя внешнее действие (например, письмо в черновики или сразу), решает команда по настройкам своей группы, которые меняет только пользователь.
- **Каждая сущность знает только своё.** Scheduler знает «когда», runner — «в каком порядке», executor — «чем», а смысл почты, файлов и прочего — только их группы команд в `internal_tools/`.

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
чат и трейс  чат → Task    исполняет Task  команды, секреты    cron
   │             │              ┆              │
   ▼             ▼              ┆              ▼
 LLM-провайдер (OpenAI API)     ┆        internal_tools (скиллы Deno + MCP — план)
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
│   ├── orchestrator.py    # compile → approval, approve → reload
│   ├── events.py          # шина событий (таблица events + SSE-подписчики)
│   ├── proxy.py, auth.py, views.py
│   └── api/               # internal, gateway, tasks, approvals
├── agent_loop/            # чат с LLM, вызов инструментов, трейс
│   ├── main.py
│   ├── loop.py            # цикл хода, повторы, «пинок» при пустом ответе
│   ├── catalog.py         # отбор инструментов по эмбеддингам
│   ├── meta_tools.py      # task_compile, task_patch, task_reject, run_status…
│   ├── history.py, prompts.py
│   └── api/chat.py        # /v1/chat/completions, /sessions
├── compiler/              # трейс чата → Task, правки → JSON Patch
│   ├── main.py, compile.py
│   ├── validate.py        # проверка по каталогу, включая ссылки шаг.output.поле
│   ├── task_settings.py   # настройки групп команд для задачи (из каталога)
│   ├── cards.py           # карточки шагов, расписание словами, diff
│   ├── store.py, prompts.py
│   └── api/tasks.py
├── runner/                # исполнение Task по шагам
│   ├── main.py, engine.py
│   └── api/runs.py
├── executor/              # единая точка вызова команд
│   ├── main.py, runtime.py
│   ├── registry.py        # каталог из internal_tools/ → таблица tools
│   ├── paths.py           # песочница путей: только рабочая папка и data:templates / data:outputs
│   ├── secrets.py         # keyring или зашифрованный файл
│   └── api/               # execute (+ /tools), accounts
├── scheduler/             # cron: когда запускать задачи
│   ├── main.py, jobs.py
│   └── api/jobs.py
├── internal_tools/        # встроенные команды: файл = команда (или пара с общим входом)
│   ├── fs/                # list, transfer (copy/move), write
│   ├── docs/              # read, fill_template, make_template, readers
│   ├── xlsx/              # read_table, write_table, aggregate
│   ├── mail/              # search, read, mark, send, create_draft, settings, adapters/
│   ├── data/              # map_rows
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
│   └── tools/             # @tool, ToolContext, DomainSettings, ошибки
├── deploy/                # docker-compose.yaml, .env.example
├── docs/SPEC.md
├── misc/make_demo_data.py # демо-папка для сценария еженедельного отчёта
├── tests/
├── launcher.py            # все сервисы процессами или один по имени
├── Dockerfile
├── .env.example
└── pyproject.toml
```

Отдельного сервиса storage нет: это пакет `common/storage`, который импортирует каждый сервис. Правила записи — в разделе «База данных». Скиллы от агента (`skills/`, Deno) пока не реализованы: в БД есть заготовка таблицы `skills`, кода нет.

## Сервисы

У каждого сервиса одна зона ответственности: main связывает, agent-loop разговаривает, compiler собирает задачи, scheduler решает «когда», runner — «в каком порядке», executor — «чем». Локально все слушают 127.0.0.1, в Docker наружу публикуется только порт main.

### main :8000

Единственная точка входа и для клиента, и для сервисов между собой. Внутри нет бизнес-логики задач и инструментов, только маршрутизация, оркестрация и подтверждения.

- **Реестр сервисов.** Каждый сервис при старте шлёт `POST /internal/register {name, url}`, main периодически проверяет `GET /health`. Так сервисы не знают адресов друг друга, только адрес main.
- **Оркестрация.** Сценарии, где участвуют несколько сервисов (подтвердить версию, затем перезагрузить триггеры), живут в `main/orchestrator.py`.
- **Одобрения и события.** Одобрение одно — версии задачи; main создаёт его после compile или patch. События сервисы шлют через `POST /internal/events`, main хранит и отдаёт клиенту через SSE.

Внешний API (для будущего фронта и curl):

| Метод и путь | Куда идёт | Что делает |
| --- | --- | --- |
| `POST /agent/v1/chat/completions` | agent-loop | Чат в OpenAI-совместимом формате, `session_id` в `metadata` |
| `GET /agent/sessions`, `GET /agent/sessions/{id}` | agent-loop | Список сессий, история и трейс |
| `POST /compiler/compile` | compiler | `{session_id, title?, hint?}` → черновик задачи и approval `version_approve` |
| `POST /compiler/patch` | compiler | `{task_id, instruction}` → черновик новой версии и diff |
| `GET /tasks`, `GET /tasks/{id}` | storage | Задачи, текущая версия, карточки шагов |
| `GET /tasks/{id}/versions/{v}/diff` | compiler | Разница с предыдущей версией |
| `POST /tasks/{id}/versions/{v}/approve` | compiler, затем scheduler | Сделать версию активной |
| `POST /tasks/{id}/pause`, `/resume`, `/archive` | compiler, затем scheduler | Управление задачей |
| `GET`, `PUT /tasks/{id}/settings` | compiler | Настройки доменов задачи с подписями; `PUT {домен: {поле: значение}}` меняет значения, ключи добавить или удалить нельзя |
| `POST /runs` | runner | `{task_id, dry_run?}` → запуск вручную |
| `GET /runs/{id}`, `GET /tasks/{id}/runs` | storage | Статус запуска и его шаги; запуски `empty` в истории только с `?include_empty=true` |
| `GET /runs/{id}/log` | storage | Журнал запуска целиком: старт, каждый шаг, уведомления, конец |
| `POST /runs/{id}/cancel` | runner | Отменить запуск |
| `GET /approvals`, `POST /approvals/{id}/answer {approve}` | main, затем compiler | Одобрение версий задач (`version_approve`) — единственное, что ждёт пользователя |
| `GET /tools`, `GET /tools/{name}` | executor | Каталог инструментов со схемами |
| `GET /accounts`, `POST /accounts`, `GET /accounts/presets`, `POST /accounts/{name}/check`, `DELETE /accounts/{name}` | executor | Почтовые аккаунты; пароль сразу уходит в keyring |
| `GET /settings`, `PUT /settings` | main | Хранилище «ключ → значение» для будущего UI; сервисы его пока не читают |
| `GET /events`, `GET /events/recent` | main | Поток событий (SSE) и последние события JSON |

Внутренние маршруты (нужен internal-токен): `POST /internal/register`, `POST /internal/events`, `POST /executor/execute` (вызывают agent-loop и runner), `POST /runner/runs` (вызывает scheduler).

В исходной схеме был путь `/agent/v1/chat/compatible`. Лучше `/chat/completions`: тогда любой OpenAI-клиент работает с нами без настроек, достаточно указать `base_url`.

### agent-loop :8001

Ведёт диалог: собирает контекст, отбирает инструменты, вызывает LLM, исполняет tool calls через executor и пишет трейс.

- **API:** `POST /v1/chat/completions` (со стримингом), `GET /sessions`, `GET /sessions/{id}`.
- **Цикл:** история сессии → отбор инструментов (топ-`AGENT_TOOLS_TOP_K`=24 по эмбеддингам плюс уже использованные в сессии и `fs.list`, `docs.read`; без эмбеддингов — все) → LLM → tool calls → `POST main/executor/execute` с `context.mode = "chat"` → результат в историю и в `trace_events` → снова LLM. Остановка, когда ответ пришёл без tool calls, или после `AGENT_MAX_ITERATIONS`=16 итераций.
- **Мета-инструменты** управляют самой системой, а не компьютером: `task_compile`, `task_patch`, `task_reject`, `task_list`, `task_run_now`, `task_control` (пауза, возобновление, архив), `run_status`, `action_confirm`. Их обрабатывает agent-loop через main, в executor они не идут. Включить задачу агент не может: такого инструмента нет, и отвечать на одобрения он не умеет.
- **Внешние действия в чате** (`side_effect: external` по каталогу) спрашивает сам agent-loop: в executor такой вызов не уходит. Agent-loop проверяет аргументы по input-схеме, записывает вызов в трейс со статусом `needs_approval` и возвращает модели `action_id`; агент спрашивает пользователя прямо в диалоге и после «да» вызывает `action_confirm` — только тогда вызов идёт в executor, один раз. Зависимые шаги (пометить письмо прочитанным и т. п.) агент делает только после подтверждения.

### compiler :8002

Превращает чат в Task и единственный пишет в `tasks` и `task_versions`.

- `POST /compile {session_id, title?, hint?}`: читает `chat_messages` и `trace_events`, просит LLM собрать Task JSON, проверяет, сохраняет версию `draft` с настройками групп по умолчанию и делает dry run; если пробный запуск упал, показывает LLM ошибку и чинит задачу (до 2 раз). Ответ: `{task_id, version_id, title, summary, trigger, settings, cards, dry_run}`.
- **Проверки собранной задачи:** схема pydantic; команды и их аргументы есть в каталоге; cron разбирается; переменные и ссылки `шаг.output.поле` существуют в output-схемах; фильтры Jinja известны; задача повторяет хотя бы одно действие из чата; тексты из чата перенесены дословно (почти совпадающая строка с опечаткой отклоняется с оригиналом). Ошибки уходят LLM обратно, до `COMPILER_MAX_ATTEMPTS`=3 попыток.
- `POST /patch {task_id, instruction}`: LLM выдаёт JSON Patch к текущей версии → новая версия `draft` и diff.
- `POST /validate {spec}`: проверки схемы и каталога (без проверок по чату), без LLM.
- `POST /tasks/{id}/versions/{v}/approve`, `/reject`, `/pause`, `/resume`, `/archive`: меняет статусы; при одобрении версии настройки задачи приводятся к её командам.
- `GET`, `PUT /tasks/{id}/settings`: настройки групп команд (см. «Настройки группы»).

### runner :8003

Исполняет активную версию задачи по шагам, сам ничего не планирует.

- `POST /runs {task_id или task_version_id, trigger, dry_run, wait}`: создаёт запись в `runs`, исполняет в фоне и сразу отвечает `run_id` (с `wait` — ждёт конца; так делает dry run компилятора).
- `POST /runs/{id}/cancel` отменяет.
- **Движок:** берёт `current_version_id` → считает `variables` → для каждого шага рендерит `args` (Jinja2, StrictUndefined) → `POST main/executor/execute` → пишет шаг в журнал → обрабатывает `on_error`.
- **Один журнал на запуск** (`run_log`, по `task_id` + `run_id`, записи только добавляются):
  - старт: название, версия, откуда запуск, пробный ли, настройки, посчитанные переменные;
  - каждый шаг и каждая попытка: команда, подставленные аргументы, выход (длинный обрезается), статус, ошибка с traceback, длительность, строки самой команды (`ctx.log`);
  - уведомления: что команда сказала пользователю через `ctx.notify` (`user.notify`). Вне пробного запуска они уходят через `POST main/internal/notify` в чат, из которого собрана задача;
  - конец: статус, ошибка, итог.
- **Срок хранения:** раз в 6 часов (и при старте) runner удаляет запуски, закончившиеся раньше `RUN_RETENTION_DAYS` (15) дней назад, вместе с журналом и папкой `data:outputs/<run_id>`.
- **Запуск никогда не ждёт человека** и идёт от начала до конца. Ошибка шага по `on_error`: `fail` (запуск упал), `retry`, `skip`; пустой результат по `on_empty`: `continue`, `fail`, `skip` (тихо закончить). Где нужен человек, команда оставляет результат вне запуска: письмо в черновиках, файл в папке на проверку.
- Один запуск задачи за раз: если предыдущий ещё идёт, runner отвечает 409, scheduler пишет это в лог и ждёт следующего срабатывания.
- Если все шаги вернули пустой результат (проверка по расписанию ничего не нашла), запуск получает статус `empty`.
- В каждый вызов executor runner передаёт настройки задачи (`task_settings`).

### executor :8004

Единственное место, где выполняются инструменты. И чат, и runner зовут его одним форматом.

- `POST /execute {tool, args, context}` → `{status, output, artifacts}`, где status: `ok` или `error`. Executor ничего не спрашивает: кто вызывает, тот уже решил, что вызов нужен (чат спросил пользователя, задачу пользователь одобрил). В `context`: режим (`chat` или `run`), `session_id` или `run_id` + `step_id`, настройки задачи (`task_settings`), `dry_run`.
- `GET /tools`, `GET /tools/{name}`, `POST /tools/reload`; аккаунты: `GET`, `POST /accounts`, `GET /accounts/presets`, `POST /accounts/{name}/check`, `DELETE /accounts/{name}`.
- **Конвейер вызова:** найти инструмент → проверить args по input-схеме → проверить аргументы-пути → пробный запуск по объявлению `dry_run` команды (`skip`, `draft` или `real`) → загрузить аккаунты для аргументов из `secrets` → вызвать с таймаутом и `ToolContext` → проверить output. Своего журнала у executor нет: всё для журнала вызывающего (длительность, traceback, строки `ctx.log`, уведомления `ctx.notify`) уходит в ответе.
- **Executor знает типы аргументов, а не группы.**
  - Аргумент типа `WorkspacePath` (или `FileRefs` — список файлов) executor до вызова проверяет песочницей `executor/paths.py`: только рабочая папка и `data:templates` / `data:outputs`, без `..` и симлинков наружу. Входы `llm.*` (`TextOrFileRefs` — путь или обычный текст) заранее не проверяются; файл из них открывается через тот же `ctx.read_path`.
  - Аргументы из `secrets` называют сохранённый аккаунт: executor сам достаёт его настройки и пароль из keyring и отдаёт команде как `ctx.credential("account")`. Неизвестный аккаунт — отказ до вызова.
  - Что именно задаче можно, задают её одобренные шаги; отбор подставляемых значений делают команды поиска (`mail.search.sender_in`).
- **Регистрация команд.** executor при старте сам сканирует `internal_tools/`, обновляет таблицу `tools` и регистрируется в main. main не знает кода инструментов, источник правды один — папка с командами.

### scheduler :8005

Отдельный сервис, который решает только «когда запускать». Runner напрямую не вызывает, только через main, как и все остальные.

- При старте и по `POST /reload {task_id?}` читает активные версии задач и пересобирает свою таблицу `triggers`. `GET /jobs` показывает, что и когда запустится.
- **Типы триггеров:** `cron` (APScheduler) и `manual` (без расписания). Scheduler знает только время запуска: почту, папки и прочее он не читает. «Ответить на новые письма» или «разобрать новые файлы» — это шаги задачи (`mail.search`, `fs.list` и т. д.), запускаемой по cron.
- Срабатывание → `POST main/runner/runs {task_id, trigger}`.

## Инструменты (команды)

Одна команда = один Python-файл с декоратором `@tool` (или пара команд с общим входом). Схемы, описание для LLM и текст для карточки лежат в этом же файле, отдельной регистрации нет. Посмотреть все команды: `GET /tools`.

```python
# internal_tools/mail/send.py (сокращённо)
class Input(ToolInput):
    account: str = Field(description="Имя почтового аккаунта отправителя")
    to: list[EmailAddress] = Field(min_length=1)
    cc: list[EmailAddress] = Field(default_factory=list)
    subject: str
    body: str
    attachments: list[WorkspacePath] = Field(default_factory=list, description="Пути файлов для вложения")

class Output(ToolOutput):
    message_id: str
    mode: str                     # "sent" или "draft"
    folder: str | None = None

@tool(
    name="mail.send",
    title="Отправить письмо",
    card="Отправить письмо {to} с темой «{subject}»",
    description="Send an email ... In a task it saves a draft ... unless the task's mail settings say to send directly.",
    side_effect="external",
    secrets=("account",),                           # executor загрузит аккаунт по имени
    dry_run="draft",
    timeout=120,
)
async def send(inp: Input, ctx: ToolContext) -> Output:
    account = ctx.credential("account")             # настройки и пароль, уже загруженные executor
    message = imap_smtp.build_message(...)

    if ctx.draft or (ctx.mode == "run" and not ctx.domain_settings(MailSettings).send_directly):
        folder = await asyncio.to_thread(imap_smtp.append_draft, account.config, account.secret, message)
        return Output(message_id=message["Message-ID"], mode="draft", folder=folder)

    await asyncio.to_thread(imap_smtp.send, account.config, account.secret, message)
    return Output(message_id=message["Message-ID"], mode="sent")
```

| Поле | Зачем |
| --- | --- |
| `name` | `группа.действие`, по нему на команду ссылаются шаги Task |
| `title`, `card` | Название и шаблон фразы для карточки шага |
| `description` | Описание для LLM |
| `Input`, `Output` | Pydantic-модели, из них строятся JSON-схемы для LLM и для валидации |
| `side_effect` | `none`, `read`, `write` или `external`; `external` в чате требует подтверждения |
| `secrets` | Какие аргументы называют сохранённый аккаунт; executor загружает его и отдаёт как `ctx.credential(<аргумент>)` |
| Типы аргументов | `WorkspacePath`, `FileRefs` — пути, executor проверяет их до вызова; `TextOrFileRefs` — путь или текст, не проверяется заранее |
| `dry_run` | Что делать в пробном запуске: `skip`, `draft` или `real` |
| `timeout` | Секунды до отмены |

### Стартовый набор

| Группа | Команды | side\_effect | На чём |
| --- | --- | --- | --- |
| `fs` | list, copy, move, write | read, write | pathlib, только внутри разрешённых папок |
| `data` | map\_rows | none | готовит строки таблиц шаблона выражениями над `row` |
| `docs` | read, fill\_template, make\_template | read, write | python-docx, docxtpl, openpyxl, pdfplumber |
| `xlsx` | read\_table, write\_table, aggregate | read, write | openpyxl; `aggregate` считает суммы, группы и топ-N кодом — числа в отчётах никогда не считает LLM |
| `mail` | search, read, mark, create\_draft, send | read, write, external | stdlib imaplib/smtplib |
| `charts` | bar, line | write | matplotlib → PNG |
| `llm` | extract, classify, write | none | common.llm, structured output |
| `user` | notify | none | в журнал запуска и сообщением в чат задачи (ничего не ждёт) |

**Настройки группы.** Группа может объявить одну плоскую pydantic-модель `DomainSettings` (`@domain_settings`): тип, ограничения, подпись и описание каждого поля, значение по умолчанию. Схема попадает в манифест команд группы (`settings_schema`, LLM её не видит). Задача, в шагах которой есть команды группы, получает раздел `tasks.settings[группа]` со значениями по умолчанию; при новой версии значения пользователя сохраняются, раздел ненужной группы убирается. Менять значения может только пользователь (`PUT /tasks/{id}/settings`); команда читает свой раздел через `ctx.domain_settings(Модель)`. Пример — `mail.send_directly` (по умолчанию `false`): `mail.send` в запуске задачи кладёт письмо в черновики, отправляет его человек из своего почтового клиента.

**Почта.** Один набор команд `mail.*` поверх stdlib imaplib/smtplib (`adapters/imap_smtp.py`), единственный вид аккаунта — `imap_smtp`. Пресеты Gmail, Яндекса, Mail.ru и Outlook.com лежат в `adapters/presets.py`; корпоративный сервер задаётся `config` аккаунта. Режим соединения — `security` (или отдельно `imap_security` / `smtp_security`): `ssl`, `starttls` или `none`; SMTP логинится, только если сервер предлагает AUTH. Детали:

- `mail.search` принимает `sender_in` — адреса или маски `*@домен`, письма от кого вообще брать; сравнивается адрес отправителя целиком (`test.local` не пропустит `x@evil-test.local.com`). Компилятор заполняет его, когда задача отвечает найденным письмам или что-то с ними делает. Подделанный From не страшен: ответ уходит на этот адрес, то есть в разрешённый домен;
- черновик — это `APPEND` в папку с флагом `\Drafts` (или «Drafts»; создаётся, если её нет);
- `mail.search` фильтрует отправителя и тему у себя по раскодированным заголовкам — серверы ищут по-разному; письма с флагом `\Deleted` не показывает;
- `mail.mark` ставит или снимает «прочитано» и перекладывает письмо (MOVE, а без него COPY + `\Deleted` + `UID EXPUNGE` только этого письма; общий EXPUNGE не делается, чтобы не стереть чужие удалённые письма);
- имена папок кодируются в modified UTF-7, так что кириллические папки работают.

**Мета-инструменты** агента не лежат в `internal_tools`: они управляют системой и живут в `agent_loop/meta_tools.py`.

### Скиллы, которые пишет агент (план, не реализовано)

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

Задача и её версии хранятся отдельно. `tasks` — это имя, статус, ссылка на активную версию и настройки групп команд (`settings`, их меняет только пользователь); `task_versions` — неизменяемые снимки сценария в JSON. Схема описана в `common/schemas/task.py` (pydantic), её же compiler отдаёт LLM как JSON-схему.

```json
{
  "schema_version": 1,
  "title": "Еженедельный отчёт по продажам",
  "trigger": { "type": "cron", "expr": "0 10 * * MON" },
  "variables": {
    "week_start": "{{ today | start_of_prev_week }}",
    "week_end": "{{ today | end_of_prev_week }}"
  },
  "steps": [
    { "id": "sales_files", "tool": "fs.list",
      "args": { "folder": "Отчёты/Продажи", "pattern": "*.xlsx",
                "modified_from": "{{ week_start }}", "modified_to": "{{ week_end }}" },
      "on_empty": "skip" },
    { "id": "totals", "tool": "xlsx.aggregate",
      "args": { "inputs": "{{ sales_files.output.files }}", "sum_columns": ["Сумма, руб."],
                "group_by": "Клиент", "top_n": 5 } },
    { "id": "top_clients", "tool": "data.map_rows",
      "args": { "rows": "{{ totals.output.groups }}", "limit": 5,
                "fields": { "client": "row.key", "amount": "row.sums['Сумма, руб.'] | format_number" } } },
    { "id": "report", "tool": "docs.fill_template",
      "args": { "template": "data:templates/weekly_sales.docx",
                "context": { "weekly_revenue": "{{ totals.output.totals['Сумма, руб.'] | format_number }} руб.",
                             "order_count": "{{ totals.output.rows }}",
                             "top_clients": "{{ top_clients.output.rows }}" },
                "output_name": "Отчёт {{ week_start | format_date('%d.%m.%Y') }}.docx" } },
    { "id": "send_report", "tool": "mail.send",
      "args": { "account": "work_mail", "to": ["boss@company.ru"],
                "subject": "Отчёт по продажам за неделю",
                "body": "Выручка: {{ totals.output.totals['Сумма, руб.'] | format_number }} руб.",
                "attachments": ["{{ report.output.path }}"] } }
  ]
}
```

| Поле | Что это |
| --- | --- |
| `schema_version` | Версия формата, сейчас 1 |
| `trigger` | `cron` (с `expr`) или `manual`: только когда запускать |
| `variables` | Считаются один раз в начале запуска (даты, имена файлов) |
| `steps[].tool`, `args` | Команда из каталога и её аргументы; `{{ step_id.output }}` ссылается на результат прошлого шага |
| `steps[].when` | Условие выполнения шага |
| `steps[].foreach` | Повторить шаг для каждого элемента списка; нужно для сортировки документов |
| `steps[].on_error` | `fail`, `retry` (с `retries`) или `skip` |
| `steps[].on_empty` | `continue`, `fail` или `skip` (запуск тихо заканчивается) |

Настроек групп (`mail.send_directly` и т. п.) в сценарии нет: они хранятся в `tasks.settings`, LLM их не видит и не задаёт.

Сортировка документов с `foreach` выглядит так:

```json
{ "id": "sort", "foreach": "{{ files.output.files }}", "as": "file", "steps": [
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

«Пометка как используемый» из исходной схемы — это approve версии. Runner всегда исполняет `current_version_id` и записывает id версии в `runs`, поэтому история запусков показывает, какой именно сценарий отработал. Дублировать активную версию в `data/tasks/<id>.yaml` для чтения глазами и git можно, но это не сделано.

## База данных

Один файл `data/app.db` (SQLite), доступ через `common/storage` на SQLAlchemy 2. Таблицы создаёт `create_all`; миграций пока нет, поэтому новую колонку в существующей базе нужно добавить вручную. Включаем `journal_mode=WAL` и `busy_timeout=5000`, чтобы несколько процессов читали параллельно.

**Главное правило: у каждой таблицы ровно один сервис-владелец.** Только он пишет в таблицу, остальные читают напрямую или просят владельца через API. Так нет конфликтов записи и понятно, кто отвечает за данные.

| Таблица | Владелец | Основные поля | Кто читает |
| --- | --- | --- | --- |
| `settings` | main | key, value\_json | пока никто |
| `approvals` | main | id, kind, status, run\_id, task\_id, task\_version\_id, session\_id, payload\_json, answer\_json, created\_at, resolved\_at | runner, compiler, agent-loop |
| `events` | main | id, type, payload\_json, created\_at, read\_at | — |
| `chat_sessions` | agent-loop | id, title, status, created\_at | compiler, main |
| `chat_messages` | agent-loop | id, session\_id, role, content, tool\_calls\_json, tool\_call\_id, name, created\_at | compiler |
| `trace_events` | agent-loop | id, session\_id, message\_id, tool, args\_json, result\_json, status, user\_feedback, created\_at | compiler |
| `tasks` | compiler | id, title, status, settings\_json, current\_version\_id, source\_session\_id, created\_at, updated\_at | все |
| `task_versions` | compiler | id, task\_id, version\_no, spec\_json, status, parent\_version\_id, patch\_json, summary, created\_by, created\_at, approved\_at | runner, scheduler, main |
| `triggers` | scheduler | id, task\_id, type, config\_json, next\_fire\_at, enabled | main |
| `runs` | runner | id, task\_id, task\_version\_id, trigger, status, dry\_run, result\_json, error, started\_at, finished\_at | main, agent-loop, compiler |
| `run_log` | runner | id, run\_id, task\_id, seq, at, kind (start, step, notify, end), step\_key, data\_json | main |
| `tools` | executor | name, source, version, manifest\_json, enabled | agent-loop, compiler |
| `skills` | executor | заготовка под скиллы, не используется | — |
| `accounts` | executor | id, kind, name, config\_json, secret\_ref, created\_at | main (без секретов) |

Значения перечислений:

- `runs.status`: queued, running, succeeded, empty, failed, cancelled. `empty` — все шаги вернули пустой результат (проверка по расписанию ничего не нашла); `GET /tasks/{id}/runs` их не показывает, `?include_empty=true` показывает.
- `approvals.kind`: ask\_user, version\_approve, skill\_permissions.
- `tools.source`: internal (skill и mcp — план).
- `accounts.kind`: imap\_smtp.

`trace_events.user_feedback` задумано для правок пользователя к результату вызова («нет, возьми другую колонку»), но сейчас не заполняется: compiler отличает удачные вызовы от неудачных по статусу и по самому разговору. Если конкуренция за SQLite станет проблемой, storage можно вынести в отдельный сервис или перейти на Postgres; для хакатона это не нужно.

## Ключевые сценарии

Пять сценариев покрывают демо целиком. Везде «main/…» означает вызов через main, который проксирует запрос в нужный сервис.

### 1. Чат с инструментами

1. Клиент → `POST main/agent/v1/chat/completions` → agent-loop.
2. agent-loop → LLM, получает tool call `docs.read`.
3. agent-loop → `POST main/executor/execute` (`mode: chat`) → executor выполняет и возвращает результат.
4. agent-loop пишет `trace_events`, снова идёт в LLM и отвечает клиенту.

### 2. «Формируй задачу раз в неделю»

1. LLM вызывает мета-инструмент `task_compile` с расписанием и пожеланиями в `hint`.
2. agent-loop → `POST main/compiler/compile {session_id, title, hint}`.
3. compiler читает трейс, собирает Task, проверяет, сохраняет версию `draft` с настройками групп по умолчанию и делает dry run через `main/runner/runs`.
4. main создаёт approval `version_approve` и возвращает agent-loop карточки шагов, настройки и итог dry run.
5. Агент показывает пользователю: «Вот задача, вот что получилось на пробном запуске, письма будут сохраняться в черновики». Включить её агент не может: у него нет такого инструмента.
6. Пользователь сам одобряет черновик в списке задач → `POST main/approvals/{id}/answer` (или `POST main/tasks/{id}/versions/{v}/approve`) → main вызывает compiler (статусы), затем scheduler `/reload`.

### 3. Правка через чат

1. «Добавь Васю в копию» → мета-инструмент `task_patch {task_id, instruction}`.
2. compiler строит JSON Patch, сохраняет новую версию `draft` и считает diff по карточкам.
3. Агент показывает «было → стало», дальше как шаг 6 предыдущего сценария.

### 4. Запуск по расписанию

1. scheduler: сработал cron → `POST main/runner/runs {task_id, trigger: "cron"}`.
2. runner создаёт run и по шагам вызывает `main/executor/execute`.
3. executor выполняет команду с настройками задачи (пароль — через `ToolContext`) и всё нужное для журнала возвращает в ответе.
4. runner пишет каждый шаг в журнал запуска, уведомления пересылает в чат задачи, в конце шлёт событие `run.finished` в main, тот отдаёт его клиенту по SSE. Посмотреть, как прошло: `GET /runs/{id}/log`.

### 5. Отправка писем из задачи

Одобрение одно — задачи целиком, после компиляции. Запуск никогда не ждёт человека: `mail.send` по умолчанию (`mail.send_directly = false`) кладёт письмо в черновики и завершает шаг, человек проверяет и отправляет его сам. Не нашлось файлов за неделю — `on_empty: skip`, запуск тихо заканчивается со статусом `empty`.

## Безопасность и секреты

Все проверки делает код, а не LLM: при запуске задачи LLM не решает, что делать, а шаги и их аргументы одобрил пользователь.

| Риск | Мера |
| --- | --- |
| Секрет утёк в лог, промпт или git | Секреты только в keyring (Windows Credential Manager, macOS Keychain, Secret Service). В БД лежит `secret_ref`; аргументы команд секретов не содержат (аккаунт указывается по имени), поэтому журнал запуска их не видит |
| Prompt injection из письма или документа | Запуск исполняет только одобренные шаги; что берётся из данных, отбирает команда поиска (`mail.search.sender_in: ["*@company.ru"]` — письмо `attacker@evil.com` задача просто не увидит) |
| Агент написал опасный код | (план) Скиллы будут запускаться только в Deno с явными `--allow-*` и только после одобрения прав пользователем |
| Доступ к файлам вне задачи | Все пути команд проходят `executor/paths.py`: только рабочая папка и `data:templates` / `data:outputs`, только разрешённые задаче папки; `..` и симлинки наружу отклоняются |
| Агент сам включил задачу или отправил черновик | Не может: у агента нет `task_approve` и нет инструментов для ответа на одобрения |
| Чужой процесс дёргает API | Наружу открыт только main. Токен на внешний API опционален (пустой API\_TOKEN — без проверки), внутренние маршруты закрыты INTERNAL\_TOKEN |
| Шаблон в Task выполняет произвольный код | Jinja2 SandboxedEnvironment и StrictUndefined |
| Письмо ушло не тому | Адресаты — одобренные аргументы шага или отобранные `sender_in`; по умолчанию задача кладёт письма в черновики, отправляет их человек. Разрешить отправку сразу (`mail.send_directly`) может только он сам, компилятор настроек не видит |

Секреты аккаунтов читает только executor, в момент вызова команды. Ключ к LLM, если он есть, задаётся через `LLM_API_KEY` и читается `common/clients/llm_client.py`; нашему серверу ключ не нужен. На Windows секреты лежат в Credential Manager, в Docker — в файле `data/secrets.enc`, зашифрованном Fernet с ключом из `KEYRING_PASSWORD` (scrypt).

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
| `LLM_BASE_URL` | `http://127.0.0.1:11434/v1` (умолчание) | то же | Любой OpenAI-совместимый `/v1` с вызовом инструментов |
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
| `AGENT_MAX_ITERATIONS`, `AGENT_TOOLS_TOP_K`, `AGENT_HISTORY_MESSAGES` | 16, 24, 40 | то же | Шаги агента за ход, сколько инструментов видит модель, длина истории |
| `COMPILER_MAX_ATTEMPTS`, `COMPILER_DRY_RUN_TIMEOUT_SEC` | 3, 300 | то же | Попытки собрать задачу, таймаут пробного запуска |
| `TOOL_TIMEOUT_SEC`, `LLM_TIMEOUT_SEC` | 300, 300 | то же | Таймауты команды и запроса к LLM |
| `RUN_RETENTION_DAYS` | 15 | то же | Сколько дней хранить запуски, их журнал и файлы |

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

- **Structured output.** `LLMClient.structured` перебирает `json_schema` → `json_object` → обычный текст со схемой в промпте и запоминает, какой режим сработал для модели. Для рекурсивных схем (Task с вложенными шагами) `json_schema` пропускается. Подсказка со схемой дописывается в единственное системное сообщение. Переход между режимами не тратит попыток; попытки (3) тратят только ответы, не прошедшие валидацию, — ошибки возвращаются модели.
- **Эмбеддинги** необязательны: без них агент видит все инструменты. Если сервер эмбеддингов не отвечает, отбор отключается на 5 минут.
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
| `qwen3.8-27b-fp8` (vLLM) | Проходит сквозные сценарии отчёта и почты одной моделью, задачу собирает с первой попытки | `LLM_MODEL`: всё |

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

- [x] `common`, main, executor и 23 встроенные команды (включая `data.map_rows` и `mail.mark`)
- [x] agent-loop: цикл, трейс, мета-инструменты, события прогресса
- [x] compiler: compile с пробным запуском и починкой, validate, patch, diff
- [x] runner (пауза только на вопросах, статус `empty`) и scheduler (только cron)
- [x] Настройки групп команд у задачи (`mail.send_directly`), одно одобрение — задачи
- [x] launcher, Docker, тесты
- [x] Сквозной сценарий «пример отчёта → такой же отчёт за прошлую неделю» на сервере команды
- [x] Сквозной сценарий целиком на `qwen3.8-27b-fp8` (vLLM): отчёт по примеру → «формируй задачу» (с первой попытки, пробный запуск верный) → одобрение в чате → запуск по кнопке за ~0,1 с без LLM → правка «топ-5, по пятницам» с diff → новая версия в расписании
- [x] Почта в сценарии: автоответчик на заявки (cron → `mail.search` → `mail.send` в черновики → `mail.mark`) на локальном GreenMail
- [ ] Переработка по `docs/PLAN.md` (сделаны шаги 1–2: права как аргументы шагов, секреты и пути в executor по объявлениям)
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
- **Scheduler знает только время.** «Новые письма» и «новые файлы» — это шаги задачи по cron, а не особые триггеры.
- **Одно одобрение — задачи целиком.** Запуски не ждут подтверждений действий; поведение действия задают настройки группы команд, их меняет только пользователь.
- **Настройки группы объявляет сама группа** (`DomainSettings`), задача только хранит значения; compiler узнаёт схемы из каталога, LLM их не видит.

## Известные проблемы

Нарушения правила «каждая сущность знает только своё», найденные при ревью; исправляются по `docs/PLAN.md`.

1. **Почтовые аккаунты зашиты в executor.** `executor/api/accounts.py` импортирует почтовый адаптер и пресеты, проверяет IMAP/SMTP-поля и логин. Шаг 7 плана.
2. **Знания групп в общих промптах.** Правила компилятора и системный промпт агента называют конкретные команды (`docs.make_template`, `xlsx.aggregate`, `data.map_rows`, `mail.send`, `sender_email`…); `CORE_TOOLS` агента — список имён.
3. **Группа `llm` импортирует `internal_tools.docs.readers`** — зависимость одной группы от внутренностей другой.
