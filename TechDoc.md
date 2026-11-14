# AFO

Единственный рабочий документ проекта. **Новая сессия: сначала прочитай разделы «Суть проекта» и «Правила работы» в конце
файла.** Что делать дальше — раздел «План».

## Карта файлов

Все файлы под git. Папки `data/`, `build/`, `.venv/` и другие, которых нет в git, сюда не входят.

### Корень

- `.gitattributes` — как git хранит файлы: переводы строк, двоичные типы
- `.gitignore` — что не попадает в git
- `.github/workflows/release.yml` — сборка установщика на GitHub по тегу vX.Y.Z и черновик выпуска
- `AFO.cmd` — запуск окна двойным щелчком, без консоли
- `CLAUDE.md` — инструкции Claude Code: работать по TechDoc.md, протокол взаимодействия
- `LICENSE` — лицензия GNU GPL 3
- `PRIVACY.md` — политика конфиденциальности, её требует подпись SignPath
- `README.md` — страница проекта на GitHub, в ней политика подписи кода
- `README.en.md` — та же страница по-английски
- `TechDoc.md` — этот файл, единственный рабочий документ: карта, план, суть, правила
- `pyproject.toml` — пакет, зависимости, настройки ruff и pytest
- `requirements.lock` — точные проверенные версии зависимостей

### common/ — общий код хаба и окна

- `common/__init__.py` — пакет общего кода
- `common/crashes.py` — след от аварийного падения окна и хаба
- `common/disks.py` — диски компьютера и их тип (NVMe, SSD, HDD, USB) для установки моделей
- `common/gguf.py` — чтение заголовка файла GGUF для оценки памяти; словарь модели для пары с черновиком
- `common/hardlinks.py` — все имена одного файла (жёсткие ссылки)
- `common/hardware.py` — видеокарты компьютера для мастера первого запуска
- `common/i18n.py` — перевод текстов: tr(), plural(), выбор языка
- `common/i18n_catalog.py` — сверка ключей английского каталога с кодом, `--sync` добавляет новые
- `common/invisible.py` — символы, которых человек не видит
- `common/kept_json.py` — JSON в папке данных, чтобы не выяснять одно и то же при каждом запуске
- `common/llama_memory.py` — сколько видеопамяти и ОЗУ займёт модель llama.cpp (с черновой моделью)
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
- `hub/autostart.py` — запуск хаба при входе в Windows; запуск llama-swap и его порт
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
- `hub/fit.py` — беседа, переросшая окно контекста модели; сколько окна занято (`filled`, этап 40)
- `hub/goals.py` — цель чата: работа ход за ходом до результата
- `hub/guard.py` — страж цели
- `hub/hf.py` — Hugging Face: поиск и сведения о моделях
- `hub/history.py` — версии файлов проекта
- `hub/judge.py` — судья цели
- `hub/language.py` — язык того, что хаб пишет людям
- `hub/limits.py` — сколько контекста получает модель
- `hub/links.py` — связи между проектами
- `hub/llama_config.py` — `config.yaml` llama-swap: чтение и запись настроек запуска; флаги угадывания вперёд по
  `--help` сборки (`server_flags`, `match_build`)
- `hub/locate.py` — найти работающий хаб или запустить новый
- `hub/mail.py` — почта автономного агента (IMAP/SMTP)
- `hub/mcp_client.py` — хаб как клиент внешних MCP-серверов
- `hub/mcp_server.py` — MCP-сервер хаба
- `hub/mcp_stdio.py` — stdio-переходник к MCP-серверу хаба
- `hub/meaning.py` — поиск по смыслу без векторов
- `hub/memory.py` — память автономного агента
- `hub/model_catalog.json` — подборка локальных моделей для установки; черновые модели для угадывания вперёд
- `hub/model_install.py` — установка моделей, пробный запуск по одной на видеокарте; черновые модели («Ускорить»)
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
- `hub/resources.py` — что нужно локальным моделям: файлы движка на диске, отвечает ли llama-swap; сканер, который
  качает свой движок AFO, когда его нет
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
- `hub/tuning.py` — подбор настроек запуска замерами; угадывание вперёд — замер самим llama-server
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
- `app/ui/qml/Composer.qml` — поле ввода чата; под ним — сколько контекста модели занято (этап 40)
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

## План

Записан по слову пользователя 2026-11-12. Этап начинается только по его слову, первый ответ по этапу — план без кода
(Правило 1). Сделанный шаг отмечается здесь в том же коммите. Номера строк — на 2026-11-12: перед работой сверить.

### Цель

Человек без усилий пользуется локальными моделями на самой большой для своего компьютера скорости. AFO сам ставит
нужные программы, сам подбирает модель и настройки; человеку остаётся один щелчок там, где нужен выбор.

### Решения пользователя (2026-11-12)

- Программы (llama.cpp, llama-swap, Strata) AFO скачивает сам при входе: сканер видит, чего нет, и докачивает.
- Первую модель AFO предлагает (мастер открывается сам, один щелчок), но сам не качает. Модели Strata (от 58 ГБ) —
  тоже по щелчку.
- Порт llama-swap (8080) занят чужой программой — llama-swap сам переходит на свободный.
- Черновую модель для ускорения AFO качает сам; кнопка «Ускорить» делает то же сразу.
- Strata (`github.com/Niko1221/Strata`, лицензия MIT): AFO ставит её сам там, где её модели помещаются. В README —
  что она делает, как ставится, благодарность автору.
- «Поместится ли модель» считается от сохранённых характеристик компьютера (`computer.json`), а не от занятого сейчас:
  видеопамять − 1 ГБ, ОЗУ − 3 ГБ (пользователь назвал 2–3; 3 — Windows 11 держит около 3). Запасы для опытных
  меняются в «Настройки → Модели».
- В окне видно, сколько контекста заполнено; щелчок ведёт к настройке контекста модели.
- Поиск на Hugging Face: больше фильтров, «подходит ли этому компьютеру» прямо в списке, вместо «bits per weight» —
  слова о качестве.
- Лицензия (2026-11-13): строго или шире, решает человек в самом фильтре — «любая», «свободная» (Apache, MIT, BSD,
  CC-BY: для работы без условий), «можно для работы» (плюс свои лицензии Llama, Gemma, Qwen — со своими условиями); и
  в открытом репозитории — что разрешает его лицензия, простыми словами.
- Черновик не помещается при нынешнем контексте (2026-11-13): контекст сам не уменьшается. Замер пробует ещё
  «черновик целиком в видеопамяти + на N слоёв модели меньше на видеокарте» — записывается, только если он быстрее
  лучшего без черновика (ngram или без угадывания) больше чем на 3 %. Иначе — ngram, а в окне: сколько черновику не
  хватает и при каком контексте он поместится; решает человек.
- Трей и видеокарта (2026-11-13, этап 43 — перед Strata): крестик окна не закрывает AFO, а прячет в трей; «Выход» в
  меню трея останавливает всё — окно, хаб, модели. AFO запускается вместе с Windows сразу в трей. Все неигровые
  программы, что рисуют на видеокарте, AFO сам переводит на встроенную графику (настройка Windows «Графика»), чтобы
  видеопамять осталась моделям; они остаются там и после выхода из AFO — вернуть можно только в настройках AFO
  («Вернуть как было»). Программы AFO сам не перезапускает — говорит, что перевод вступит после перезапуска.
- Ускорение для всех моделей (2026-11-14, этап 44 — перед Strata): не новые движки, а то, что уже есть в llama.cpp, —
  эксперты MoE в ОЗУ и их кэш на видеокарте, угадывание вперёд MTP, DFlash, EAGLE-3. Что из этого быстрее, AFO
  решает замером на этом компьютере, как в этапе 42; пишется только то, что быстрее.
- Движки — сменные части (2026-11-14, шаг 45.1): своего движка не пишем; своё у AFO — решения (что где держать, какое
  угадывание, какой контекст) и замер, а движки (llama.cpp, Strata, потом другие) стоят за одним общим входом.
- KTransformers (2026-11-14, этап 46): на Windows он работает только в WSL — AFO ставит его сам, но только спросив
  человека и сказав, что будет (права администратора, WSL, память, место на диске). Сначала замер: стоит ли он того.

### Как это выглядит (примеры; скорости условные, настоящие даёт замер)

1. Ноутбук: RTX 3060 6 ГБ, 16 ГБ ОЗУ, в IntelliJ запущен Tomcat на 8080. При входе строка «Скачиваю программу для
   локальных моделей… 340 из 820 МБ», без вопросов; llama-swap встаёт на свободный порт. Мастер предлагает модель,
   которая целиком в видеопамяти. После установки — подбор, ngram, черновая модель: «Ускорено: 38 → 61 ток/с». Под
   полем ввода — «Контекст: 3,2 тыс. из 32 тыс.».
2. 24 ГБ видеопамяти и 96 ГБ ОЗУ: сканер ставит Strata (~72 МБ). Мастер предлагает Qwen3.8-Flash-Next через Strata:
   «самая сильная для этого компьютера, ~100 ток/с, 58 ГБ» (по щелчку). Модель запускает llama-swap, как сейчас
   «coder» у автора AFO.
3. Компьютер со своим llama-swap и Strata (как у автора AFO): сканер ничего не качает и не трогает.
4. Без видеокарты: сборка llama.cpp для процессора, маленькая модель, из ускорения — ngram.

### Что уже есть и остаётся

- Сборка llama.cpp под видеокарту (`hardware.build_for` в `hub/setup.py`).
- Самая сильная модель, которая целиком влезает в видеопамять (`suggestions` в `hub/setup.py`).
- Самый большой контекст, который помещается (`CONTEXTS` и `launch_plan` в `hub/model_install.py`).
- Подбор flash attention, KV-кэша и ubatch замером (`hub/tuning.py`).

### Что найдено (2026-11-12)

1. Мастер принимает любую программу на порту llama-swap за llama-swap: `Setup.state` (`hub/setup.py` 103–104) верит
   `autostart.answers` (любой код ниже 500), а «Проверить систему» сначала смотрит `port_taken`. Мастер сам не
   открывается, кнопки «Установить движок» в нём нет. → 39.1
2. После «Позже» мастер сам больше не открывается (`app/ui/setup_view.py` 182, 263–265); лист установки пишет «нет
   конфига llama-swap», но к мастеру не ведёт. → 39.3
3. `room()` (`hub/model_install.py` 145–161) считает от занятого сейчас. При загруженной модели видеопамять —
   «всего − 1,6 ГБ», ОЗУ — «всего − занято сейчас − 4 ГБ», и ОЗУ, которую держит та же модель, не считается
   освобождаемой: модели в 3 ГБ на карте 6 ГБ «не поместятся» (скриншот с ноутбука, 14B из Ollama загружена). → 38.1
4. Буферы llama.cpp — две цифры: 0,8 ГБ (`common/llama_memory.py` 16, её берёт установщик) и 1,5 ГБ (`hub/tuning.py`
   62). → 38.1, одна цифра по замеру
5. Подбор мерит `llama-bench`, у которого нет черновой модели. В llama.cpp b11899 угадывание задают `--spec-type`
   (draft-simple, draft-mtp, ngram-*), `--spec-draft-model` (`-md`), `--spec-draft-ngl`, `--spec-draft-n-max`; старый
   `--draft-max` удалён и роняет запуск. В ответе сервера есть `timings.predicted_per_second` и `draft_n_accepted`. → 42
6. Окно не показывает заполнение контекста: `chat.done` несёт сумму за ход (`hub/chat.py` 1583–1597), а не размер
   последнего запроса. Настройка контекста — глубоко в «Настройки → Модели» (`ModelsSheet.qml` 456). → 40
7. Фильтры поиска: задача (разговоры, код, понимает картинки), размер, порядок, «без снятых отказов» (`hub/hf.py`
   45–46, `app/ui/install_view.py` 52–55). Подходит ли модель, видно только в открытом репозитории. → 41
8. Strata запускает только Qwen3.8-Flash-Next (архитектура `qwen4exp`, от 58 ГБ), другие модели не берёт. Скорость у
   неё — от угадывания вперёд (`--spec`, MTP, DFlash2), кэша экспертов MoE и KV-кэша int8. → 45
9. (2026-11-13) Рабочий стол и программы держат видеопамять, нужную моделям: у автора оба монитора на RTX 5080, без
   моделей занято 1,0–1,8 ГБ. Настройка Windows «Графика» — `HKCU\Software\Microsoft\DirectX\UserGpuPreferences`,
   значение — путь программы, данные — `GpuPreference=1;` (встроенная) или `=2;` (видеокарта) рядом с полями Windows
   (`AppStatus`, `SwapEffectUpgradeEnable`). Замер: Telegram с `GpuPreference=1` после перезапуска ушёл с RTX
   (`nvidia-smi` −86 МБ), значение убрано — вернулся. Видеопамять каждой программы на каждом адаптере — счётчики
   Windows `GPU Process Memory` (как в диспетчере задач); их сумма больше занятого (вытесненное, общие поверхности) —
   верна разница. Крестик окна закрывает AFO (`Main.qml` 22), трей — только уведомления (`app/ui/notify.py`); второй
   запуск открывает второе окно; при входе в Windows стартует только хаб (`hub/autostart.py`, `installer/afo.iss` 89).
   → 43
10. (2026-11-14) Приёмы Strata есть в самом llama.cpp, для любых моделей. В b11899 (`--help`): `--cpu-moe` и
    `--n-cpu-moe N` (эксперты MoE в ОЗУ, остальное на видеокарте), `--moe-cache-mib N` (кэш часто нужных экспертов на
    видеокарте, PR #29887 от 07.10.2026; по замерам автора 1,4–2,2×; `--fit` учитывает его в бюджете), виды
    `--spec-type`: `draft-eagle3`, `draft-mtp`, `draft-dflash`, `draft-dspark`. AFO знает только `n_cpu_moe` (ставит
    человек) и мерит только ngram и `draft-simple`; заголовок GGUF не читает ни число экспертов, ни слой MTP. Черновики
    DFlash в GGUF выложены сообществом (Qwen3.5/3.6/3.8-27B, Qwen3.6-35B-A3B, Gemma 4 31B; часть — под ответвления
    llama.cpp), MTP-версии Qwen3.6 — у ggml-org, EAGLE-3 в GGUF — единицы. Выигрыш не гарантирован: на RTX 3090 с
    Qwen3.6-35B-A3B Q4 ни один вид угадывания не обогнал запуск без него. Другие движки в этап 44 не берём:
    KTransformers на Windows — только через WSL (этап 46, по согласию человека); у ik_llama.cpp нет своих сборок под
    Windows, его форматы не читает llama.cpp; OpenVINO — уже часть llama.cpp, NPU в замерах не быстрее процессора. → 44

### Этап 43. Трей и видеокарта — моделям

- 43.1 ✓ Одно окно: второй запуск (ярлык, вход в Windows) показывает уже открытое (локальный сокет по папке данных);
  `--tray` — запуск без окна, только трей. Файлы: `app/__main__.py`, `app/ui/main.py`. Сделано: `app/single.py` —
  первый держит `data/app.lock` (`InstanceLock` хаба: двое разом — один из них) и слушает `QLocalServer` с именем по
  папке данных (только свой пользователь); второй говорит «show» (или «tray» — со входа в Windows: ничего) и выходит.
  `QuickApp(hidden=)` — окно невидимо (`StartHidden` в `Main.qml`), `show_window` показывает его как оставили
  (развёрнутое — развёрнутым); без трея `--tray` показывает окно, иначе AFO не найти. Смена языка (`restart`) сперва
  отпускает замок и сокет — новый процесс становится первым. Тесты — `test_a_start_in_the_tray_shows_no_window_until_asked`
  (`tests/test_tray.py`), `test_a_second_start_shows_the_first_window_and_leaves` (настоящие процессы).
- 43.2 ✓ Крестик прячет окно в трей (переключатель, по умолчанию так; без трея — закрывает, как сейчас). «Выход» в меню
  трея останавливает всё: окно, хаб (`POST /v1/shutdown`), модели и llama-swap, если его запустил AFO. Файлы:
  `Main.qml`, `app/ui/notify.py`, `app/ui/main.py`, `hub/api/system.py`, `hub/autostart.py`. Сделано: `closeToTray`
  (`ui/close_to_tray` в `app.ini`), `hidesOnClose` — трей есть, переключатель включён, «Выход» не идёт; `onClosing`
  прячет окно; при трее `setQuitOnLastWindowClosed(False)`, а `_shown` решает: спрятано — AFO живёт, закрыто — выход
  (после «Выхода» сперва `shutdown(everything=True)`). `quit_all`: окно видно — закрывается (несохранённые правки
  спрашивает редактор; «Отмена» — `closeCancelled`, выход отменён), спрятано — сразу. `POST /v1/shutdown`
  `{"everything": true}` — `autostart.stop_llama_swap`: модели выгружены; llama-swap, который запускает AFO
  (`llama_swap_wanted`), остановлен деревом (`llama_swap_process` — по имени и `-listen`; с ним llama-server и Strata);
  свой llama-swap человека — только выгружен. Тесты — `test_the_cross_hides_the_window_in_the_tray_and_quitting_stops_everything`,
  `test_quitting_from_the_tray_stops_the_models_and_llama_swap` (настоящий процесс `llama-swap.exe`: лаунчер venv под
  этим именем, его «модель» — дочерний процесс).
- 43.3 ✓ Меню трея: «Открыть AFO», «Новый чат», «Проекты» (последние), загруженная модель и «Выгрузить», «Видеокарта —
  моделям» (галочка и сколько освобождено), «Тихие часы», «Остановить агента» (уже есть), «Настройки», «Выход».
  Сделано: `app/ui/tray.py` — `TrayMenu`: пункты — `QAction` (QtGui, без виджетов), `QMenu` из них — только при
  QtWidgets (тесты окна идут на `QGuiApplication`: `QMenu` там роняет процесс, 0xC0000409); заполняется при каждом
  открытии (`aboutToShow` → `refresh`): `recent_projects` — проекты, чьи чаты были последними, затем остальные (8),
  «Выгрузить модель: coder» (нет загруженной — «Модель не загружена», недоступно), «Тихие часы (23:00–08:00)» с
  галочкой (переключает `setQuiet`), «Остановить агента» — пока агент за столом (`Notifier.stop_shown`). Новый чат,
  проект и «Настройки» показывают окно. Меню ушло из `Notifier` (`use_menu`). «Видеокарта — моделям» — в 43.6. Тест —
  `test_the_trays_menu_does_what_the_window_would`.
- 43.4 ✓ Вместе с Windows: значение «AFO» в Run — `pythonw -m app --tray` (окно само поднимает хаб); старое «AFO hub»
  становится им при старте; задача установщика `atlogin` — так же. Сделано: `RUN_VALUE` «AFO», `OLD_RUN_VALUES`
  («AFO hub» — хаб один, «LocalChat hub»); `renamed` при старте хаба ставит новую команду (не копирует старую) и
  убирает старые; уже стоящее «AFO» остаётся. Установщик: `atlogin` включена по умолчанию (слово пользователя), пишет
  «AFO» и убирает «AFO hub»; удаление убирает оба, если они этой копии. Тексты — «Запускать AFO при входе в Windows —
  сразу в трей». Тесты — `test_the_login_value`, `test_the_login_value_under_an_old_name_moves`, `test_the_api`,
  `test_the_wizard_agrees_with_the_program`.
- 43.5 ✓ `hub/gpu_prefs.py`: адаптеры (DXGI: имя, производитель, LUID, своя память — есть ли встроенная и дискретная),
  видеопамять программ по счётчикам Windows (PDH), значения `UserGpuPreferences`: меняется только `GpuPreference`,
  поля Windows остаются; что было до AFO — в `settings`; «Вернуть как было» — точно и только свои значения (то, что
  человек поменял после AFO, не трогается). Тесты — настоящий реестр в своём тестовом разделе. Сделано:
  `with_preference`/`preference` — поля «Ключ=Значение;»; `Registry` (ключ из `LOCALCHAT_GPU_PREFS_KEY` в тестах);
  `Prefs.move` — уже есть выбор (человека, Windows, AFO) — не трогает; `gpu.moved` — путь → {before, wrote};
  `give_back` — {given_back, left}. `adapters()`, `choice()` — какой адаптер Windows даёт на «Энергосбережение» и на
  «Высокую производительность» (`EnumAdapterByGpuPreference`, DXGI 1.6 через ctypes; `two` — их два);
  `process_memory()` — (pid, luid) → МиБ. У автора: Intel Graphics и RTX 5080 (`two`), в счётчиках NVIDIA Overlay
  6,7 ГБ, dwm 3,8 ГБ, проводник 0,5 ГБ. Тесты — `tests/test_gpu_prefs.py`.
  Находка (2026-11-14, весь pytest шага 44.2): страж реестра человека в `tests/conftest.py` возвращал «Графику» как
  была до прогона — и забрал у настоящего AFO автора (он запускается с Windows) Wallpaper Engine, переведённую им во
  время прогона; запись в `gpu.moved` у AFO осталась, и сам он её больше не писал (значение возвращено руками).
  Исправлено: изменение, которое AFO человека записал в свою базу (`gpu.moved`, только чтение) — перевёл или вернул,
  — его (`persons_afo_did`); остальное страж по-прежнему возвращает, и прогон падает. Тест —
  `test_the_registry_guard_leaves_the_persons_own_afo_alone`.
- 43.6 ✓ Кого переводить: программы на дискретной видеокарте, кроме игр (список игр Windows `GameConfigStore`, папки
  Steam, Epic, GOG, долгая нагрузка 3D), программ, которым она нужна (OBS, Blender, DaVinci, Unity, Unreal, CUDA,
  llama-server, llama-swap, Strata), частей Windows, программ с уже выбранной графикой и списка человека «оставить на
  видеокарте». Хаб смотрит каждые 30 с, пока переключатель включён; переведённые остаются и после «Выхода». Событие
  `gpu.prefs`: кто переведён, кто ждёт перезапуска, сколько видеопамяти они держали. Сделано: `why_kept` — выбор
  человека первым («card»/«integrated», `gpu.choices`), «windows» (папка Windows, `WindowsApps` — у пакетов
  настройка по пакету), «tool» (`NEEDS_CARD`, `python*`, `godot*`, папка NVIDIA, свои движки AFO), «game» (список
  Windows, папки магазинов, «game» в пути — и «GameDevelop» автора, сборка Unreal, `UnityPlayer.dll` рядом,
  `AppStatus` Windows, 3D ≥ 15 % — запоминается в `gpu.games`), «chosen»; Wallpaper Engine — не игра. `look_now` —
  программы на адаптерах карты (счётчики Windows, нагрузка 3D за секунду). `Watcher` (`gpu.on`, по умолчанию
  включён — слово пользователя; `gpu_interval_s` 30 только у `python -m hub`, в тестах 0): `check`, `set_on`
  (выключен — не переводит и не возвращает), `choose` («card» — вернуть сразу), `give_back` (и выключить);
  программы — «waits» (переведена, ещё на карте), «moved», «kept» с причиной, «card»; `freed_mib`. API —
  `/v1/gpu` (GET, PUT), `/v1/gpu/programs`, `/v1/gpu/give-back`. Тесты не трогают реестр человека: `conftest` даёт
  каждому свой ключ. Сухой прогон у автора: переводятся Telegram, VS Code, Docker Desktop, Claude, PowerToys,
  WebView2, E-IMZO (~0,5 ГБ по счётчикам); остаются части Windows и NVIDIA Overlay. Тесты — `tests/test_gpu_prefs.py`.
- 43.7 ✓ «Настройки → Трей и видеокарта»: крестик, запуск с Windows, пункты меню трея; «Программы — на встроенную
  графику» (только где она есть), список программ с «оставить на видеокарте», «Вернуть как было». Сделано:
  `SettingsTraySheet.qml` (категория в `SettingsSheet.qml`, `App.openUi("settings-tray")`); `app/ui/gpu_view.py` —
  `GpuView` (`Gpu` в QML): `summary` («Встроенная: … · видеокарта: … · переведено программ: 2, освобождено ~126 МБ ·
  ждут перезапуска: 1»), `programs` (где и почему; кнопка — «Перевести на встроенную» / «Оставить на видеокарте»; у
  частей Windows кнопки нет), `setOn`, `choose`, `giveBack` (сперва вопрос); `gpu.moved` — строка в окне и
  уведомление в трей «Перезапустите их, чтобы освободить видеопамять для моделей: …». Меню трея: «Видеокарта —
  моделям (освобождено ~126 МБ)» с галочкой; какие из «Проекты», «Загруженная модель», «Тихие часы», «Видеокарта —
  моделям» показывать — `trayItems` (`tray/<имя>` в `app.ini`). Окно подписано на `gpu.*`. Спрятанное окно — не
  «впереди» для уведомлений (`isVisible` и `isActive`). Тест — `test_the_graphics_card_for_models_in_the_window_and_the_tray`;
  снимок без экрана сверен.
- 43.8 ✓ Живая проверка на компьютере автора — по его слову, после `nvidia-smi`. Перед ней — слово пользователя
  (2026-11-13): VS Code и Claude AFO не трогает никогда — `NEVER` в `hub/gpu_prefs.py`, причина «never» («AFO её
  не трогает», без кнопки); и ручное «Перевести на встроенную» их не переводит. Итог (2026-11-13, AFO автора из
  этой папки, `--tray`): первый же взгляд хаба перевёл 6 программ (Telegram, Docker Desktop, E-IMZO, две PowerToys,
  WebView2); VS Code и Claude — «never», части Windows и NVIDIA Overlay — на карте; поля Windows в реестре не
  тронуты. Telegram перезапущен — рисует на Intel, на карте −167 МБ; AFO показал его «moved». «Вернуть как было» —
  реестр ровно как до проверки, переключатель выключен; включён снова — переведены те, кто на карте. Второй запуск
  `--tray` — «a second start asks: tray», выход 0. Найдено и сделано: `data/hub.json` хаба, умершего 29.10, держал
  порт 8765 — теперь `hub_address` не верит `hub.json`, чей процесс умер. Не сделано: порт 8765 у автора занят
  Docker-контейнером `ai-sandbox-gateway` — хаб проверки шёл на 8766 (`LOCALCHAT_HUB_PORT`); как быть насовсем —
  решает пользователь (решил — 43.9).
- 43.9 ✓ Хаб уходит с занятого порта (слово пользователя 2026-11-13: «хаб сам уходит на свободный порт»). `serve`
  выбирает порт до `hub.json`: `autostart.hub_port` — сначала тот, куда хаб ушёл раньше (`hub.port` в `settings`:
  `{wanted, port}`, читается `kept_hub_port` без записи до открытия базы), потом свой, потом 20 следующих
  (`free_port`: и слушает ли кто, и можно ли занять). Ушедший остаётся там и когда свой освободился: адрес, вписанный
  людьми в другие программы, держится, а контейнер, что поднимается после входа в Windows позже AFO, находит свой
  порт свободным. Занят и запомненный — назад на свой, запись удаляется. Выбранный порт — `HubSettings.port`,
  просимый — `wanted_port`; `create_app` хранит их после миграций, адрес для Claude Code из AFO и для блока «В проект»
  (`export.block(..., hub_url=)`, раньше там стояло `8765`) — `app.state.hub_url`. Окно (`running_hub`) больше не
  останавливается на «порт занят другой программой»: запускает хаб и ждёт его `hub.json`. Окно говорит о переезде
  раз на каждый новый порт (вспышка и, в трее, уведомление; `hub/told_port` в `app.ini`), «Настройки → Хаб» —
  пока он длится: «Порт 8765 занят другой программой — хаб AFO теперь на 127.0.0.1:8766. Где адрес хаба вписан
  руками (Cline, расширение VS Code), впишите новый». Расширение VS Code не менялось — у него своя настройка
  `localchat.hubUrl`. Тесты — `test_the_hub_moves_off_a_port_another_program_holds` (`test_app_client.py`:
  настоящий хаб и чужой HTTP-сервер на его порту; `test_autostart.py`: порядок портов на настоящих сокетах и запись в
  базе), `test_the_window_says_the_hub_moved_off_its_port`, `test_export.py::test_the_api`. Живая проверка
  (2026-11-14, AFO автора, 8765 у Docker-контейнера): AFO остановлен как «Выходом» (окно, хаб, llama-swap) и запущен
  без `LOCALCHAT_HUB_PORT` — хаб сам встал на 8766 («port 8765 is taken by another program: the hub listens on 8766»),
  в базе `{"wanted": 8765, "port": 8766}`, окно подключилось и сказало о переезде (`told_port=:8766`), второй запуск
  `--tray` услышан (43.10). Попутно: AFO, запущенный из сессии Claude Code, наследовал её окружение (`CLAUDECODE` и
  др. — Claude Code внутри AFO счёл бы себя вложенным); теперь он запущен ярлыком через Проводник — окружение
  пользователя.
- 43.10 ✓ Слово второго запуска не теряется. Найдено полным pytest этапа (2026-11-13): второй запуск «попросил
  показать», первое окно не услышало. Опыт: второй писал слово и сразу уходил — если поток первого окна в это время
  занят дольше ~2 с (строится QML, подвисание), слово уходило вместе с каналом, и ярлык ничего не делал (занят 2 с —
  дошло, 4 с — нет). Теперь первое отвечает `ok` (`HEARD`), прочитав слово, а `ask_running` ждёт его до `HEAR_S` (30 с)
  и только потом отключается; не услышал — `False` и предупреждение в журнале. Следующий полный прогон упал
  (access violation в следующем тесте окна): Single, закрытый и отпущенный сразу после слова (так делает
  перезапуск окна), уносил с собой сокет второго запуска с `ok` в пути, и Qt трогал его уже удалённым. Теперь
  `close()` сначала обрывает принятые сокеты (`abort`). Тест —
  `test_a_second_starts_word_is_not_lost_while_the_first_is_busy`: «ухо» первого окна — свой Python
  (`QGuiApplication`), каждый второй запуск — свой; первое слово приходит, пока поток первого занят 6 с, второе — в
  живое окно, после них оно живёт дальше; третье — снова в занятое, второй запуск уходит незамеченным, и сразу
  `close()`, как при перезапуске. Без `ok` теряется слово, без `abort` процесс падает. Первый вариант теста держал
  Single в процессе pytest — его обёртки PySide роняли потом чужие тесты окна, поэтому только процессы. Там же
  найдено: тест 43.7 ждал уведомление в трее, а с 23:00 до 08:00 его по умолчанию придерживают «тихие часы» — тест
  теперь от часов не зависит (`notifier.quiet`, как в `test_ui.py`).
- Риски: игра, переведённая по ошибке, теряет кадры — исключения 43.6 и список человека; реестр человека — пишутся
  только свои значения и возвращаются точно; кадр программы на встроенной копируется на экран дискретной (оба монитора
  на ней) — замер показал, что это работает; второй экземпляр AFO не должен открыть второе окно и второй трей.
  Случилось (2026-11-13, весь pytest этапа): тест MCP stdio дал адаптеру своё окружение из четырёх переменных, тот
  поднял настоящий `python -m hub` без изоляции, и его наблюдатель перевёл восемь программ автора в настоящем
  реестре (убраны вручную, всё как было). Теперь окружение теста несёт все `LOCALCHAT_*`, а `conftest`
  (`persons_registry_untouched`) после прогона сверяет «Графику» и значения AFO в Run: изменились — возвращает и
  роняет прогон. Второй весь прогон — 1188, реестр не тронут.

### Этап 38. Оценка памяти по характеристикам компьютера

- 38.1 ✓ `room()` от `computer.json`: видеопамять − 1 ГБ, ОЗУ − 3 ГБ; одна цифра буферов для установщика и подбора.
  Файлы: `hub/model_install.py`, `common/llama_memory.py`, `hub/tuning.py`. Тест сначала падает: при загруженной модели
  3-гигабайтная получает «не поместится». Сделано: запасы и буферы — в `common/llama_memory.py` (`RESERVE_GIB` 1,0,
  `RAM_RESERVE_GIB` 3,0, `COMPUTE_GIB` 0,8 до замера); тест —
  `test_a_model_is_judged_by_this_computer_not_by_what_is_busy_now`.
- 38.2 ✓ Сейчас занято больше, чем заложено, — не запрет, а слова при загрузке: «сейчас свободно N ГБ ОЗУ — закройте
  лишние программы, иначе будет медленно». Сделано: слова — `short_now` в `common/llama_memory.py`; в чате их
  считает `Installer.memory_now` по последнему замеру, когда llama-swap начал загрузку (свои загруженные модели
  llama-swap считаются свободными: они выгрузятся; конфиг читает под `llama_config.writing()`, а пока идёт запись —
  молчит), и несёт `chat.loading` (`memory`), окно пишет их под «Загрузка модели»; в окне установки — `busy` из
  `room()` (только видеопамять и только когда llama-swap пуст), строка ОЗУ — «ОЗУ для модели ≈». Тест —
  `test_a_model_loading_while_other_programs_hold_the_memory_says_so`.
- 38.3 ✓ Запасы для опытных — в «Настройки → Модели». Сделано: `hub.db` `settings`, ключ `install.reserve`
  (`reserve_saved`/`reserve_save` в `hub/model_install.py`, границы 0–8 и 0–32 ГБ); установщик читает их при старте,
  `room()` и всё, что от неё (мастер, поиск, план запуска, подбор), считает по ним; `GET /v1/install/system` —
  `reserve`, `PUT /v1/install/reserve`; оценка окна «Настройка модели» берёт `vram_reserve_gib` из
  `/v1/llama/models`. Окно — `Installer.reserve`/`setReserve`/`resetReserve` (`app/ui/install_view.py`), поля в
  `SettingsModelsSheet.qml`. Тесты — `test_the_memory_reserves_are_the_users_to_change`,
  `test_the_memory_reserves_in_the_models_settings`.
- Заодно (2026-11-12): запись `config.yaml` ждёт читателя файла (`_replace` в `hub/llama_config.py`): Windows не даёт
  заменить файл, открытый на чтение, а llama-swap перечитывает его сразу после записи хаба.
- ~~Цифру буферов проверить замером на настоящей карте~~ — не нужно (слово пользователя 2026-11-12): `COMPUTE_GIB`
  остаётся 0,8. Запас видеопамяти по умолчанию — 1 ГБ, свой — в «Настройки → Модели».

### Этап 39. Ресурсы сами (сканер)

- 39.1 `hub/resources.py` (новый): при старте хаба и при входе окна проверяет файлы — llama-swap.exe, config.yaml,
  llama-server и llama-bench по конфигу, черновики из конфига, — а не «кто отвечает на порту». Недостающее качает
  через `Setup` (одно скачивание за раз: `Setup.start` отдаёт уже идущее). Свой llama-swap человека не трогает.
  Создание config.yaml — под `llama_config.writing()`. Выключатель как `fetch_ripgrep`, в тестах выключен. Файлы:
  `hub/resources.py`, `hub/setup.py`, `hub/server.py`.
  - 39.1а ✓ Готовность — по файлам: `resources.scan()` (программа llama-swap, конфиг, llama-server; llama-bench
    только называется), `resources.llama_swap_answers()` — отвечает его `/running`, а не любая программа на порту.
    `Setup.state` — `needed`, если нет файлов и не отвечает настоящий llama-swap; `resources` в ответе. Первый
    config.yaml — под замком. Тест — `test_the_engine_is_judged_by_its_files_not_by_who_answers_on_its_port`.
  - 39.1б ✓ Скачивание само: `resources.Resources.check()` при старте хаба (`_initial_sync`) и по
    `POST /v1/setup/check` (окно, 39.3). Качает только свой движок AFO (`afo_own()`: программа и конфиг в
    папке движков или ничего не выбрано), сборку по видеокарте (`Setup.recommended_build`), через
    `Setup.start(auto=True)`; свой llama-swap человека — только слова. Выключатель
    `HubSettings.fetch_engines`. Тест — `test_a_computer_with_nothing_gets_the_engine_by_itself`.
  - 39.1в ✓ Риски. Сбой (лимит GitHub, нет сети) — повтор сам через `RETRY_S` (30 мин; `Setup.on_finished`,
    `Setup.finished_at`), окно до этого не гоняет GitHub. Программа, которая была (`resources.seen` в
    `settings`), пропала — «удалил антивирус», сама не качается. Место на диске (архивы × 2) — до скачивания,
    `_room` в `hub/setup.py`. Тест — `test_the_engines_fetch_meets_github_s_limit_an_antivirus_and_a_full_disk`.
- 39.2 Свободный порт: 8080 занят чужой программой — другой порт, запоминается в базе. Выбирается при старте хаба до
  сборки служб: адрес llama-swap запоминают 26 мест в 13 файлах. Переносится только llama-swap.exe, который запускает
  AFO; запущенный своим скриптом — нет. Файлы: `hub/server.py`, `hub/autostart.py`.
  ✓ Сделано: `autostart.llama_swap_url` в начале `lifespan` (до сборки служб): свой llama-swap на запомненном порту
  (`llama_swap.port` в `settings`) — там и остаётся; порт по умолчанию свободен или на нём llama-swap — он; занят
  чужой программой, а запуск — .exe — первый свободный из следующих 20 (`free_port`: никто не слушает и сокет
  садится). Выключатель `HubSettings.move_llama_swap` — только когда адрес не задан `LOCALCHAT_LLAMA_SWAP_URL`.
  Тест — `test_llama_swap_moves_off_a_port_another_program_holds` (настоящие сокеты).
- 39.3 Окно: ход скачивания простыми словами в строке состояния, без мастера; мастер открывается сам, когда движок
  готов, — на «Первая модель». Установка модели, пока движок качается, ждёт его, а не падает с 409. Файлы:
  `app/ui/setup_view.py`, строка состояния.
  - 39.3а ✓ Окно: при входе `POST /v1/setup/check`, потом состояние; пока сканер качает (`job.auto`) — мастер закрыт,
    в строке состояния `statusEngine` («Скачиваю программу для локальных моделей… 340 МБ из 820 МБ», `Setup.status`);
    движок готов — мастер открывается сам на «Первая модель» (`setup-first-model`, `WizardSheet.revealModels`) один
    раз (`setup/modelOffered`), и после «Позже» тоже. Тест —
    `test_the_engine_fetched_by_itself_shows_in_the_status_bar_then_offers_a_model`.
  - 39.3б ✓ Хаб: `Installer.engine_coming` (движок качается — `Setup.job`): установка без конфига не 409, модель
    качается, затем ждёт «в очереди» с пояснением (`_engine_wait`), а `_configure` заново выбирает свободный id. Тест —
    `test_a_model_installed_while_the_engine_comes_waits_for_it`.
- Риски: лимит GitHub — 60 запросов в час (ходить, только когда чего-то нет, ответы запоминать); нет сети или 403 —
  повтор позже и простые слова; антивирус удаляет exe — не качать по кругу, сказать прямо; место на диске — проверить
  до скачивания.

### Этап 40. Контекст на виду

- 40.1 ✓ `chat.done` несёт «занято N из M» по последнему запросу и окну контекста модели. Файл `hub/chat.py`. Миграция
  не нужна: `usage` хранит токены каждого ответа. Сделано: `fill` — `{used, window, shortened, estimated}`
  (`fit.filled`): вход последнего запроса прогона и его ответ (следующий запрос несёт оба; размышления модели могут в
  него не входить — цифра чуть больше), окно отвечавшей модели (`capabilities.context`), `shortened` — `fit` уже
  сжимал этот запрос (в `usage.meta`). Открытый заново чат — тот же `fill` в `GET /v1/conversations/{id}`
  (`store.context_fill`: последний расход с сообщением — судья цели и отказ его не меняют); `null` до первого ответа и
  у остановленного без счёта. Claude Code ведёт свой контекст сам — у его `chat.done` поля нет. Тест —
  `test_the_window_hears_how_full_the_models_context_is` (и `shortened` в
  `test_a_chat_past_its_window_still_asks_the_model`).
- 40.2 ✓ Под полем ввода «Контекст: 12,4 тыс. из 32 тыс.»; от 80 % — жёлтым, с пояснением, что старые результаты
  инструментов сожмутся (`hub/fit.py`); щелчок открывает настройку контекста модели. Файлы: `app/ui/controller.py`,
  `Composer.qml`. Порог — 80 % или `fit.room`, если он ниже (окно 8 тыс.); «из» — окно выбранной сейчас модели. Чип
  проектов «Контекст ≈ N» становится «О проектах ≈ N» (слово пользователя 2026-11-12: две надписи «Контекст» рядом
  путали бы). Сделано: строка `contextFill` (`presenter.context_fill`: текст, цвет, подсказка; больше окна — красным,
  `shortened` — «уже сжаты»; окно неизвестно — число без «из» и «укажите в настройках»; Claude Code — без строки),
  `fill` — из `chat.done` и открытого чата; числа — `presenter.tokens_words` (окна llama.cpp — по 1024: 32768 —
  «32 тыс.»). Щелчок — `ModelsSheet.openAtContext`: поле `-c` запуска локальной модели (Strata — её раздел запуска)
  или окно среди возможностей облачной, курсор в поле (`ModelSetup.contextPlace`). Тесты —
  `test_how_full_the_models_context_is_under_the_input`, `test_tokens_and_the_projects_chip_in_words`.

### Этап 41. Поиск на Hugging Face

- 41.1 ✓ Возле каждого репозитория — «≈ 4,4 ГБ в Q4_K_M — поместится в видеопамять», по числу весов из ответа поиска,
  без лишних запросов (оценка; точное — в открытом репозитории). Цифры — те же, что в этапе 38. Файлы: `hub/hf.py`,
  `hub/model_install.py`, `app/ui/install_view.py`. Сделано: `search_fit` (число весов × `FIT_BITS`, `verdict` с
  запасами этапа 38; число не прочитано — оценки нет), у каждой строки поиска — `fit` {gb, quant, verdict, words}; MoE
  — `hf.experts` (архитектура `…moe`, тег, имя вида «30B-A3B»), его же теперь берёт и открытый репозиторий. Окно —
  строка «≈ 16,7 ГБ в Q4_K_M: часть в ОЗУ — медленнее» цветом вердикта (`install_view.fit_of`, `installHitFit`). Тесты
  — `test_each_found_model_says_whether_it_fits_this_computer`, `test_installing_a_local_model_from_the_window`.
- 41.2 Фильтры: задача + «Рисует», «Видео»; устройство — MoE или обычная (по архитектуре и имени вроде «30B-A3B»:
  метку moe ставят не все); язык; лицензия — любая / свободная / можно для работы (решение пользователя выше; и слова о
  лицензии в открытом репозитории); кто сжал (unsloth, bartowski, ggml-org…); порядок +
  «Новые». У моделей для картинок и видео «Установить» — только у тех, что AFO умеет собрать (сейчас 4 из каталога), у
  остальных — ссылка на страницу и «AFO пока не умеет ставить её сам».
  - 41.2а ✓ Хаб: `GET /v1/install/search` — `task` image/video (`text-to-image`, `text-to-video`), `language` ru/en
    (тег), `author` (5 известных, `author=` Hugging Face), `sort=new` (`createdAt`); свои фильтры хаба — `build`
    moe/dense (`hf.experts`) и `license` free/work: `hf.license_kind` — free (Apache, MIT, BSD, CC-BY…), terms (Llama,
    Gemma, Qwen, OpenRAIL…), closed (NC, research), '' — не разобрать; у каждой строки — `license_kind`. Модель картинок
    или видео — `picture`: набор AFO, если это основной файл набора (`PICTURE_MAIN`), и без оценки памяти. Тесты —
    `test_licences_in_words`, `test_the_search_by_build_language_licence_author_and_newest`.
  - 41.2б ✓ Окно: «Для чего» + «Рисовать», «Видео»; «Сначала» + «Новые»; «Ещё фильтры (N)» (свёрнуты, число выбранных
    видно) — устройство, язык (с пояснением: метки ставят не все), лицензия «Любая / Свободная / Можно для работы» с
    пояснением под ней, кто сжал. У каждой найденной — «лицензия mit — свободная»; в открытом репозитории — строка
    «Лицензия gemma: для работы можно, но со своими условиями — прочитайте их на странице модели» цветом
    (`repo.licence`, `licenceTone`). Модель картинок или видео (`drawn` от хаба): набор AFO — щелчок ведёт в
    «Рисовать» к его карточке (`shownSet`, рамка), другая — её страница на Hugging Face (`openFound`). Тест —
    `test_the_search_s_filters_licences_and_pictures_in_the_window`.
- 41.3 ✓ Вместо «bits per weight» — слова: «качество почти как у оригинала» (от ~4,5 бита), «заметно хуже» (ниже 3,5);
  биты — во всплывающей подсказке. Сделано: хаб — `quality_of` (same от `SAME_BITS` 7,5, near от `GOOD_BITS`, less от
  `MIN_BITS`, worse ниже), `quality` у каждого варианта открытого репозитория; окно — «как у оригинала», «качество почти
  как у оригинала», «немного хуже оригинала», «заметно хуже» (`QUALITY`), биты — в подсказке строки варианта
  (`bitsTip`). Тесты — `test_a_variants_quality_in_words`, `test_installing_a_local_model_from_the_window`.

### Этап 42. Ускорение: угадывание вперёд

- 42.1 ✓ Конфиг знает флаги угадывания; их имена — из `--help` той сборки, что в конфиге. Файл `hub/llama_config.py`;
  тест — настоящий файл на диске и чтение параллельным потоком. Сделано: `server_flags` (`--help` один раз на файл
  сборки, `parse_help`: синонимы, «has been removed», виды `--spec-type`; не запускается — `None`); `FLAGS` +
  `spec_type`, `draft_model`, `draft_gpu_layers`, `draft_max`, `draft_min`, `draft_cache_k/v` (старые имена читаются);
  `model_view` — `spec`; `edit(..., build=)` пишет их именами сборки (без сборки — «не умеет угадывать вперёд»), без
  черновых видов — флаги черновика снимаются, `none` — все. `match_build`: после обновления llama.cpp в мастере, в той
  же записи под замком (`Setup._update`), — старое имя под новым, незнакомые виды и снятые сборкой флаги прочь, пустая
  строка уходит. В b11899 `--spec-type` по умолчанию `none` (один `-md` не угадывает), своего контекста у черновика нет
  (его KV — на весь `-c`). Тесты — `test_the_speculative_flags_are_the_builds_own`,
  `test_a_new_llama_cpp_gets_the_flags_it_takes_while_llama_swap_reads_the_file`,
  `test_the_engine_is_updated_beside_the_old_one`.
- 42.2 ✓ Оценка памяти учитывает черновик: его веса и KV на весь контекст. Файлы: `common/llama_memory.py`,
  `app/ui/model_setup.py`. Сделано: `estimate(..., draft=)` — веса черновика, его KV на весь `-c` с его типом кэша
  (без `-ctkd` — f16, как у llama.cpp) и `DRAFT_COMPUTE_GIB` 0,3 (до замера); черновик — в видеопамяти первым, его слои
  в ОЗУ — «часть в ОЗУ»; `draft_gb` и «черновая модель N ГБ» в тексте. `draft_load(view)`; хаб читает заголовок
  черновика (`draft_info` в `/v1/llama/models`, `_files`), его считают окно «Настройка модели» (`launch_judged`),
  проверка после пробного запуска (`_estimate`) и слова при загрузке (`_estimated`); файла нет — оценка без него. Пример:
  Qwen3-0.6B при 32 тыс. — 4,4 ГБ в f16, 2,8 ГБ в q8_0. Тест — `test_the_draft_model_takes_its_share_of_the_memory`.
- 42.3 ✓ Пара по токенизатору: `common/gguf.py` дочитывает токенизатор; черновики — в `hub/model_catalog.json`.
  Сделано: `gguf.tokenizer_info` (вид, токены, bos/eos и добавляются ли), `same_vocabulary` — правило llama.cpp
  (`common_speculative_are_compatible`: тот же вид и bos/eos, разница не больше 128 токенов, токены с пятого одни);
  `hf.tokenizer` — словарь черновика из первых байт его файла на Hugging Face, до скачивания (`_first_bytes`, общий с
  `hf.header`). Каталог `drafts`: Qwen3-0.6B, Qwen3.5-0.8B, Qwen2.5-Coder-0.5B (`names`: только для «coder»),
  Qwen2.5-0.5B — Q8_0, по `architectures`. `Installer.draft_for(файл модели)`: семья, модель не меньше чем в
  `DRAFT_RATIO` 5 раз больше файла черновика (больший — его заголовок даже не читается), словари одни. Сверено с
  Hugging Face 2026-11-13: Qwen3-0.6B — пара Qwen3-8B; Qwen3.5-0.8B — Qwen3.5-35B-A3B; черновики Qwen2.5 0.5B —
  Qwen2.5-Coder-7B и Qwen2.5-VL-7B (словарь 152064 и 151936 — разница ровно 128). Без пары: Qwen3-Coder-30B-A3B от
  unsloth и Qwen3.6/3.8 — другой bos; у Gemma 4 словарь свой (`gemma4`), её черновики — MTP и DFlash («Потом»).
  Тесты — `test_a_draft_model_is_paired_by_its_tokenizer`, `test_the_catalogs_drafts`.
- 42.4 ✓ Замер самим llama-server: без угадывания, ngram, черновик (не помещается — «черновик + часть слоёв в ОЗУ»,
  решение пользователя выше); при температуре из настроек модели. Победитель
  пишется, если быстрее больше чем на 3 %, затем проверочный запуск, как сейчас. Ручной «Подобрать» тоже ждёт видео и
  останавливается, если начался чат. Файл `hub/tuning.py`; тесты — `tests/test_tuning.py` и `tests/test_together.py`.
  - 42.4а ✓ Сам замер. `spec_command`: команда модели, какой её записал бы конфиг (`llama_config.edit` именами сборки),
    макросы подставлены, `${PORT}` — свой порт замера (`free_port`), мимо llama-swap; `llama_config.arguments` —
    слова команды без кавычек. `spec_variants`: none, ngram (`ngram-mod`, нет — `ngram-simple`), черновик (KV — тип кэша
    модели) — рядом с моделью как есть, а не помещается (оценка 42.2, запас — `space`) — `draft_fewer`: черновик целиком
    на видеокарте, у модели на сколько нужно слоёв меньше; при `--fit` — просто черновик; что черновику не хватает —
    `{short_gb, context}` для окна. `Tuner._serve`: журнал в файл (`logs/tuning-server.log`, не pipe), ждёт `/health`,
    разминка, `SPEC_PROMPTS` (правка кода — ngram угадывает переписанные строки — и обычный ответ, на языке окна) с
    `SEED` и выборкой модели (`sampling` из её настроек), `timings.predicted_per_second` и доля угаданных
    (`draft_n_accepted`/`draft_n`); не запустился — последние слова его журнала; потом процесс снимается с детьми
    (`_kill` через psutil: у `.cmd` и обёрток — дочерний процесс). Тесты —
    `test_guessing_ahead_is_measured_by_llama_server_itself` (подставной llama-server — настоящий процесс на своём
    порту), `test_what_guessing_ahead_is_measured`.
  - 42.4б ✓ Подбор: после замера llama-bench — замер угадывания, выбор, запись под замком, проверочный запуск; ручной
    «Подобрать» ждёт видеокарту и останавливается чатом. Сделано: `_tune` = `_tune_bench` + `_tune_spec`, общая
    запись с проверкой — `_write` (файл как он есть после замера, свои строки назад). `_tune_spec`: сборка без
    `--spec-type` — `unsupported`; черновик — от `draft` (42.5) или из команды (`_find_draft`); перед замером модели
    снова выгружаются (проверка llama-bench её загрузила); вариант, что не запустился, пропускается (без угадывания не
    запустился — ошибка этапа); `spec_choice`: каждый дороже (ngram, черновик, черновик с меньшими слоями) — только если
    быстрее каждого дешевле больше чем на 3 %; выбранное пишется именами сборки и проверяется; не встало — свои строки
    назад, а то, что записал llama-bench, остаётся (`spec` — `failed`). Итог — `result["spec"]`: status, kind,
    spec_type, before/after, accepted, measured, short, draft. Ручной подбор ждёт тихую видеокарту и останавливается
    чатом или установкой (`_watch` всегда), снова в очередь — своего вида. Тесты —
    `test_the_fastest_guessing_ahead_goes_into_the_config_and_is_checked`,
    `test_guessing_ahead_that_does_not_start_is_passed_over_or_taken_back`,
    `test_tuning_by_hand_waits_for_the_card_and_gives_way_to_a_chat`, в `test_together.py` —
    `test_a_chat_that_begins_stops_tuning_by_hand_and_it_goes_on_after` (настоящий чат хаба).
- 42.5 ✓ Черновая модель качается сама; не дала выигрыша — файл удаляется, если им никто не пользуется. Кнопка
  «Ускорить». Файлы: `hub/model_install.py`, `hub/api/install.py`. Сделано: `Installer.speed_up` (после установки —
  `on_done`, сам; «Ускорить» — `POST /v1/install/speedup/{id}`, сразу): пара из каталога (`draft_for`) — уже скачанная
  берётся снова, иначе ждёт загрузки установок (одна за раз) и качается на самый быстрый диск с местом в
  `…\AFO\models\drafts` (sha256, докачка, повторы как у модели); ход — `drafting` (`GET /v1/install/speedup`, событие
  `install.draft`); без видеокарты не качается (только замедлит процессор — меряется ngram); затем подбор
  (`on_speed_up` → `tuner.request`, сразу или в тишине). Записи — `install.drafts` в `settings` (файл → repo, file,
  sha256, size, `waiting` — модели, чей подбор его ещё не мерил); подбор берёт его через `draft_path`. После подбора
  (`Tuner.on_tuned` → `tuned`) черновик, который ни одна команда не использует и никто не ждёт, удаляется (`prune_drafts`;
  занятый файл — в следующий раз); файл черновика пропал — `unhook_lost_drafts` под замком снимает угадывание с каждой
  команды, что на него ссылается (и при старте хаба). Снять угадывание (`spec_type: none`) можно без сборки. Тест —
  `test_a_draft_model_comes_by_itself_and_goes_when_it_gives_nothing`.
- 42.6 ✓ Окно: какое ускорение стоит и сколько дало. Файлы: `app/ui/model_setup.py`, `ModelsSheet.qml`. Сделано:
  в «Модели → Запуск → Подбор по замерам» — «Угадывание вперёд» (`spec_text`): «Ускорение — черновая модель
  Qwen3-0.6B-Q8_0.gguf: ответ 40 → 61 ток/с, угадано 72 %.», `draft_fewer` — «…, часть слоёв модели в ОЗУ», «ngram
  (повторы уже написанного)», «не дало выигрыша — выключено» с тем, чего черновику не хватило и при каком контексте он
  поместится; ход загрузки черновика («Качаю черновую модель Qwen3 0.6B: 300 МБ из 767 МБ…», событие `install.draft`
  — теперь в `ModelSetup`, не в окно установки), «Меряю угадывание вперёд: 1 из 3…», сборка без угадывания, ошибка.
  Итог подбора показывается, пока команда модели такая, какой её оставил подбор; иначе — что в команде сейчас
  («Сейчас: ngram.»). Кнопки «Ускорить» (`speedUp` → `POST /v1/install/speedup/{id}`) и «Снять ускорение» (`speedOff`
  → `spec_type: none`, только пока угадывание стоит). Тесты — `test_guessing_ahead_in_the_models_sheet`,
  `test_guessing_ahead_in_the_window` (QML, снимок без экрана сверен).
- 42.7 ✓ Живая проверка (2026-11-13, по слову пользователя, RTX 5080, сборка b11899; стенд — свой хаб и свой
  llama-swap на копии конфига, черновики в папке стенда) и что она нашла. «Ускорить» для Qwen3-VL-8B Q8_0 прошло
  целиком за 2 минуты: черновик скачан (sha256), llama-bench, три варианта, ничего не записано (без угадывания 94,6
  ток/с, ngram 96,3, черновик при 28 слоях из 36 — 50,1), черновик удалён. Вручную: кэш q8_0 — без черновика 90,3,
  с черновиком целиком на видеокарте 94,2 (правка кода 121 при 89 % угаданного, обычный ответ 67 при 34 %); кэш f16
  с черновиком не помещается (пик 15,4 из 16 ГБ, остальное Windows вытесняет — вдвое медленнее), оценка не зря его не
  мерила. `--fit` черновик учитывает, но место ему даёт, уводя слои модели в ОЗУ (46 ток/с) — замер это отбрасывает.
  Исправлено: (1) в каталоге Qwen3 0.6B — и для `qwen3vl`, `qwen3vlmoe` (словарь Qwen3-VL — Qwen3, каталог эту пару
  не называл); (2) llama-server с черновиком другого словаря не падает, а идёт без угадывания («the target and draft
  vocabs are not compatible»), держа черновик в видеопамяти (+0,9 ГБ), и его замер — шум (+2,5 % при пороге 3 %):
  вариант с черновиком, за замер не угадавший ни одного токена, — ошибка со словами llama.cpp (`_said`), не замер.
  Подозрение на `add_bos` (нет ключа против `false`) не подтвердилось: ту пару отвергают сами токены (250 `[PAD…]`
  против токенов звука), как и llama.cpp. Тест — `test_a_draft_llama_cpp_does_not_take_is_not_measured`, каталог —
  `test_the_catalogs_drafts`.
- Риски: обновление llama.cpp в мастере переименует флаги — после обновления сверить их и под замком переписать или
  снять; удалили черновик — снять `-md` у всех моделей, что на него ссылаются; черновик занимает видеопамять —
  включать только по замеру; «speculative decoding not supported by this context» — ловит проверочный запуск.

### Этап 44. Ускорение для всех моделей

Находка 10. Всё мерится самим llama-server, как в 42.4 (`spec_command`, свой порт, мимо llama-swap); вариант пишется,
если быстрее лучшего без него больше чем на 3 %, затем проверочный запуск. Имена флагов — из `--help` сборки (42.1):
у сборки без флага вариант не мерится.

- 44.1 ✓ Конфиг и оценка памяти знают MoE. `common/gguf.py` читает из заголовка число экспертов и активных на слово
  (`*.expert_count`, `*.expert_used_count`) и слой MTP (`*.nextn_predict_layers`); `hub/llama_config.py` — флаги
  `cpu_moe`, `moe_cache_mib`; `common/llama_memory.py` — эксперты в ОЗУ не в видеопамяти, кэш — в видеопамяти. Тест —
  настоящий GGUF на диске (заголовок MoE) и оценка: модель вдвое больше видеопамяти «помещается» с экспертами в ОЗУ.
  Сделано: `header_info` — `experts`, `experts_used`, `mtp_layers`; у модели с экспертами `model_info` читает весь
  заголовок и таблицу тензоров (`_tensors`: размер каждого по `TYPE_SIZES` — типы ggml, блок и его байты) и даёт
  `expert_bytes` — вес экспертов в каждом слое; эксперты — тензоры, которые выносит сам llama.cpp (шаблон b11899
  `blk\.\d+\.ffn_(up|down|gate_up|gate)_(ch|)exps`), тип, которого таблица не знает, — его доля по числу значений от
  байтов файла, что не заняли другие. Модель, разбитая на файлы (`…-00001-of-0000N.gguf`, `parts`): размер и эксперты
  — всех частей (раньше оценка видела только первый файл). Конфиг: `cpu_moe` (`-cmoe/--cpu-moe`) — флаг без значения
  (`_set_switch`: пишется один раз, снимается один, не забирая слово после себя), `moe_cache_mib` — только сборке, что
  его знает (`spelled`), снять можно без сборки; `match_build` снимает флаги экспертов, которых новая сборка не знает.
  `estimate(..., moe=moe_load(view))` — эксперты всех слоёв или первых N в ОЗУ (не «медленные слои»: каждое слово
  берёт немногих), их кэш — в видеопамяти; итог «эксперты MoE … ГБ в ОЗУ», «кэш экспертов … ГБ», «помещается: эксперты
  MoE в ОЗУ, остальное на видеокарте»; `moe` передают все четыре оценки (окно, проба хаба, установщик, подбор). Сверка
  с Qwen3-30B-A3B Q4_K_M: таблица тензоров (Q4_K, Q6_K, F32) — байт в байт с размером файла (остаток 19 — выравнивание),
  заголовок — 0,05 с; оценка при контексте 16384 и q8_0: `--cpu-moe` 2,5 ГБ, с кэшем 8000 МБ 10,3, `--n-cpu-moe 12`
  14,7, `--fit` 14,9 — замер 46.1 (вся видеокарта, с рабочим столом) 3,1 / 11,1 / 15,5 / 15,1. Тесты —
  `test_moe_experts_in_ram_and_their_cache_on_the_card`, `test_the_experts_flags_in_the_config`.
  Найдено 2026-11-14 (при 44.7): в `estimate` вес черновика (42.2) писался в ту же переменную `held`, что эксперты в
  ОЗУ, — у любой модели с черновиком окно писало «эксперты MoE 0.0 ГБ в ОЗУ» и «помещается: эксперты MoE в ОЗУ», а у
  MoE-модели с черновиком пропадали её эксперты в ОЗУ. У черновика своя переменная; тесты 42.2 и 44.1 проверяют и это.
- 44.2 ✓ Подбор для MoE: модель с экспертами, что целиком в видеопамять не входит, — варианты «эксперты N слоёв в ОЗУ»
  (`--n-cpu-moe`, от `--fit` и меньше) и «все эксперты в ОЗУ + кэш» (`--cpu-moe --moe-cache-mib`: ~10 % экспертов и
  сколько оставляет видеопамять). Сетка маленькая: замер одной модели — минуты, не час. Файл `hub/tuning.py`; тест —
  `tests/test_tuning.py` (подставной llama-server отвечает скоростью по флагам).
  Сделано: подбор — llama-bench, затем эксперты (`_tune_moe`, итог — `result["moe"]`), затем угадывание вперёд; у
  каждого шага своя ошибка. `moe_variants`: как в команде сейчас (после установки — `--fit`); эксперты наименьшего
  числа первых слоёв в ОЗУ, при котором остальное помещается по оценке 44.1 (`layers`), и на `MOE_MORE` = 3 слоя больше
  (`layers_more`: запас буферам, которых оценка не видит); все эксперты в ОЗУ и кэш их в видеопамяти — сколько
  оставляет место, шагом 256 МБ, от 1 ГБ (`cache`, только сборке с `--moe-cache-mib`) — все с `-ngl 99` без `--fit`;
  эксперты только в пределах ОЗУ (`ram_gib`); вариант, который уже стоит в команде, не мерится дважды; модель, что
  помещается целиком, а команда держит экспертов в ОЗУ, — вариант «вся на видеокарте» (`card`). Мерит llama-server
  (llama-bench не знает `--cpu-moe`): ответы как в 42.4 и чтение длинного вопроса (`LONG_PROMPT`, ~3 тыс. токенов,
  `_serve(..., reading=True)` → `pp`; при контексте от 4096). `moe_choice`: свой вариант, если другой не отвечает
  быстрее больше чем на 3 % при чтении не медленнее больше чем на 10 % (`READ_DROP`); свой не запустился — самый
  быстрый из запустившихся. Выбор пишется и проверяется (`_write`). Вес MoE-модели — по её таблице тензоров
  (`_weights`: у Qwen3-30B-A3B на 19 байт выравнивания меньше файла), так подбор мерится в тестах на заголовке без
  весов. Тесты — `test_where_the_experts_are_measured`, `test_the_experts_go_where_the_model_answers_fastest`.
- 44.3 ✓ MTP: у модели есть слой MTP (44.1) и сборка знает `draft-mtp` — вариант без лишнего файла. Файл
  `hub/tuning.py`; тест — подставной сервер и заголовок со слоем MTP.
  Сделано: `spec_variants` — вариант `mtp` (`--spec-type draft-mtp`, без `-md`: `_uses_draft` его не считает
  черновиком) после ngram и перед черновой моделью — дешевле её, своего файла и словаря нет. Слой, с которым llama.cpp
  запустился, но за замер не угадал ни одного токена, — ошибка варианта со словами llama.cpp (как черновик в 42.7), не
  замер. Окно знает вид (`SPEC_KINDS["mtp"]` — «слой MTP самой модели»). Тест —
  `test_a_model_with_a_layer_of_its_own_guesses_ahead_with_it`.
- 44.4 ✓ DFlash и EAGLE-3: каталог черновиков (`drafts` в `hub/model_catalog.json`) получает вид (`dflash`, `eagle3`) и
  модель, для которой черновик сделан (черновик DFlash обучен под одну модель — пара по ней, а не по словарю, как в
  42.3); `Installer.draft_for` — по виду; вариант `draft-dflash` / `draft-eagle3` с `-md`. В каталог — только черновики,
  что берёт обычный llama.cpp (не ответвления); перед записью сверить на Hugging Face файлы и лицензии. Отказ сборки
  взять черновик ловит 42.7. Файлы: `hub/model_catalog.json`, `hub/model_install.py`, `hub/tuning.py`.
  Сделано (✓ механизм; каталог — после живой проверки): вид черновика — по его заголовку (`general.architecture`:
  `dflash`, `eagle3` — так их называет b11899, строки в `llama.dll`), `DRAFT_KINDS` → `--spec-type draft-dflash` /
  `draft-eagle3` с `-md`; сборка без вида — вариант не мерится; черновик из команды человека мерится своим видом.
  Каталог: у записи `kind` (`simple` по умолчанию, `dflash`, `eagle3`) и обязательный `names` — файлы модели, для
  которой черновик сделан; `draft_for(model_file, kinds)` берёт такой раньше черновика семейства, если сборка знает
  его вид (`_fetch_draft` передаёт `--spec-type` сборки), словарь сверяется так же. Сверка на Hugging Face
  (2026-11-14): документация llama.cpp (`docs/speculative.md`) не называет ни одного готового GGUF — черновики
  DFlash (z-lab) и EAGLE-3 (AngelSlim, RedHatAI, lmsys, nvidia) выложены исходными весами и конвертируются
  `convert_hf_to_gguf.py --target-model-dir <исходная модель>` (берут её словарь и вложения); готовые GGUF для
  моделей каталога есть только у частных авторов (Qwen3.6-27B, Qwen3.6-35B-A3B, Gemma 4 26B-A4B), среди них пробные,
  под ответвления, и сделанные до смены формата DFlash (их надо переконвертировать). Поэтому в каталог пока ничего не
  записано: черновик туда попадает только после живой проверки с b11899 (44.6, по слову человека). Поправка
  2026-11-14 (44.7, 44.8): сверка была неполной — готовые `dflash-` и `mtp-` выкладывают сами ggml-org в репозиториях
  моделей каталога, и llama.cpp ищет их там сам (`--dflash`, `--mtp`); они в каталоге с 44.8. Тесты —
  `test_a_draft_made_for_its_model_guesses_its_own_way`, `test_a_draft_made_for_the_model_comes_before_its_familys`,
  `test_the_catalogs_drafts`.
- 44.5 ✓ Окно: в «Настройка модели» — где эксперты (видеокарта / ОЗУ / ОЗУ + кэш) и какое угадывание вперёд выбрал
  замер; можно поменять руками. Файлы: `app/ui/model_setup.py`, `ModelsSheet.qml`.
  Сделано: у модели с экспертами (`info.experts`) в «Запуске» строка «Эксперты MoE» (`launchExperts`): «на
  видеокарте» / «первых слоёв — в ОЗУ» с числом / «все в ОЗУ» с кэшем на видеокарте в МБ (`launch_draft`: `moe_mode`,
  `moe_n`, `moe_cache`; `launch_changes` — только то, что отличается от команды; оценка памяти считает экспертов по
  полям); у модели без экспертов строки нет. Запись кэша из окна — `PUT /v1/llama/models/{id}` спрашивает сборку
  (`--help`), без `--moe-cache-mib` — 422 и файл как был. Итог подбора (`moe_text`, `moeText`): «Эксперты MoE: 14
  первых слоёв в ОЗУ — ответ 102 → 111 ток/с, чтение подсказки 1455 → 1425 ток/с.», «быстрее всего как сейчас — …»,
  «не подобраны: …» — пока команда держит экспертов так, как оставил подбор (`placed` в итоге 44.2), иначе — где они
  сейчас («все в ОЗУ, их кэш 7,8 ГБ на видеокарте», «как раскладывает llama.cpp (--fit)»); замер — «Меряю, где
  держать экспертов MoE: 1 из 4…». Угадывание: «черновик DFlash …», «черновик EAGLE-3 …», «Сейчас: слой MTP самой
  модели.». Снимок окна без экрана сверен. Тесты — `test_where_the_experts_are_in_the_models_sheet`,
  `test_the_experts_in_the_window`, `test_the_window_writes_the_experts_cache_by_the_builds_word`.
- 44.6 Весь pytest (`--ignore=tests/test_computer.py`) и живая проверка на настоящих моделях — по слову пользователя,
  после `nvidia-smi`: модель MoE с кэшем и без, модель с MTP, пара с DFlash; цифры — в этот файл. После 44.7–44.8 —
  на Qwen3.6-35B-A3B Q4_K_M (MoE больше видеокарты) с её `dflash-` и `mtp-`: эксперты, оба черновика, запись итога.
- 44.7 ✓ Слой MTP отдельным файлом (найдено 2026-11-14, после 44.5): ggml-org кладёт рядом с моделями каталога
  `mtp-<модель>-Q8_0.gguf` и `dflash-<модель>-Q8_0.gguf` (Qwen3.6-35B-A3B, Qwen3.8-27B), а в самой модели слоя MTP нет
  — 44.3 его не видел. Сверено по исходникам b11899: `common/speculative.cpp` (`common_speculative_init_result`) — файл
  `-md` грузится своей моделью, при `draft-mtp` её контекст — MTP; без `-md` — контекст MTP на самой модели;
  `common/arg.cpp` — `-hf … --mtp` кладёт файл `mtp-` в путь черновика, вид без `--spec-type` — по тензору
  `blk.{block_count-1}.nextn.eh_proj.weight`. Файл ggml-org: `qwen35moe`, `block_count` 41, `nextn_predict_layers` 1,
  23 тензора — блок 40 и словарь (`token_embd`, `output`), без блоков 0–39; в основной Q4_K_M — 40 блоков.
  Сделано: `common/gguf.py` — у заголовка со слоем MTP читается таблица тензоров (`_weights` даёт и блоки); все блоки
  не ниже `layers − mtp_layers` — `mtp_head`, и KV-кэш — только этих слоёв (`kv_layers = mtp_layers`); целая модель
  со своим слоем MTP (маленький черновик семейства Qwen3.5) — не голова. `hub/tuning.py` — `draft_kind`: голова —
  `draft-mtp` с `-md`, вариант `draft` (память — её блок и KV); сборка без `draft-mtp` — не мерится.
  `hub/llama_config.py` — `_set_spec`: `draft-mtp` держит файл, пока изменения его дают или вид не трогают; `draft-mtp`
  без файла — слой самой модели, `-md` снимается. Окно: «слой MTP из файла …». Тесты —
  `test_an_mtp_head_in_a_file_of_its_own`, `test_an_mtp_head_of_its_own_file_guesses_ahead`,
  `test_where_the_experts_are_in_the_models_sheet`.
- 44.8 ✓ Каталог черновиков: файлы ggml-org (`dflash-`, `mtp-`) для Qwen3.6-35B-A3B и Qwen3.8-27B — вид, `names`,
  лицензия (Apache-2.0 у обоих, сверено на Hugging Face 2026-11-14); поправить итог 44.4 («готовых нет»).
  Сделано: в `drafts` четыре записи — `dflash-…-Q8_0.gguf` и `mtp-…-Q8_0.gguf` из `ggml-org/Qwen3.6-35B-A3B-GGUF`
  (`qwen35moe`; 0,42 и 1,99 ГБ) и `ggml-org/Qwen3.8-27B-GGUF` (`qwen35`; 2,06 и 3,16 ГБ), `kind` `dflash` / `mtp`,
  `names` — файлы самой модели в любом сжатии (`^Qwen3\.6-35B-A3B[-.]`). Сверено по заголовкам на Hugging Face: у
  всех тот же словарь (248 320 токенов, `qwen35`), черновик DFlash — `dflash`, 6 и 5 блоков; b11899 сверяет словарь
  только у `draft-simple`. Какой из двух быстрее, говорит только замер — поэтому меряются оба: `Installer.drafts_for`
  (было `draft_for`) даёт каждый сделанный для модели черновик, чей вид знает сборка, а черновик семейства — только
  когда таких нет (его тогда и не читает); `_fetch_draft` качает каждый, `draft_paths` (было `draft_path`) отдаёт
  подбору все ждущие. Подбор (`_find_drafts`): найденные и тот, что в команде, — каждый свои варианты
  (`draft_variants`, имя файла при варианте и в замере), меньший раньше (дороже — только если быстрее больше чем на
  3 %); чего не хватает в видеопамяти — меньшая нехватка, пока не помещается ни один. Проигравший черновик удаляется,
  как в 42.5. Тесты — `test_a_draft_made_for_the_model_comes_before_its_familys`, `test_the_catalogs_drafts`,
  `test_each_draft_made_for_the_model_is_measured`.
- Риски: угадывание бывает медленнее (RTX 3090) — пишется только измеренный выигрыш; кэш экспертов и черновик делят
  видеопамять с контекстом — оценка 44.1 считает всё вместе; флаги меняются между сборками — имена из `--help`;
  черновики сообщества бывают под ответвления llama.cpp — их нет в каталоге; замер многих вариантов долог — сетка
  маленькая, очередь видеокарты держится как в 42.

### Этап 45. Движки — сменные части; Strata и README

- 45.1 Общий вход движков: для каждого — берёт ли он эту модель (архитектура из заголовка), какие у него настройки и их
  имена, команда запуска для llama-swap, как его мерить. Новый файл (`hub/runtimes.py`: имя `hub/engines.py` занято
  скачиванием llama.cpp). Первый движок — llama.cpp: его особое из `hub/llama_config.py`, `hub/tuning.py`,
  `hub/model_install.py` переезжает за вход без смены поведения — их тесты те же и проходят без правок; второй — Strata
  (45.2). Тест — оба движка отвечают на одни вопросы; модель, которую берёт только Strata, llama.cpp не предлагается.
- 45.2 Сканер ставит Strata Terminal из выпусков `Niko1221/Strata` (sha256) там, где её модели помещаются; поместится
  ли — спрашивает у неё (`strata launch --plan --output json`). Профиль готовит Strata (`--no-tui --yes`), модель
  запускает llama-swap (`strata serve --profile …`). На время её подбора держится очередь видеокарты. Своя Strata
  человека (папка или `engine: strata` в конфиге) не трогается.
- 45.3 `README.md` и `README.en.md`: раздел о Strata — что делает (движок для Qwen3.8-Flash-Next с кэшем экспертов MoE
  и угадыванием вперёд: MTP, DFlash2), как AFO её ставит и запускает, лицензия MIT, благодарность Niko1221 со ссылкой
  на `github.com/Niko1221/Strata`. Политику подписи кода не трогать: её проверяют тесты.

### Этап 46. KTransformers через WSL — по согласию человека (закрыт замером 46.1)

KTransformers (`kvcache-ai/ktransformers`, Apache-2.0) держит экспертов MoE в ОЗУ и считает их процессором, видеокарта —
остальное; силён на огромных MoE (DeepSeek, Kimi, GLM-5, Qwen3-MoE, MiniMax). Своя Windows у него «временно снята» —
только WSL. Его главное преимущество — ядра AMX и AVX-512 (серверные Xeon); с 2026-03 есть ядро AVX2. У автора AFO
(2026-11-14): WSL2 2.7.11 есть (Ubuntu, kali-linux, docker-desktop), Core Ultra 9 285 — AVX2 без AVX-512, в
`.wslconfig` память не задана — WSL берёт половину ОЗУ (~32 из 63 ГБ).

- 46.1 ✓ Стоит ли: на компьютере автора, по его слову и после `nvidia-smi` — KTransformers в отдельном дистрибутиве WSL
  (чужие не трогать), одна MoE-модель, которую берут оба, против llama.cpp с подбором 44.2 при той же памяти. Не
  быстрее больше чем на 20 % — этап закрывается, причина пишется сюда; остальные шаги — только при выигрыше.
  Сделано (2026-11-14): дистрибутив `AFO-kt` (Ubuntu 24.04, `wsl --import`, 31 ГБ — половина ОЗУ), kt-kernel,
  sglang-kt, ktransformers 0.7.2, Torch 2.9.1 cu128, CUDA Toolkit 12.8. Модель — Qwen3-30B-A3B (Apache-2.0): GGUF
  Q4_K_M (17,3 ГиБ, sha256 сверен) — эксперты обоих движков; BF16 (57 ГиБ) — часть KTransformers на видеокарте.
  Замер одинаковый (`kt_bench`: поток, температура 0, без размышлений, каждый вопрос с уникальным началом — кэш
  вопросов не помогает): ответ 256 токенов, длинный вопрос ~4 тыс. токенов, медиана двух. llama.cpp b11899 (Windows,
  `-c 16384 -fa on -ctk/-ctv q8_0 -t 24`): `--fit` — 15,1 ГБ видеопамяти, 102–111 ток/с, чтение 1455 ток/с;
  все эксперты в ОЗУ (`--cpu-moe`) — 3,1 ГБ, 42–45 ток/с, 476; то же + кэш экспертов 8 ГБ (`--moe-cache-mib 8000`) —
  11,1 ГБ, 73–103 ток/с, 882; эксперты 12 слоёв в ОЗУ (`--n-cpu-moe 12`) — 15,5 ГБ, 105–116 ток/с, 1425.
  KTransformers (LLAMAFILE: у процессора AVX2 без AVX-512/AMX), все эксперты в ОЗУ: 24 потока — ~1 ток/с при 100 %
  процессора (потоки на энергоэффективных ядрах), 6/8/12 потоков с `KT_KERNEL_CPU_VARIANT=avx2` — 12,8/13,3/13,6 ток/с
  при 6,1 ГБ видеопамяти — втрое медленнее `--cpu-moe` llama.cpp. По пути найдено в sglang-kt 0.7.2: обёртку
  KTransformers для MoE регистрирует только DeepSeek-V4 — у Qwen3-MoE все эксперты шли на видеокарту (в тестовой
  копии — заплатка `import kt_ep_wrapper` в `qwen3_moe.py`); с экспертами на видеокарте больше нуля видеопамять
  всё равно занята целиком; графам CUDA нужен `nvcc`, сборке — `ninja` без путей Windows в PATH. **Этап закрыт**:
  на таком компьютере llama.cpp быстрее в 3 раза при меньшей памяти, а установка требует заплаток. Шаги 46.2–46.3
  не делаются. Вернуться — если KTransformers получит Windows без WSL и обгонит llama.cpp на процессорах без AMX.
  Попутно для этапа 44: кэш экспертов поднял `--cpu-moe` в 1,7–2,4 раза, `--n-cpu-moe 12` быстрее `--fit` на 3–5 %.
- 46.2 Согласие и установка: окно говорит, что будет, до первого действия — WSL (права администратора, может быть
  перезагрузка, включённая виртуализация в BIOS), свой дистрибутив AFO (`wsl --import`), память WSL (`.wslconfig`
  человека меняется только с его «да», прежний файл сохраняется), место на диске, модели внутри WSL (с дисков Windows
  через `/mnt` медленно). Отказ — ничего не меняется и больше не спрашивается.
- 46.3 KTransformers — третий движок за общим входом (45.1): llama-swap запускает его в WSL на своём порту, его модели —
  в каталоге с пометкой «через WSL»; «Вернуть как было» убирает дистрибутив AFO.
- Риски: виртуализация выключена или занята другим гипервизором; WSL Docker Desktop и другие дистрибутивы человека
  трогать нельзя; половина ОЗУ по умолчанию мала для экспертов; CUDA в WSL зависит от драйвера Windows; без AVX-512 и
  AMX выигрыш против llama.cpp 44.2 может не быть.

### Потом

- Установка любых моделей для картинок и видео с Hugging Face.
- Подсказка «подключите зарядку» на ноутбуке от батареи.

### Открытое из аудита хаба 2026-11-10

- #8 Снимок версии пишется не атомарно, sha256 при чтении не сверяется (`hub/changes.py` 109–123). Заодно хвост #7:
  `keep` обновляет mtime при повторном использовании снимка, очистка перепроверяет перед удалением.
- #9 «Сравнить с текущим» читает файл без предела размера (`hub/api/history.py` 66).
- #10 Два `connect` одного MCP-сервера гоняются за `_held` (`hub/mcp_client.py` 226–231, 279–280).
- #11 `projects.secret_file` работает в цикле событий (`hub/api/projects.py` 127, 203, 248, 255).
- Отложены пользователем: `IntegrityError` при удалении чата посреди ответа; строка `delegate_tasks` остаётся
  «running», когда удалён чат подпрогона; `stop()` с `cancelled:false` не снимает «busy» в окне.

Порядок: 38 → 39 → 40 → 41 → 42 → 43 → 44 → 45 → 46. 38 — маленький, и его ошибка видна на ноутбуке сейчас; 39
чинит ноутбук; 40 — небольшой и виден сразу; 42 — самый большой; 43 (трей) и 44 (ускорение для всех моделей) — по
слову пользователя перед Strata; 46 (KTransformers) закрыт замером 46.1 (2026-11-14). Живая проверка на настоящих моделях — в конце этапа, по слову
пользователя и после `nvidia-smi`; весь pytest — один раз в конце этапа (Правило 4). Пункты аудита — между этапами,
по слову пользователя.

## Суть проекта

AFO — локальная ИИ-экосистема для Windows, публичный репозиторий, лицензия GNU GPL 3. Модели работают на компьютере
пользователя; облачные подключаются по его ключам.

- **Хаб** (`hub/`) — один фоновый процесс на `127.0.0.1:8765` (порт занят другой программой — на следующем свободном,
  адрес в `data/hub.json`, 43.9), живёт и после закрытия окна. Владеет проектами,
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
