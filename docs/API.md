# API

Все запросы клиента идут в `main`. Его адрес launcher печатает при старте и пишет в
`DATA_DIR/runtime.json` (`main_url`); в примерах ниже — `http://127.0.0.1:8000`, как
при `MAIN_PORT=8000` или в Docker (см. [DEPLOY.md](DEPLOY.md)).
Интерактивная документация со схемами — `GET /docs`.

## Доступ

- **Внешний API.** Всегда нужен заголовок `Authorization: Bearer <токен>`: `API_TOKEN`
  или токен из `DATA_DIR/api_token`, который main создаёт, если `API_TOKEN` пуст (см.
  [SECURITY.md](SECURITY.md#токены)). Без токена — `401`. Открыт только `/health`.
- **Откуда.** main отвечает только на `Host` из `ALLOWED_HOSTS` (по умолчанию
  `127.0.0.1,localhost`, иначе `400`), CORS разрешён только источникам из
  `ALLOWED_ORIGINS` (по умолчанию никому).
- Свои учётные данные клиента main дальше сервисов не передаёт.
- **Внутренние маршруты** (`/internal/*`, `/executor/*`, `/runner/*`, `/scheduler/*`)
  требуют заголовок `X-Internal-Token: <INTERNAL_TOKEN>`. Их зовут только сервисы.

Ошибки везде одного вида:

```json
{"error": {"code": "CONFLICT", "message": "Task `task_1` already has an unfinished run", "details": {"run_id": "run_…"}}}
```

`code` следует из HTTP-статуса: `BAD_REQUEST` (400), `UNAUTHORIZED` (401),
`NOT_FOUND` (404), `CONFLICT` (409), `UNPROCESSABLE` (422), `INTERNAL_ERROR` (500),
`UPSTREAM_FAILURE` (502), `NOT_READY` (503), `UPSTREAM_TIMEOUT` (504).

## Чат

| Маршрут | Что делает |
| --- | --- |
| `POST /agent/v1/chat/completions` | Ход чата в формате OpenAI, со стримингом (`stream: true`) |
| `GET /agent/v1/models` | Единственная «модель» агента: `office-agent` |
| `GET /agent/sessions` | Последние 50 чатов: `id`, `title`, `updated_at`, `task_id` (у чата задачи; `null` — временный) |
| `GET /agent/sessions/{id}` | Чат целиком: сообщения, трейс вызовов команд и `busy` — идёт ли в нём сейчас ход агента |
| `GET /agent/meta-tools` | Собственные инструменты агента (задачи, одобрения) с названиями для пользователя |
| `DELETE /agent/sessions/{id}` | Удалить временный чат; чат задачи или чат, где агент ещё работает, — `409`. Временные и так удаляются через `chat_idle_hours` без сообщений ([CHAT.md](CHAT.md#сессии)) |

Запрос — обычный OpenAI `chat/completions`. Из `messages` берётся последнее
сообщение пользователя: история хранится на стороне агента. Чат определяется по
`session_id` (поле тела, например через `extra_body` SDK, или `metadata.session_id`),
а без него — по присланной переписке. Подробно — в [CHAT.md](CHAT.md#сессии).

Ответ — OpenAI `chat.completion` с двумя дополнительными полями:

```json
{
  "id": "chatcmpl-…",
  "object": "chat.completion",
  "model": "office-agent",
  "session_id": "ses_…",
  "tool_events": [{"tool": "fs.list", "status": "ok"}, {"tool": "fs.move", "status": "needs_approval"}],
  "choices": [{"index": 0, "message": {"role": "assistant", "content": "…"}, "finish_reason": "stop"}],
  "usage": {}
}
```

При `stream: true` агент, пока работает, шлёт комментарии `: keepalive` раз в
10 секунд, а в конце — два чанка `chat.completion.chunk` с `session_id` (весь ответ, затем
пустой с `finish_reason: stop`) и `data: [DONE]`. `tool_events` в потоке нет; при ошибке
вместо ответа приходит `data: {"error": …}`.

## Задачи

| Маршрут | Что делает |
| --- | --- |
| `GET /tasks` | Задачи, кроме архивных, с карточками шагов активной версии и расписанием словами (`schedule`) |
| `GET /tasks/{id}` | Задача, её активная версия, список всех версий и `tool_changes` — команды, изменившиеся после одобрения (`[{tool, fields}]`) |
| `GET /tasks/{id}/versions/{v}` | Одна версия: сценарий, карточки, правка, кем и из какого чата создана |
| `GET /tasks/{id}/versions/{v}/diff` | Что изменилось относительно предыдущей версии |
| `POST /compiler/compile` | Собрать задачу из чата → черновик и одобрение |
| `POST /compiler/patch` | Правка словами (LLM) → черновик новой версии, diff и одобрение |
| `POST /tasks/{id}/versions` | Правка вручную без LLM → черновик новой версии, diff и одобрение |
| `GET /tasks?tool=telegram.send` | Задачи, активная версия которых (или черновик до первого одобрения) вызывает эту команду |
| `POST /tasks/{id}/tools/refresh` | Те же шаги с командами, как они есть сейчас → черновик, diff по командам и одобрение; команды не менялись или шаги не подходят к новым схемам — `422` (см. [TASKS.md](TASKS.md#команды-задачи)) |
| `POST /tasks/{id}/versions/{v}/approve` | Сделать версию активной |
| `POST /tasks/{id}/versions/{v}/reject` | Отклонить черновик |
| `POST /tasks/{id}/pause`, `/resume`, `/archive` | Приостановить, возобновить, убрать в архив |
| `GET /tasks/{id}/settings` | Настройки команд задачи: по умолчанию, для всей задачи, для каждого шага |
| `PATCH /tasks/{id}/settings` | `{changes: [{tool, field, value, step?}]}` — без `step` для всей задачи, со `step` для шага; `value: null` — снять изменение |
| `PUT /tasks/{id}/settings` | Изменить значения настроек |

**Сборка.** `POST /compiler/compile {session_id, title?, hint?}` →

```json
{"task_id": "task_…", "version_id": "ver_…", "title": "…", "summary": "…",
 "trigger": "по понедельникам в 10:00", "settings": ["Отправлять письма сразу: выключено (по умолчанию). …"],
 "cards": [ … ]}
```

**Правка словами.** `POST /compiler/patch {task_id, instruction, session_id?}` →
`{task_id, version_id, title, summary, diff, changes, cards}`, где `diff` —
строки «было → стало» для человека, `changes` — то же структурой.

**Правка вручную.** `POST /tasks/{id}/versions` с одним из двух:

```json
{"ops": [{"op": "replace", "path": "/trigger/expr", "value": "0 9 * * FRI"}], "summary": "по пятницам"}
```

```json
{"spec": { … задача целиком … }}
```

Задача проходит те же проверки по каталогу, что и собранная; ответ такой же, как у
правки словами. Правка, которая ничего не меняет, — 422.

**Одобрение.** `approve` → `{task_id, status, current_version_id}`; `reject` →
`{task_id, version_id, status}`. Одобрить или отклонить можно только черновик (иначе
409). `pause`, `resume`, `archive` → `{task_id, status}`; недопустимый переход — 409.

**Настройки.** `GET` → `{task_id, settings}`: по команде с настройками
`{group, tool, tool_title, fields}`, у поля — `name`, `title`, `description`, схема,
`default`, `all` (значение для всей задачи, `null` — не меняли) и `steps`: каждый шаг с
этой командой — `{step, card, value, effective}`, где `effective` — с чем шаг работает.
`PATCH` принимает список изменений и отвечает тем же видом:

```json
{"changes": [
  {"tool": "mail.send", "field": "send_directly", "value": true},
  {"tool": "mail.send", "field": "send_directly", "step": "to_client", "value": false}
]}
```

`value: null` снимает изменение: шаг снова берёт значение задачи, задача — значение по
умолчанию. Поля и шаги — только существующие, значение проверяется по схеме; всё или
ничего, ошибки — 422 со списком в `details.errors`. Подробно — в
[TASKS.md](TASKS.md#настройки).

## Одобрения

| Маршрут | Что делает |
| --- | --- |
| `GET /approvals?session_id=` | Что ждёт решения: черновики версий неархивных задач; с `session_id` — сделанные в этом чате |

Одобрение одно — версии задачи, и отдельной записи у него нет: ждёт решения сам
черновик версии. У задачи черновик один (новый заменяет прежний: `superseded`), так что
ничего устаревшего не остаётся. Запись: `{task_id, version_id, session_id, title,
summary, cards, created_at}`. Одобрить или отклонить —
`POST /tasks/{id}/versions/{version_id}/approve | reject`.

## Запуски

| Маршрут | Что делает |
| --- | --- |
| `POST /runs` | Запустить задачу сейчас или пробно |
| `GET /runs/{id}` | Запуск и его шаги |
| `GET /runs/{id}/log` | Журнал запуска целиком |
| `POST /runs/{id}/cancel` | Отменить запуск |
| `GET /tasks/{id}/runs?include_empty=false` | Последние 50 запусков задачи |
| `GET /jobs` | Что и когда запустит scheduler |

`POST /runs`:

```json
{"task_id": "task_…"}
{"task_version_id": "ver_…", "dry_run": true, "wait": true}
```

По `task_id` исполняется активная версия. По `task_version_id` — эта версия; не
одобренную можно исполнить только пробно. Без `wait` ответ приходит сразу (запуск в
статусе `queued`), с `wait` — после окончания (не дольше 600 секунд, иначе 504 и
запуск закрывается как `failed`). У задачи может идти только один настоящий запуск:
второй — 409.

Запуск: `{id, task_id, task_version_id, trigger, status, dry_run, result, error, started_at, finished_at}`;
`POST /runs/{id}/cancel` → `{run_id, status}`: `cancelled`, если запуск ещё ждал в
очереди и уже закрыт, `cancelling` — если он идёт и остановится перед следующим шагом;
законченный — 409. `GET /runs/{id}` добавляет `steps` — записи журнала о шагах. Запуски со статусом
`empty` (проверка ничего не нашла, в том числе остановка по `on_empty: skip` на первом
же шаге) в истории задачи видны только с `include_empty=true`. Статусы, журнал и пробный запуск — в [RUNS.md](RUNS.md).

## Команды и аккаунты

| Маршрут | Что делает |
| --- | --- |
| `GET /tools` | Каталог команд: манифесты со схемами и хешем определения (`hash`) |
| `GET /tools/{name}` | Манифест одной команды |
| `GET /external-tools`, `PUT /external-tools/{name}/draft`, … | Свои команды: одобренный код, черновик, одобрение — см. [EXTERNAL_TOOLS.md](EXTERNAL_TOOLS.md#api) |
| `GET /accounts` | Аккаунты без паролей: `id`, `name`, `kind`, `config` |
| `GET /accounts/kinds` | Виды аккаунтов, которые объявили группы команд: поля для формы, название секрета, пресеты |
| `POST /accounts` | Создать или изменить аккаунт (по имени); без `secret` остаётся сохранённый пароль |
| `POST /accounts/{name}/check` | Попробовать войти |
| `DELETE /accounts/{name}` | Удалить аккаунт и его пароль |

```json
{"name": "work", "kind": "imap_smtp", "preset": "yandex", "config": {"email": "me@yandex.ru"}, "secret": "пароль приложения"}
```

`name` — латиница, цифры, `_` и `-`, до 40 символов. Пароль сразу уходит в хранилище
секретов и наружу не возвращается. Неполные настройки вида — 400 с объяснением; вход не
удался — 502; аккаунта нет — 404. Манифест команды и виды аккаунтов — в
[TOOLS.md](TOOLS.md).

## Настройки

| Маршрут | Что делает |
| --- | --- |
| `GET /settings` | Рабочие настройки: значение, откуда оно (`env`, `db`, `default`), можно ли менять |
| `PATCH /settings` | Изменить `{настройка: значение}`, всё или ничего; `null` — вернуть значение по умолчанию |

Список настроек, формат ответа и ошибок — в [SETTINGS.md](SETTINGS.md).

## LLM

| Маршрут | Что делает |
| --- | --- |
| `GET /llm` | Провайдеры и кто обслуживает каждую роль (`chat`, `instruct`, `embed`) |
| `PATCH /llm` | Назначить провайдеров на роли; каждый сначала проверяется на свою роль, всё или ничего |
| `POST /llm/providers` | Добавить провайдера: название, адрес, модель, ключ |
| `PATCH /llm/providers/{id}` | Изменить провайдера, в том числе ключ |
| `DELETE /llm/providers/{id}` | Удалить (кроме назначенного на роль) |
| `POST /llm/models` | Модели сервера из `/v1/models` |
| `POST /llm/providers/{id}/check` | Проверить провайдера на роль |

Поля, форматы и проверки — в [LLM.md](LLM.md).

## Логи

| Маршрут | Что делает |
| --- | --- |
| `GET /logs?lines=200` | Последние строки (до 2000) каждого текущего файла логов: `{"dir": …, "files": {"agent.log": [строки]}}` |

Какие бывают файлы — в [DEPLOY.md](DEPLOY.md#логи).

## События

| Маршрут | Что делает |
| --- | --- |
| `GET /events?since=<id>` | Поток событий (SSE); с `since` сначала отдаются пропущенные (до 500) |
| `GET /events/recent?since=&limit=100` | Последние события JSON |
| `GET /services` | Какие сервисы зарегистрированы и когда последний раз |

Событие: `{id, type, payload, created_at}`; в SSE его `id` и `type` — это `id:` и
`event:`. Без событий поток раз в 15 секунд шлёт комментарий-пинг. Отстающему
подписчику события не копятся бесконечно: после 256 в очереди новые для него
пропускаются, догнать можно через `since`.

| Тип | Когда |
| --- | --- |
| `agent.tool` | агент вызвал команду в чате: `{session_id, tool, status}` |
| `task.draft` | появился черновик версии, он ждёт решения (`task_id`, `version_id`, `session_id`, `title`) |
| `task.approved`, `task.rejected` | версия одобрена или отклонена |
| `task.pause`, `task.resume`, `task.archive` | задача приостановлена, возобновлена, в архиве |
| `task.tools_changed` | запуск не начат: команды задачи не те, с которыми её одобрили (`task_id`, `run_id`, `tools`, `dry_run`) |
| `run.started`, `run.finished` | запуск начался или закончился (`status`, `error`, `dry_run`) |
| `notify` | запуск сообщил что-то пользователю |

События старше [`retention_days`](SETTINGS.md) удаляются.

## Внутренние маршруты

Только для сервисов, с `X-Internal-Token`:

| Маршрут | Кто зовёт | Что делает |
| --- | --- | --- |
| `POST /internal/register {name, url}` | все сервисы | Сообщить свой адрес |
| `POST /internal/events {type, payload}` | runner, agent-loop | Опубликовать событие |
| `POST /internal/notify {task_id, run_id, title, message}` | runner | Доставить сообщение запуска в чат задачи |
| `POST /internal/tools-changed {task_id, run_id, title, tools, dry_run}` | runner | Запуск не начат или остановлен: команды задачи изменились. main ставит задачу на паузу и пишет в её чат (пробный — только событие) |
| `POST /executor/execute` | agent-loop, runner | Выполнить команду (формат — в [TOOLS.md](TOOLS.md#вызов-команды)) |
| `POST /executor/mcp` | — | Те же команды по MCP: `tools/list`, `tools/call` (см. [TOOLS.md](TOOLS.md#по-mcp)) |
| `/runner/*` | scheduler | Запустить задачу по cron |
| `/scheduler/*` | — | Прокси к scheduler: `POST /reload {task_id?}` — перечитать задачи, `GET /jobs` (main сам зовёт scheduler напрямую после одобрения и смены статуса) |
