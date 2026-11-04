# API хаба AFO

Справочник сделан из кода командой `python -m hub.api_reference` — не правьте его руками: тест сверяет его с кодом.

## Как подключаться

- Адрес: `http://127.0.0.1:8765` (порт меняет `LOCALCHAT_HUB_PORT`). Хаб отвечает только на запросы к этому
  компьютеру и не принимает запросы со страниц браузера (D31).
- Токен: заголовок `Authorization: Bearer <токен>`. У окна AFO свой токен (`data/tokens/app.token`), у
  внешних клиентов (Cline, Claude Code, расширение VS Code) — свои, их выдаёт окно в «Подключениях». Без токена
  открыт только `GET /v1/health`.
- «Только окно» — запрос, который хаб принимает лишь от окна AFO; другим клиентам он отвечает 403. Другой
  клиент читает проекты так же, как через MCP (облачный — только проекты, где можно облако; без секретных файлов, текст
  через фильтр секретов), а разговоры видит и продолжает только свои; по WebSocket он получает состояние хаба и
  проектов и события своих разговоров (D31, D46).
- Ошибки — JSON `{"detail": ...}`: 401 — нет токена или он неверный, 403 — не тот клиент, 404 — нет такого, 409 —
  конфликт состояния, 422 — неверные данные (у ошибок проверки `detail` — список полей).
- Ответ модели в чате идёт не в ответе на `POST .../messages`, а событиями `chat.*` по WebSocket `/v1/events`.
- MCP для внешних агентов (D32): `/mcp` (Streamable HTTP, с токеном клиента). OpenAI-совместимый вход (D33):
  `http://127.0.0.1:8765/v1/openai`.

## Эндпоинты

### system

/v1/health (open, so a client can tell whether a hub is running) and /v1/info (needs a token).

| Метод | Путь | Кто | Что | Параметры |
|---|---|---|---|---|
| GET | `/v1/health` | без токена | `health()` |  |
| GET | `/v1/info` | токен | `info()` |  |
| GET | `/v1/usage` | только окно | `usage()` |  |
| GET | `/v1/feed` | только окно | The event log, newest first (D29): what happened, for the window's feed. `types` - a comma list of types or prefixes ("terminal.*"); `before` - an event id, for the next page. | `?limit: int = 100`<br>`?before: int \| None = None`<br>`?project_id: str \| None = None`<br>`?types: str = ''` |
| GET | `/v1/status` | токен | GPU, memory and the loaded local model (the latest sample, or a fresh one), plus busy chats. The same picture arrives as `system.status` events whenever it changes. |  |
| POST | `/v1/shutdown` | только окно | Graceful stop: the hub finishes requests, publishes hub.stopping and removes hub.json. |  |

### language

/v1/language - the language of what the hub writes for people (step 10.5): the desktop app chooses it (Settings → Language) and tells it again on every connect.

| Метод | Путь | Кто | Что | Параметры |
|---|---|---|---|---|
| GET | `/v1/language` | только окно | `get_language()` |  |
| PUT | `/v1/language` | только окно | `set_language()` | тело `{language: str}` |

### models

/v1/models - the model catalog (D5-D7); /v1/models/sync - refresh it from the providers.

| Метод | Путь | Кто | Что | Параметры |
|---|---|---|---|---|
| GET | `/v1/models` | токен | `models()` |  |
| POST | `/v1/models/sync` | только окно | `sync_models()` |  |
| POST | `/v1/models/unload` | только окно | Frees the GPU: llama-swap stops whatever local model it runs (it loads one again on the next message). |  |

### model_settings

/v1/model_settings - what the user set for each model (D39, stage 8; hub/model_settings.py), and /v1/models/check - a short answer from a model with its settings, to see that it works and how fast.

| Метод | Путь | Кто | Что | Параметры |
|---|---|---|---|---|
| GET | `/v1/model_settings` | только окно | `read()` | `?model_id: str = ''` |
| PUT | `/v1/model_settings` | только окно | `write()` | тело `{model_id: str = '', settings: dict[str, Any]}` |
| POST | `/v1/model_settings/revert` | только окно | An earlier version back - as a new version, so the one replaced stays too. | тело `{model_id: str = '', version: int}` |
| POST | `/v1/models/check` | только окно | One short answer with the model's settings and system prompts: its words, how soon the first one came, how fast the rest, what it cost (recorded as usage of the app, so budgets count it). | тело `{model_id: str}` |
| GET | `/v1/model_settings/export` | только окно | Every model's settings as they are now, and the general row - a file to keep or to take to another hub. |  |
| POST | `/v1/model_settings/import` | только окно | An exported file back: each model of this hub gets its settings as a new version; a model this hub does not have is skipped, a role naming one is dropped; a wrong value is said, and the rest still saved. | тело `{data: dict[str, Any]}` |

### portable

/v1/settings - all of the hub's settings as one file, and the file back after a preview (D46, step 13.10; hub/portable.py). The desktop app's alone: the file holds what every model is told, the projects and what agents may do there. The parts behind the connections' password load with its ticket (X-LocalChat-Unlock), as their own changes do.

| Метод | Путь | Кто | Что | Параметры |
|---|---|---|---|---|
| GET | `/v1/settings/export` | только окно | Every setting as it is now, no secret among them: a file to keep or to take to another hub. |  |
| POST | `/v1/settings/import/preview` | только окно | What loading the file would do, line by line - loaded and taken back, nothing written. | тело `{data: dict[str, Any], parts: list[str] \| None = None}` |
| POST | `/v1/settings/import` | только окно | The file loaded: it adds and changes, never removes; an MCP server and a schedule come in turned off. | тело `{data: dict[str, Any], parts: list[str] \| None = None}` |

### llama

/v1/llama - a local model's launch settings in llama-swap's config.yaml (stage 8.5, D39, Q25; hub/llama_config.py). The desktop app's alone: it decides how the user's models start.

| Метод | Путь | Кто | Что | Параметры |
|---|---|---|---|---|
| GET | `/v1/llama/models` | только окно | `models()` |  |
| PUT | `/v1/llama/models/{model_id}` | только окно | `change()` | тело `{changes: dict[str, Any]}` |
| POST | `/v1/llama/models/{model_id}/trial` | только окно | The model started with its settings and asked one short question. When it does not answer and its settings were changed by the last write, that file's copy comes back (Q25). |  |
| POST | `/v1/llama/restore` | только окно | `restore()` | тело `{backup: str}` |

### install

/v1/install - local models from Hugging Face into llama-swap (stage 10.1, D37; hub/model_install.py). The desktop app's alone, like the launch settings (hub/api/llama.py): it decides what runs on the user's computer.

| Метод | Путь | Кто | Что | Параметры |
|---|---|---|---|---|
| GET | `/v1/install/system` | только окно | `system()` |  |
| GET | `/v1/install/catalog` | только окно | `catalog()` |  |
| GET | `/v1/install/search` | только окно | `search()` | `?q: str` |
| GET | `/v1/install/repo/{author}/{name}` | только окно | `repository()` | `?fresh: bool = False` |
| POST | `/v1/install` | только окно | `install()` | тело `{repo: str, quant: str, projector: bool \| None = None, drive: str = '', folder: str = '', name: str = ''}` |
| GET | `/v1/install/jobs` | только окно | `jobs()` |  |
| POST | `/v1/install/jobs/{job_id}/{action}` | только окно | `act()` |  |
| DELETE | `/v1/install/jobs/{job_id}` | только окно | `forget()` |  |
| DELETE | `/v1/install/models/{model_id}` | только окно | `remove()` |  |

### setup

/v1/setup - the first-run wizard (stage 10.2, D37; hub/setup.py). The desktop app only: it puts programs on the computer.

| Метод | Путь | Кто | Что | Параметры |
|---|---|---|---|---|
| GET | `/v1/setup` | только окно | `state()` |  |
| GET | `/v1/setup/models` | только окно | `first_models()` |  |
| POST | `/v1/setup/engine` | только окно | `engine()` | тело `{build: str = ''}` |
| GET | `/v1/setup/update` | только окно | The llama.cpp build running and whether GitHub has a newer one of its kind (10.4). |  |
| POST | `/v1/setup/update` | только окно | `update()` |  |
| POST | `/v1/setup/engine/cancel` | только окно | `cancel()` |  |

### release

/v1/release - AFO's own updates (step 10.4d): the newest release on GitHub, its installer's download and its run over the installed program. The desktop app only.

| Метод | Путь | Кто | Что | Параметры |
|---|---|---|---|---|
| GET | `/v1/release` | только окно | `get_release()` |  |
| PUT | `/v1/release` | только окно | `set_release()` | тело `{auto: bool}` |
| POST | `/v1/release/check` | только окно | `check()` |  |
| POST | `/v1/release/download` | только окно | `download()` |  |
| POST | `/v1/release/cancel` | только окно | `cancel()` |  |
| POST | `/v1/release/install` | только окно | The installer runs on its own: it stops this hub and the window, and opens the window again when done. |  |

### support

/v1/support - «Проверить систему» and the logs for support (stage 10.6, D37; hub/diagnostics.py), and what the hub keeps on disk with its limits (D30, hub/retention.py). The desktop app only: the check reads the computer, the report gathers its logs.

| Метод | Путь | Кто | Что | Параметры |
|---|---|---|---|---|
| GET | `/v1/support/check` | только окно | `check()` |  |
| POST | `/v1/support/report` | только окно | `report()` |  |
| GET | `/v1/support/retention` | только окно | How long and how much the hub keeps (D30), and what each kind takes now. |  |
| PUT | `/v1/support/retention` | только окно | `set_kept()` | тело `{days: int, max_gb: float}` |
| POST | `/v1/support/retention/prune` | только окно | «Очистить сейчас»: everything past its term or over the size goes now. |  |

### memory

/v1/memory and /v1/skills - the autonomous agent's memory as the user sees and corrects it (stage 9.3, D40, Q24): the facts kept for good, a run's notes, and the skills. Every client may read the facts a cloud model may read; the whole list - personal facts too - and every change are the desktop app's (the user's own memory).

| Метод | Путь | Кто | Что | Параметры |
|---|---|---|---|---|
| GET | `/v1/memory/facts` | токен | `facts()` | `?q: str = ''` |
| POST | `/v1/memory/facts` | только окно | A fact the user writes in themselves. | тело `{text: str, about: str = '', personal: bool = False, cloud_ok: bool = False}` |
| PUT | `/v1/memory/facts/{fact_id}` | только окно | `change_fact()` | тело `{text: str \| None = None, about: str \| None = None, personal: bool \| None = None, cloud_ok: bool \| None = None}` |
| DELETE | `/v1/memory/facts/{fact_id}` | только окно | `delete_fact()` |  |
| GET | `/v1/runs/{run_id}/notes` | только окно | `run_notes()` |  |
| GET | `/v1/skills` | только окно | `skills()` | `?q: str = ''` |
| POST | `/v1/skills` | только окно | A finished task's plan kept as a skill - the user chose to. | тело `{run_id: str, name: str = ''}` |
| DELETE | `/v1/skills/{skill_id}` | только окно | `delete_skill()` |  |

### schedules

/v1/schedules - autonomous goals started on a schedule or an event (stage 9.7, Q23), and /v1/agent/quiet - the quiet hours. Only the desktop app sets them up: a schedule starts agents with nobody at the computer.

| Метод | Путь | Кто | Что | Параметры |
|---|---|---|---|---|
| GET | `/v1/schedules` | только окно | `list_schedules()` |  |
| POST | `/v1/schedules` | только окно | `add_schedule()` | тело `{title: str = '', goal: dict[str, Any], trigger: dict[str, Any], computer: bool = False, enabled: bool = True}` |
| PUT | `/v1/schedules/{sid}` | только окно | `change_schedule()` | тело `{title: str \| None = None, goal: dict[str, Any] \| None = None, trigger: dict[str, Any] \| None = None, computer: bool \| None = None, enabled: bool \| None = None}` |
| DELETE | `/v1/schedules/{sid}` | только окно | `delete_schedule()` |  |
| POST | `/v1/schedules/{sid}/run` | только окно | Now, not waiting for its time or the morning. |  |
| GET | `/v1/agent/quiet` | только окно | `get_quiet()` |  |
| PUT | `/v1/agent/quiet` | только окно | `set_quiet()` | тело `{enabled: bool \| None = None, start: str \| None = None, end: str \| None = None}` |

### mcp_servers

/v1/mcp/servers - the outside MCP servers whose tools the hub gives to new chats and autonomous runs (9.5, D42). The desktop app only.

| Метод | Путь | Кто | Что | Параметры |
|---|---|---|---|---|
| GET | `/v1/mcp/servers` | только окно | `list_servers()` |  |
| POST | `/v1/mcp/servers` | только окно | Added, started, and its tools listed - or why not (it stays added: the user may fix its environment). | тело `{name: str, target: str, env: str = ''}` |
| PUT | `/v1/mcp/servers/{name}` | только окно | `change_server()` | тело `{enabled: bool \| None = None, env: str \| None = None}` |
| POST | `/v1/mcp/servers/{name}/restart` | только окно | `restart_server()` |  |
| PUT | `/v1/mcp/servers/{name}/tools/{tool}` | только окно | `set_tool_class()` | тело `{cls: str}` |
| DELETE | `/v1/mcp/servers/{name}` | только окно | `remove_server()` |  |

### mail

/v1/mail - the mailbox the autonomous agent reads and writes from (stage 9.4b, Q21). The desktop app only.

| Метод | Путь | Кто | Что | Параметры |
|---|---|---|---|---|
| GET | `/v1/mail` | только окно | `get_mail()` |  |
| PUT | `/v1/mail` | только окно | `set_mail()` | тело `{address: str, password: str = '', login: str = '', imap_host: str = '', imap_port: int \| None = None, imap_security: str = '', smtp_host: str = '', smtp_port: int \| None = None, smtp_security: str = ''}` |
| DELETE | `/v1/mail` | только окно | `delete_mail()` |  |
| POST | `/v1/mail/check` | только окно | `check_mail()` |  |

### sandbox

/v1/conversations/{id}/sandbox - what the model changed in a «Песочница» chat (D17, stage 9.4b): per project, the files and the diff; «Применить» puts it onto the project's working tree, «Отбросить» throws it away. The desktop app only: the user decides what reaches the project.

| Метод | Путь | Кто | Что | Параметры |
|---|---|---|---|---|
| GET | `/v1/conversations/{conversation_id}/sandbox` | только окно | `sandbox_changes()` |  |
| POST | `/v1/conversations/{conversation_id}/sandbox/{project_id}/apply` | только окно | `apply_sandbox()` |  |
| POST | `/v1/conversations/{conversation_id}/sandbox/{project_id}/discard` | только окно | `discard_sandbox()` |  |

### conversations

/v1/conversations - chats and their messages; posting a message starts a streamed assistant reply.

| Метод | Путь | Кто | Что | Параметры |
|---|---|---|---|---|
| GET | `/v1/conversations` | токен | `list_conversations()` |  |
| POST | `/v1/conversations` | токен | `create_conversation()` | тело `{title: str = '', home_project: str \| None = None}` |
| GET | `/v1/conversations/{conversation_id}` | токен | `get_conversation()` |  |
| PUT | `/v1/conversations/{conversation_id}/context` | только окно | What the chat's context includes from its next message on (D22): the map, the live state, pinned files. | тело `{map: bool \| None = None, live: bool \| None = None, pinned: list[Pinned] \| None = None, mode: Literal['read_only', 'ask', 'sandbox', 'trusted'] \| None = None, instructions: str \| None = None, preset: Literal['', 'precise', 'balanced', 'creative'] \| None = None, refusal_vector: Literal['', 'on', 'off'] \| None = None}` |
| PUT | `/v1/conversations/{conversation_id}/projects` | только окно | The projects this chat works with (D27): its tools reach only these, its prompt describes them. | тело `{project_ids: list[str]}` |
| PATCH | `/v1/conversations/{conversation_id}` | токен | `rename_conversation()` | тело `{title: str}` |
| DELETE | `/v1/conversations/{conversation_id}` | токен | `delete_conversation()` |  |
| POST | `/v1/conversations/{conversation_id}/messages` | токен | `send_message()` | тело `{content: str = '', model_id: str, effort: str = 'medium', confirm_over_budget: bool = False, files: list[SentFile]}` |
| POST | `/v1/conversations/{conversation_id}/cancel` | токен | `cancel_run()` |  |

### goals

/v1/conversations/{id}/goal - a chat's goal (stage 11.1, D44): the chat works turn after turn until the hub's check says it is met, a limit is reached, or it needs the human.

| Метод | Путь | Кто | Что | Параметры |
|---|---|---|---|---|
| GET | `/v1/conversations/{conversation_id}/goal` | токен | The chat's goal - the open one, else its last - with its recent checks and what the user wrote to it. |  |
| PUT | `/v1/conversations/{conversation_id}/goal` | только окно | A new goal for the chat; its first turn starts at once. | тело `{objective: str, criteria: list[Criterion], constraints: list[str], model_id: str, effort: str = 'medium', limits: Limits, judge_model_id: str \| None = None}` |
| POST | `/v1/conversations/{conversation_id}/goal/draft` | только окно | A contract drafted by a model from the user's words, for the window to show and change; nothing is set. | тело `{text: str, model_id: str = ''}` |
| POST | `/v1/conversations/{conversation_id}/goal/pause` | только окно | No next turn starts; the turn running now finishes. |  |
| POST | `/v1/conversations/{conversation_id}/goal/resume` | только окно | Back to work (a parked goal with higher limits): the next turn starts. | тело `Resume \| None` |
| DELETE | `/v1/conversations/{conversation_id}/goal` | только окно | The goal is given up; the turn running now finishes, no next one starts. |  |
| POST | `/v1/conversations/{conversation_id}/goal/steer` | только окно | The user's word while the goal works: read at the start of the next turn, without stopping this one. | тело `{text: str}` |

### proving

/v1/proving - the proving ground and the models' exam (stage 12, D45): the tasks, a try of one with a model, exams of chosen tasks on chosen models with their table.

| Метод | Путь | Кто | Что | Параметры |
|---|---|---|---|---|
| GET | `/v1/proving/tasks` | только окно | The proving ground's tasks, the easy ones first. |  |
| POST | `/v1/proving/try` | только окно | One task for one model on a fresh copy: its chat (with the goal) is ready to watch. | тело `{task_id: str, model_id: str}` |
| GET | `/v1/proving/runs` | только окно | The tries («Попробовать»), the newest first. |  |
| DELETE | `/v1/proving/runs/{run_id}` | только окно | A try gone, with its copy, chat and project. |  |
| GET | `/v1/proving/exams` | только окно | The exams, the newest first, each with its runs and its table per model. |  |
| POST | `/v1/proving/exams` | только окно | Chosen tasks on chosen models, one run after another, each on its own copy. | тело `{model_ids: list[str], task_ids: list[str], judge: bool = False}` |
| GET | `/v1/proving/exams/{exam_id}` | только окно | An exam with its runs and its table per model. |  |
| POST | `/v1/proving/exams/{exam_id}/stop` | только окно | The run going now ends; those waiting are skipped. |  |
| DELETE | `/v1/proving/exams/{exam_id}` | только окно | An exam gone, with its runs' copies, chats and projects. |  |

### connections

/v1/connections - API keys, tokens and addresses (D13) behind the user's password. The desktop app only.

| Метод | Путь | Кто | Что | Параметры |
|---|---|---|---|---|
| GET | `/v1/connections` | только окно | `list_connections()` |  |
| POST | `/v1/connections/password` | только окно | The first password, or a new one (with the old). Every open window is locked again. | тело `{password: str, old: str = ''}` |
| POST | `/v1/connections/unlock` | только окно | `unlock()` | тело `{password: str}` |
| POST | `/v1/connections/lock` | только окно | `lock()` |  |
| POST | `/v1/connections/telegram/link` | только окно | A one-time code for "/link <code>" to the user's bot: the chat that sends it gets the approvals (D21). |  |
| DELETE | `/v1/connections/telegram/link` | только окно | `telegram_unlink()` |  |
| PUT | `/v1/connections/{name}` | только окно | `set_connection()` | тело `{value: str}` |
| DELETE | `/v1/connections/{name}` | только окно | `delete_connection()` |  |
| GET | `/v1/connections/{name}/models` | только окно | Everything a provider with too many models (OpenRouter) has, and which ones the user chose to offer. |  |
| PUT | `/v1/connections/{name}/models` | только окно | The models to offer; the catalog follows at once. | тело `{ids: list[str]}` |
| POST | `/v1/connections/{name}/check` | только окно | Tries the connection the way the hub uses it: lists the provider's models, searches, asks the bot its name. |  |

### autostart

/v1/autostart - starting by itself (Q8): the hub at Windows login, llama-swap with the hub; and where things are (10.3): llama-swap's config and what starts it - chosen in the window, or the defaults - and LocalChat's own folders. The desktop app only.

| Метод | Путь | Кто | Что | Параметры |
|---|---|---|---|---|
| GET | `/v1/autostart` | только окно | `get_autostart()` |  |
| PUT | `/v1/autostart` | только окно | `set_autostart()` | тело `{login: bool \| None = None, llama_swap: bool \| None = None, llama_swap_config: str \| None = None, llama_swap_start: str \| None = None}` |

### editor

/v1/editor - what the LocalChat extension for VS Code reports (stage 6.6): the open file, finished terminal commands. Any client with a token may report (the extension has its own, `python -m hub token create vscode`); everyone reads. And the window's own editor (D47): which files it holds with edits not saved yet, and its code completion - the window's alone.

| Метод | Путь | Кто | Что | Параметры |
|---|---|---|---|---|
| POST | `/v1/editor/focus` | токен | `focus()` | тело `{path: str, line: int = 1}` |
| POST | `/v1/editor/command` | токен | `command()` | тело `{terminal: str = '', cwd: str = '', command: str, exit_code: int \| None = None, output: str = '', duration_s: float \| None = None}` |
| PUT | `/v1/editor/dirty` | только окно | The files the window's editor holds with edits not saved yet (D47, step 14.2c): the whole list, each time it changes and whenever the window's events connect; the hub forgets it a while after the last window went. A model's write into one of them is asked in every mode, a trusted chat's too, and no answer covers it. | тело `{files: list[Unsaved]}` |
| POST | `/v1/editor/complete` | только окно | Code completion at the cursor (D47, step 14.6), the window's alone: lines of JSON - {model, how}, then {text} pieces, then {done}; or one {paused, message} when it does not run now (no model chosen, a local one not loaded, a cloud one for a local-only project, a secret file). The next request cancels this one. | тело `{project_id: str, path: str, before: str = '', after: str = '', load: bool = False}` |
| POST | `/v1/editor/edit` | только окно | The model edits the selection (D47, step 14.7, Ctrl+I), the window's alone: {replacement, model} - the window shows it as a diff, the person takes it or not. 409 {reason, message} when it may not be done (no model chosen, a cloud one for a local-only project, a secret file). | тело `{project_id: str, path: str, selected: str, before: str = '', after: str = '', instruction: str}` |
| GET | `/v1/editor` | токен | The open file and the latest commands, newest first. | `?limit: int = 20` |

### budgets

/v1/budgets - spending limits for paid models (D12): what each allows, what it has used, whether it is reached.

| Метод | Путь | Кто | Что | Параметры |
|---|---|---|---|---|
| GET | `/v1/budgets` | только окно | `list_budgets()` |  |
| PUT | `/v1/budgets` | только окно | Creates the budget for (scope, provider, period) or replaces its limits. | тело `{scope: str = 'total', provider: str = '', period: str = 'month', soft_usd: float \| None = None, hard_usd: float \| None = None, hard_action: str = 'ask'}` |
| DELETE | `/v1/budgets/{budget_id}` | только окно | `delete_budget()` |  |

### projects

/v1/projects - the registry, a project's tree, files and git (stage 2, D4).

| Метод | Путь | Кто | Что | Параметры |
|---|---|---|---|---|
| GET | `/v1/projects` | токен | `list_projects()` |  |
| POST | `/v1/projects` | только окно | `add_project()` | тело `{path: str, name: str = ''}` |
| PATCH | `/v1/projects/{project_id}` | только окно | `change_project()` | тело `{name: str \| None = None, description: str \| None = None, cloud_ok: bool \| None = None}` |
| DELETE | `/v1/projects/{project_id}` | только окно | Forgets the project; its folder is not touched. |  |
| GET | `/v1/projects/{project_id}/tree` | токен | `project_tree()` | `?path: str = ''` |
| GET | `/v1/projects/{project_id}/file` | токен | `project_file()` | `?path: str` |
| PUT | `/v1/projects/{project_id}/file` | только окно | The user's save from the window's editor (D47, step 14.1): the window's alone. Written whole, in the file's own encoding, BOM and line ends, when the file is still the version the editing began from; else 409 with what is there now (`reason`: changed, gone, exists). Kept in «Изменения» as the user's (`save`), to undo like a model's change; a secret file's content is not kept. `file.saved` goes out. | тело `{path: str, text: str, encoding: str = 'utf-8', base: str = ''}` |
| GET | `/v1/projects/{project_id}/git` | токен | `project_git()` | `?log: int = 20` |
| GET | `/v1/projects/{project_id}/search` | токен | Lines that match `q` (text, or a regular expression with `regex`), grouped by file (hub/search.py). | `?q: str`<br>`?regex: bool = False`<br>`?case: str = 'smart'`<br>`?glob: str = ''`<br>`?limit: int = 500` |
| GET | `/v1/projects/{project_id}/files` | токен | Files whose path matches `q`, best first - for picking a file by a few letters (hub/search.py). | `?q: str = ''`<br>`?limit: int = 20` |
| GET | `/v1/projects/{project_id}/symbols` | токен | Definitions in the project's code (hub/symbols.py, D26): those named like `q`, or the outline of `path`. | `?q: str = ''`<br>`?path: str = ''`<br>`?kind: str = ''`<br>`?limit: int = 50` |
| GET | `/v1/projects/{project_id}/diff` | токен | `project_diff()` | `?path: str = ''`<br>`?staged: bool = False` |

### programs

/v1/programs - the programs of this computer and what the autonomous agent may control (D43). The desktop app's alone: an agent or an MCP client neither reads the list nor decides for itself.

| Метод | Путь | Кто | Что | Параметры |
|---|---|---|---|---|
| GET | `/v1/programs` | только окно | `list_all()` |  |
| POST | `/v1/programs/scan` | только окно | A look now; what it finds comes as `programs.scanned` (and `programs.found` for new programs). |  |
| PUT | `/v1/programs/{pid}` | только окно | `change()` | тело `{decision: str \| None = None, later: bool = False}` |
| POST | `/v1/programs` | только окно | A program the look does not see (portable, without a shortcut): an .exe the user picked. | тело `{path: str}` |
| DELETE | `/v1/programs/{pid}` | только окно | Only a program added by hand; the ones the look finds are decided, not removed. |  |
| POST | `/v1/programs/offer/dismiss` | только окно | The list was offered after the first look: not again. |  |

### processes

/v1/processes - processes and ports in project folders (stage 2, D4), and stopping one (the app only).

| Метод | Путь | Кто | Что | Параметры |
|---|---|---|---|---|
| GET | `/v1/processes` | только окно | project id -> its processes (the latest scan, or a fresh one). The same arrives as `processes.changed`. |  |
| POST | `/v1/processes/{pid}/stop` | только окно | Ends a process of a project and its children. Never the hub, never a process outside the projects. |  |

### terminals

/v1/terminals - terminals the hub runs (stage 2, D4, D31).

| Метод | Путь | Кто | Что | Параметры |
|---|---|---|---|---|
| GET | `/v1/terminals` | только окно | `list_terminals()` | `?project_id: str \| None = None`<br>`?limit: int = 50` |
| POST | `/v1/terminals` | только окно | `start_terminal()` | тело `{project_id: str \| None = None, path: str = '', cwd: str \| None = None, command: str \| None = None, shell: str = '', cols: int = 120, rows: int = 30, title: str = ''}` |
| GET | `/v1/terminals/{terminal_id}` | только окно | `get_terminal()` |  |
| GET | `/v1/terminals/{terminal_id}/output` | только окно | The last output. While the terminal runs, `start` and `end` are the offsets of its first character and right after it: `terminal.output` events with a smaller offset are already in `data`. `since`: the output from that offset on, when the hub still has it (then `start` equals it) - what a client missed after a break. | `?max_chars: int = 65536`<br>`?since: int \| None = None` |
| POST | `/v1/terminals/{terminal_id}/input` | только окно | `terminal_input()` | тело `{data: str}` |
| POST | `/v1/terminals/{terminal_id}/resize` | только окно | `terminal_resize()` | тело `{cols: int, rows: int}` |
| POST | `/v1/terminals/{terminal_id}/stop` | только окно | `stop_terminal()` |  |
| DELETE | `/v1/terminals/{terminal_id}` | только окно | `remove_terminal()` |  |

### cards

/v1/projects/{id}/card - the project card (stage 3, D24): a model drafts it, the user confirms it.

| Метод | Путь | Кто | Что | Параметры |
|---|---|---|---|---|
| GET | `/v1/projects/{project_id}/card` | только окно | `get_card()` |  |
| GET | `/v1/projects/{project_id}/card/export` | только окно | What writing the card into each file would do: create it, add the block at its end, replace the block. |  |
| POST | `/v1/projects/{project_id}/card/export` | только окно | `export_card()` | тело `{targets: list[str]}` |
| POST | `/v1/projects/{project_id}/card/draft` | только окно | `draft_card()` | тело `{model_id: str, update: bool = False}` |
| PUT | `/v1/projects/{project_id}/card` | только окно | The user's text (the draft as edited, or their own) becomes the next confirmed version. | тело `{text: str}` |
| POST | `/v1/projects/{project_id}/card/restore` | только окно | `restore_card()` | тело `{version: int}` |
| DELETE | `/v1/projects/{project_id}/card/draft` | только окно | `drop_draft()` |  |

### notes

/v1/projects/{id}/notes and /v1/notes/{id} - a project's notes for models (stage 3, D22 L4).

| Метод | Путь | Кто | Что | Параметры |
|---|---|---|---|---|
| GET | `/v1/projects/{project_id}/notes` | только окно | `list_notes()` |  |
| POST | `/v1/projects/{project_id}/notes` | только окно | `add_note()` | тело `{text: str}` |
| PUT | `/v1/notes/{note_id}` | только окно | `update_note()` | тело `{text: str}` |
| DELETE | `/v1/notes/{note_id}` | только окно | `delete_note()` |  |

### links

/v1/links - links between projects (stage 3, D25): proposed from evidence, confirmed by the user.

| Метод | Путь | Кто | Что | Параметры |
|---|---|---|---|---|
| GET | `/v1/links` | только окно | `list_links()` |  |
| POST | `/v1/links/scan` | только окно | Looks for evidence between every pair of projects; new proposals join the list. |  |
| POST | `/v1/links` | только окно | `add_link()` | тело `{a_id: str, b_id: str, kind: str = 'uses', description: str = ''}` |
| PUT | `/v1/links/{link_id}` | только окно | `change_link()` | тело `{status: str \| None = None, kind: str \| None = None, description: str \| None = None}` |
| DELETE | `/v1/links/{link_id}` | только окно | `delete_link()` |  |

### approvals

/v1/approvals - the approval queue (stage 4, D17, D18): what agents wait to be allowed, and the user's answers.

| Метод | Путь | Кто | Что | Параметры |
|---|---|---|---|---|
| GET | `/v1/approvals` | только окно | `pending()` |  |
| GET | `/v1/approvals/{approval_id}` | только окно | `get_approval()` |  |
| POST | `/v1/approvals/{approval_id}` | только окно | `answer()` | тело `{allow: bool, scope: Literal['once', 'chat', 'project'] = 'once', comment: str = ''}` |

### claude_code

/v1/claude-code - the user's Claude Code under the hub (stage 4, D8): is it installed, and run it in a terminal.

| Метод | Путь | Кто | Что | Параметры |
|---|---|---|---|---|
| GET | `/v1/claude-code` | только окно | `status()` |  |
| POST | `/v1/projects/{project_id}/claude-code/terminal` | только окно | `open_terminal()` | тело `{cols: int = 120, rows: int = 30}` |

### changes

/v1/.../changes and /v1/.../rules - what the hub's models wrote, and the project's standing answers (stage 5, D17).

| Метод | Путь | Кто | Что | Параметры |
|---|---|---|---|---|
| GET | `/v1/projects/{project_id}/changes` | только окно | `list_changes()` | `?limit: int = 50` |
| GET | `/v1/changes/{change_id}` | только окно | The change with its diff, from the versions the hub kept. |  |
| POST | `/v1/changes/{change_id}/revert` | только окно | `revert()` | тело `{force: bool = False}` |
| GET | `/v1/projects/{project_id}/rules` | только окно | `list_rules()` |  |
| DELETE | `/v1/rules/{rule_id}` | только окно | `delete_rule()` |  |

### agents

/v1/agents and /v1/runs - the hub's own agents (stage 5, D16): profiles, and runs of tasks.

| Метод | Путь | Кто | Что | Параметры |
|---|---|---|---|---|
| GET | `/v1/agents` | только окно | `list_profiles()` |  |
| POST | `/v1/agents` | только окно | `create_profile()` | тело `{name: str, instructions: str = '', model_id: str, tools: list[str], mode: str = 'ask', project_ids: list[str], max_steps: int = 30, max_minutes: int = 30, budget_usd: float \| None = None}` |
| GET | `/v1/agents/{agent_id}` | только окно | `get_profile()` |  |
| PUT | `/v1/agents/{agent_id}` | только окно | `update_profile()` | тело `{name: str, instructions: str = '', model_id: str, tools: list[str], mode: str = 'ask', project_ids: list[str], max_steps: int = 30, max_minutes: int = 30, budget_usd: float \| None = None}` |
| DELETE | `/v1/agents/{agent_id}` | только окно | `delete_profile()` |  |
| GET | `/v1/agents/{agent_id}/versions` | только окно | `versions()` |  |
| POST | `/v1/agents/{agent_id}/runs` | только окно | `start_run()` | тело `{task: str, project_ids: list[str] \| None = None}` |
| GET | `/v1/runs` | только окно | `list_runs()` | `?limit: int = 50` |
| GET | `/v1/runs/{run_id}` | только окно | `get_run()` |  |
| POST | `/v1/runs/{run_id}/cancel` | только окно | `cancel_run()` |  |
| POST | `/v1/runs/autonomous` | только окно | `start_autonomous()` | тело `{goal: str, done_when: str = '', profile_id: str \| None = None, model_id: str \| None = None, planner_model_id: str \| None = None, project_ids: list[str] \| None = None, autonomy: str = 'plan', max_steps: int = 100, max_minutes: int = 60, max_replans: int = 3, budget_usd: float \| None = 1.0, network: bool = False, refusal_vector: bool \| None = None}` |
| GET | `/v1/runs/{run_id}/plan` | только окно | An autonomous run's goal, every version of its plan with the steps, its questions, its report. |  |
| GET | `/v1/conversations/{conversation_id}/questions` | только окно | A chat's questions still waiting for the user (its model's ask_user, Claude Code's own), with their options; answered through /runs/{id}/questions/{question_id} like an agent's. |  |
| POST | `/v1/runs/{run_id}/questions/{question_id}` | только окно | `answer_question()` | тело `{answer: str}` |
| POST | `/v1/runs/{run_id}/pause` | только окно | The run stops before its next model call; what it runs now finishes. |  |
| POST | `/v1/runs/{run_id}/resume` | только окно | `resume_run()` |  |
| POST | `/v1/runs/{run_id}/message` | только окно | The user writes to a running task, as in a chat: the answer to the question it waits on, or else a word it reads before its next step (another limit, a hint, a change of mind). | тело `{text: str}` |
| PUT | `/v1/runs/{run_id}/autonomy` | только окно | The run's level changes on the way; «Сам» also answers what it waits on now (its plan, a step, acting in a program); sending is still asked. | тело `{autonomy: str}` |
| PUT | `/v1/runs/{run_id}/plan` | только окно | The user's own plan for the run: a new version; the agent hears it before its next step. | тело `{steps: list[Any], reason: str = ''}` |
| GET | `/v1/agent/access` | только окно | `agent_access()` |  |
| GET | `/v1/agent/keys` | только окно | The user's API keys for http_request: name, host, header - and whether set; never a key. |  |
| PUT | `/v1/agent/keys/{name}` | только окно | A key behind the connections' password (D13): the hub adds it to the requests to its host only. | тело `{host: str, header: str = 'Authorization', prefix: str = 'Bearer ', value: str = ''}` |
| DELETE | `/v1/agent/keys/{name}` | только окно | `delete_agent_key()` |  |
| PUT | `/v1/agent/access` | только окно | `set_agent_access()` | тело `{folders: list[dict[str, Any]] \| None = None, domains: list[str] \| None = None, screen_cloud: bool \| None = None}` |

### stats

/v1/stats - what the hub has learnt about its models and its spending (stage 6, D11, D12). Open to every client.

| Метод | Путь | Кто | Что | Параметры |
|---|---|---|---|---|
| GET | `/v1/stats` | только окно | `get_stats()` | `?days: int = 30` |

### advice

/v1/advice - which model for this chat, and what an agent's task may cost (stage 6, D11, D12). Open to every client; the hub only advises - nothing is switched (D11).

| Метод | Путь | Кто | Что | Параметры |
|---|---|---|---|---|
| GET | `/v1/advice` | только окно | `advice()` | `?conversation_id: str \| None = None`<br>`?project_ids: str = ''`<br>`?priority: str = 'balanced'` |
| GET | `/v1/agents/{agent_id}/estimate` | только окно | `estimate()` |  |

### openai

/v1/openai - the hub as an OpenAI-compatible server (stage 6, D33): base URL http://127.0.0.1:8765/v1/openai, the client's token as the API key. See hub/openai_proxy.py.

| Метод | Путь | Кто | Что | Параметры |
|---|---|---|---|---|
| GET | `/v1/openai/models` | токен | `models()` |  |
| POST | `/v1/openai/chat/completions` | токен | `completions()` |  |

## WebSocket `/v1/events`

```text
WebSocket /v1/events: the live event stream (D31).

Protocol (JSON text frames):
- the client connects with `Authorization: Bearer <token>`; without a valid token the handshake is refused;
- the hub sends `{"type": "hub.hello", ...}` and then events matching the subscription (all, at first);
- the client may send `{"op": "subscribe", "topics": ["chat.*"], "since": 123}` at any time: from then on
  only matching events arrive, and stored events with id > since are sent first, so a reconnecting client
  misses nothing;
- `{"op": "ping"}` gets `{"type": "pong"}`.
If the client reads too slowly its queue overflows and the hub closes with code 1013: reconnect with `since`.

The window gets every event. Another client (D31, D46) gets the hub's and the projects' state (OUTSIDE_EVENTS) - a
cloud one only of the projects that allow the cloud - and the events of the conversations it began; never another
conversation's text, an approval or a terminal's output.
```

Каждое событие — JSON-объект `{"id", "ts", "type", "payload", "project_id"}`. `id` есть только у событий, которые хаб хранит в журнале (их можно дочитать после переподключения: `since`, а также `GET /v1/feed`); у живых событий `id` — `null`.

### Типы событий

| Тип | Хранится | Поля payload | Где публикуется |
|---|---|---|---|
| `agent.access` | да | `folders` | hub/api/agents.py |
| `agent.keys` | да | `name`, `host`, `removed` | hub/api/agents.py |
| `agent.run` | да | `run_id`, `message`, `autonomy` | hub/api/agents.py |
| `agents.changed` | нет | `agent_id` | hub/api/agents.py |
| `approval.decided` | да | `approval` | hub/approvals.py |
| `approval.requested` | да | `approval` | hub/approvals.py |
| `autostart.changed` | да | `login`, `llama_swap` | hub/api/autostart.py |
| `budget.changed` | да | `budget`, `by`, `deleted` | hub/api/budgets.py |
| `budget.overridden` | да | `model_id`, `client_id`, `budgets` | hub/chat.py |
| `budget.{kind}` | да | `model_id`, `budget` | hub/api/model_settings.py, hub/api/openai.py, hub/chat.py, hub/judge.py |
| `card.confirmed` | да | `project_id`, `name`, `version`, `restored_from` | hub/api/cards.py |
| `card.drafted` | да | `project_id`, `name`, `model_id`, `cost_usd`, `update` | hub/api/cards.py |
| `card.drafting` | нет | `project_id`, `model_id` | hub/api/cards.py |
| `card.dropped` | нет | `project_id` | hub/api/cards.py |
| `card.exported` | да | `project_id`, `files` | hub/api/cards.py |
| `card.failed` | да | `project_id`, `detail` | hub/api/cards.py |
| `chat.context` | нет | `conversation_id`, `message_id`, `tokens` | hub/chat.py |
| `chat.delta` | нет | `conversation_id`, `kind`, `text` | hub/chat.py, hub/claude_code.py |
| `chat.done` | да | `conversation_id`, `message_id`, `usage`, `stats`, `cost_usd`, `steps`, `stop_reason`, `cancelled` | hub/chat.py, hub/claude_code.py |
| `chat.error` | да | `conversation_id`, `detail`, `refusal`, `model_id`, `usage`, `cost_usd` | hub/chat.py, hub/claude_code.py |
| `chat.fallback` | да | `conversation_id`, `model_id`, `name`, `from`, `from_name`, `reason`, `planned` | hub/chat.py |
| `chat.loading` | нет | `conversation_id`, `text` | hub/chat.py |
| `chat.message` | да | `conversation_id`, `message` | hub/chat.py, hub/claude_code.py |
| `chat.projects` | нет | `conversation_id`, `projects` | hub/api/conversations.py |
| `chat.run_started` | нет | `conversation_id`, `model_id` | hub/chat.py |
| `chat.titled` | да | `conversation_id`, `title` | hub/api/conversations.py, hub/chat.py |
| `chat.tool` | нет | `conversation_id`, `name`, `input` | hub/chat.py |
| `claude_code.limit` | да | — | hub/claude_code.py |
| `computer.control` | нет | `run_id`, `window`, `program`, `action` | hub/computer.py |
| `computer.paused` | да | `run_id`, `why` | hub/computer.py |
| `computer.released` | нет | `run_id` | hub/computer.py |
| `connections.denied` | да | `wait` | hub/api/connections.py |
| `connections.password` | да | `changed` | hub/api/connections.py |
| `editor.command` | да | — | hub/editor.py |
| `editor.focus` | нет | — | hub/editor.py |
| `file.changed` | да | `change`, `project_id` | hub/chat.py |
| `file.reverted` | да | `change`, `project_id` | hub/api/changes.py |
| `file.saved` | да | `project_id` | hub/api/projects.py |
| `git.head` | да | `project_id` | hub/watch.py |
| `goal.check` | да | `conversation_id`, `goal_id`, `check` | hub/goals.py |
| `goal.checking` | нет | `conversation_id`, `goal_id` | hub/goals.py |
| `goal.judging` | нет | `conversation_id`, `goal_id`, `model_id` | hub/goals.py |
| `goal.set` | да | `conversation_id`, `goal` | hub/goals.py |
| `goal.status` | да | `conversation_id`, `goal_id`, `status`, `detail`, `goal`, `proving` | hub/goals.py |
| `goal.steered` | да | `conversation_id`, `goal_id`, `id`, `text` | hub/goals.py |
| `hub.shutdown_requested` | да | `by` | hub/api/system.py |
| `hub.started` | да | `version`, `migrations_applied` | hub/server.py |
| `hub.stopping` | да | — | hub/server.py |
| `install.changed` | да | `job` | hub/model_install.py |
| `install.forgotten` | да | `id` | hub/model_install.py |
| `install.progress` | нет | — | hub/model_install.py |
| `link.confirmed` | да | `a_name`, `b_name`, `kind`, `description` | hub/api/links.py |
| `links.changed` | нет | — | hub/api/links.py, hub/links.py |
| `links.proposed` | да | `added`, `links` | hub/links.py |
| `llama.config` | да | `model_id`, `changes`, `backup`, `verified`, `restored`, `installed` | hub/api/llama.py, hub/model_install.py |
| `llama_swap.started` | да | `ready` | hub/server.py |
| `mail.connected` | да | `address` | hub/api/mail.py |
| `mail.disconnected` | да | — | hub/api/mail.py |
| `mcp.added` | да | `server`, `target` | hub/api/mcp_servers.py |
| `mcp.removed` | да | `server` | hub/api/mcp_servers.py |
| `mcp.status` | нет | `server`, `state`, `detail`, `tools` | hub/mcp_client.py |
| `memory.changed` | нет | `fact_id`, `deleted`, `skill_id` | hub/api/memory.py, hub/autonomous.py |
| `model.settings` | да | `model_id`, `version`, `name`, `imported` | hub/api/model_settings.py |
| `models.synced` | да | — | hub/api/connections.py, hub/api/models.py, hub/api/portable.py, hub/server.py |
| `models.unloaded` | да | `by` | hub/api/models.py |
| `note.changed` | нет | `project_id` | hub/api/notes.py |
| `port.closed` | да | `project_id`, `port`, `pid`, `name` | hub/processes.py |
| `port.opened` | да | `project_id`, `port`, `pid`, `name`, `args` | hub/processes.py |
| `process.stopped` | да | `pid`, `name`, `cmdline`, `pids`, `by` | hub/api/processes.py |
| `processes.changed` | нет | `projects` | hub/processes.py |
| `program.decided` | да | `id`, `name`, `decision` | hub/api/programs.py |
| `programs.found` | да | `programs` | hub/programs.py |
| `programs.scanned` | нет | `removed_manual`, `added`, `first`, `count`, `new`, `removed`, `failed`, `error` | hub/api/programs.py, hub/programs.py |
| `programs.scanning` | нет | — | hub/programs.py |
| `project.added` | да | `project` | hub/api/portable.py, hub/api/projects.py |
| `project.changed` | да | `project` | hub/api/projects.py |
| `project.files` | нет | — | hub/watch.py |
| `project.found` | да | `project_id`, `name`, `path` | hub/watch.py |
| `project.git` | нет | `project_id` | hub/watch.py |
| `project.missing` | да | `project_id`, `name`, `path` | hub/watch.py |
| `project.moved` | да | `project_id`, `name`, `from`, `to`, `project` | hub/watch.py |
| `project.removed` | да | `id`, `name` | hub/api/projects.py |
| `proving.deleted` | да | `exam_id`, `run_id` | hub/proving.py |
| `proving.exam` | да | `exam` | hub/proving.py |
| `proving.run` | да | `run` | hub/proving.py |
| `release.changed` | да | `job` | hub/release.py |
| `release.updated` | да | `version`, `from` | hub/release.py |
| `rules.changed` | нет | `project_id` | hub/api/changes.py |
| `run.answered` | да | `run_id`, `id`, `by` | hub/autonomous.py |
| `run.finished` | да | `run` | hub/agents.py |
| `run.notice` | да | `run_id`, `text` | hub/agent_tools.py |
| `run.paused` | да | `run_id` | hub/autonomous.py |
| `run.plan` | нет | `run_id`, `plan` | hub/autonomous.py |
| `run.question` | да | `run_id`, `id`, `question`, `options` | hub/autonomous.py |
| `run.report` | да | `run_id`, `report` | hub/autonomous.py |
| `run.resumed` | да | `run_id` | hub/autonomous.py |
| `run.started` | да | `run`, `resumed` | hub/agents.py |
| `run.status` | нет | `run_id`, `status` | hub/agents.py |
| `sandbox.applied` | да | — | hub/api/sandbox.py |
| `sandbox.changed` | да | `conversation_id`, `project_id`, `path` | hub/chat.py |
| `sandbox.discarded` | да | — | hub/api/sandbox.py |
| `schedule.added` | да | `schedule` | hub/api/schedules.py |
| `schedule.changed` | да | `schedule` | hub/api/schedules.py |
| `schedule.quiet` | да | `quiet` | hub/api/schedules.py |
| `schedule.removed` | да | `id` | hub/api/schedules.py |
| `schedule.started` | да | `schedule`, `run_id`, `why` | hub/schedule.py |
| `schedule.waiting` | да | `schedule`, `until` | hub/schedule.py |
| `secrets.changed` | да | `name`, `title`, `set` | hub/api/connections.py |
| `settings.imported` | да | `parts` | hub/api/portable.py |
| `setup.changed` | да | `job` | hub/setup.py |
| `setup.progress` | да | — | hub/setup.py |
| `system.status` | нет | — | hub/system_status.py |
| `telegram.linked` | да | `chat_id` | hub/telegram.py |
| `telegram.message` | да | `text` | hub/telegram.py |
| `telegram.unlinked` | да | — | hub/api/connections.py |
| `terminal.exited` | да | `terminal_id`, `exit_code`, `title`, `stopped` | hub/terminals.py |
| `terminal.output` | нет | `terminal_id`, `offset`, `data` | hub/terminals.py |
| `terminal.removed` | нет | `terminal_id` | hub/api/terminals.py |
| `terminal.started` | да | `terminal` | hub/terminals.py |
| `tools.fetched` | да | `tool`, `version`, `path` | hub/ripgrep.py |
