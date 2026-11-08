# AFO

Единственный рабочий документ проекта. **Новая сессия: сначала прочитай разделы «Суть проекта» и «Правила работы» в конце
файла.**

## Карта файлов

Все файлы под git. Папки `data/`, `build/`, `.venv/` и другие, которых нет в git, сюда не входят.

### Корень

- `.gitattributes` — как git хранит файлы: переводы строк, двоичные типы
- `.gitignore` — что не попадает в git
- `.github/workflows/release.yml` — сборка установщика на GitHub по тегу vX.Y.Z и черновик выпуска
- `AFO.cmd` — запуск окна двойным щелчком, без консоли
- `Claude.md` — указание Claude Code: работать строго по TechDoc.md и сначала задавать вопросы
- `LICENSE` — лицензия GNU GPL 3
- `PRIVACY.md` — политика конфиденциальности, её требует подпись SignPath
- `README.md` — страница проекта на GitHub, в ней политика подписи кода
- `README.en.md` — та же страница по-английски
- `TechDoc.md` — этот файл, единственный рабочий документ: карта, суть, правила
- `pyproject.toml` — пакет, зависимости, настройки ruff и pytest
- `requirements.lock` — точные проверенные версии зависимостей

### common/ — общий код хаба и окна

- `common/__init__.py` — пакет общего кода
- `common/crashes.py` — след от аварийного падения окна и хаба
- `common/disks.py` — диски компьютера и их тип (NVMe, SSD, HDD, USB) для установки моделей
- `common/gguf.py` — чтение заголовка файла GGUF для оценки памяти
- `common/hardlinks.py` — все имена одного файла (жёсткие ссылки)
- `common/hardware.py` — видеокарты компьютера для мастера первого запуска
- `common/i18n.py` — перевод текстов: tr(), plural(), выбор языка
- `common/i18n_catalog.py` — сверка ключей английского каталога с кодом, `--sync` добавляет новые
- `common/invisible.py` — символы, которых человек не видит
- `common/kept_json.py` — JSON в папке данных, чтобы не выяснять одно и то же при каждом запуске
- `common/llama_memory.py` — сколько видеопамяти и ОЗУ займёт модель llama.cpp
- `common/locale/en.json` — английский каталог: русский текст из кода → перевод
- `common/logging_setup.py` — журналы всех процессов AFO
- `common/paths.py` — где AFO держит данные и установленное
- `common/picture_sizes.py` — ступени размера картинки, общие для хаба и окна
- `common/plain_errors.py` — ошибки простыми словами

### hub/ — хаб: основные модули

- `hub/__init__.py` — пакет хаба
- `hub/__main__.py` — командная строка: запуск, остановка, токены, восстановление базы
- `hub/advisor.py` — какая модель подойдёт чату или задаче и сколько задача будет стоить
- `hub/agent_tools.py` — инструменты автономного агента за пределами проектов
- `hub/agents.py` — агенты хаба: запуск, очередь видеокарты (`_gpu`), итоги, возобновление
- `hub/api_reference.py` — справка по API из кода, по запросу: `python -m hub.api_reference`
- `hub/appcontainer.py` — скрипты агента в Windows AppContainer
- `hub/approvals.py` — очередь разрешений
- `hub/attachments.py` — файлы и картинки, отправленные с сообщением
- `hub/auth.py` — токены клиентов хаба
- `hub/autonomous.py` — автономный агент и его режимы
- `hub/autostart.py` — запуск хаба при входе в Windows
- `hub/backup.py` — копии базы средствами SQLite
- `hub/budgets.py` — бюджеты платных моделей
- `hub/cards.py` — карточки проектов
- `hub/changes.py` — запись файлов моделями: diff, откат
- `hub/chat.py` — один ответ модели через шлюз с потоком в шину событий
- `hub/checkpoints.py` — контрольные точки цели
- `hub/claude_code.py` — Claude Code под управлением хаба
- `hub/commands.py` — команды, которые запускают модели
- `hub/compact.py` — определения инструментов по размеру модели
- `hub/completion.py` — автодополнение кода для редактора окна
- `hub/computer.py` — управление окнами программ через UI Automation
- `hub/connections.py` — ключи, токены и адреса за паролем подключений
- `hub/context.py` — что модель узнаёт о проектах
- `hub/delegate.py` — инструменты координатора: раздача задач агентам
- `hub/diagnostics.py` — «Проверить систему» и журналы для поддержки
- `hub/editor.py` — что пользователь делает в VS Code
- `hub/engines.py` — движки локальных моделей: llama.cpp, llama-swap
- `hub/events.py` — шина событий: таблица `events` и живые подписчики
- `hub/export.py` — выгрузка карточки в проект
- `hub/ffmpeg.py` — ffmpeg для MP4 из кадров видео
- `hub/fit.py` — беседа, переросшая окно контекста модели
- `hub/goals.py` — цель чата: работа ход за ходом до результата
- `hub/guard.py` — страж цели
- `hub/hf.py` — Hugging Face: поиск и сведения о моделях
- `hub/history.py` — версии файлов проекта
- `hub/judge.py` — судья цели
- `hub/language.py` — язык того, что хаб пишет людям
- `hub/limits.py` — сколько контекста получает модель
- `hub/links.py` — связи между проектами
- `hub/llama_config.py` — `config.yaml` llama-swap: чтение и запись настроек запуска
- `hub/locate.py` — найти работающий хаб или запустить новый
- `hub/mail.py` — почта автономного агента (IMAP/SMTP)
- `hub/mcp_client.py` — хаб как клиент внешних MCP-серверов
- `hub/mcp_server.py` — MCP-сервер хаба
- `hub/mcp_stdio.py` — stdio-переходник к MCP-серверу хаба
- `hub/meaning.py` — поиск по смыслу без векторов
- `hub/memory.py` — память автономного агента
- `hub/model_catalog.json` — подборка локальных моделей для установки
- `hub/model_install.py` — установка моделей, пробный запуск по одной на видеокарте
- `hub/model_scan.py` — модели, уже лежащие на дисках
- `hub/model_settings.py` — настройки моделей из окна
- `hub/notes.py` — заметки проекта для моделей
- `hub/openai_proxy.py` — хаб как OpenAI-совместимый сервер
- `hub/portable.py` — все настройки хаба одним файлом
- `hub/processes.py` — процессы и порты в папках проектов
- `hub/programs.py` — программы компьютера и права агента на них
- `hub/projects.py` — реестр проектов, дерево, файлы, git
- `hub/proving.py` — полигон и экзамен моделей
- `hub/release.py` — обновления самой AFO
- `hub/retention.py` — сколько хаб хранит на диске
- `hub/rewrite.py` — модель правит выделенное в редакторе
- `hub/ripgrep.py` — загрузка ripgrep
- `hub/sandbox.py` — режим чата «Песочница»
- `hub/schedule.py` — задачи по расписанию и событиям
- `hub/search.py` — поиск по файлам проекта
- `hub/secret_filter.py` — секреты не попадают к моделям
- `hub/secrets.py` — хранение ключей в диспетчере учётных данных Windows
- `hub/security.py` — с хабом говорят только программы этого компьютера
- `hub/server.py` — веб-приложение хаба: сборка и запуск служб
- `hub/settings.py` — адрес хаба и папка данных
- `hub/setup.py` — первый запуск: подбор и установка движков и моделей
- `hub/singleton.py` — только один хаб одновременно
- `hub/stats.py` — что хаб узнал о моделях и расходах
- `hub/store.py` — запросы к базе: каталог, чаты, сообщения, расход
- `hub/symbols.py` — индекс символов на tree-sitter
- `hub/syntax.py` — разбираются ли изменённые файлы
- `hub/system_status.py` — видеокарта, память, llama-swap, освобождение видеопамяти
- `hub/telegram.py` — разрешения с телефона через Telegram-бота
- `hub/terminals.py` — терминалы хаба через ConPTY (pywinpty)
- `hub/tools.py` — инструменты модели в чате
- `hub/tuning.py` — подбор настроек запуска замерами
- `hub/visual.py` — картинки и видео
- `hub/watch.py` — слежение за папками проектов
- `hub/web.py` — сеть для моделей хаба
- `hub/websearch.py` — поиск в сети (`web_search`)
- `hub/winjob.py` — Windows Job для процессов агента

### hub/api/ — HTTP и WebSocket под /v1

- `hub/api/__init__.py` — сборка всех маршрутов /v1
- `hub/api/advice.py` — `/v1/advice`: совет по модели и цена задачи
- `hub/api/agents.py` — `/v1/agents`, `/v1/runs`: агенты и прогоны
- `hub/api/approvals.py` — `/v1/approvals`: очередь разрешений
- `hub/api/autostart.py` — `/v1/autostart`: запуск при входе в Windows
- `hub/api/budgets.py` — `/v1/budgets`: бюджеты
- `hub/api/cards.py` — карточка проекта
- `hub/api/changes.py` — что записали модели, постоянные ответы проекта
- `hub/api/claude_code.py` — `/v1/claude-code`: Claude Code пользователя
- `hub/api/connections.py` — `/v1/connections`: ключи и адреса
- `hub/api/conversations.py` — `/v1/conversations`: чаты и сообщения
- `hub/api/deps.py` — общие зависимости запросов: токен, права
- `hub/api/editor.py` — `/v1/editor`: сообщения расширения VS Code
- `hub/api/events_ws.py` — WebSocket `/v1/events`
- `hub/api/goals.py` — цель чата
- `hub/api/history.py` — история файлов проекта
- `hub/api/install.py` — `/v1/install`: установка локальных моделей
- `hub/api/language.py` — `/v1/language`: язык хаба
- `hub/api/links.py` — `/v1/links`: связи проектов
- `hub/api/llama.py` — `/v1/llama`: настройки запуска в `config.yaml`
- `hub/api/mail.py` — `/v1/mail`: почта агента
- `hub/api/mcp_servers.py` — `/v1/mcp/servers`: внешние MCP-серверы
- `hub/api/memory.py` — `/v1/memory`, `/v1/skills`: память агента
- `hub/api/model_settings.py` — `/v1/model_settings`: настройки моделей
- `hub/api/models.py` — `/v1/models`: каталог моделей
- `hub/api/notes.py` — заметки проекта
- `hub/api/openai.py` — `/v1/openai`: OpenAI-совместимый сервер
- `hub/api/portable.py` — `/v1/settings`: перенос настроек
- `hub/api/processes.py` — `/v1/processes`: процессы и порты
- `hub/api/programs.py` — `/v1/programs`: программы для агента
- `hub/api/projects.py` — `/v1/projects`: реестр, дерево, файлы, git
- `hub/api/proving.py` — `/v1/proving`: полигон и экзамен
- `hub/api/release.py` — `/v1/release`: обновления AFO
- `hub/api/sandbox.py` — «Песочница» чата
- `hub/api/schedules.py` — `/v1/schedules`, тихие часы
- `hub/api/setup.py` — `/v1/setup`: мастер первого запуска
- `hub/api/stats.py` — `/v1/stats`: статистика
- `hub/api/support.py` — `/v1/support`: проверка системы и журналы
- `hub/api/system.py` — `/v1/health`, `/v1/info`
- `hub/api/terminals.py` — `/v1/terminals`: терминалы
- `hub/api/tuning.py` — `/v1/tune`: подбор настроек замерами
- `hub/api/visual.py` — `/v1/visual`: картинки и видео

### hub/db/ — база SQLite

- `hub/db/__init__.py` — пакет базы
- `hub/db/database.py` — одно соединение SQLite на хаб
- `hub/db/migrate.py` — применение нумерованных миграций
- `hub/db/migrations/001_init.sql` — клиенты и токены, настройки, каталог моделей, чаты, сообщения, расход, события
- `hub/db/migrations/002_budgets.sql` — бюджеты
- `hub/db/migrations/003_projects.sql` — реестр проектов
- `hub/db/migrations/004_terminals.sql` — терминалы
- `hub/db/migrations/005_conversation_projects.sql` — проекты чата
- `hub/db/migrations/006_conversation_context.sql` — что входит в контекст чата
- `hub/db/migrations/007_project_cards.sql` — карточки проектов
- `hub/db/migrations/008_notes.sql` — заметки
- `hub/db/migrations/009_project_links.sql` — связи проектов
- `hub/db/migrations/010_conversation_tools.sql` — инструменты, отправленные чату вначале
- `hub/db/migrations/011_card_baseline.sql` — против чего подтверждена карточка
- `hub/db/migrations/012_approvals.sql` — очередь разрешений
- `hub/db/migrations/013_file_changes.sql` — правки файлов, разрешение на весь проект
- `hub/db/migrations/014_agents.sql` — агенты хаба
- `hub/db/migrations/015_subtasks.sql` — подзадачи координатора
- `hub/db/migrations/016_programs.sql` — программы и права агента
- `hub/db/migrations/017_model_settings.sql` — настройки моделей по версиям
- `hub/db/migrations/018_chat_instructions.sql` — инструкции чата
- `hub/db/migrations/019_autonomous.sql` — автономные задачи
- `hub/db/migrations/020_memory.sql` — память агента
- `hub/db/migrations/021_agent_access.sql` — доступ агента вне проектов
- `hub/db/migrations/022_mcp_servers.sql` — внешние MCP-серверы
- `hub/db/migrations/023_computer.sql` — журнал управления компьютером
- `hub/db/migrations/024_schedules.sql` — расписания и события
- `hub/db/migrations/025_sandbox_mode.sql` — режим «Песочница»
- `hub/db/migrations/026_model_installs.sql` — установки моделей
- `hub/db/migrations/027_refusal_vector.sql` — снятие отказов у модели
- `hub/db/migrations/028_chat_refusal_vector.sql` — снятие отказов в чате
- `hub/db/migrations/029_question_options.sql` — варианты ответа в вопросах
- `hub/db/migrations/030_chat_home_project.sql` — домашний проект чата
- `hub/db/migrations/031_goals.sql` — цели чатов
- `hub/db/migrations/032_goal_judge.sql` — судья цели
- `hub/db/migrations/033_proving.sql` — полигон и экзамен
- `hub/db/migrations/034_conversation_client.sql` — какой клиент создал чат
- `hub/db/migrations/035_model_installs_own_files.sql` — модели с дисков без копии
- `hub/db/migrations/036_delegated_tasks.sql` — пакеты задач координатора
- `hub/db/migrations/037_delegated_task_checks.sql` — критерии проверки задач пакета
- `hub/db/migrations/038_approval_hunks.sql` — разрешение части diff
- `hub/db/migrations/039_goal_guard.sql` — страж цели
- `hub/db/migrations/040_goal_milestones.sql` — этапы цели
- `hub/db/migrations/041_goal_checkpoints.sql` — контрольные точки цели
- `hub/db/migrations/042_goal_strong_model.sql` — сильная модель цели
- `hub/db/migrations/043_screen_cloud_per_task.sql` — видит ли облако окна, по задаче
- `hub/db/migrations/044_run_step_facts.sql` — факты шага прогона
- `hub/db/migrations/045_program_kinds.sql` — виды программ
- `hub/db/migrations/046_project_ports.sql` — порты проекта
- `hub/db/migrations/047_file_versions.sql` — версии файлов
- `hub/db/migrations/048_visual_jobs.sql` — задания на картинки
- `hub/db/migrations/049_visual_source.sql` — картинка по картинке
- `hub/db/migrations/050_visual_video.sql` — кадры и частота видео
- `hub/db/migrations/051_full_autonomy.sql` — режим агента «Полный»
- `hub/db/migrations/052_model_installs_trial_states.sql` — состояния пробного запуска

### hub/gateway/ — шлюз моделей

- `hub/gateway/__init__.py` — пакет шлюза
- `hub/gateway/anthropic_provider.py` — адаптер Anthropic
- `hub/gateway/base.py` — общие типы шлюза
- `hub/gateway/cloud_openai.py` — облачные провайдеры по протоколу OpenAI: OpenAI, DeepSeek, OpenRouter
- `hub/gateway/gemini_provider.py` — адаптер Gemini
- `hub/gateway/openai_compat.py` — OpenAI-совместимый адаптер (llama-swap)
- `hub/gateway/openai_responses.py` — адаптер OpenAI Responses
- `hub/gateway/registry.py` — адаптеры по провайдерам, таблица моделей, поиск модели по id
- `hub/gateway/vertex_provider.py` — Gemini на Vertex AI

### hub/proving_tasks/ — учебные задания полигона

- `hub/proving_tasks/csv_report/exam/check.py` — скрытая проверка «Отчёт по продажам»
- `hub/proving_tasks/csv_report/files/sales.csv` — данные задания
- `hub/proving_tasks/csv_report/task.json` — описание задания
- `hub/proving_tasks/fizzbuzz/exam/check.py` — скрытая проверка «Почини FizzBuzz»
- `hub/proving_tasks/fizzbuzz/files/fizzbuzz.py` — код с ошибкой
- `hub/proving_tasks/fizzbuzz/files/test_fizzbuzz.py` — тесты задания
- `hub/proving_tasks/fizzbuzz/task.json` — описание задания
- `hub/proving_tasks/log_parse/exam/check.py` — скрытая проверка «Разбор журнала»
- `hub/proving_tasks/log_parse/files/app.log` — журнал для разбора
- `hub/proving_tasks/log_parse/files/logparse.py` — заготовка функции
- `hub/proving_tasks/log_parse/files/test_logparse.py` — тесты задания
- `hub/proving_tasks/log_parse/task.json` — описание задания
- `hub/proving_tasks/readme_docs/exam/check.py` — скрытая проверка «README по коду»
- `hub/proving_tasks/readme_docs/files/tool.py` — программа, которую надо описать
- `hub/proving_tasks/readme_docs/task.json` — описание задания
- `hub/proving_tasks/refactor/exam/check.py` — скрытая проверка «Вынеси повторяющийся код»
- `hub/proving_tasks/refactor/files/check_refactor.py` — проверка: цена с НДС считается в одном месте
- `hub/proving_tasks/refactor/files/inventory.py` — код с повторами
- `hub/proving_tasks/refactor/files/test_inventory.py` — тесты задания
- `hub/proving_tasks/refactor/task.json` — описание задания
- `hub/proving_tasks/shop_bug/exam/check.py` — скрытая проверка «Найди ошибку в магазине»
- `hub/proving_tasks/shop_bug/files/shop/__init__.py` — маленький магазин: корзина и скидка
- `hub/proving_tasks/shop_bug/files/shop/cart.py` — корзина
- `hub/proving_tasks/shop_bug/files/shop/discounts.py` — скидка
- `hub/proving_tasks/shop_bug/files/shop/money.py` — деньги в центах
- `hub/proving_tasks/shop_bug/files/test_shop.py` — тесты задания
- `hub/proving_tasks/shop_bug/task.json` — описание задания

### installer/ — установщик

- `installer/__init__.py` — пакет установщика
- `installer/afo.iss` — мастер установки Inno Setup
- `installer/build.py` — сборка папки программы и установщика

### vscode/ — расширение VS Code

- `vscode/.vscodeignore` — что не входит в пакет расширения
- `vscode/extension.js` — сообщает хабу открытый файл и команды терминалов
- `vscode/l10n/bundle.l10n.ru.json` — русские строки расширения
- `vscode/lib.js` — чистые функции расширения
- `vscode/package.json` — описание расширения
- `vscode/package.nls.json` — английские подписи
- `vscode/package.nls.ru.json` — русские подписи
- `vscode/test/lib.test.js` — тесты `lib.js`

### app/ — окно: основа

- `app/__init__.py` — пакет окна
- `app/__main__.py` — вход: `python -m app` открывает окно, находит или запускает хаб
- `app/events.py` — живые события хаба на фоновом потоке → сигналы Qt
- `app/hub_client.py` — клиент API хаба
- `app/palette.py` — цвета и размеры тёмной и светлой темы
- `app/presenter.py` — что показывает окно, простым текстом, без Qt
- `app/render.py` — Markdown → то, что показывает чат
- `app/tasks.py` — блокирующие вызовы вне потока окна
- `app/vscode.py` — «Открыть в VS Code»

### app/ui/ — окно: логика для QML

- `app/ui/__init__.py` — пакет интерфейса Qt Quick
- `app/ui/agent_keys_view.py` — ключи API для запросов агента
- `app/ui/agents.py` — агенты хаба в окне
- `app/ui/approvals.py` — очередь разрешений в окне
- `app/ui/autonomy.py` — режим «Агент»
- `app/ui/brand.py` — значок AFO
- `app/ui/chat_folder.py` — папка чата Claude Code без проекта
- `app/ui/check_view.py` — «Проверить систему»
- `app/ui/commands.py` — палитра команд Ctrl+K
- `app/ui/completions.py` — дополнения и переход к определению без модели
- `app/ui/composer.py` — упоминания в поле ввода
- `app/ui/computer_view.py` — агент на рабочем столе глазами человека, клавиша остановки
- `app/ui/connections.py` — подключения
- `app/ui/controller.py` — главная логика окна для QML
- `app/ui/editor.py` — редактор кода
- `app/ui/feed.py` — лента событий
- `app/ui/feed_rows.py` — строки ленты
- `app/ui/finder.py` — поиск и замена в редакторе
- `app/ui/ghost.py` — серый текст модели после курсора
- `app/ui/goal_view.py` — цель чата
- `app/ui/highlighter.py` — подсветка кода
- `app/ui/history_view.py` — «История файла»
- `app/ui/hitches.py` — сторож задержек потока окна
- `app/ui/icons.py` — поставщик значков для QML
- `app/ui/install_view.py` — установка моделей
- `app/ui/links_text.py` — связи проектов текстом
- `app/ui/logo.png` — логотип AFO
- `app/ui/mail_view.py` — почта агента
- `app/ui/main.py` — запуск QML-окна
- `app/ui/mcp_view.py` — внешние MCP-серверы
- `app/ui/memory_view.py` — память агента
- `app/ui/mentions.py` — фокус по упоминанию
- `app/ui/model_setup.py` — настройки моделей
- `app/ui/models.py` — списочные модели для QML
- `app/ui/notify.py` — уведомления вне окна
- `app/ui/programs.py` — программы для агента
- `app/ui/project_rows.py` — данные проекта → строки QML
- `app/ui/projects.py` — сторона проектов
- `app/ui/proving_view.py` — полигон и экзамен
- `app/ui/reader.py` — текст просмотрщика
- `app/ui/release_view.py` — обновления AFO
- `app/ui/rewrite.py` — правка выделенного моделью
- `app/ui/rows.py` — данные хаба → словари для QML
- `app/ui/sandbox_view.py` — «Песочница»
- `app/ui/schedule_view.py` — расписания
- `app/ui/setup_view.py` — мастер первого запуска
- `app/ui/snap.py` — поведение окна без рамки в Windows
- `app/ui/tasks_view.py` — доска координатора
- `app/ui/term_screen.py` — экран терминала
- `app/ui/terminals.py` — панель терминалов
- `app/ui/theme.py` — тема для QML
- `app/ui/transfer_view.py` — перенос настроек
- `app/ui/translator.py` — перевод QML
- `app/ui/visual_view.py` — «Нарисовать»

### app/ui/qml/ — экраны и элементы окна

- `app/ui/qml/AgentAccessSheet.qml` — лист: папки и сайты для агента
- `app/ui/qml/AgentComposer.qml` — поле ввода режима «Агент»
- `app/ui/qml/AgentView.qml` — основная область режима «Агент»
- `app/ui/qml/AgentsSheet.qml` — лист агентов хаба
- `app/ui/qml/ApprovalBanner.qml` — карточка разрешений над беседой
- `app/ui/qml/ApprovalCard.qml` — один запрос разрешения
- `app/ui/qml/AssistantMessage.qml` — ответ модели
- `app/ui/qml/BudgetsSheet.qml` — лист бюджетов
- `app/ui/qml/CardSheet.qml` — редактор карточки проекта
- `app/ui/qml/ChatFolderBar.qml` — полоса папки чата
- `app/ui/qml/ChatView.qml` — беседа
- `app/ui/qml/CheckSheet.qml` — лист проверки системы
- `app/ui/qml/Chip.qml` — компактная кнопка в поле ввода
- `app/ui/qml/CodeBlock.qml` — блок кода в ответе
- `app/ui/qml/CodeEditor.qml` — редактируемый текст вкладки
- `app/ui/qml/CodePieces.qml` — просмотр слишком большого файла
- `app/ui/qml/CodeView.qml` — просмотр файла или diff
- `app/ui/qml/CommandPalette.qml` — палитра команд Ctrl+K
- `app/ui/qml/Composer.qml` — поле ввода чата
- `app/ui/qml/ComputerFrame.qml` — рамка и «Стоп» вокруг окна, которым управляет агент
- `app/ui/qml/ConfirmDialog.qml` — диалог подтверждения
- `app/ui/qml/ConnectionsSheet.qml` — лист подключений
- `app/ui/qml/ContextSheet.qml` — инспектор контекста
- `app/ui/qml/ErrorMessage.qml` — карточка ошибки
- `app/ui/qml/FeedSheet.qml` — лист ленты событий
- `app/ui/qml/Field.qml` — однострочное поле
- `app/ui/qml/FindBar.qml` — полоса поиска и замены
- `app/ui/qml/GitMark.qml` — отметка git у файла
- `app/ui/qml/GoalBar.qml` — полоса цели над полем ввода
- `app/ui/qml/GoalSheet.qml` — договор цели
- `app/ui/qml/HistorySheet.qml` — лист истории файла
- `app/ui/qml/HubNote.qml` — слово хаба модели между ходами
- `app/ui/qml/Icon.qml` — значок с цветом темы
- `app/ui/qml/IconButton.qml` — плоская кнопка-значок
- `app/ui/qml/InstallSheet.qml` — лист установки моделей
- `app/ui/qml/Kbd.qml` — подсказка клавиш
- `app/ui/qml/KeysSection.qml` — раздел ключей API агента
- `app/ui/qml/MailSection.qml` — раздел почты агента
- `app/ui/qml/Main.qml` — главное окно
- `app/ui/qml/McpSection.qml` — раздел MCP-серверов
- `app/ui/qml/MemorySheet.qml` — лист памяти агента
- `app/ui/qml/MenuPopup.qml` — маленькое меню
- `app/ui/qml/Meter.qml` — подпись, полоска и значение
- `app/ui/qml/ModelsSheet.qml` — лист настроек моделей
- `app/ui/qml/NewProgramPopup.qml` — вопрос о новой программе
- `app/ui/qml/PlainButton.qml` — текстовая кнопка
- `app/ui/qml/ProgramsSheet.qml` — лист программ для агента
- `app/ui/qml/ProjectSheet.qml` — настройки проекта
- `app/ui/qml/ProjectView.qml` — основная область проектов
- `app/ui/qml/ProjectsPanel.qml` — боковая панель проектов
- `app/ui/qml/Prose.qml` — текст ответа
- `app/ui/qml/ProvingSheet.qml` — лист полигона и экзамена
- `app/ui/qml/Pulse.qml` — индикатор работы
- `app/ui/qml/RenameDialog.qml` — переименование чата
- `app/ui/qml/SandboxBar.qml` — полоса «Песочницы»
- `app/ui/qml/SandboxSheet.qml` — лист «Песочницы»
- `app/ui/qml/ScheduleList.qml` — список расписаний
- `app/ui/qml/SchedulePanel.qml` — «Когда» новой задачи
- `app/ui/qml/SectionTitle.qml` — маленький заголовок
- `app/ui/qml/Segmented.qml` — один выбор из нескольких
- `app/ui/qml/Separator.qml` — вертикальный разделитель
- `app/ui/qml/SettingsAboutSheet.qml` — настройки: о программе
- `app/ui/qml/SettingsAgentSheet.qml` — настройки: автономный агент
- `app/ui/qml/SettingsHubSheet.qml` — настройки: хаб
- `app/ui/qml/SettingsLookSheet.qml` — настройки: язык и вид
- `app/ui/qml/SettingsModelsSheet.qml` — настройки: модели
- `app/ui/qml/SettingsSheet.qml` — список настроек
- `app/ui/qml/SettingsStartupSheet.qml` — настройки: запуск
- `app/ui/qml/SettingsTransferSheet.qml` — настройки: перенос
- `app/ui/qml/Sheet.qml` — выдвижная панель справа, основа листов
- `app/ui/qml/Sidebar.qml` — боковая панель
- `app/ui/qml/SoftShadow.qml` — мягкая тень без шейдеров
- `app/ui/qml/StatusBar.qml` — строка состояния
- `app/ui/qml/SymbolBadge.qml` — вид определения одной буквой
- `app/ui/qml/TasksBar.qml` — полоса задач координатора
- `app/ui/qml/TasksSheet.qml` — доска координатора
- `app/ui/qml/TerminalPanel.qml` — панель терминалов
- `app/ui/qml/ThinScrollBar.qml` — тонкая полоса прокрутки
- `app/ui/qml/Thinking.qml` — размышления модели
- `app/ui/qml/TitleBar.qml` — заголовок окна
- `app/ui/qml/ToolCalls.qml` — вызовы инструментов в ответе
- `app/ui/qml/TransferSheet.qml` — загрузка настроек
- `app/ui/qml/UserMessage.qml` — сообщение пользователя
- `app/ui/qml/VisualSheet.qml` — лист «Нарисовать»
- `app/ui/qml/WaitingPopup.qml` — кто ждёт ответа
- `app/ui/qml/WizardSheet.qml` — мастер первого запуска

### app/ui/icons/ и app/ui/fonts/ — значки и шрифты

- `app/ui/icons/*.svg` — значки Lucide, имя говорит, что нарисовано: activity.svg, arrow-left.svg, arrow-up.svg, bot.svg, check.svg, chevron-down.svg, chevron-right.svg, cloud.svg, copy.svg, cpu.svg, diff.svg, ellipsis.svg, external-link.svg, file.svg, folder-plus.svg, folder.svg, git-branch.svg, git-commit-horizontal.svg, history.svg, image.svg, key-round.svg, keyboard.svg, layers.svg, list-tree.svg, lock.svg, message-square.svg, moon.svg, panel-left.svg, paperclip.svg, pencil.svg, pin.svg, plus.svg, power.svg, refresh-cw.svg, search.svg, shield-check.svg, sliders-horizontal.svg, sparkle.svg, square-terminal.svg, sun.svg, trash.svg, wallet.svg, x.svg, zap.svg
- `app/ui/icons/LICENSE-lucide.txt` — лицензия значков
- `app/ui/fonts/Inter-*.ttf` — шрифт интерфейса Inter: Inter-Bold.ttf, Inter-BoldItalic.ttf, Inter-Italic.ttf, Inter-Regular.ttf, Inter-SemiBold.ttf
- `app/ui/fonts/JetBrainsMono-*.ttf` — моноширинный шрифт JetBrains Mono: JetBrainsMono-Bold.ttf, JetBrainsMono-Italic.ttf, JetBrainsMono-Regular.ttf
- `app/ui/fonts/LICENSE-Inter.txt` — лицензия Inter
- `app/ui/fonts/LICENSE-JetBrainsMono.txt` — лицензия JetBrains Mono

### tests/ — тесты и подставные программы

- `tests/conftest.py` — изоляция тестов: папка данных, секреты, язык, буфер обмена
- `tests/fake_app_window.py` — своё тестовое окно для управления компьютером
- `tests/fake_claude.py` — подставной `claude`
- `tests/fake_mail_server.py` — подставной почтовый сервер
- `tests/fake_mcp_server.py` — подставной MCP-сервер
- `tests/test_advisor.py` — совет по модели и цена задачи
- `tests/test_agent_tools.py` — инструменты автономного агента
- `tests/test_agents.py` — агенты хаба
- `tests/test_anthropic.py` — адаптер Anthropic
- `tests/test_api.py` — HTTP и WebSocket API
- `tests/test_api_reference.py` — справка по API
- `tests/test_app_client.py` — клиент хаба
- `tests/test_approvals.py` — очередь разрешений
- `tests/test_attachments.py` — вложения
- `tests/test_auth.py` — токены
- `tests/test_autonomous.py` — автономный агент
- `tests/test_autostart.py` — запуск при входе
- `tests/test_backup.py` — копии базы
- `tests/test_budgets.py` — бюджеты
- `tests/test_cards.py` — карточки проектов
- `tests/test_changes.py` — запись файлов моделями
- `tests/test_chat.py` — чат через хаб целиком
- `tests/test_checkpoints.py` — контрольные точки цели
- `tests/test_claude_code.py` — Claude Code под хабом
- `tests/test_client_rights.py` — права клиентов кроме окна
- `tests/test_cloud_openai.py` — OpenAI, DeepSeek, OpenRouter
- `tests/test_code_editor.py` — редактор кода
- `tests/test_commands.py` — команды моделей
- `tests/test_compact.py` — размер определений инструментов
- `tests/test_completion.py` — автодополнение
- `tests/test_computer.py` — управление настоящими окнами, на компьютере пользователя не запускать
- `tests/test_connections.py` — подключения
- `tests/test_context.py` — слои контекста
- `tests/test_db.py` — база и миграции
- `tests/test_diagnostics.py` — проверка системы, простые слова
- `tests/test_editor.py` — сообщения VS Code
- `tests/test_engines.py` — движки моделей
- `tests/test_entry_points.py` — настоящие процессы хаба и окна
- `tests/test_export.py` — выгрузка карточки
- `tests/test_ffmpeg.py` — ffmpeg
- `tests/test_fit.py` — длинная беседа и окно контекста
- `tests/test_gateway.py` — OpenAI-совместимый адаптер
- `tests/test_gemini.py` — адаптер Gemini
- `tests/test_goal_ui.py` — цель в окне
- `tests/test_goals.py` — цель чата
- `tests/test_guard.py` — страж цели
- `tests/test_hardware.py` — что решает картинки и видео на этом компьютере
- `tests/test_history.py` — версии файлов
- `tests/test_history_ui.py` — история файла в окне
- `tests/test_i18n.py` — перевод
- `tests/test_installer.py` — установщик
- `tests/test_judge.py` — судья цели
- `tests/test_license.py` — лицензия
- `tests/test_links.py` — связи проектов
- `tests/test_llama_config.py` — `config.yaml` llama-swap
- `tests/test_logging.py` — журналы
- `tests/test_mail.py` — почта агента
- `tests/test_mcp.py` — MCP-сервер хаба
- `tests/test_mcp_client.py` — хаб как MCP-клиент
- `tests/test_mcp_stdio.py` — stdio-переходник
- `tests/test_meaning.py` — поиск по смыслу
- `tests/test_memory.py` — память агента
- `tests/test_mentions.py` — упоминания
- `tests/test_model_install.py` — установка моделей
- `tests/test_model_scan.py` — модели на дисках
- `tests/test_model_settings.py` — настройки моделей
- `tests/test_name.py` — имя AFO
- `tests/test_notes.py` — заметки
- `tests/test_openai_proxy.py` — OpenAI-совместимый сервер
- `tests/test_openai_responses.py` — адаптер Responses
- `tests/test_paths.py` — пути данных
- `tests/test_plain.py` — простые слова окна
- `tests/test_portable.py` — перенос настроек
- `tests/test_presenter.py` — тексты окна без окна
- `tests/test_prices.py` — цены моделей
- `tests/test_processes.py` — процессы и порты
- `tests/test_programs.py` — программы для агента
- `tests/test_projects.py` — проекты
- `tests/test_proving.py` — полигон и экзамен
- `tests/test_proving_ui.py` — полигон в окне
- `tests/test_release.py` — обновления AFO
- `tests/test_render.py` — Markdown
- `tests/test_retention.py` — хранение на диске
- `tests/test_ripgrep.py` — ripgrep
- `tests/test_sandbox.py` — «Песочница»
- `tests/test_save.py` — сохранение из редактора
- `tests/test_schedule.py` — расписания
- `tests/test_search.py` — поиск по проекту
- `tests/test_secret_filter.py` — фильтр секретов
- `tests/test_secrets.py` — хранилище секретов
- `tests/test_setup.py` — мастер первого запуска, сторона хаба
- `tests/test_singleton.py` — один хаб
- `tests/test_snap.py` — прилипание окна
- `tests/test_stats.py` — статистика
- `tests/test_status.py` — состояние видеокарты и памяти
- `tests/test_symbols.py` — индекс символов
- `tests/test_telegram.py` — разрешения через Telegram
- `tests/test_terminals.py` — терминалы
- `tests/test_together.py` — сквозные сценарии нескольких модулей
- `tests/test_tools.py` — инструменты чата
- `tests/test_tuning.py` — подбор настроек замерами
- `tests/test_ui.py` — окно: контроллер и настоящий QML
- `tests/test_ui_bounds.py` — длинные имена в окне, контраст
- `tests/test_vertex.py` — Vertex AI
- `tests/test_visual.py` — картинки и видео
- `tests/test_watch.py` — слежение за проектами
- `tests/test_web.py` — сеть моделей
- `tests/test_websearch.py` — поиск в сети

### tests/stress/ — нагрузочный стенд (`pytest -m stress`)

- `tests/stress/__init__.py` — пакет стенда
- `tests/stress/conftest.py` — настройка стенда и пределы
- `tests/stress/stand.py` — общие части стенда
- `tests/stress/test_chat.py` — длинный чат: 1000 сообщений
- `tests/stress/test_editor_typing.py` — редактор на файле 1 МБ
- `tests/stress/test_meaning.py` — поиск по смыслу на коде AFO
- `tests/stress/test_project.py` — большой проект: 20 000 файлов
- `tests/stress/test_scroll.py` — прокрутка
- `tests/stress/test_start.py` — время запуска хаба и окна
- `tests/stress/test_terminal.py` — болтливый терминал: 200 000 строк

## Суть проекта

AFO — локальная ИИ-экосистема для Windows, публичный репозиторий, лицензия GNU GPL 3. Модели работают на компьютере
пользователя; облачные подключаются по его ключам.

- **Хаб** (`hub/`) — один фоновый процесс на `127.0.0.1:8765`, живёт и после закрытия окна. Владеет проектами,
  терминалами, процессами, моделями, агентами, разрешениями. API: HTTP и WebSocket под `/v1`. База — SQLite `hub.db`
  в папке данных, схема — нумерованные миграции. События идут через шину `hub/events.py`.
- **Окно** (`app/`) — PySide6 и QML, рисует процессор: видеокарта остаётся моделям. Логика — `app/ui/controller.py`,
  связь с хабом — `app/hub_client.py`. Запускает хаб, если его нет.
- **Модели** — шлюз `hub/gateway/`: локальные через llama-swap (конфиг `config.yaml`), Anthropic, OpenAI, Gemini,
  DeepSeek, OpenRouter, Vertex AI, Claude Code. На видеокарте одна модель за раз.
- **Что умеет**: чаты; проекты с редактором, git и терминалами; режим «Цель» — работа до результата под судьёй;
  автономный агент с режимами «Каждый шаг», «Сначала план», «Авто», «Полный» и управлением окнами разрешённых
  программ; координатор с пакетами задач; «Песочница»; очередь разрешений; бюджеты; MCP; картинки и видео; установка
  моделей; обновление самой AFO.
- **Снаружи подключаются** VS Code (`vscode/`), Claude Code и агенты по MCP.

Стек: Python 3.14, FastAPI, PySide6, SQLite, pytest, ruff (длина строки 120). Исходный язык интерфейса — русский:
русский текст в коде (`tr()`, `plural()`, `qsTr()`) — это ключ каталога `common/locale/en.json`.

Команды: окно — `python -m app`; хаб — `python -m hub`, остановка — `python -m hub stop`; новые тексты —
`PYTHONIOENCODING=utf-8 python -m common.i18n_catalog --sync`, затем английский в каталоге; установщик —
`python -m installer.build`.

## Правила работы

Других рабочих документов в проекте нет. Не ищи `ARCHITECTURE.md`, `journal.md`, `problems.md`, `API.md`.
`README.md`, `README.en.md` и `PRIVACY.md` — для людей и SignPath: для работы их не читай, но и не удаляй, тесты
проверяют в них политику подписи кода. Пометки в комментариях
кода вроде `D44`, `Q26`, `stage 11.1` и ссылки на эти файлы — следы прежних документов: смысл бери из кода рядом.

### Правило 1. В первом ответе новой сессии — никакого кода

Даже если просят «реализуй». Первый ответ — только разведка по реальным файлам: найди их по карте, прочитай и выпиши:

- кто владеет записью (файл, таблица `hub.db`);
- где блокировки и очереди;
- какие события уходят в шину `hub/events.py`;
- какие краевые случаи возникнут при параллельном запуске.

Пример: задача — синхронизировать правку `config.yaml`. Читаешь `hub/llama_config.py`, `hub/tuning.py`,
`hub/model_install.py` и выписываешь: кто пишет файл, что будет, если замер и установка запустятся одновременно. Код —
только после этого и по слову пользователя.

### Правило 2. Контекст — только под задачу

Нужны: текст задачи словами пользователя, сигнатуры и модели данных затронутых компонентов, код модулей, которые
участвуют во взаимодействии. Норма: **1 задача = 2–3 файла кода + 1 падающий тест**. Больше — раздели задачу.

### Правило 3. Сначала настоящий процесс, потом подмены

Тест сначала должен упасть на старом коде, и упасть по существу, а не из-за отсутствующего имени.

- Терминалы (`hub/terminals.py`): тест запускает настоящий ConPTY, хотя бы `cmd.exe /c echo test`.
- Конфиги (`hub/llama_config.py`): тест пишет файл на диск во временную папку, читает его параллельным потоком,
  проверяет YAML и структуру.
- Взаимодействие модулей: тест — в сквозном `tests/test_together.py`, а не в изолированном файле.
- Окно: настоящий QML без экрана (`QT_QPA_PLATFORM=offscreen`, `QuickApp`, `grabWindow`), как в
  `tests/test_ui_bounds.py`.

Подмены допустимы только там, где настоящее нельзя: облачные сервисы и `claude` — `tests/fake_*.py`. Настоящие модели и
llama-swap — только по слову пользователя. `tests/test_computer.py` управляет настоящими окнами, на компьютере
пользователя его не запускать.

### Правило 4. Чек-лист перед каждым коммитом

1. Разделяемые ресурсы — `config.yaml`, `hub.db`, очередь видеокарты (`hub/agents.py`, пробный запуск
   `hub/model_install.py`, замеры `hub/tuning.py`): кто ещё их трогает и что будет при параллельном доступе?
2. Нет блокирующих вызовов в `async`-коде хаба и в потоке окна (для окна — `app/tasks.py`)?
3. Файлы, потоки, процессы закрываются в `finally` или `with`?
4. Прошли `ruff check hub tests app`, `ruff format --check hub tests app` и тесты затронутых **и смежных** модулей? Весь
   набор — `pytest --ignore=tests/test_computer.py` — один раз в конце этапа.
5. Файл добавлен, удалён или сменил назначение — строка в карте этого файла обновлена в том же коммите?

### Запреты

- В GitHub — только по слову пользователя. Тег выпуска аннотированный: `git tag -a -m "AFO X.Y.Z"`; отправленный тег
  не заменяют.
- В коммитах и PR нет строки соавтора Claude. Личную почту пользователя не писать в репозиторий.
- Новый этап начинается только по слову пользователя.
- `data/` пользователя — только чтение, его чаты не читать: поведение модели воспроизводи на стенде.
- Перед настоящими моделями проверь `nvidia-smi`: карта может быть занята игрой.
- Хаб, запущенный вручную, — только с изоляцией как в `isolated_data_dir` из `tests/conftest.py`, иначе он запустит
  llama-swap пользователя.
- Пользователю отвечай по-русски.
