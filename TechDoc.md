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
- `common/efforts.py` — глубины размышлений по порядку и ближайшая, которую берёт модель, — для хаба и окна
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
- `hub/gpu_card.py` — кто держит видеокарту: задание Студии или проба установки, и держит ли её от чатов (`gate`)
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
- `hub/db/migrations/053_visual_last_frame.sql` — последний кадр видео по двум кадрам

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
- `app/ui/qml/StudioView.qml` — экран «Студия»: картинки и видео (48.7; был лист «Нарисовать»)
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
- `tests/test_tasks.py` — очередь окна для запросов к хабу: долгий запрос не держит место
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
- 44.6 ✓ Весь pytest (`--ignore=tests/test_computer.py`) и живая проверка на настоящих моделях — по слову
  пользователя, после `nvidia-smi`: модель MoE с кэшем и без, модель с MTP, пара с DFlash; цифры — в этот файл. После
  44.7–44.8 — на Qwen3.6-35B-A3B Q4_K_M (MoE больше видеокарты) с её `dflash-` и `mtp-`: эксперты, оба черновика,
  запись итога.
  Сделано (2026-11-14, по слову пользователя «да, загружай», RTX 5080 16 ГБ, b11899; стенд — свой хаб на 8798 со всей
  изоляцией тестов и своим ключом «Графика», свой llama-swap на 8799 со своим конфигом и портами с 12001, черновики —
  в папке стенда; модель скачана загрузчиком AFO в `D:\AI model\models\Qwen3.6-35B-A3B-GGUF`, sha256 сверен, 19,1 ГиБ
  за 12 минут). Команда — как пишет установщик модели больше видеокарты: `--fit on -c 32768 -ctk q8_0`. «Ускорить»
  прошло целиком за 5 минут: оба черновика скачаны (0,4 и 1,9 ГБ, sha256), llama-bench (6 прогонов, записывать
  нечего), эксперты — `--fit` 96,7 ток/с (чтение 1149), 13 первых слоёв в ОЗУ 104,1 (1270) — записано, 16 слоёв 93,7,
  все в ОЗУ с кэшем 11,5 ГБ 84,6 (795); угадывание — без него 104,9, ngram 102,5, DFlash 108,5 (64 %), MTP 68,4
  (72 %). Ручной замер (тем же `_serve`) нашёл, почему MTP проиграл: оба черновика мерились «черновик + слои модели
  в ОЗУ» (`-ngl 38`, у MTP `-ngl 35`) — у MoE-модели дешевле освободить место экспертами ещё нескольких слоёв: MTP
  с `-ngl 99 -ncmoe 18` — 120,4, DFlash с `-ngl 99 -ncmoe 15` — 127,8 против 117,9 (исправлено в 44.9).
  `--spec-draft-n-max` у DFlash: 3 (по умолчанию) 127,8 ток/с при 64 % угаданного, 7 — 131,2 при 44 % (в пределах
  шума), 15 (пример документации llama.cpp) — 88,6 при 22 % — AFO его не пишет. Пик видеопамяти всей карты (с рабочим
  столом 1,3 ГБ): без угадывания 15,1 ГБ, с DFlash 15,8, с MTP 14,8. После 44.9 «Ускорить» снова: эксперты — 13 слоёв
  в ОЗУ (96,7 → 104,1), угадывание — DFlash, эксперты 16 слоёв в ОЗУ (`draft_experts`): 105,8 → 129,8 ток/с при 71 %
  угаданного, MTP так же — 121,0 при 72 %; записано и проверено запуском, файл MTP удалён. Итог для модели после
  установки — 96,7 → 129,8 ток/с (+34 %). Стенд разобран; модель осталась на D:.
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
- 44.9 ✓ Место черновику у MoE-модели — экспертами (найдено живой проверкой 44.6): черновик, что не помещается рядом
  с моделью как она лежит, раньше получал место, уводя целые слои модели в ОЗУ (`draft_fewer`), — у Qwen3.6-35B-A3B
  голова MTP так давала 70 ток/с, а с экспертами ещё 5 слоёв в ОЗУ — 120. Сделано: `draft_variants` у модели с
  экспертами (`expert_bytes`), пока не все они в ОЗУ (`cpu_moe`) и сборка знает `--n-cpu-moe`, — вариант
  `draft_experts`: эксперты наименьшего числа первых слоёв в ОЗУ, больше нынешнего, при котором черновик помещается
  по оценке 44.1, слои модели — как были; нет такого — `draft_fewer`, как прежде. Окно: «черновик DFlash …, ещё часть
  экспертов MoE в ОЗУ». Тесты — `test_a_draft_beside_a_moe_model_takes_the_room_of_its_experts`,
  `test_where_the_experts_are_in_the_models_sheet`.
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

### Этап 47. Модели для рисования с Hugging Face

Найдено 2026-11-14 (снимок пользователя: из десяти найденных «AFO ставит её сам» — у одной). Модель для рисования —
три части: сама модель, кодировщик текста и VAE (`model_catalog.json` `pictures`); в репозитории на Hugging Face —
только первая, в разных квантованиях. AFO ставит лишь четыре своих набора, а поиск узнаёт набор только по репозиторию
его главного файла (`PICTURE_MAIN`, `search` в `hub/model_install.py`): `leejet/Z-Image-Turbo-GGUF` — «ставит сам»,
`unsloth/Z-Image-Turbo-GGUF` с той же моделью — нет. Движок не мешает: sd.cpp `master-1053` (`model.h`) знает
Qwen-Image, Qwen-Image 2.1, FLUX.1/2/3, Z-Image. `gguf.architecture` Hugging Face ненадёжен — `flux` у FLUX.1,
FLUX.2-klein и flux-3 (кодировщики разные), у трёх из десяти его нет; семейство — по тегу `base_model:quantized:` или
`base_model:finetune:` (не `adapter:` — это LoRA, и не `merge:`), он уже приходит в ответе поиска.
`AtomicChat/Qwen-Image-2.1-Turbo-Uncensored-GGUF` — кодировщик текста (qwen3vl, с mmproj), не модель для рисования.

Память — по исходникам sd.cpp `master-1053`: раскладку частей подбирает сам движок (`auto_fit`, включён по умолчанию:
`stable-diffusion.cpp` 355, `diffusion_engine.cpp` 937). По очереди модель, кодировщик, VAE — каждая на видеокарту,
если остаётся место с запасом на вычисления (2 ГБ модели, 2 ГБ кодировщику, 1 ГБ VAE: `backend_fit.cpp` 120–122),
иначе в ОЗУ, иначе на диск; от свободной видеопамяти отнимается 512 МБ, от ОЗУ — 2 ГБ или 10 % (159, 450). Модель не
на карте — кодировщик и VAE тоже в ОЗУ и уходят с карты после своей части. `--offload-to-cpu` — это
`--params-backend *=cpu` (`common.cpp` 939): все веса в ОЗУ, `auto_fit` выключен — AFO его не ставит. Размер картинки:
2 ГБ — оценка sd.cpp, настоящий буфер растёт с числом точек; что не входит, считается частями (медленнее, не отказ),
VAE при нехватке повторяет плитками.

Решения пользователя (2026-11-15): кодировщики и VAE — общие для семейства (`pictures/_shared`), без отдельного
счётчика ссылок; новые семейства — только через поиск, без карточек в «Рисовать» (установленная модель там видна, как
сейчас); 47.1 — первым: его ошибка есть уже сейчас; видео — сначала разведка, без кода (этап 48).

- 47.1 ✓ Пробная картинка установки ждёт, пока хаб рисует: llama-swap грузил её модель посреди чужой картинки (одна
  модель на видеокарте). `Visual.busy` — работник очереди картинок и видео жив; `Installer.drawing` — в тот же `_card`,
  что ждёт подбор и ответ чата (34.4). Файлы: `hub/visual.py`, `hub/model_install.py`, `hub/server.py`. Тест —
  `tests/test_together.py`: llama-swap держит картинку, установка второй модели стоит «ждёт пробного запуска»,
  пробная — после картинки. Сделано: `busy` — `_task` работника не завершён (вся очередь, картинки и видео, а не одна
  `_running`: между двумя картинками пробная не проскочит); сервер связывает `installer.drawing` с ним. Подмена
  llama-swap (`tests/test_llama_config.py`) отвечает на `/sdapi/v1/txt2img`, пока не отпущено `held`. Тест —
  `test_an_installs_trial_picture_waits_while_the_hub_draws` (на старом коде пробная прошла посреди картинки).
- 47.2 ✓ Семейство — по базовой модели. `hf.summary` — `bases`: модели тегов `base_model:quantized:` и
  `base_model:finetune:`. У набора каталога — `bases`: для каких исходных моделей верны его флаги (у
  Wan2.2-TI2V-5B-Turbo их нет: обычной TI2V-5B нужны другие шаги). Поиск: `picture` — набор по репозиторию или по
  базовой модели. Файлы: `hub/hf.py`, `hub/model_install.py`, `hub/model_catalog.json`. Тест:
  `unsloth/Z-Image-Turbo-GGUF` — `z-image-turbo`; LoRA (`adapter:`) и обычная TI2V-5B — нет. Сделано: `hf.bases`
  (`BASES`: quantized, finetune; без учёта регистра при сравнении), у строки поиска — `family` рядом с прежним
  `picture` (его окно ведёт к карточке набора — до 47.7 окно то же), `drawn` — и у узнанной по семейству. `bases`
  наборов: sdxs — `IDKiro/sdxs-512-0.9` (у `concedo/…` тегов нет), FLUX.2-klein-4B и Z-Image-Turbo — свои, видео
  Turbo — `quanhaol/Wan2.2-TI2V-5B-Turbo` (база `hum-ma/…`; обычная `Wan-AI/Wan2.2-TI2V-5B` — не его). Тест —
  `test_a_found_picture_model_is_known_by_its_family`.
- 47.3 Общие файлы. Файл — по репозиторию, пути и sha256. Новая установка сначала ищет его среди файлов установок
  (`model_installs.files`) и берёт на месте; нет — качает в `pictures/_shared`. Удаление и отмена удаляют общий файл,
  только если его не называет ни одна другая установка (кроме отменённых и удалённых): счёт — по `model_installs`, под
  замком установщика, под которым новая установка выбирает свои файлы. Четыре нынешних набора — поиском в
  установленных или переносом в `_shared`: по выбору пользователя. Файл: `hub/model_install.py`. Тест: две установки
  делят кодировщик; удаление первой его оставляет, второй — удаляет.
  ✓ Сделано (выбор пользователя 2026-11-15 — поиск: рабочие модели, их `config.yaml` и файлы не трогаются).
  `_share` — у кодировщика и VAE новой установки `at`: где файл с тем же репозиторием, путём и sha256 держит другая
  установка (в папке набора тоже), иначе `pictures/_shared/<автор>/<репозиторий>/<имя>` (имя занято файлом с другим
  sha256 — папка по его началу); `done` — файл уже на диске. `_dest` знает `at`; `_named` — установки, кроме
  отменённых и удалённых (неудачная держит файлы для «Повторить», добавленная с дисков называет файлы человека).
  `Installer._files` (asyncio.Lock): выбор файлов и запись установки — под ним, удаление (`_throw_away`: отмена,
  «убрать из списка», удаление модели, отмена посреди загрузки) — тоже; `_delete_files(found, kept)` не трогает
  названные другими, пустые папки общего файла уходят с ним до `pictures`. Место на диске — только под то, что
  качается. Кодировщик в «найденных на дисках» — настоящая модель для разговора (Qwen3-4B и т. п.): добавить её можно,
  счёт установок не даст удалить её файл. Тест — `test_a_picture_familys_encoder_and_vae_are_one_copy_for_its_models`
  (и установка до 47.3, с файлами в папке набора).
- 47.4 Поместится ли — по размерам, правилами `auto_fit` и запасами этапа 38: «быстро» — модель с запасом на
  вычисления при своём размере картинки входит в видеопамять (кодировщик и VAE могут ждать в ОЗУ); «медленнее» —
  модель в ОЗУ, на видеокарту частями; «не поместится» — все части не входят и в ОЗУ. Запас растёт с числом точек;
  пока не измерен — 2 ГБ sd.cpp на размер семейства, число из 47.9 его заменит. Файл: `hub/model_install.py`. Тест:
  Qwen-Image-2512 Q4_K_M (13,3 + 4,7 + 0,3 ГБ) на 16 ГБ — «медленнее», а не «не поместится».
- 47.5 Хаб: квантования найденной. `GET /v1/install/pictures/repo/{a}/{b}` — варианты с итогом вместе с недостающими
  частями, вердиктом 47.4 и рекомендуемым; что докачается, а что уже есть (47.3). Файлы: `hub/model_install.py`,
  `hub/api/install.py`. Тест — на подставном Hugging Face.
- 47.4–47.5 ✓ Одним шагом: вердикт без ответа хаба проверялся бы только через новое имя (Правило 3). Сделано:
  `picture_fit(model, parts, space, side, own)` — «gpu»: модель с запасом (`PICTURE_COMPUTE_GIB` 2, растёт с числом
  точек `(side/own)²`) входит в видеопамять за вычетом `SD_MARGIN_GIB` 0,5, а кодировщик и VAE — на карту с их
  запасом (`PARTS_COMPUTE_GIB` 3) или в ОЗУ; «split» — модель в ОЗУ, все части входят в ОЗУ; «cpu» — без карты; «no» —
  и ОЗУ мало; слова — `VERDICTS`. По этим правилам кодировщик места модели на карте не занимает: Qwen-Image-2512
  Q4_K_M на 16 ГБ (12,4 + 2 против 14,5 ГиБ) — на самой границе «быстро», Q5_K_M — «медленнее»; границу покажет 47.9.
  `picture_family` — набор по своему репозиторию или по `bases` (им же теперь пользуется поиск). `picture_details` —
  варианты (`hf.variants`) с `with_parts`, `coming` (без частей, что уже на диске: `_share` 47.3), `bits`, `quality`;
  рекомендуемый — самый большой «gpu» не крупнее `MAX_BITS` (без бит — не 16-битный), нет такого — самый маленький
  не «no»; `parts` с `have`; `engine` — придёт ли движок. Неизвестное семейство — 404 словами. Тест —
  `test_a_found_picture_models_variants_judged_with_the_parts_it_runs_with`.
- 47.6 Хаб: установка найденной. `POST /v1/install/pictures` `{id, repo, quant}` — главный файл набора заменён
  выбранным, дальше прежний путь (движок, блок, пробная картинка); «уже установлена» — по набору, репозиторию и
  квантованию; имя, id модели, лицензия — найденной. Файлы: `hub/model_install.py`, `hub/api/install.py`. Тест —
  сквозной в `tests/test_together.py`: найденная Z-Image берёт кодировщик набора, не скачивая его.
  ✓ Сделано: `_found_main` — вариант найденной (его семейство — набор из `id`, иначе 422 словами; нет варианта в одном
  файле — 404: sd-server берёт по файлу на часть, варианты из нескольких файлов и в 47.5 не показываются) в роли
  главного файла набора; кодировщик и VAE — через `_share` (47.3). Папка — `pictures/<автор>/<репозиторий>`, id —
  `id_base` (`draw-more-q4_0`), имя — «<модель> <квантование>», в `plan` — `found`: карточка набора в «Рисовать» и
  её установка в окне (`_job_for_set`, `picture_catalog`) найденную за себя не берут. Тест —
  `test_a_found_picture_model_installs_with_its_familys_parts_and_draws` (на старом коде: «эта модель уже
  установлена — «Draw»»).
- 47.7 Окно: у найденной — «AFO ставит её сам: ≈ 9,9 ГБ с кодировщиком — поместится в видеопамять»; щелчок — лист
  квантований, что докачается, «Установить». Файлы: `app/ui/install_view.py`, его QML, `app/hub_client.py`. Тест —
  `tests/test_ui.py` без экрана.
  - 47.7а ✓ Хаб: у найденной узнанного семейства в поиске — `fit` {gb, quant, verdict, words, parts}: модель по числу
    весов в `FIT_QUANT` вместе с кодировщиком и VAE семейства, вердикт `picture_fit` (`_picture_fits`: части — по
    одному запросу на семейство, в кэше ответов Hugging Face; не перечислились — оценки нет). Тест —
    `test_a_found_picture_model_says_what_it_needs_here`.
  - 47.7б ✓ Окно: строка найденной узнанного семейства — «AFO ставит её сам: ≈ 6,6 ГБ с кодировщиком и VAE —
    помещается в видеопамять — быстро» цветом вердикта (`picture_fit_of`; без оценки — «щелчок покажет её
    варианты»); щелчок (`openFound`) — тот же лист репозитория, но из `install_picture_repo` (47.5): в заметках —
    «семейство «…»», «докачаются кодировщик и VAE — N ГБ» или «уже есть», «и программа для рисования»; варианты —
    `variant_rows` (у `picture_details` теперь и `recommended` у каждого); «Установить» — `install_pictures_start`
    `{id: семейство, repo, quant}`. QML не менялся. Тест — `test_a_found_picture_model_installed_from_the_search`.
- 47.8 Семейства каталога (без карточек): Qwen-Image 2.1 и 2.1-Turbo (Qwen3-VL-8B, свой VAE; Turbo — `--sigmas
  qwen-image-2.1-turbo --steps 8 --cfg-scale 1`), FLUX.3 klein (Qwen3-VL-4B, VAE FLUX.2, 16 шагов), Qwen-Image и 2512
  (Qwen2.5-VL-7B и VAE: `Comfy-Org/Qwen-Image_ComfyUI`, откуда его советует sd.cpp, Hugging Face не отдаёт — найти
  другой), FLUX.1 dev и schnell (clip_l, t5xxl, ae), Z-Image base, FLUX.2-klein-9B и base. Флаги — из документации
  sd.cpp той же сборки. Тест: у каждого — роли, которые знает sd-server, и разные имена файлов.
  ✓ Сделано: 11 семейств с `"card": false` (`picture_catalog` их не показывает, поиск и установка — знают):
  `qwen-image-2.1` и `-turbo` (Qwen3-VL-8B, VAE 2.1; Turbo — главный файл unsloth: у leejet его нет), `flux3-image-klein`
  и `-base` (Qwen3-VL-4B, VAE FLUX.2 — общий с `flux2-klein-4b`), `qwen-image` (база — и Qwen-Image, и 2512;
  Qwen2.5-VL-7B; VAE — `Comfy-Org/Qwen-Image`: `…_ComfyUI` переименован, Hugging Face отвечает 307), `flux1-dev` и
  `flux1-schnell` (clip_l, t5xxl Q8_0 city96, ae — общий с `z-image-turbo`), `z-image` (Qwen3-4B-Instruct-2507 и ae —
  общие с Turbo), `flux2-klein-9b`, `flux2-klein-base-4b`, `flux2-klein-base-9b` (Qwen3-8B или Qwen3-4B, VAE FLUX.2).
  Шаги у каждого — явно: окно шлёт `steps` из флагов, без `--steps` было бы 4 (у sd.cpp по умолчанию 20). Лицензии —
  исходных моделей: `qwen-research` у Qwen-Image 2.1, некоммерческие у FLUX.1-dev и FLUX.2-klein-9B/base-9B,
  `flux-kommunity-license` у FLUX.3, Apache-2.0 у остальных. Все файлы есть на Hugging Face с sha256 (проверено
  2026-11-15: от 5,3 ГБ у klein-base-4B до 18,0 у Qwen-Image). Тест — `test_the_catalogs_picture_families`: девять из
  десяти строк поиска пользователя узнаются, AtomicChat (кодировщик) — нет.
- 47.9 Живая проверка на RTX 5080 — по слову пользователя и после `nvidia-smi`: картинка каждого нового семейства; по
  журналу sd-server (`compute buffer size … peak`) — запас на вычисления при 512², 1024², 1536² для 47.4. Заодно:
  берёт ли sd-server через `/sdapi` `--sigmas` из командной строки (Turbo 2.1), читает ли веса unsloth и city96, и
  где на самом деле граница «быстро» у Qwen-Image-2512 Q4_K_M на 16 ГБ.
- 47.9 ✓ Сделано 2026-11-15 на стенде (свой хаб на 8798, свой llama-swap на 8799, модели — в `D:\AFO-stand47`, движок —
  с GitHub, как на новом компьютере). Через поиск, как кнопкой окна, поставлены Qwen-Image 2.1 Turbo Q4_K_M (unsloth),
  FLUX.3 klein Q4_K (leejet), Qwen-Image-2512 Q4_K_M (unsloth). Каждая нарисовала 512², 1024², 1536² (seed 47), надпись
  на табличке везде верна. Время и пик видеопамяти: Turbo — 8,4 с (с загрузкой) / 6,2 / 15,9, все части на карте
  (8,9 ГБ весов), пик 15,6 ГБ; klein — 5,0 / 7,0 / 21,2 с, на карте (5,9 ГБ), пик 7,3 / 9,8 / 14,1 ГБ; 2512 — 17,3 /
  36,7 / 124 с: auto-fit положил все веса в ОЗУ (модель 12 683 МиБ + запас 2 048 > бюджета 14 473), карта считает
  кусками. `--sigmas` работает: `/sdapi/v1/txt2img` начинает с параметров командной строки (`routes_sdapi.cpp`:
  `gen_params = *default_gen_params`), запрос меняет только шаги и cfg; `capabilities` живого sd-server — 9 sigmas
  Turbo. Веса unsloth читаются; city96 (FLUX.1) не качались — не проверены. Найдено и исправлено:
  1. Hugging Face переименовал `Comfy-Org/flux2-klein-4B` в `Comfy-Org/Flux-2-Klein-4B` и отвечает на старое имя 307;
     запросы хаба к API по переадресации не шли — не ставились ни FLUX.3 klein, ни старый набор FLUX.2 klein 4B.
     `hf._get` идёт по ней (httpx оставляет токен только на хосте Hugging Face); имя в каталоге прежнее — по нему
     узнаются общие файлы. Тест — `test_a_picture_set_whose_repository_hugging_face_renamed_installs`.
  2. У найденной без лицензии на странице (FLUX.3 klein от leejet) окно вариантов показывает лицензию семейства, как
     уже делала установка.
  3. Установки, вставшие в очередь до того, как первая вписала движок в конфиг, несли его архивы, и следующая
     распаковывала их поверх папки, из которой работал sd-server другой модели: Windows не дала (`ggml-base.dll`:
     Permission denied), 2512 не встала. Движок, который конфиг уже называет, больше не распаковывается; скачан он
     один раз — архивы лежат в общей `engines/downloads`. Тест —
     `test_a_picture_engine_the_config_names_is_not_unpacked_again` («Повторить» при занятых файлах движка).
  4. Оценка 47.4. Auto-fit (`backend_fit.cpp`, `compute_plan`) ставит модель на карту, если рядом остаются запас 2 ГиБ —
     один на все части, они считают по очереди — и самый крупный тензор кодировщика (staging, у Qwen ≈ 0,5 ГиБ). Не
     встала модель — не встаёт ничего: кодировщику на карте нужно место под её куски. Раскладка — при запуске, до
     размера картинки; вычисления потом режутся под остаток: размер меняет время, а не места. Было: запас рос с
     `(side/own)²`, без staging — 2512 Q4_K_M «быстро». Стало: `picture_fit(model, parts, space)`,
     `PICTURE_STAGING_GIB` 0,5, `PARTS_COMPUTE_GIB` убран; 2512 Q4_K_M — «медленнее», рекомендован Q4_K_S (11,5 ГиБ).
     Строка поиска — оценка типичного 4-битного варианта (`FIT_BITS`): у 2512 там «быстро», точный вердикт по
     квантованиям — в списке вариантов.
  Флаги каталога взяты из документации sd.cpp; в его рецептах (`examples/server/recipes`) cfg у четырёх иной:
  Qwen-Image 2.1 — 1 против 6, Qwen-Image — 4 против 2,5, FLUX.3 klein base — 4 против 3,5, Z-Image — 4 против 5.
  2512 с 2,5 рисует хорошо — оставлено.
- Риски: веса city96 и unsloth документация sd.cpp называет рабочими — подтвердит только картинка; дообучению
  (`finetune:`) могут быть нужны другие шаги, чем базовой, — покажет пробная картинка; квантование дообучения (его
  база — не модель семейства) не узнаётся — позже, одним запросом к Hugging Face; общий файл держит sd-server другой
  модели — удаляется после `_withdraw`, с повторами, как сейчас.

### Этап 48. Видео с Hugging Face и «Студия»

Найдено (разведка 2026-11-15). Видео в AFO — один набор, Wan2.2-TI2V-5B-Turbo, и всё вокруг подогнано под него:
размеры 832×480 и 1280×704, 24 к/с, кадров 4k+1 (`common/picture_sizes.py`); пороги «видео здесь / медленно / в
облаке» — замеры этой модели (`common/hardware.py`: `VIDEO_GIB`, `TRY_VIDEO_GIB`, `VIDEO_RAM_GIB`), а не размер модели;
проба — 480×272, 9 кадров (`visual.trial_video`). `Visual._run_video` шлёт `vid_gen` через `/upstream/<id>/sdcpp/v1` с
`sample_params` из флагов блока, остальное sd-server берёт из командной строки (как `/sdapi`, 47.9). `sd_block` пишет
роль файла флагом: `high-noise-diffusion-model`, `clip_vision`, `embeddings-connectors`, `audio-vae` лягут без правок.
- Поиск «Видео» — только тег `text-to-video` (`hf.TASKS`): Wan2.2-I2V-A14B (1,1 млн загрузок), Wan2.1-I2V и FLF2V не
  видны.
- В одном репозитории — несколько моделей: unsloth/LTX-2.3 — dev и distilled (папки), unsloth/MiniMax-H3 — fl2va,
  ref2va и кодировщик, QuantStack Wan2.2 A14B — пары HighNoise и LowNoise. `hf.variants` (`by_quant`) оставляет по
  файлу на квантование и молча выбрасывает остальные.
- У семейства свой ролик (рецепты sd.cpp, `examples/server/recipes`): Wan 16 к/с, TI2V-5B, LTX и MiniMax — 24 (MiniMax —
  только 24); кадров у Wan и Hunyuan 4k+1, у LTX 8k+1; размеры — от 832×480 до 1344×768.
- Режимы sd-server по HTTP (`common.cpp` 2407–2418): `init_image`, `end_image`, `control_frames`, `ref_images`,
  `ref_videos`, `ref_audios`. Из кадров sd.cpp сам готовит только контуры (Canny, `docs/image_preprocessing.md`) —
  глубины и скелета нет. Wan-Animate и Wan2.2-Fun-Control в sd.cpp не нашлись.
- Звук: LTX-2.3/2.5 и MiniMax-H3 снимают со звуком (`--audio-vae`), AFO просит WebP — звук теряется. Плеера со звуком
  в окне нет: AFO ставит PySide6-Essentials, QtMultimedia — в PySide6-Addons.
- Размеры (Q4): TI2V-5B 3,4 ГБ; Wan2.1 1.3B — 1,0; Wan2.2 A14B — 9,7 + 9,7; LTX-2.3 — 14,3 + Gemma-3-12B
  (`unsloth/gemma-3-12b-it-qat-GGUF`) + коннекторы 2,3; LTX-2.5 — 13,6 + Gemma-4-12B (у unsloth только bf16, 26,3 ГБ;
  GGUF — чужой, `elix3r/gemma4-12b-with-proj-ltx-2.5-GGUF`); MiniMax-H3 — 11,4 + кодировщик 18,2 + VAE 5,2;
  HunyuanVideo 1.5 — `jayn7/…`, T2V и I2V, 480p и 720p — отдельные репозитории.
- Окно: картинки и видео — в окошке «Нарисовать» поверх чата (`VisualSheet.qml`, до 760 точек): модель не выбрать
  (только «Быстрее ↔ Качественнее»), мест под кадры нет, очереди нет, видео — WebP без перемотки, последние — значки.

Решения пользователя (2026-11-15): «Студия» — свой экран, четвёртый режим боковой панели рядом с «Чаты», «Проекты»,
«Агент»: слева настройки (модель, режим, кадры), в центре холст или плеер, внизу очередь и галерея. Семейства — Wan,
LTX-2.3/2.5, HunyuanVideo 1.5, MiniMax-H3. Сначала без звука. Режимы — по тексту, по картинке, по первому и последнему
кадру; по видео (VACE по контурам), по образцам и по звуку (S2V) — этап 49.

- 48.1 Поиск «Видео» — оба тега, `text-to-video` и `image-to-video`: два запроса, найденное слито по имени, порядок —
  как просили. Файлы: `hub/hf.py`, `hub/model_install.py`. Тест — подставной Hugging Face: модель только с
  `image-to-video` находится. ✓ Сделано: `TASKS["video"]` — два тега; `hf.search` спрашивает по запросу на тег
  одновременно (`asyncio.gather`), сливает по имени и сортирует по полю Hugging Face (`SORTS`; для слияния оно просится
  в `expand[]`). `hub/model_install.py` менять не пришлось. Тест —
  `test_the_video_search_finds_models_by_text_and_by_picture`.
- 48.2 Модель внутри репозитория. Вариант — по модели: папка и имя без квантования (dev и distilled, fl2va и ref2va);
  кодировщик в репозитории модели — не её вариант; пара HighNoise и LowNoise одного квантования — один вариант, файлы
  с ролями `diffusion-model` и `high-noise-diffusion-model`. У семейства — каким файлам репозитория оно соответствует.
  Файлы: `hub/hf.py`, `hub/model_install.py`. Тест — подставной репозиторий «две модели и кодировщик» и пара.
  Разделён (Правило 2): 48.2а — хаб, 48.2б — окно ставит вариант его семейства.
  - 48.2а ✓ `hf.model_of` — путь без квантования и слова половины (`NOISE`: «HighNoise», «_high_noise_»);
    `hf.picture_variants(files, has, lacks)` — вариант одной модели одного квантования: файл (`diffusion-model`) или
    пара (низкая — модель, высокая — `high-noise-diffusion-model`; половина без пары не предлагается), разбитые на
    части — нет; осталось несколько моделей — в `quant` и имя модели. `PICTURE_MAIN` знает вторую половину. У
    семейства каталога — `names` (`has`, `lacks`); `picture_families` — все семейства репозитория (свой главный
    репозиторий или база), `picture_family` — первое; `family_variants`. `picture_details` — варианты каждого
    семейства с его частями (`family`, `name`, `files`; бит на вес — у половины), `families`; рекомендуемый — сам
    вариант, не квантование. `_found_main` — файлы варианта списком. Тест —
    `test_a_found_video_model_among_the_others_of_its_repository`.
  - 48.2б ✓ Окно: `variant_key` — квантование, у варианта семейства — с семейством; строка варианта — `key`,
    `variant`, `family`, подпись «LTX-2.3 distilled · Q4_K_M», когда семейств два; «Установить» (`install(key)`) ставит
    вариант как его семейства; загрузка видна на своём варианте (`_job_for` — по `plan.set`). Файлы:
    `app/ui/install_view.py`, `app/ui/qml/InstallSheet.qml`. Тест —
    `test_a_found_repository_of_two_families_in_the_window`.
- 48.3 Свой ролик у семейства: в каталоге `video` — к/с, шаг кадров (4 или 8), размеры обычный и HD; `Visual.video` и
  проба берут их вместо общих TI2V-5B. Файлы: `common/picture_sizes.py`, `hub/visual.py`, `hub/model_catalog.json`.
  Тест: у LTX кадров 8k+1, у Wan2.1 — 16 к/с. ✓ Сделано: у семейства в каталоге — `clip` (`fps`, `step`, `grid`,
  `sizes`, `hd`), чего нет — TI2V-5B (`picture_sizes.CLIP`, `clip_of`); установка кладёт его в план, `installed` отдаёт
  его у видеомодели (поставленная раньше — TI2V-5B). `video_size(shape, hd, clip)`, `frames_for(seconds, fps, step)`,
  `trial_size(clip)` — проба 480×272 до сетки семейства (у сетки 32 — 480×288), к/с семейства, 9 кадров (и 4k+1, и
  8k+1). Строка модели в окне — по её `clip` (`app/ui/visual_view.py`). Каталог наполнит 48.6. Тест —
  `test_a_video_familys_own_clip` и проба пары в `test_a_found_video_model_among_the_others_of_its_repository`.
- 48.4 Память видео — по размерам, правилами auto-fit sd.cpp (47.9), с парой экспертов (как sd.cpp держит вторую
  половину — по исходнику). Пороги `pictures_for` — для своего набора, найденные — по размерам. Файл:
  `hub/model_install.py`. ✓ Сделано. По исходнику (`backend_fit.cpp` `classify_tensor`): auto-fit считает моделью
  только `model.diffusion_model.`, вторая половина грузится как `model.high_noise_diffusion_model.`
  (`diffusion_engine.cpp` 700) в тот же модуль diffusion и в план не входит; у модуля — вытеснение весов
  (`device_residency_manager.h`: `evict_compute_backend_params`) — на карте половина, которая считает. `variant_fit`:
  у пары на карте — большая половина, вторая ждёт в ОЗУ с частями; окно вариантов судит так. Найденное видео ставится
  по размерам: отказ — только «не поместится» (`InstallError`), пороги TI2V-5B (`picture_verdict`) — только для своего
  набора. Подтвердит живая проверка 48.8 (Wan2.2 A14B на 16 ГБ). Тест — `test_a_found_video_model_judged_by_its_sizes`.
- 48.5 Режимы в хабе: видео по картинке и по двум кадрам — источник как у картинок (`from_job`, `from_blob`,
  `from_png`) и последний кадр, в `vid_gen` — `init_image` и `end_image`; у семейства — какие режимы оно умеет (по
  рецептам); модель «только по картинке» (Wan2.2-I2V-A14B, Wan2.1-I2V) без кадра не предлагается, её проба — с кадром.
  Файлы: `hub/visual.py`, `hub/api/visual.py`, `hub/model_install.py`. ✓ Сделано: `POST /v1/visual/videos` —
  `from_job`/`from_blob`/`from_png` (первый кадр, как у «По картинке») и `last_job`/`last_blob`/`last_png`
  (последний); оба — PNG рядом с заданием (`source`, новый `last_frame`, миграция 053); форма ролика — по первому
  кадру (`picture_sizes.shape_of`); в `vid_gen` — `init_image` и `end_image` (base64, движок сам обрежет). У семейства
  каталога — `modes` (`text`, `picture`, `frames`), установка кладёт их в план, `installed` отдаёт (поставленная
  раньше — только `text`); без модели в запросе — первая, что так снимает; не умеет — отказ словами (`MODE_WORDS`);
  последний кадр без первого — отказ. Проба модели только по картинке — с кадром AFO (`visual.trial_frame`: небо над
  снегом, PNG размера пробы). Тест — `test_a_video_by_a_picture_and_by_two_frames`, проба — в
  `test_a_found_video_model_among_the_others_of_its_repository`.
- 48.6 Семейства в каталог (как 47.8: `card: false`, флаги — по рецептам sd.cpp, sha256 у всех файлов): Wan2.1 T2V 1.3B
  и 14B, I2V 480p и 720p (`clip_vision`), FLF2V 720p; Wan2.2 TI2V-5B (обычная), T2V A14B, I2V A14B; LTX-2.3 и 2.5 — dev
  и distilled; HunyuanVideo 1.5 T2V и I2V; MiniMax-H3 fl2va — без `--audio-vae`. Общие части: umt5, VAE Wan2.1,
  clip_vision_h. Тест — `test_the_catalogs_video_families`. ✓ Сделано (скрипт `videos48.py` сессии): 15 семейств,
  у Turbo — `modes` (TI2V снимает и по картинке). Части: umt5 Q4_K_M (city96, общий с Turbo), VAE Wan2.1 и
  `clip_vision_h` (Comfy-Org/Wan_2.1), VAE Wan2.2 (общий с Turbo); LTX-2.3 — Gemma-3-12B qat UD-Q4_K_XL (unsloth),
  коннекторы и видео-VAE своей модели (dev или distilled); LTX-2.5 — Gemma-4-12B с проекцией Q4_K_M (elix3r), видео-VAE
  unsloth; HunyuanVideo 1.5 — Qwen2.5-VL-7B Q4_K_M (общий с Qwen-Image), byT5 и VAE (Comfy-Org/HunyuanVideo_1.5);
  MiniMax-H3 — кодировщик и VAE из своего репозитория. Флаги — рецепты sd.cpp (у A14B — `--high-noise-cfg-scale`,
  `--high-noise-sampling-method`, `--moe-boundary`; `--high-noise-flow-shift` в командной строке нет); шаги — у всех
  явно (MiniMax — 20). `names`: LTX — `dev` / `distilled` (distilled-1.1 — вариант distilled), Hunyuan — `t2v` / `i2v`
  без `distilled` (cfg-distilled — другие флаги), MiniMax — `fl2va` (ref2va — этап 49). Сетки: у LTX 32 (768×512,
  1216×704), у остальных 16. Лицензии — карточек исходных моделей: Apache-2.0 у Wan,
  `ltx-2-community-license-agreement`, `ltx-2.x-…`, `tencent-hunyuan-community`,
  `minimax-h3-community-license-agreement`. Проверено на Hugging Face
  (`verify48.py`): все файлы есть с sha256, главный файл каждого семейства — среди его вариантов (`family_variants`), у
  MiniMax кодировщик — не вариант; объёмы с частями — от 4,9 ГБ (Wan2.1 1.3B) до 34,8 (MiniMax-H3). В тесте 47.8 —
  правила 48.2: семейства одной базы различаются `names`, у пары — два главных файла; обычная TI2V-5B — своё
  семейство, не Turbo.
- 48.7 «Студия», частями (Правило 2); тесты — настоящий QML без экрана, как `tests/test_ui_bounds.py`:
  - 48.7а Экран: режим «Студия» в боковой панели, три зоны; «Нарисовать» в поле ввода чата открывает Студию с текстом;
    окошко `VisualSheet` уходит. Файлы: `app/ui/qml/StudioView.qml` (новый), `app/ui/qml/Main.qml`,
    `app/ui/qml/Sidebar.qml`, `app/ui/controller.py`. ✓ Сделано: `MODES` — и `studio`; вкладка «Студия» (Ctrl+4) — на
    узкой боковой панели (меньше 340 точек) подпись у выбранной, у остальных значок и подсказка с именем;
    `StudioView`: слева «Что сделать» (описание, картинка или видео, форма, длина, «Быстрее ↔ Качественнее», «По
    картинке…», кнопка, модель), в центре ход и холст, под ним действия (переносятся), внизу «Последние» лентой;
    `Visual.load()` — когда экран показан. `openUi("draw")` (фишка «Нарисовать» в поле чата, картинка чата) — в Студию;
    заголовок окна — «Студия». Имена элементов прежние (`visualPrompt`, `visualDraw`…). `VisualSheet.qml` удалён, тест
    длинного заголовка листа — на `budgetsSheet`. Тест — `test_drawing_a_picture_in_the_window` (переход в Студию,
    описание из фишки), снимок без экрана — скрипт `shot48.py` сессии.
  - 48.7б Настройки: модель (установленные картинки и видео; «Авто» — нынешнее «Быстрее ↔ Качественнее»), режим и
    места под кадры, форма, длина, HD. Файлы: `app/ui/visual_view.py`, `StudioView.qml`. ✓ Сделано: `modelChoices`
    («Авто» и установленные этого вида; у видео в пояснении — как она снимает), `setModel`, фишка «Модель: …» с
    меню; выбранная модель рисует сама (у картинки — и по образцу, если умеет), «Быстрее ↔ Качественнее» у картинок —
    только при «Авто»; смена картинка ↔ видео — снова «Авто». У видео `videoModes` — как снимает выбранная (при «Авто» —
    любая из установленных), переключатель «Описание | Кадр | Два кадра» (короткие слова: колонка узкая), неумеющая —
    к ближнему (два кадра → кадр); при «Авто» снимает первая, что умеет. Первый кадр — тот же образец «По картинке»
    («Первый кадр…»; выбран при «Описании» — видео по нему), без «насколько менять»; последний — `chooseLast`,
    `clearLast` (`image://pictures/last/…`). Без первого кадра «Снять видео» не идёт — «Выберите первый кадр». Тест —
    `test_the_studio_chooses_its_model_and_films_by_two_frames`.
  - 48.7в Очередь и галерея: задания с ходом и отменой; картинки и видео, по щелчку — на холст. ✓ Сделано:
    «Нарисовать» / «Снять видео» во время чужого задания (кнопка «В очередь», Enter) ставит новое в очередь хаба,
    холст остаётся за первым (`_send`); `queue` — задания в очереди и в работе, по порядку хаба (`_keep`: новое —
    первым, известное — на своём месте), у каждого ход (`visual.progress` — по каждому, не только показанному), ✕ —
    `cancelJob`; лента карточек над «Последними». Галерея — 30 последних (`RECENT`), «Всё | Картинки | Видео»
    (`setGallery`), у видео — значок; надпись на пустом холсте — по виду показанного задания (`shownVideo`). Тест —
    `test_the_studios_queue_and_gallery`.
  - 48.7г Плеер видео без звука: кадры WebP с паузой и перемоткой. ✓ Сделано: под холстом — «Пауза»/«Дальше», ползунок
    кадра и время «0:02 / 0:03» по к/с задания (`videoFps`). Qt читает WebP и GIF только вперёд: перемотка — по
    кадрам, что хранит `AnimatedImage` (`cache`), а хранить можно короткий ролик — до 200 МБ кадров
    (`SEEKABLE_BYTES`: 2 с HD, не 4 с); длиннее — ползунок не двигается, пауза и время есть. Тест —
    `test_the_studios_video_player` (GIF из трёх кадров собран вручную: Qt не пишет анимацию, Pillow нет).
  - 48.7д Найденные видео — в поиске и окне вариантов, как 47.7 (`app/ui/install_view.py`, `InstallSheet.qml`).
    ✓ Сделано: путь 47.7 общий (строка поиска с оценкой, окно вариантов, «Установить» — семейству варианта, 48.2б); хаб
    отдаёт `modes` у репозитория и у варианта, окно пишет «видео: по картинке · по двум кадрам», вариант-пара — «две
    половины — на карте по одной» (`VIDEO_MODES` — из `visual_view`). `InstallSheet.qml` менять не пришлось. Тест —
    `test_a_found_video_model_in_the_window`, `modes` хаба — в
    `test_a_found_video_model_among_the_others_of_its_repository`.
- Ревью этапа 48 (2026-11-15, только чтение, 16 неотправленных коммитов): 11 находок и «неудобно». До 48.8, по слову
  пользователя, — 1, 2, 6, 7, 8 и ход видео; на каждую сначала тест, который падает на нынешнем коде (Правило 3): не
  падает — находка неверна, не чинится. Решения пользователя: предел видео — «нет хода 15 мин» (число поправит 48.8),
  у модели без шага — 3 ч на всё и прошедшее время в окне; ✕ во время видео — по-прежнему выгрузка модели; замок карты —
  свой модуль, не `Visual`.
  - Р1 Половины с одним именем в разных папках (`HighNoise/m-Q4_K_M.gguf` и `LowNoise/m-Q4_K_M.gguf`; dev и distilled
    одного репозитория) ложились в один файл: `_dest` — по имени, папка одна на репозиторий, файл того же размера —
    «скачан» без sha256. Файл найденной модели — с пометкой `tree`: лежит по своему пути в репозитории внутри папки
    установки; прежние установки — как были; путь с `..` или абсолютный — по имени. Файл: `hub/model_install.py`. ✓
    Сделано: `_found_main` ставит `tree`, `_delete_files` убирает и опустевшие папки репозитория. Тест —
    `test_two_halves_of_one_name_in_two_folders_are_two_files`.
  - Р7 «Продолжить» оборванной загрузки отказывал «не хватает места»: нужно было место под весь недокачанный файл, а
    его `.part` уже занял его (`hf._pieces`: `truncate`). Нужное — без байтов `.part` на диске. Файл:
    `hub/model_install.py`. ✓ Сделано: `_left`; проверки файлов в `_download` — в потоке. Тест (настоящий диск: NTFS
    занимает место при `truncate`) — `test_a_paused_download_goes_on_where_its_part_already_took_the_room`.
  - Р2 Проба не держала карту: `_card` ждал один раз, между ним и пробой задание Студии и чат успевали взять карту. Замок
    карты — `hub/gpu_card.py` (после 48.8 — один хозяин карты, 48.9): кто держит и держит ли от чатов. Проба берёт его
    там же, где проверяет подбор и чат, — без `await` между проверкой и взятием, щели нет; держит до конца пробы;
    работник Студии — на каждое задание. Взаимного ожидания с чатом нет: установщик ждёт чат, только пока не держит
    карту, чат ждёт карту, только пока её держат. Проба чат-модели держит карту от Студии, но не от чатов: её запрос
    идёт через тот же `gpu_gate` и ждал бы сам себя. Разделён: 2а — Студия и проба (`hub/gpu_card.py`,
    `hub/model_install.py`, `hub/visual.py`, проводка в `hub/server.py`), 2б — чат ждёт пробу картинки или видео со
    своими словами (`hub/gateway/openai_compat.py`). ✓ 2а: `GpuCard` (`try_take`, `take`, `give`, `free`, `gate`) —
    один на хаб (`app.state.card`); `Installer._card` — `tuning() or chat_busy() or not card.try_take(TRIAL)`, отдаёт в
    `finally` `_run`; `installer.drawing` ушёл; работник `Visual` берёт карту (`STUDIO`, видео — и от чатов) до чтения
    задания заново (✕, пока ждало) и отдаёт по его концу, видео — как только движок встал. Тест —
    `test_an_installs_trial_video_holds_the_card_from_the_studio` (видео Студии ждёт, пока пишется конфиг пробы и пока
    она снимает). ✓ 2б: проба модели картинок и видео берёт карту и от чатов (`_card(chats=…)`); `GpuCard.words` — кто
    держит, словами, провайдер llama-swap — `gate_words`. Тесты —
    `test_a_chat_waits_for_an_installs_trial_video_and_the_trial_for_a_chat_never_both` (ответ чата, потом проба, потом
    следующий чат — с «пробный запуск») и `test_a_chat_models_trial_does_not_wait_for_itself` (за 20 с).
  - Ход видео: в `vid_gen` — `preview: proj` (шаг sd-server отдаёт только в `preview` опроса, `async_jobs.cpp` 249;
    проекция латентов в цвет, без VAE); шаг — в `visual.progress`, у пары — сквозной через два прохода (`pass`); у модели
    без проекции (HunyuanVideo 1.5: 32 канала латентов, `diffusion_engine.cpp` 2052–2120) — прошедшее время. Окно:
    «снимаю: шаг N из M» или «снимаю: 12:34». Файлы: `hub/visual.py`, `app/ui/visual_view.py`. ✓ Сделано: `_Steps`
    (проходы `pass`, всего — `--steps` и `--high-noise-steps`: `high_steps` в `installed`), `_said` — шаг или прошедшее
    время (`seconds`, без шага — раз в `VIDEO_CLOCK_S`); окно — `filming`, `clock_text` (холст и карточка очереди).
    Тесты — `test_a_videos_steps_shown_as_it_films`, `test_the_studio_shows_a_videos_steps_or_its_time`.
  - Р6 По пределу времени движку не говорили остановиться: sd-server снимал дальше, следующее видео вставало в его
    очередь. Отмена — `POST …/jobs/<id>/cancel` (только если модель в `/running` llama-swap: `/upstream` загрузил бы её
    ради отмены), «cancelled» ждётся до `CANCEL_WAIT_S`, иначе модель выгружается; карта — после. Предел — «нет хода»
    (`VIDEO_STALL_S`: статус, проход, шаг, место в очереди), у модели без шага — 3 ч на всё; у пробы — так же. Ошибка
    движка — `message` его объекта `{code, message}`. Файл: `hub/visual.py`. ✓ Сделано: `halt` (и у `trial_video`),
    `_engine_error`; `VIDEO_STALL_S` = 15 мин — после первого шага, до него и у модели без шага — `VIDEO_TIMEOUT_S` =
    3 ч от начала. Тесты — `test_a_video_that_stops_moving_is_stopped_in_the_engine` (медленное, но идущее не
    обрывается; вставшее отменено в движке; неотменяемое выгружено; ошибка движка — словами),
    `test_a_trial_video_past_its_time_is_stopped_in_the_engine`.
  - Р8 Половина, удалённая вручную, — ролик «готово» (sd.cpp лишь предупреждает, `diffusion_engine.cpp` 698–702): перед
    `vid_gen` файлы установки на месте и своего размера, нет — «не вышло» со словами. Файлы: `hub/visual.py`,
    `hub/model_install.py`, `hub/server.py`. ✓ Сделано: `Installer.missing` (через `_dest`, в потоке) —
    `Visual.missing`; «Докачать» — не делали. Тест — `test_a_video_whose_half_was_deleted_by_hand_fails_in_words`.
- 48.8 Живая проверка на RTX 5080 — по слову пользователя и после `nvidia-smi`: Wan2.2 TI2V-5B, Wan2.2 I2V A14B (по
  картинке), LTX-2.3 distilled; MiniMax-H3 — по отдельному слову (≈ 35 ГБ). Записать сюда: строку «auto-fit plan»
  журнала sd-server; что обе половины A14B загрузились (журнал sd-server); пик видеопамяти при смене половин; время
  ролика; самый долгий промежуток без шага (загрузка половины, VAE) — для `VIDEO_STALL_S`; проба FLF2V с одним кадром.
  Стенд (2026-11-15): хаб 8798 и свой llama-swap 8799 (пустой конфиг, движок с GitHub), модели — `D:\AFO-stand48`;
  окно и хаб пользователя остановлены на время проверки, его llama-swap выгружен. Стенд целиком (данные хаба,
  движки, конфиг, скрипты stand.py, one.py, film.py, vram.py — nvidia-smi раз в 0,5 с, window.py, stand_stop.py) —
  `D:\AFO-stand48\stand`, как запускать и останавливать — его `README.md`; для перепроверок 48.9, 48.10 и плеера 48.12.
  - Wan2.2 TI2V-5B (QuantStack Q8_0 — рекомендованный: 9,7 ГиБ с частями, «помещается — быстро»): загрузка 10,6 ГБ за
    9 мин, проба 12,4 с. auto-fit plan: CUDA0 free 14985 MiB, budget 14473; DiT 5150 MiB + запас 2048, Conditioner
    (umt5) 3479 + 2048, VAE 1344 + 1024 — всё `params CUDA0`; итого 9977 МБ в видеопамяти, ОЗУ 0. Ролик по умолчанию
    832×480, 49 кадров, 20 шагов: 43,6 с (семплинг 26,3 с, декодирование VAE 13,4 с); шаги по ~1 с, самый долгий
    промежуток без шага — 16,8 с после последнего (VAE и WebP), первый шаг — через 2 с. Видеопамять: 11,6 ГБ с
    загруженной моделью, пик 15,5 ГБ из 16,3. Проба 480×272 выровнена движком до 480×288 («multiple=32»): у TI2V-5B
    сетка 32, в каталоге её нет — проба шлёт размер не по сетке (у Turbo — то же).
    HD (1280×704, 49 кадров, 20 шагов; модель грузилась заново): 166,5 с — семплинг 70,1 с (шаг ~3,5 с), VAE 87,3 с.
    VAE движок резал плитками: целиком не влез («need 24590 MB device / 23566 MB budget, available 14729 / 14473»),
    с временными плитками — 29072 MB, затем «Reducing VAE decode tiles from 1280x704 to 640x352» и «retrying with
    spatial+temporal tiling» (`backend_fit.cpp` 526–530) — так и декодировал. Первый шаг — через 8,3 с (загрузка),
    самый долгий промежуток без шага — 95,2 с после последнего (VAE плитками). Видеопамять: пик 12,6 ГБ из 16,3 —
    ниже, чем у 832×480 (плитки меньше целого декодирования).
  - Wan2.2 I2V A14B (QuantStack Q6_K — рекомендованный: 26,0 ГиБ с частями, «помещается — быстро»): загрузка 26 ГиБ
    за 10,4 мин (umt5 уже был), проба (480×272, 9 кадров) 56,8 с. Обе половины загрузились — «loading diffusion model
    … LowNoise-Q6_K.gguf» и «loading high noise diffusion model … HighNoise-Q6_K.gguf» — и обе отработали
    («sampling(high noise) completed», «sampling completed»). auto-fit plan: CUDA0 free 14985 MiB, budget 14473; DiT
    11447 MiB + запас 2048 → `params CUDA0`; Conditioner (umt5) 3479 + 2048 и VAE 242 + 1024 → `params cpu` (считают
    на карте): `--backend "diffusion=CUDA0,te=CUDA0,vae=CUDA0" --params-backend "te=cpu,vae=cpu"`; итого 26633 МБ
    (VRAM 22911 — обе половины, на карте по очереди; ОЗУ 3722). Ролик по первому кадру 832×480, 33 кадра (16 к/с),
    с холодной загрузки: 599,7 с — высокая половина 20 шагов за 293,5 с, низкая 20 шагов за 290,7 с, VAE 5,7 с (без
    плиток). Шаг ~14 с; часть весов половины подгружается на каждом шаге (~11 раз по 0,2 с). Первый шаг — через 31,6 с
    (обе половины, umt5 на процессоре, кодирование кадра); до него окно показывало прошедшее время. Смена половин —
    22,2 с между 20-м и 21-м шагом (обычный — 14 с), пик видеопамяти при ней — 15,7 ГБ из 16,3, самый высокий за
    ролик (в остальном ~15,1). Самый долгий промежуток без шага — 31,6 с, до первого. Журнал ролика — последние 100 КБ
    (столько держит llama-swap), строка auto-fit — из журнала пробы.
  - Найдено при 48.8, не чинилось: у пары без `--high-noise-steps` (A14B ставится без него) sd.cpp делает `--steps` в
    каждом проходе — 20 + 20, а хаб считает всего 20 (`installed`: `high_steps` — 0, `_run_video`): окно показывает
    «шаг 20 из 20» на середине ролика, затем «шаг 21 из 40» — ещё ~5 мин.
  - LTX-2.3 distilled (unsloth, `ltx-2.3-22b-distilled-1.1 Q4_0` — у distilled хаб не советует ни один, взят тот же
    квант, что советует у `-dev`: 22,3 ГиБ с частями, «помещается — быстро»): загрузка 22,3 ГиБ за 15 мин (последние
    10% — 0–2 МиБ/с со стороны Hugging Face), проба (480×288, 9 кадров) 29,5 с. auto-fit plan: CUDA0 free 14985 MiB,
    budget 14473; DiT 12127 MiB + 2048, Conditioner (gemma-3-12b) 9286 + 2048, VAE 1384 + 1024 — все `params cpu`,
    считают на карте: `--params-backend "diffusion=cpu,te=cpu,vae=cpu"`; итого 23613 МБ в ОЗУ, 0 в видеопамяти. Ролик
    по тексту 768×512, 49 кадров, 8 шагов, с холодной загрузки: 62,4 с — gemma 10,1 с, чтение DiT 9,9 с, семплинг
    44,0 с (шаг ~4 с), VAE 2,9 с (без плиток). Первый шаг — через 29,5 с, самый долгий промежуток без шага — он же.
    Видеопамять: пик 15,7 ГБ из 16,3. Но ролик был белым — см. ниже; с поправкой: 64,0 с, 49 кадров, 6,4 МБ (лиса
    в снегу), gemma 9,6 с, семплинг 44,0 с, VAE 3,0 с; первый шаг — через 28,5 с; пик 15,7 ГБ.
  - Найдено пользователем на стенде (ролик LTX 960×960 в окне «готово за 104 с» — белый, плеер «0:00 / 0:00»), не
    чинилось в коде: LTX-2.3 снимает белым — и мой замер выше, и проба (её проверка — только «completed»). Каждый кадр
    белый (Y 235), libwebp сливает одинаковые кадры — WebP одна картинка (VP8, 1,5–3 КБ, без ANIM), поэтому и плеер
    «0:00». Латенты чёрные с первого шага (`preview` proj — Y 16), `preview` ни при чём (без него — то же). Причина —
    flash attention в текстовом кодировщике (gemma-3-12b, CUDA MMA): с `--flash-attn off` ролик верный, с
    `--flash-attn auto,te=off` — тоже (у DiT flash attention остаётся, время то же). Стенд — блок LTX в config.yaml
    с этим флагом вручную. В коде: флаг — в `flags` семейств LTX каталога (2.3 и 2.5: у 2.5 кодировщик gemma-4 — не
    проверено); в выпусках LTX ещё не было (122ecf5 — после v0.1.13), установленных у людей нет. И проба, и ролик,
    вернувшие одну картинку вместо видео, — не «готово», а ошибка словами. ✓ Сделано по слову пользователя: флаг — у
    четырёх семейств LTX (`hub/model_catalog.json`); `moving` (`hub/visual.py`) — WebP из кадров, больше одного
    (`ANMF`), иначе `STILL_WORDS` — у `trial_video` и `_run_video`, файл не хранится. Тесты —
    `test_a_family_with_a_gemma_text_encoder_runs_it_without_flash_attention`,
    `test_a_video_that_came_back_as_one_still_picture_fails_in_words` (оба падали на старом коде); `WEBP` тестов —
    теперь анимированный, как у движка, `STILL` — одна картинка. Открыто (слово пользователя, не чинилось): флаг у
    LTX-2.5 (кодировщик gemma-4) вживую не проверен — поставлен по аналогии с 2.3.
  - Ролики 48.8 по файлам стенда (слово пользователя: смотреть кадры, а не только время; `live48/frames_check.py` —
    кадры `ANMF`, яркость и изменение от кадра к кадру по ffmpeg `signalstats`, средний кадр глазами): TI2V-5B 832×480 —
    49 кадров из 49, яркость 161–169, изменение 3,8–30,0; TI2V-5B HD — 49 из 49, 149–154, 14,9–18,6; A14B — 33 из 33,
    152–165, 10,8–33,2; LTX с поправкой — 49 из 49, 99–125, 5,4–16,5. На средних кадрах — лиса в снежном лесу (у
    A14B — сцена первого кадра, лиса вошла в кадр). Пустых и из одного кадра нет.
  - Та же проверка у картинок (слово пользователя) — уже есть с 29.1 и 29.3б: `blank` (`hub/visual.py`) — картинка
    одного цвета (разброс яркости < `FLAT_SPREAD`) у пробы — ошибка установки «картинка вышла пустой, одного цвета…»
    (`trial`; в установщике — «не вышло» с «Повторить»), у картинки по просьбе — ступень ниже, затем ошибка
    (`_run`). Тесты — `test_a_blank_picture_is_told_from_a_real_one`, `test_a_picture_that_does_not_come_out_says_why`,
    в `tests/test_model_install.py` — проба серой картинкой на карте и на процессоре.
  - Найдено пользователем на стенде, не чинилось (разведка без кода, `live48/seek_probe.py` — `AnimatedImage` как в
    `StudioView.qml` над файлами стенда, нарисованное сверено с кадром ffmpeg): перемотка не двигает ролик. Его ролик
    LTX — 960×960, 73 кадра (3 с HD), 9,6 МБ файла, кадров в памяти 256,6 МиБ > `SEEKABLE_BYTES` 200 МиБ
    (`app/ui/visual_view.py` 43, `videoSeekable`): `cache: false`, ползунок выключен, окно не говорит почему; без
    кэша `currentFrame` не уходит с 0 — у Qt WebP только вперёд (`QImageReader.jumpToImage` — False). Ниже предела на
    настоящем WebP sd-server перемотка верна (768×512, 49 кадров: 30, 10, 45, 0 — расхождение с кадром ffmpeg 1,3–1,4,
    с первым 39–48; окно +89 МиБ); тест 48.7г — только GIF 8×8 из трёх кадров. Тот же ролик с кэшем — верно, +244 МиБ;
    с кэшем и `sourceSize` 480×480 — верно, +79 МиБ. Qt читает WebP с начала за 12–16 мс на кадр (73 кадра 960×960 —
    0,9 с, 49 кадров 1280×704 — 0,8 с). Выше предела — только HD 3 с (окно: 1–3 с) и HD 3–4 с (чат: до 4 с); больше
    всех — MiniMax-H3 HD 4 с, 1344×768×97 — 382 МиБ. QtMultimedia в окне нет (`PySide6-Essentials`, без Addons).
    Варианты (выбор — за пользователем): слова у выключенного ползунка — в любом случае; предел от свободной ОЗУ
    вместо 200 МиБ (худший ролик каталога — 382 МиБ); кадры в кэше — по размеру холста (`sourceSize`); перемотка без
    кэша — перечитывать с начала до кадра в потоке окна и показывать его поверх (до ~1,5 с, память — один кадр);
    MP4 через QtMultimedia — настоящая перемотка и звук этапа 49 (WebP звука не несёт), но PySide6-Addons и ffmpeg.
  - Найдено пользователем на стенде (обход Студии руками), не чинилось: при одних видеомоделях Студия пуста — «Модели
    для картинок ещё нет» и «Поставить модель картинок», панель слева скрыта, снять видео нельзя. `Visual.hasModels`
    (`app/ui/visual_view.py` 281) — только модели картинок (`_models`), видео (`_videos`) не в счёт; так с 29.8 — в
    тестах окна модель картинок есть всегда. Пользователь поставил из «Подборки» Wan2.2 TI2V 5B Turbo (Q4_K_M, 8,5 ГБ,
    проба 11,4 с) — Студия осталась пустой. Заодно: шапка установщика называет загруженную модель по id
    («wan2.2-ti2v-5b-turbo»), не по имени. ✓ Сделано по слову пользователя, посреди обхода: `hasModels` — модели
    картинок или видео; `_kinds` — что есть чем делать: переключатель «Картинка / Видео» — только при обоих
    (`modes`), без моделей картинок Студия встаёт на видео (`load`, `setMode`). Тест —
    `test_the_studio_with_only_video_models_films` (на старом коде — `hasModels` ложно).
  - Время до первого шага (для предела «до первого шага» вместо 3 ч — решить после 48.8): TI2V-5B — 2,0 с (модель
    загружена пробой), 8,3 с с холодной загрузки (HD); A14B — 31,6 с с холодной; LTX — 29,5 с с холодной. Самый долгий
    промежуток без шага после первого — 95,2 с (VAE плитками у TI2V-5B в HD): `VIDEO_STALL_S` = 15 мин — с запасом
    в 9 раз.
  - Найдено при 48.8: «Снять видео» в окне не работало никогда — `HubClient.visual_video` (с 29.8б, 24727dd) зовёт
    `_call(..., json=spec)`, а `_call` берёт тело позиционно: TypeError, «background call failed». Тесты окна
    подменяют клиента (`FakeClient.visual_video`) и этого не видели. ✓ Сделано по слову пользователя, пока качался
    A14B: тело — позиционно. Тест — `test_the_windows_client_asks_the_hub_for_a_video` (настоящий `HubClient` на
    настоящем хабе, HD; на старом коде — тот же TypeError). Поиск по `app/hub_client.py`: имена и число аргументов всех
    227 вызовов `_call` и 3 `_raw` сверены с их подписями (ast) — другой такой ошибки нет; метод и путь каждого из 230
    вызовов сверены с маршрутами хаба — у каждого маршрут есть.
- 48.9 Один хозяин видеокарты для чата, подбора, пробы, Студии и «Выгрузить» (находки 3 и 4) — после 48.8, отдельным
  шагом: разведка без кода, затем план. 3: подбор (`hub/tuning.py` 541, 558) не знает о Студии — пока видеомодель
  грузится, карта «тихая», llama-bench идёт рядом и выгружает все модели (822). 4: «Выгрузить модель»
  (`hub/api/models.py` 41) Студии не говорит, опрос через `/upstream` через секунду грузит модель снова. Вопрос
  пользователя: что будет, если во время пробы чат-модели придёт чат к другой локальной модели (проба карту от чатов не
  держит — её запрос идёт через тот же `gate`, Р2б).
  - Разведка (2026-11-15, без кода). Кто трогает карту сейчас: `GpuCard` (`hub/gpu_card.py`) — Студия (`STUDIO`:
    видео держит карту от чатов, картинка — нет: её синхронный запрос llama-swap сам не выгрузит) и проба установки
    (`TRIAL`: видео- и картинко-модели — от чатов, чат-модель — нет). Ворота `gate` в провайдере llama-swap
    (`hub/gateway/openai_compat.py`) — все локальные запросы: чаты и агенты (`chat.py` 1417), проверка модели и проба
    чат-модели (`run_check`, `hub/api/model_settings.py` 133), внешние клиенты `/v1/openai` (`hub/api/openai.py`),
    автодополнение (`completion.py`), черновик карточки (`api/cards.py`), `judge.py`, `rewrite.py`, `computer.py`.
    Мимо `GpuCard`: подбор (`hub/tuning.py`: `_wait` — `chat.busy`, установки, загрузка карты < `BUSY_PCT` 15 %;
    `_unload` — llama-swap выгружает всё; llama-bench и llama-server мимо llama-swap; `_watch` останавливает замер
    только от чата или установки); «Выгрузить модель» (`/v1/models/unload`, `require_client` — окно, трей, любой
    клиент) — всё, никому не говоря; удаление модели (`Installer._unload`) — всё, если удаляемая загружена; «Выход» —
    всё (хаб уходит). `chat.busy` — идущие разговоры, облачные тоже; остальные локальные запросы в нём не видны.
  - Столкновения по коду: Р3 в обе стороны — видео грузится (карта «тихая») → подбор выгружает всё, llama-bench рядом,
    видео «не вышло», замер сохранён; видео начато во время замера → Студия берёт карту (подбор её не держит), sd-server
    рядом с llama-bench, `_watch` Студию не видит. Р4 — «Выгрузить» во время видео: опрос `/upstream` грузит снова, 404.
    «Выгрузить» во время пробы установки — проба «не запустилась», установщик берёт меньшие настройки (`smaller`) у
    исправной модели; во время картинки — ступень ниже, и она запоминается как место модели (`visual.py` 527–571).
    Автодополнение, `/v1/openai`, черновик карточки, проверка модели — не `chat.busy`: подбор их не ждёт и не
    останавливается (llama-bench рядом с моделью), проба установки их не ждёт. Облачный разговор — `chat.busy`: подбор и
    проба ждут его зря.
  - Ответ на вопрос пользователя — по исходнику llama-swap v267 (`internal/router/scheduler/fifo.go`, `OnRequest`,
    правила 4–5; конфиг без групп — одна модель за раз): модель с идущим запросом не выгружается, запрос к другой ждёт в
    очереди, пока проба не ответит (загрузка + ответ, до 300 с `run_check`), затем llama-swap меняет модель. Проба не
    страдает; чат ждёт молча — ворота карту от чатов не держат, слов «пробный запуск» нет. Опасен обратный порядок: чат
    вошёл между концом `_card` и запросом пробы (запись конфига и перечитывание llama-swap) или между попытками — проба
    ждёт весь его ответ: её время раздуто, а дольше 300 с — «не запустилась», и установщик ставит меньшие настройки
    исправной модели. Подтвердить на стенде (две малые чат-модели).
  - Порядок старшинства (решения пользователя 2026-11-15): 1) человек сейчас > 2) работа человека без него > 3) проба
    установки > 4) подбор. Старший, кому нужна карта, прерывает младшего (1 и 2 — пробу и подбор, 3 — подбор);
    между собой 1 и 2 друг друга не прерывают — их запросы ставит в очередь llama-swap (модель с идущим запросом он не
    выгружает). Прерванное не считается проваленным и ничего не сохраняет: видео и картинка — «отменено», как ✕ (у
    картинки — без ступени ниже, место модели не запоминается); проба — назад в «ждёт пробного запуска»
    (`trialwait`), настройки не уменьшаются, попытка не пишется; подбор — замер не сохраняется, модель снова в его
    очереди. «Выгрузить» значит «карта нужна мне сейчас».
    - 1, человек сейчас: сообщение человека в чате (окно, Telegram) и ответ на него с инструментами; Студия
      («Нарисовать», «Снять видео», её очередь); «Выгрузить»; кнопки «Пробный запуск», «Подобрать», «Проверить»,
      «Набросать карточку»; «Продолжить». Карту, отданную по «Выгрузить», берёт — это и есть «Продолжить».
    - 2, работа человека без него: автономный агент (`/v1/runs/autonomous`, шаги цели), задачи по расписанию,
      автодополнение в редакторе, внешние клиенты через `/v1/openai`. Прерывают пробу и подбор. Карту, отданную по
      «Выгрузить», не берут: агент и расписание ждут у ворот со словами «видеокарта отдана — „Продолжить“» (прогон
      стоит, не падает); автодополнение — сразу без подсказки (ждать незачем: подсказка устаревает за секунды);
      `/v1/openai` — отказ 503 со словами и `Retry-After` (внешний клиент не висит).
  - Ответы пользователя: «Выгрузить» во время видео — остановить, как ✕; во время пробы — прервать и отложить; Студия
    во время замера прерывает подбор; проба чат-модели и чужой чат — вариант А: чат прерывает пробу (по старшинству), а
    отложенная проба не стартует, как только карта свободна (иначе при живом чате модели грузятся туда-сюда на каждое
    сообщение), — она ждёт тишины, как подбор.
  - Тишина сейчас (только у подбора, `hub/tuning.py` `_wait`): нет ни одного разговора (`chat.busy`, облачные тоже),
    у установщика нет работы (`installer.busy` — и скачивание), загрузка карты < 15 % (`BUSY_PCT`, проба монитора
    раз в 2 с); автоматическому подбору — всё это 60 с подряд (`IDLE_S`), ручному — сразу; прерванный ручной подбор
    возвращается в очередь ручным — тоже сразу, без минуты: та самая «туда-сюда». Не видит Студию, «Выгрузить»,
    автодополнение, `/v1/openai`, проверку модели.
  - Тишина — одно правило для всякой фоновой работы (предложение): автоматический подбор, прерванный подбор (ручной
    тоже), отложенная проба. Тихо, когда 3 мин подряд: ни одного локального запроса в воротах (любого ранга; облачные
    не в счёт), Студия карту не держит, карта не «отдана человеку», загрузка карты < 15 % (игра, чужая программа),
    и на карте нет игры, даже когда загрузка ниже (игра в меню, на паузе; слово пользователя) — её узнаёт «Видеокарта
    — моделям» (43.6, `hub/gpu_prefs.py`: `why_kept` → "game", взгляд раз в 30 с; при одной видеокарте он не смотрит —
    остаётся загрузка). 3 мин — на обдумывание следующего сообщения; число поправим по жизни. Скачивание установщика тишину не нарушает
    (карту не трогает). Начатое кнопкой человека («Подобрать», «Пробный запуск») — ранг 1: стартует, как только карта
    свободна, без трёх минут; прерванное — становится фоновым и ждёт тишины.
  - После «Выгрузить» (слово пользователя: очередь Студии, отложенная проба и подбор не забирают карту сами): карта
    «отдана человеку» до первого действия ранга 1 или кнопки «Продолжить» (трей, лента очереди Студии, строка
    установки «ждёт пробного запуска»). По «Продолжить» — сразу, в порядке старшинства: очередь Студии, работа ранга 2,
    отложенные пробы, подбор; по действию человека (например, чат) — само действие, а фоновое — после тишины. До того у
    каждого — слова «видеокарта отдана — „Продолжить“». Пока карта отдана, это видно в строке состояния окна с кнопкой
    «Продолжить» (слово пользователя); автодополнение не молчит без объяснения — редактор говорит те же слова.
    Перезапуск хаба (вход в Windows) снимает «отдано». Без таймера: игра на паузе карту не отпускает.
  - Известное ограничение (слово пользователя: записать, не делать): ранги 1 и 2 ждут друг друга в очереди llama-swap
    — модель с идущим запросом он не выгружает, и чат человека к другой модели ждёт, пока ответит запрос агента
    (автодополнения, `/v1/openai`).
  - План (код — по слову пользователя 2026-11-15; каждый шаг — отдельный коммит, у каждого сначала тест, который
    падает на прежнем коде; полный прогон — в конце 48.9д; живая проверка 48.9е — по его слову):
    48.9а — ворота шлюза считают локальные запросы и знают, кто спросил (чат, агент, автодополнение, `/v1/openai`,
    кнопка человека, проба), — вместо `chat.busy` у подбора и установщика; облачные не в счёт. ✓ Счёт — у
    `GpuCard` (`asked`: кто → сколько, `asking`); кто спросил — `ASKER` в контексте (`named` в корутине, `ask_as` в
    запросе к API хаба — у него своя задача, поток ответа от неё же). Считаются: каждый запрос провайдера llama-swap,
    пока идёт, и пока ждёт у ворот тоже; ход чата на локальной модели — целиком, от начала до конца (между его
    запросами работает инструмент, а модель ждёт следующего — подбор и проба не влезают); автодополнение — целиком
    (его `/infill` идёт мимо ворот, через `/upstream`). Имена: сообщение человека — `chat`; прогон агента,
    автономный, ход цели, задача по расписанию — `agent` (он же — когда никто не назвался); `completion`; `openai`;
    «Проверить», пробный запуск настроек, «Набросать карточку», набросок цели, Ctrl+I — `button`; проба установки —
    `trial`. Подбор (`_wait`, `_watch`) и проба (`_card`) смотрят на `asked`. Тесты (падали на прежнем коде: облачный
    чат держал и подбор, и пробу) — `test_tuning_waits_for_any_local_request_and_not_for_a_cloud_chat`,
    `test_an_installs_trial_waits_for_the_editors_completion_and_not_for_a_cloud_chat`;
    48.9б1 — подбор — держатель карты `TUNE` (сейчас мимо неё); старшинство в `GpuCard`: держатели по рангу, старший
    прерывает младшего через его «стоп», прерванное — не провал; проба не ждёт подбор, а прерывает его. ✓ Ранги —
    `RANKS` (`chat`, `button`, Студия — 1; `agent`, `completion`, `openai` — 2; проба — 3; подбор — 4), `senior`.
    Держатель называет свой «стоп» (`try_take`/`take` … `stop=`); старший, которому нужна карта, — берёт её
    (`try_take`, `take`) или спрашивает у ворот (`gate`) — зовёт «стоп» младшего и ждёт, пока тот отдаст. Подбор
    берёт карту в `_wait` (тем же шагом, что смотрит на тишину) и держит от локальных запросов (`chats=True`:
    llama-bench и llama-server — мимо llama-swap), отдаёт в `_work`; Студия или проба держат — ждёт. Его «стоп»
    (`_stop`): замер отпущен (`_halt`, `_kill`), проверка записанных настроек доходит до конца, следующий шаг не
    начинается и модели не выгружает (`_unload` смотрит на `_halt`; llama-bench, начавшийся после «стопа», тут же
    отпущен) — «halted», ничего не сохранено, модель снова в очереди. Проба больше не ждёт `installer.tuning`
    (убран): её `try_take` останавливает подбор. Автодополнение через `/infill` проходит ворота карты. У ворот слова
    «видеокарта останавливает подбор параметров — ответ начнётся сразу после». Попутно закрыт изъян: `_watch`
    следил только за первым замером — после записи лучших настроек («checking») он выходил, и замеры экспертов и
    угадывания никто не останавливал; теперь их останавливает «стоп» через карту (`_watch` остаётся до 48.9б2 —
    для работы установщика). Тесты (падали на прежнем коде: чат уходил в llama-swap рядом с замером, подбор
    мерил, пока Студия рисовала) — `test_a_local_request_stops_tuning_and_goes_once_it_lets_the_card_go`,
    `test_the_studio_stops_tuning_and_tuning_waits_for_the_studio`;
    48.9б2 — одно правило тишины для фоновой работы (3 мин без локальных запросов, Студия не держит, карта не отдана,
    загрузка < 15 %, нет игры). ✓ `GpuCard.still` — на карте сейчас ничего: нет локального запроса, её никто не
    держит, загрузка ниже `BUSY_PCT` (15 %, взгляд монитора состояния — `load`), нет игры (`game`: строка
    «Видеокарта — моделям» с `why` "game", не перенесённая); `Quiet` — тихо `QUIET_S` (180 с) подряд, считая от начала
    ожидания (прерванное не возвращается сразу). «Карта не отдана» войдёт в `still` с 48.9г. У подбора очередь
    помнит, фоновая ли работа: автоматический — фоновый, «Подобрать» — ждёт только `still`, прерванный старшим —
    становится фоновым (выключатель «автоматически» по-прежнему снимает только автоматический). `IDLE_S` (60 с),
    своя загрузка карты и ожидание работы установщика (`installing` — скачивание тоже) у подбора убраны; `_watch`
    останавливает замер от локального запроса весь подбор, не только первый замер (запрос не через ворота — чат
    чужой программы на карте). Тесты (падали на 48.9б1: прерванный «Подобрать» вернулся через 0,04 с после чата;
    подбор мерил рядом с игрой) — `test_tuning_a_chat_stopped_goes_on_after_quiet_not_at_once`,
    `test_background_tuning_waits_for_a_game_and_not_for_a_download`; переписан
    `test_tuning_waits_for_an_installs_trial_and_gives_way_to_one` (вместо «ждёт работы установщика»);
    48.9б3 (найдено перед 48.9е, слово пользователя: отдельным шагом) — игра для тишины. 43.6 считал игрой и
    лаунчер Epic с его помощником и оверлеем (`EpicGamesLauncher.exe`, `EpicWebHelper.exe`,
    `EOSOverlayRenderer-Win64-Shipping.exe`: «Games» в пути папки, 13–35 МиБ, 3D 0 %), и пока лаунчер открыт, фоновая
    работа не шла никогда, «Продолжить» тоже. ✓ `gpu_prefs.playing`: игра для тишины — та, что знает Game Bar
    Windows или запомнил 43.6, видев, как она рисует (в меню и на паузе — всё ещё игра), или любая программа, что
    рисует 3D сейчас от `BUSY_3D` (15 %, порог 43.6); не процессы самого AFO (его движки — `OWN_ENGINES`; папки AFO,
    и базового интерпретатора venv — `afo`) и не `dwm.exe`. Строки «Видеокарта — моделям» получили `playing`, карта
    смотрит на него (`card.game`); решение 43.6 держать программу на карте (`why_kept`) не тронуто. Тест (падал на
    48.9б2: три процесса Epic держали подбор) — `test_a_launcher_does_not_break_the_quiet_a_known_game_paused_does`:
    Epic, Riot и Rockstar, `dwm.exe` и llama-server, рисующие 3D, тишину не ломают; игра из списка Windows и игра,
    которую помнит 43.6, на паузе — ломают; незнакомая программа, рисующая 3D, — ломает. Известное ограничение (слово
    пользователя, переписано в 48.9б4): работа не уступает игре, но её результат не сохраняется — игра, запущенная,
    когда подбор или проба уже идут, их не останавливает (подбор останавливают только локальный запрос, Студия,
    проба и «Выгрузить», пробу — локальный запрос, Студия и «Выгрузить»), а намеренное рядом с ней не записывается
    (48.9б4); полный прогон после 48.9б3 (без `tests/test_computer.py`, 2026-11-15): 1268
    тестов, одно падение — `test_an_ollama_model_is_named_by_ollama_not_by_its_file` (второе «Добавить файл…» не
    получило задания); отдельно прошёл 7 раз из 7, к 48.9 не относится — в список «Стабилизация»;
    48.9б4 (после 48.9е, до 48.10; слово пользователя: результат, полученный не в тишине, не сохраняется) — по коду
    оба ответа были «да»: подбор, домеривший рядом с игрой, писал выбранное в config.yaml (на каждом шаге — llama-bench,
    эксперты, угадывание наперёд) и итог в базу; проба, у которой игра заняла память, писала меньшие настройки
    (`_relaunch`), затем «не запустилась» и убирала модель из конфига (модель картинок — навсегда на процессор). ✓
    `GpuCard`: `Played` — была ли игра на карте (`card.game`, 48.9б3), пока работа держала карту: сейчас или хоть раз
    с её начала. Подбор: `_watch` заодно смотрит на игру каждые `WAIT_S`; перед каждой записью конфига (`_write`) и в
    конце — игра была: работа прервана (`_Halted`) — ничего не пишет, итога нет, модель в очереди как фоновая работа
    (тишина `QUIET_S`). Проба: игру смотрит задача рядом с ней (`Played.watch`, каждые `CARD_WAIT_S`, с взятия карты в
    `_card` до конца `_run`); ответ или отказ каждого вопроса пробы (`_attempt`) при игре не берётся — `_Deferred`, как
    при старшем: попыток не записано, меньших настроек нет, вердикта нет, «ждёт пробного запуска» и тишина. Саму работу
    посреди игры не останавливают: идущий замер и вопрос пробы доходят до конца; подбор, увидевший игру, кончается на
    ближайшей записи. Сколько игра видна: 43.6 смотрит программы раз в `CHECK_S` (30 с) — игра короче может пройти
    незамеченной. Тесты (падали на 48.9е: подбор записал `-b 2048 -ub 2048 -t 16` по замеру рядом с игрой; проба —
    «не запустилась») — `test_what_tuning_measured_beside_a_game_is_not_kept` (игра осталась к записи; игра пришла и
    ушла во время замера; замер не остановлен), `test_a_game_that_takes_the_card_during_a_trial_leaves_no_verdict`;
    тестовый llama-swap отказывает и после задержанного ответа (`fail` во время `answering`). Тесты шага (подбор,
    установки, «вместе», конфиг llama-swap, поиск моделей) нашли гонку теста 48.9б1
    `test_a_local_request_stops_tuning_and_goes_once_it_lets_the_card_go`: он смотрел, отдана ли карта, в тот же миг,
    как увидел итог подбора, а подбор отдаёт её чуть позже (запись итога в базу, новость окну) — падал и на прежнем
    коде (1 из 10); теперь ждёт, пока карта отдана (10 из 10), отдельным коммитом; полный прогон после 48.9б4 (без
    `tests/test_computer.py`, 2026-11-16): 1270 тестов, все прошли. Снимки окна без экрана (2026-11-16, слово
    пользователя; 21 проверка 48.9е шла только через хаб): окно на `FakeClient` с ответами хаба из 48.9е, изоляция
    тестов, временный файл в `tests/` удалён после снимков — строка состояния «Видеокарта отдана» и «Продолжить»;
    нижняя строка редактора «Дописывание ждёт: видеокарта отдана — „Продолжить“»; «Установки»: отложенная проба —
    «ждёт пробного запуска», причины нет (в «Стабилизацию»);
    48.9б5 (слово пользователя, до 48.10а) — проба по кнопке при игре грузила модель рядом с игрой, а ответ потом
    выбрасывался (48.9б4): игре мешали, работа впустую. ✓ `_card` смотрит игру до загрузки: игра на карте — проба,
    и по кнопке, и первая после загрузки файлов, ждёт тишины (`Quiet`), модель не грузится; в строке «Установок» —
    «ждёт: на видеокарте игра» (`GAME_WORDS`, в `error` ожидающей пробы, как «видеокарта отдана»; без имени игры),
    снимаются, когда проба взяла карту. Шаг — в три файла (`hub/model_install.py`, `common/locale/en.json`,
    `tests/test_together.py`), слова вошли. Тест (падал на 48.9б4: вопрос пробы ушёл в llama-swap рядом с игрой) —
    `test_a_trial_by_the_button_with_a_game_on_the_card_loads_nothing`; тест 48.9б4 ждёт теперь эти слова у отложенной
    пробы, а не пустые;
    48.9в — чат прерывает пробу чат-модели (вариант А): проба — в `trialwait`, ждёт тишины. ✓ По старшинству — не
    только чат и не только чат-модели: любой локальный запрос (ранги 1–2) и Студия прерывают пробу любой модели
    (чат-модели, картинки, видео). Проба называет свой «стоп» (`_stop_trial`): её вопрос отпущен (`_attempt` —
    задача, которую «стоп» отменяет; пробное видео при этом останавливается в движке, `halt`, как ✕ в Студии);
    конфиг, если пишется, дописывается целиком; меньших настроек не ставится (`_relaunch` после «стопа» не идёт).
    Установка — снова «ждёт пробного запуска», без ошибки, попытка не пишется (`_Deferred`), и ждёт как фоновая
    работа — тихо `QUIET_S` (`_card(background=True)`, `_quiet`); путь `trialwait` теперь различает картинку и чат
    (пробу картинки отложенной установки ведёт `_try_picture`). Ворота зовут «стоп» младшего и тогда, когда карту от
    чатов он не держит (проба чат-модели): запрос чата идёт сразу, вопрос пробы снят. Слова у ворот — «видеокарта
    откладывает пробный запуск новой модели — ответ начнётся сразу после». Переписаны (было «Студия и чат ждут конца
    пробного видео») `test_the_studio_stops_an_installs_trial_video_and_it_films_after_quiet`,
    `test_a_chat_stops_an_installs_trial_video_and_the_trial_waits_for_a_chat`; новый —
    `test_a_chat_stops_a_chat_models_trial_and_it_waits_for_quiet`; все три падали на 48.9б2 («still trial»).
    Известное ограничение: пока отложенная проба ждёт тишины, работник установщика стоит на ней — загрузки следующих
    установок ждут (как ждали чата и до 48.9, теперь дольше: 3 мин тишины);
    48.9г — «Выгрузить модель» и удаление — через хозяина: видео — отмена, как ✕, картинка — отмена без ступени, проба
    — `trialwait`, подбор — стоп; карта «отдана человеку»; ранг 2 при отданной карте — ждёт, без подсказки или 503.
    ✓ `/v1/models/unload`: очередь Студии придержана (`Visual.hold`), `GpuCard.give_away` — «стоп» держателя любого
    ранга (Студия — её задание как ✕, `_stop_job`: видео отменено в движке, картинка — «отменено», ступень ниже не
    берётся и не запоминается; проба — отложена, 48.9в; подбор — остановлен) и карта отдана; llama-swap выгружает всё,
    а если карту кто-то держал — ещё раз, когда он отпустил (до 15 с): его последний взгляд через `/upstream` мог
    загрузить модель (находка 4). Пока отдана: `try_take` никому, `still` — нет (фоновое ждёт); у ворот ранг 1 берёт
    карту обратно (`take_back`), ранг 2 ждёт со словами «видеокарта отдана — „Продолжить“»; автодополнение — сразу
    пауза «given» со словами; `/v1/openai` к локальной модели — 503 «card_given» с `Retry-After: 60`. Обратно берут:
    запрос чата человека и кнопок (`chat`, `button`), задание Студии от человека (`STUDIO` — API Студии; инструмент
    чата человека), «Подобрать», «Подобрать для всех», «Пробный запуск». Очередь Студии после «Выгрузить» ждёт
    задания человека в Студии (или «Продолжить», 48.9д), а не любого его действия: иначе после сообщения в чат
    видео из очереди взяло бы карту и ответ ждал бы его. Удаление модели: `GpuCard.let_go` — держатель, который
    работает с ней (Студия, подбор называют модель при `take`), остановлен, как ✕; выгружается только она
    (`/api/models/unload/<id>`), а не все; карта не отдаётся. Перезапуск хаба «отдано» забывает. Тесты (падали на
    48.9в: видео не отменено, проба и подбор шли дальше) — `test_unload_stops_the_studio_as_by_x_and_its_queue_waits_for_the_person`,
    `test_unload_puts_a_trial_off_and_the_persons_unattended_work_waits_for_them`,
    `test_unload_stops_tuning_and_it_waits_while_the_card_is_the_persons`; тестовый llama-swap записывает выгрузки;
    48.9д — «Продолжить»: строка состояния окна («видеокарта отдана» и кнопка), слова у очереди Студии, пробы,
    подбора, прогона агента и в редакторе у автодополнения; продолжение по действию человека или кнопке. ✓ Хаб:
    `POST /v1/gpu/continue` — очередь Студии отпущена, `GpuCard.go_on`: карта не отдана, и всё ждавшее идёт сразу, в
    порядке старшинства (очередь Студии берёт карту, ранг 2 проходит ворота, фоновое — когда карта `still`, без
    `QUIET_S`: `Quiet` видит «Продолжить», сказанное после начала ожидания, `pushed_at`). Состояние карты — в статусе
    хаба (`/v1/status` и `system.status`: `card: {given, studio_held}`); при каждой смене монитор переотправляет
    последний снимок с ним сразу (`card_changed`, без нового опроса машины). Слова: проба — в `error` строки
    установки «видеокарта отдана — „Продолжить“», пока карта отдана (снимаются, когда проба взяла карту — `_deferring`
    сбрасывается до любого `await`); подбор — `job.why` ("given", "quiet", "busy") и строка окна по нему; прогон
    агента — слова у ворот (48.9г); автодополнение — пауза «given» (48.9г). Окно: в строке состояния справа
    «Видеокарта отдана» (или «Очередь Студии ждёт», если чат уже взял карту, а очередь ещё ждёт) и «Продолжить»
    (`App.continueCard`); очередь Студии — «видеокарта отдана — „Продолжить“» у ждущих; в трее пункт модели —
    «Продолжить — видеокарта отдана»; вопрос перед «Выгрузить» говорит, что карта останется вашей. Тесты (падали на
    48.9г: нет `/v1/gpu/continue`, нет состояния карты в статусе и строке) —
    `test_continue_gives_the_card_back_and_tuning_goes_on_at_once`,
    `test_continue_lets_a_trial_put_off_by_unload_go_on_at_once`,
    `test_the_card_given_shows_in_the_status_bar_and_continue_gives_it_back`;
    полный прогон после 48.9д (без `tests/test_computer.py`, 2026-11-15): 1268 тестов, одно падение —
    `test_status_and_unload_endpoints` сверял поля `/v1/status` и не знал нового `card`; тест дополнен (поля и
    `card` по умолчанию), его файл прошёл;
    48.9е — живая проверка на стенде (`D:\AFO-stand48\stand`): две чат-модели, Turbo, подбор, «Выгрузить»,
    «Продолжить», автодополнение и `/v1/openai` при отданной карте. ✓ 2026-11-16, после 48.9б3 (слово пользователя),
    `check489.py`: хаб стенда на 8798, его llama-swap на 8799, AFO пользователя остановлен на время и возвращён через
    ярлык Проводника; модели — файлы пользователя, вписанные в конфиг стенда там, где лежат (Qwen3-VL-8B Q8_0 — B,
    Kimi-VL-A3B-Thinking Q4_K_M — A); лаунчер Epic открыт нарочно (слово пользователя). 21 проверка из 21: проба B —
    84 ток/с; чат к B остановил пробу A — «ждёт пробного запуска», попыток не записано; проба A вернулась через
    180 с тишины при открытом Epic, одна попытка с прежними настройками; чат к A остановил «Подобрать» B — ничего
    не сохранено; подбор вернулся через 181 с тишины и домерил (better: f16, ubatch 1024); «Выгрузить» во время
    видео Turbo — «отменено», карта человека, очередь Студии ждёт, видео из очереди карту не взяло, `/v1/openai` —
    503 и Retry-After 60, автодополнение — пауза «видеокарта отдана — „Продолжить“», на карте ничего; «Продолжить»
    — второе видео снято (49 кадров, движутся); «Выгрузить» и чат человека — ответ, карта снова его. Видеопамять
    (`vram.csv`): между владельцами она падала до стола (≈ 1,25 ГиБ) — двух моделей на карте разом не было; пик
    15 684 МиБ из 16 303 — проверка подбора, B с кэшем f16. Сбои — мои, в скрипте: «Пробный запуск» попрошен до того,
    как добавление дописало конфиг (409 — хаб прав), и печать ответа Kimi в консоль cp1251 (`◁`); проверка продолжена
    с середины шага 2 (`check489b.py`), пройденное не повторялось.
- 48.10 Затем находки 5, 9, 10, 11. 5: ✕ у задания, которое работник только что взял из очереди, перетирается
  «running» (`Visual.cancel` и `_work`). 9: кадр, удалённый до начала задания, молча пропускается (`_run_video`; у
  картинок — ошибка словами). 10: чату предлагается `generate_video`, когда стоят только модели «по картинке»
  (`can_film`). 11: остановка хаба при очереди — снято только текущее, работник берёт следующее (`Visual.stop`).
  План (слово пользователя 2026-11-16): шаги а–г по находкам 5, 9, 10, 11; на каждую сначала тест, падающий на нынешнем
  коде; отдельные коммиты; полный прогон в конце; живой проверки нет (✕ в окне — на проверке плеера 48.12); очередь
  после перезапуска — как сейчас («не нарисована»), продолжение не делается.
  - 48.10а (находка 5) — ✕ у задания, которое работник взял из очереди, но ещё не назвал «running»: `cancel` видел «в
    очереди» и писал «отменено», работник писал поверх «running» — картинка рисовалась, видео снималось. «Выгрузить»
    (48.9г) идёт через тот же `cancel`: картинку уже останавливал вовремя, видео снималось на отданной карте. ✓ Работник
    называет задание своим (`_running`) сразу, как взял карту; `cancel` такого задания — как идущего (`_stopping`,
    выгрузка его модели, если уже просилась), у задания в очереди — тоже `_stopping`; перед тем, как просить модель,
    работник смотрит `_stopping` без `await` между проверкой и запросом — у картинки после записи «running», у видео
    перед `vid_gen` (и «running» не пишется) — и кончает «отменено». `_stopping` очищается, когда очередь пуста.
    Тесты (падали на 48.9б5: картинка «done» после ✕; видео после «Выгрузить» попросило модель) —
    `test_x_on_a_job_the_worker_has_just_taken_stops_it_before_its_model_loads`,
    `test_unload_in_the_same_instant_stops_a_job_the_worker_has_just_taken` (картинка и видео; работник задержан между
    «взял» и «running»); тестовый llama-swap Студии понимает выгрузку всех моделей.
  - 48.10б (находка 9) — кадр видео (первый или последний), удалённый с диска, пока задание ждало в очереди, молча
    пропускался: видео «по картинке» снималось по одним словам, по двум кадрам — без последнего. В хабе кадры заданий
    никто не удаляет — только человек руками. ✓ `_run_video` до модели смотрит, на месте ли кадры: нет — «не вышло»
    со словами «первый кадр этого видео уже удалён» / «последний кадр этого видео уже удалён» (как «образец этой
    картинки уже удалён» у картинок); кадр, пропавший между проверкой и чтением, — «кадр задания не прочитан», не
    пропуск. Тест (падал на 48.10а: «done» без первого кадра) —
    `test_a_videos_frame_deleted_before_it_begins_fails_it_with_words`.
  - 48.10в (находка 10) — `generate_video` снимает только по словам, а новому чату он предлагался при любой видеомодели,
    и при той, что снимает только по картинке (Wan2.2-I2V-A14B): каждый вызов — отказ «снимает только по картинке». ✓
    `can_film` — есть модель, что снимает по словам (`modes` с "text"); чат, начатый раньше (его инструменты не
    меняются, D14), при вызове слышит: «the video models here film only from a picture … in the window's Studio». Тест
    (падал на 48.10б: инструмент предложен) — `test_a_chat_is_offered_a_video_by_words_only_with_a_model_that_films_by_words`.
  - 48.10г (находка 11) — остановка хаба снимала только идущее задание и ждала работника, а тот брал следующее из
    очереди и грузил его модель, пока хаб уходил. ✓ `Visual.stop` сначала закрывает очередь (`_closing`): работник не
    берёт следующего и не начинает то, что ждало карту; идущее — как по ✕. Ждавшее остаётся «в очереди» и при запуске
    хаба — «не вышло», как было (слово пользователя: очередь после перезапуска не продолжается), но словами своего
    вида (уточнение пользователя): видео — «хаб перезапускался — видео не снято», картинка — «… картинка не
    нарисована» (было одно на всё). Тест (падал на 48.10в: второе видео попросило модель, пока хаб останавливался) —
    `test_the_hub_stopping_with_a_queue_begins_nothing_and_says_what_was_left`.
  - Полный прогон после 48.10 (без `tests/test_computer.py`, 2026-11-16): 1276 тестов, все прошли (17 — стенд
    нагрузки, `-m stress`, как всегда, отдельно).
- 48.11 (после 48.10, по слову пользователя — урок `visual_video`): тест, который вызывает каждый метод `HubClient`
  на настоящем хабе (`create_app` + `TestClient`, как `tests/test_app_client.py`) — хоть раз каждый, ответ той формы,
  что берёт окно; и сверка подписей `FakeClient` (`tests/test_ui.py`) с `HubClient` — имена методов и аргументов,
  тест падает на расхождении. Уже видно: у `FakeClient` нет `claude_code_limits` и `gpu` — окно в тестах зовёт их, и
  каждый тест окна пишет «background call failed» (AttributeError).
- 48.12 Плеер видео на QtMultimedia с FFmpeg из Qt (выбор пользователя после разведки 48.8 — вариант «д»; после
  48.9 и 48.10, до выпуска; код — по его слову). Предел 200 МиБ и «а + б» (предел от свободной ОЗУ, слова у
  ползунка) не делаются: плеер на `MediaPlayer` перематывает любой ролик и не держит его кадры. В план шага входит:
  карточка видео в чате (`ToolCalls.qml`, `toolVideo`) не держит кадры в памяти — сейчас `AnimatedImage` по
  умолчанию хранит все кадры в полном размере (960×960×73 — 257 МиБ на ролик при показе 420 точек), перемотки там нет;
  и плеер Студии отпускает ролик, когда Студия скрыта (находка ревью «кадры плеера при скрытой Студии»).
  - Разведка 2026-11-15, без кода: QtMultimedia в окне нет
    (`PySide6-Essentials`); отдельного колеса нет — из `PySide6-Addons` 6.12.0 (40,5 МБ, 100 МиБ распакованным) нужны
    по таблицам импорта DLL: с бэкендом FFmpeg — 11 файлов, +21,1 МиБ в папке программы, ≈ +7 МиБ в установщике
    (85,1 → ≈ 92 МиБ); с бэкендом Windows Media Foundation (не выбран) — 6 файлов, +2,0 МиБ, ≈ +0,6 МиБ, но кодеки — Windows
    (MP4 — да, кроме Windows N без Media Feature Pack; WebM — только с «Web Media Extensions»), то есть каждый ролик —
    через MP4 и ffmpeg хаба (скачивается ~86 МБ при первом). Остальное (Qt6Core, Gui, Network, Qml, Quick, MSVC) — уже
    в Essentials. FFmpeg Qt знает VP8/VP9, H.264, HEVC, AAC, Opus, Vorbis и WebM/MP4/AVI. sd-server отдаёт видео и в
    WebM (`output_format: webm` — VP8 из кадров libwebp, 25 кадров 480×288 — 1,9 МБ, примерно вчетверо больше WebP) и
    AVI; звук LTX он кладёт в WebM («mux audio chunk into WebM») — путь звука этапа 49.
  - Шаги: 1) сборка — Addons в `requirements.lock`, в `installer/build.py` — только эти 11 файлов, проверка, что
    `import QtMultimedia` в QML грузится в собранной папке (таблицы импорта — только статические зависимости: то, что
    грузится на ходу, покажет этот шаг), лицензия LGPL у FFmpeg; 2) хаб — какой файл играет: WebM от sd-server или MP4
    из WebP ffmpeg'ом (решить с пользователем; касается «Сохранить как», вложений чата, срока хранения, первого кадра в
    ленте); 3) окно, Студия — `MediaPlayer` + `VideoOutput` вместо `AnimatedImage`, ползунок по времени,
    `SEEKABLE_BYTES` и `videoSeekable` уходят, Студия скрыта — ролик отпущен; тест — настоящий QML без экрана на
    настоящем файле: перемотка к кадру, сверка с кадром ffmpeg; 4) окно, чат — карточка `toolVideo` на том же плеере,
    кадры в памяти не держит; тест; 5) живая проверка собранного установщика на стенде: показ, перемотка длинного HD,
    память окна.
  - Разведка перед кодом (2026-11-16, слово пользователя: «работай без остановок», проверка — его): FFmpeg из Qt —
    n9.0.1, собран без ограничений по форматам (строка `configure` в DLL), LGPL 2.1+; WebP он читает (`webp_anim`),
    но у ролика нет длительности (`Duration: N/A` — `MediaPlayer.duration` 0), и перемотка не показывает кадра (5 с
    ожидания на каждую, ролик стенда 960×960×73). MP4, который хаб делает из того же WebP для «Сохранить как»
    (`ffmpeg.to_mp4`), — 0,9 с, 0,7 МБ, длительность 3066 мс, перемотка к кадру за 0,34 с, кадр сходится с кадром
    ffmpeg; WebM от sd-server (`probe-webm.webm`) — так же. Переложить WebP в WebM без перекодирования нельзя: кадры
    sd-server (libwebp) — с прозрачностью (`ALPH`), наложением на прежний и рамкой меньше холста у части. Значит,
    ролики, что уже есть (и 960×960×73 пользователя), перематываются только через MP4 из WebP — выбор шага 2
    следует из его требования, WebM от sd-server помог бы лишь новым (и останется путём звука этапа 49).
  - ✓ 48.12.1 (сборка): `PySide6-Addons` 6.12.0 — в `app` (`pyproject.toml`) и в `requirements.lock`;
    `installer/build.py` (`player_only`) оставляет из него 11 файлов `PLAYER` — без Python-модуля и бэкенда Windows
    Media Foundation; файлы, что называет и RECORD Essentials (`PySide6/__init__.py`, — без него первая сборка упала
    «DLL load failed while importing QtCore»), не трогает; файла плеера нет (новый Addons) — сборка стоит со словами.
    Проверка собранной папки — `PLAYER_CHECK`: QML `MediaPlayer` показывает кадр WebP через FFmpeg («player: ok»).
    Лицензия FFmpeg (`installer/ffmpeg-license.txt`: какие DLL, версия, исходник, `configure`, текст LGPL 2.1) — рядом с
    лицензией Addons; README (обе) называют её. Установщик 85,16 → 92,01 МиБ (+6,85), папка +21,2 МиБ. Тесты —
    `test_the_program_takes_only_the_players_files_of_pyside6_addons`,
    `test_a_pyside6_addons_without_the_players_files_stops_the_build` (на настоящих RECORD Addons и Essentials).
  - ✓ 48.12.2 (хаб): играет MP4 из WebP — тот же, что «Сохранить как» (`Visual.mp4`, рядом с WebP; ffmpeg скачивается
    при первом, ~86 МБ); WebP остаётся файлом видео — «Сохранить как», вложения чата, срок хранения и первый кадр в
    ленте не меняются. Карточка чата показывает копию чата (`data/blobs`), не задание: `GET
    /v1/visual/pictures/<sha256>/mp4` (`Visual.blob_mp4`, `HubClient.visual_picture_mp4`) делает её MP4 так же, один
    раз, рядом (`<sha256>.mp4` — его не называет сообщение: уходит со старыми файлами через `days` и делается снова).
    Тест — `test_a_chats_video_has_its_mp4_as_a_studios_does` (на старом коде — 404).
  - ✓ 48.12.3 (окно, Студия): `VideoPlayer.qml` — `MediaPlayer` + `VideoOutput` над MP4 (`Visual.videoMp4`: файл есть —
    играет, нет — окно просит хаб, после привязки, один раз; не вышло — снова при следующем открытии Студии), повтор
    (`loops: Infinite`); пока MP4 нет — WebP (`Visual.videoFile`) через `AnimatedImage` с `cache: false`: кадр за кадром,
    не хранит, не перематывает; скрыт сам или выше (Студия) — ролик отпущен. `VideoControls.qml` — пауза, ползунок по
    времени (мс), время или, пока MP4 нет, почему не перематывает (`Visual.videoNote`: «готовлю перемотку… В первый раз
    AFO скачает ffmpeg (~86 МБ)», «без перемотки: …»). `SEEKABLE_BYTES`, `videoSeekable`, `videoFps`, `videoSource`
    ушли. Тест — `test_the_studios_player_goes_to_any_frame_and_keeps_none` (вместо GIF 8×8 48.7г): настоящий MP4 —
    тесты ffmpeg не запускают, `mjpeg_mp4` пишет тот же контейнер (ISO MP4, время кадра в таблице) с кадрами JPEG Qt,
    каждый своего цвета (ffmpeg стенда читает его: 12 кадров, 1,5 с); перемотка к 5, 2, 9, 0, 11 — показан кадр того
    цвета (кадр из `videoSink`), время, повтор после последнего, Студия скрыта — `source` пуст; на старом коде — MP4 не
    просится.
  - ✓ 48.12.4 (окно, чат): карточка `toolVideo` (`ToolCalls.qml`) — тот же `VideoPlayer` и `VideoControls` под вызовом:
    копия чата (`data/blobs`) кадр за кадром без хранения, пока хаб не сделал её MP4 (`Visual.showVideo` → `chatVideos`
    — {mp4, note} по sha256), потом MP4 с паузой, перемоткой и временем; размер карточки — по кадру ролика
    (`VideoPlayer.frame`); чат скрыт — ролик отпущен. Тест —
    `test_a_chats_video_card_plays_on_the_same_player_and_keeps_no_frames` (на старом коде — MP4 не просится; там
    `AnimatedImage` хранил все кадры в полном размере и перемотки не было).
  - ✓ 48.12.4б (окно, память): замер без моделей (процесс на вариант, `psutil` private, 6 с показа, 420 точек):
    ролик 960×960×73 — старая карточка (`AnimatedImage`, кэш) +280 МиБ, WebP без кэша +26, новый плеер над MP4 +165;
    1280×704×49 — старый +192, новый +158; MJPEG 64×64 — +13. Разница — аппаратное декодирование: FFmpeg Qt по
    умолчанию берёт карту (d3d11va) — +178 МиБ, на процессоре +74 (ядер 2–24 — 118–165 с картой, то есть не потоки).
    Окно и так рисует процессором, чтобы видеопамять оставалась моделям (D3), — видео теперь тоже:
    `use_software_rendering` ставит `QT_FFMPEG_DECODING_HW_DEVICE_TYPES=,` — список без устройств (пустое значение
    Windows выбрасывает — проверено: +174; на `none` окно стенда при старте писало в журнал «Unknown hw device type»),
    если человек не задал своё. Тест — `test_the_window_decodes_a_video_on_the_cpu`: плеер в своём процессе, начатом
    как окно, — «No HW decoder found» и ни слова о неизвестном устройстве.
  - Полный прогон после 48.12.1–4б (без `tests/test_computer.py`, 2026-11-16): 1281 тест, все прошли. Сборка для живой
    проверки — `build\dist\AFO-Setup-0.1.13.exe` 92,00 МиБ (до 48.12 — 85,16), проверки папки: imports, окно без
    предупреждений QML, «player: ok». На стенде окно собранной программы — `D:\AFO-stand48\stand\window_build.py`
    (изоляция `window.py`, `build\AFO\python\pythonw.exe -m app`). Шаг 5 (живая проверка) — за пользователем.
  - ✓ 48.12.6 (сборка, до выпуска — слово пользователя; найдено при 48.12): README (обе) говорят, что тексты лицензий
    библиотек — рядом с ними, а колёса Qt (PySide6-Essentials, -Addons, shiboken6) несут только
    `LicenseRef-Qt-Commercial.txt`. `installer/qt-license.txt` — какие файлы, версия 6.12.0, где исходники, что LGPL 3
    опирается на GPL 3 (её текст — `LICENSE` AFO) и текст LGPL 3 (взят у pyte, он его несёт) — `qt_licences` кладёт в
    `licenses` каждого из трёх, RECORD его называет; FFmpeg — с 48.12.1 (`ffmpeg-license.txt`). Тест —
    `test_qts_licence_lies_beside_each_qt_library`.
  - ✓ 48.12.7 (окно, живая проверка п. 1, 2026-11-16): ролик 960×960 — первая сцена, потом белое, ползунок идёт.
    Разбор по кадрам: 1) MP4 хаба цел — кадры 0, 1, 2, середина и последний (ffmpeg стенда → PNG) сходятся с кадрами
    WebP, как их накладывает Qt (среднее и отличие от первого — в 1–2 из 255), на глаз — сцена; так у всех шести
    роликов стенда. «Сохранить как MP4» белого не отдавало ни с какого коммита. 2) окно — программный граф сцены (D3)
    `VideoOutput` не рисует вовсе (он рисует шейдерами карты): снимок окна — один цвет фона все 2,5 с, а `videoSink`
    получает верные кадры (~300 цветов); так и при декодировании процессором, и картой (8e12f6c ни при чём); через
    графический API (RHI) тот же ролик виден. Первая сцена — WebP в `AnimatedImage`, пока хаб делал MP4. Пусто — с
    48.12.3 (`4c5b35f`, Студия) и 48.12.4 (`404244b`, карточка чата): тесты 48.12 смотрели в `videoSink`, не на
    картинку. Починка — `app/ui/video_surface.py`: `VideoSurface` (`QQuickPaintedItem`, QML `import AFO.Video`) —
    свой `QVideoSink` берёт кадры плеера, последний рисуется `QPainter` по форме кадра, держит один кадр; `frameSize` —
    форма карточки чата; `VideoPlayer.qml` — он вместо `VideoOutput`. Для этого в программе Python-модуль Qt Multimedia
    (`PySide6/QtMultimedia.pyd`, 566 КиБ; грузит лишь то, что в программе уже есть) — в `PLAYER`; проверка папки
    (`PLAYER_CHECK`) смотрит на нарисованное: кадр в две краски, окно рисует процессор (со старым `VideoOutput` — «the
    player drew no frame»). Замер (как окно: программный граф, процессор декодирует, 420 точек, ~25 кадров в секунду,
    6 с): 960×960 — +63 МиБ (без рисования +62), 13% ядра (11); 1280×704 — +66 (+61), 20% (15). Тесты:
    `test_the_studios_player_goes_to_any_frame_and_keeps_none`,
    `test_a_chats_video_card_plays_on_the_same_player_and_keeps_no_frames` — кадр смотрят на снимке окна (`drawn`):
    кадр из середины не одного цвета (у кадров `mjpeg_mp4` тёмная полоса) и
    не первый, перемотки — по цвету нарисованного (на старом коде: Студия — отличие от первого 0,0, карточка — один
    цвет); `test_each_qt_module_the_programs_code_imports_is_in_the_program` (на старом `PLAYER` — нет `QtMultimedia`);
    `test_a_videos_mp4_has_each_frame_as_the_webp_lays_it` (`tests/test_ffmpeg.py`, настоящий ffmpeg из PATH; нет его —
    пропуск): WebP как у движка (кадры меньше холста, прозрачные края, наложение на прежний) → MP4 хаба: середина не
    одного цвета и не первый кадр, половины первого и все квадраты на месте. На старом коде он проходит — пересборка
    цела; кадры без наложения (сброс к фону) он ловит — проверено.
  - Полный прогон после 48.12.7 (без `tests/test_computer.py`, 2026-11-16): 1284 теста, все прошли. Сборка для
    повторной живой проверки (п. 1 и 2) — `AFO-Setup-0.1.13.exe` 92,13 МиБ (+0,12 — `QtMultimedia.pyd`), проверки
    папки прошли («player: ok» — по нарисованному кадру); её Python и её `app` на ролике стенда 960×960 рисуют сцену
    (~260 цветов) и в окне Windows, не только offscreen.
  - ✓ 48.12.8 (окно, живая проверка п. 1–5 прошла, 2026-11-16; два недочёта плеера). 1) Ролик стартовал дважды: ~1 с
    кадры, потом с начала — окно играло WebP, пока хаб делал MP4 по первому показу, затем MP4 с начала. Теперь хаб
    делает MP4 сразу после съёмки (`_run_video` → `_mp4_now`), если ffmpeg уже есть: первый (~86 МБ) скачивается при
    первом показе — окно попросит, — а не перед ответом модели в чате. Ролик чата получает свой MP4 до того, как
    модель слышит «снято»: копия MP4 задания рядом с копией чата (`_chat_mp4`, имя — sha256, как у `attachments.keep`).
    Пока MP4 нет (старые ролики, первый без ffmpeg) — первый кадр WebP стоит, подпись «готовлю перемотку…»; MP4 не
    сделать (нет сети) — `videoMp4` и `chatVideos` отдают сам WebP, он играет без перемотки, как до 48.12. 2) Перемотка
    дёргалась. Замер на ролике стенда 960×960, как играет окно (программный граф, декодирует процессор,
    `VideoSurface`): переход в середину на паузе — 15–20 мс с одним опорным кадром (x264 ставит его раз в 250 —
    у роликов стенда он один, кадр 0), ~11 мс с опорным каждым; оба быстрее кадра. Причина другая: ползунок просит время
    на каждое движение мыши — экраны этого компьютера 200–240 Гц, движение каждые ~4 мс, — и каждое новое
    `setPosition` бросало недоделанное: протяжка через ролик за 1,9 с — 2 кадра, отставание до 1,5 с (с опорным каждым
    кадром — 48 кадров, до 0,6 с). Теперь `VideoPlayer.seek` держит у плеера одно время: следующее — когда пришёл кадр
    прежнего (или через 250 мс), последнее из попрошенных; ползунок стоит на нём (`position` — `wanted`). С этим — 66–67
    смен кадра на ~66 кадров пути, ползунок назад не прыгает, отставание — медиана 41 мс (опорный один) и 27 мс
    (каждый), не больше 144 мс. Опорный каждый кадр тоже взят (`-g 1`): MP4 втрое больше (ролик 3 с 960×960 — 2,3 МБ
    вместо 0,76). MP4, сделанные раньше, остаются с одним опорным — перемотка в них тоже ровная (41 мс). Файлы: п. 1 —
    `hub/visual.py`, `app/ui/visual_view.py`, `app/ui/qml/VideoPlayer.qml`; п. 2 — `hub/ffmpeg.py` и тот же
    `VideoPlayer.qml`. Тесты (на старом коде падают все): `test_a_videos_mp4_is_made_as_it_is_filmed_and_a_chats_card_has_it_at_once`
    (MP4 не было); в тестах Студии и карточки чата — `stands_still` (WebP движка, кадр — цвет: на старом коде
    нарисованы цвета 0…7) и `begins_once`; `test_the_slider_dragged_to_the_middle_shows_the_frames_on_its_way`
    (настоящий ffmpeg, аргументы хаба, 960×960, мышь каждые 4 мс от начала до середины: не меньше 12 кадров по пути из
    36, отставание не больше 0,4 с, средний кадр не позже 0,3 с после остановки; на старом коде — 1 кадр);
    `test_a_video_whose_mp4_cannot_be_made_plays_its_webp`; `-g 1` и все кадры опорные — `test_a_webp_into_mp4`,
    `test_a_videos_mp4_has_each_frame_as_the_webp_lays_it` (на старом — 1 из 9).
  - Полный прогон после 48.12.8 (без `tests/test_computer.py`, 2026-11-16): 1287 тестов, все прошли.
- Стабилизация — список этапа (слово пользователя 2026-11-15; сам этап и порядок — по его слову):
  - где окно говорит, что фоновая работа ждёт (строка подбора, строка установки «ждёт пробного запуска»), назвать
    причину словами: «ждёт: запущена игра <имя>» (48.9б3). Больше пары строк: `card.game` должен отдавать имя игры,
    `job.why` подбора и слова пробы — нести его, окно — показывать; не делалось. У пробы слова без имени есть с
    48.9б5 («ждёт: на видеокарте игра»); имя игры и подбор — открыто.
  - ✓ 48.9б5 (было «мешает человеку»): «Пробный запуск» по кнопке при открытой игре грузил модель рядом с игрой, а
    ответ потом не брался (48.9б4), строка — «ждёт пробного запуска» без слов (снимок окна 2026-11-16). Теперь модель
    не грузится, строка говорит «ждёт: на видеокарте игра», проба ждёт тишины.
  - тяжесть «ломает функцию»: второе «Добавить файл…» сразу за первым не получило задания (плавающее падение
    `test_an_ollama_model_is_named_by_ollama_not_by_its_file` в полном прогоне после 48.9б3; отдельно прошёл 7 раз из
    7). Причина не установлена: ответ хаба в том прогоне не сохранился.
  - найдено при 48.9е: рассуждение Kimi-VL-A3B-Thinking приходит в тексте ответа, с её метками («◁think▷Хорошо,
    пользователь спрашивает…») — llama-server b11899 не отделяет их в `reasoning_content`, а хаб берёт рассуждение
    только оттуда (`openai_compat`); в чате оно — часть ответа, не свёрнутое «думает».
  - тяжесть «мешает человеку» (слово пользователя при 48.10): у задания Студии «хаб перезапускался — … не нарисована /
    не снято» нет кнопки «Повторить» (по коду: кнопки под кадром — «Ещё вариант» и др. — видны только у готового,
    `Visual.done`).
  - найдено при 48.12: плеер (Студия, карточка чата) отпускает ролик, когда скрыт его вид, но не когда окно AFO убрано
    в трей или свёрнуто — ролик крутится и декодируется дальше (так было и с `AnimatedImage`).
  - найдено при 48.12.8: пока MP4 нет, ролик стоит, а подпись говорит «готовлю перемотку…» — вернее «готовлю видео…»;
    не менялась: это ключ каталога `common/locale/en.json`, четвёртый файл шага.
  - Порция 0.1.15 — шаги 1–8 плана `D:\AFO-plans\plan-0.1.15.md` (вне репозитория). Правило на шаги 5–8 (слово
    пользователя 2026-11-16, при шаге 4): разведку по Правилу 1 сессия пишет, но «начни» пользователя не ждёт, если шаг
    не расходится с планом; на вопросы, где у неё есть совет, действует по совету и пишет об этом в отчёте.
    Останавливается и спрашивает, только если тест не падает на старом коде, нужна настоящая модель, правка выходит за
    файлы шага или затрагивает папку `data/` пользователя или GitHub. Каждый шаг — отдельный коммит; весь
    `pytest --ignore=tests/test_computer.py` — один раз в конце порции; в GitHub — ничего.
  - ✓ 0.1.15, шаг 1 (найдено пользователем при выпуске 0.1.14; слово — «начни», четвёртая фраза — его выбор):
    пресеты «Быстрее», «Баланс» и «Качественнее» ставили все слои на видеокарту, даже когда модель в неё не
    помещается, — модель работала в разы медленнее. Теперь «все» — только когда модель с контекстом и кэшем пресета
    помещается целиком по той же оценке, что внизу листа (запас памяти человека, черновая модель, эксперты в ОЗУ,
    проектор), иначе «сколько поместится», как у установщика (`launch_plan`); карта или файл модели не известны —
    тоже «сколько поместится». Под пресетами — строка вердикта (`launch_verdict`): «Поместится в видеокарту целиком —
    быстро.», «Поместится не вся: часть ляжет в обычную память — в несколько раз медленнее.», «Не поместится: с этими
    настройками модель больше видеокарты. Выберите „сколько поместится“ или модель меньше.», у модели с экспертами в
    ОЗУ — «Поместится: часть модели в обычной памяти — так она быстрее всего.»; из описания пресетов убрано «целиком
    на видеокарте», добавлено «На видеокарту ляжет столько модели, сколько поместится». Файлы — `app/ui/model_setup.py`,
    `ModelsSheet.qml`, `common/locale/en.json`. Тесты — `test_a_preset_puts_all_of_a_model_on_the_card_only_when_it_fits`
    (окно без экрана: 6 ГБ под модели, модель 12 ГБ, «Баланс»; на старом коде — «все»),
    `test_whether_a_local_model_fits_the_card_in_plain_words`, `test_faster_or_better_for_a_local_model`.
  - ✓ 0.1.15, шаг 2 (слово — «начни», окончание текста — рекомендованное): «Записать в config.yaml» с настройками
    больше видеокарты (строка под пресетами — «Не поместится») сначала спрашивает: «Модель не поместится в
    видеокарту» — «С этими настройками модель больше видеокарты: она будет работать в разы медленнее или не
    запустится. Надёжнее выбрать „сколько поместится“ или модель меньше. Записать всё равно?», кнопка «Записать всё
    равно»; без неё ничего не пишется, «Отмена» или Escape — «Не записано.». Пишется то, что было на экране при
    вопросе. «Поместится не вся» (медленнее, но запустится) и неизвестная карта — пишется сразу, как раньше. Файлы —
    `app/ui/model_setup.py`, `common/locale/en.json`. Тест — `test_writing_a_model_larger_than_the_card_asks_first`
    (окно без экрана: 6 ГБ под модели, модель 12 ГБ на видеокарте целиком; на старом коде записано сразу, вопроса нет).
  - ✓ 0.1.15, шаг 3а (слово — «начни», после записанных настроек — рекомендованное): «Остановить» закрывал только
    работающий llama-bench — подбор шёл дальше (следующий проход, эксперты, ускорение), а половина замера
    записывалась провалом «llama-bench не дал замеров» — красная строка «Не подобрано». «Стоп» во время «Жду…»: ожидание
    шло дальше, подбор брал видеокарту и падал на пустой очереди — карта оставалась за подбором, разговоры на
    локальных моделях ждали её до перезапуска хаба. Теперь «Стоп» — не провал (`Tuner.cancel`, `_stopped`): ничего не
    сохраняется, прежний результат остаётся, модель снова сама не замеряется; ожидание кончается без карты (`_stops`),
    «Подобрать» сразу после — не теряется; «Стоп», когда настройки llama-bench уже записаны (проверка, эксперты,
    ускорение), сохраняет то, что они дали, — строка совпадает с config.yaml, шагов дальше нет, черновая модель, ждущая
    замера, не удаляется (`on_tuned` не зовётся). Окно — «Замер остановлен.». Проверку записанных настроек «Стоп» не
    прерывает — она кончается сама. Файлы — `hub/tuning.py`, `app/ui/model_setup.py`, `common/locale/en.json`. Тесты —
    `test_stop_is_not_a_failure` (настоящая программа вместо llama-bench, «Стоп» после первой строки: на старом коде —
    провал «не дал замеров»; «Стоп» во время «Жду…»: на старом — карта у подбора, `IndexError` в `_work`),
    `test_stopped_after_its_settings_were_written_keeps_what_they_gave`, `test_tuning_by_measurement_in_the_models_sheet`.
  - найдено при 3а: в `Tuner._work` между чтением очереди и `pop` — ожидание базы (автоподбор выключен?); «Стоп» в
    это мгновение — `IndexError`, подбор падает (карта не взята, следующий «Подобрать» работает).
  - ✓ 0.1.15, шаг 3б (слово — «начни»; «нет llama-bench» — рекомендованное; когда удалять черновик — слово
    пользователя): llama-bench не дал замеров, упал или его нет рядом с llama-server — подбор кончался, ускорение с
    черновой моделью, скачанной «Ускорить», не мерилось, а сам черновик после этого удалялся. Теперь сбой llama-bench —
    свой (`Tuner._bench_all`, `_tune_bench`): строка «Не подобрано: …», ничего не записано, а эксперты MoE и ускорение
    меряются дальше с настройками config.yaml. «Стоп» после такого сбоя — ничего не сохраняется (записано ничего не
    было). Черновик удаляется в одном случае: его замер дошёл до конца и выигрыша нет — `on_tuned` зовётся, только
    когда каждый черновик, ждущий модель, замерен до конца (`_drafts_measured`); сбой или «Стоп» основного замера или
    замера черновика, чат, Студия, игра — файл остаётся и ждёт, «Ускорить» снова берёт его с диска. Заодно: черновик,
    который llama.cpp не взял (другой словарь, 42.7), тоже остаётся и меряется при каждом подборе; из двух черновиков
    одной модели, когда один не замерен, ждут оба. Файл — `hub/tuning.py`. Тесты (на старом коде падают оба) —
    `test_llama_bench_failing_does_not_cancel_the_drafts_measuring` (настоящая программа вместо llama-bench: строки без
    замеров, падение, её нет; на старом — подбор кончен, черновик не запущен),
    `test_a_draft_goes_only_once_measured_to_its_end_without_a_gain` (черновики держит установщик хаба; на старом —
    удалён, когда настройки llama-bench не встали).
  - найдено при 3б: настройки, найденные llama-bench, не встали (проверка, конфиг возвращён) — подбор кончается,
    ускорение с черновиком не меряется (черновик теперь остаётся и ждёт). Это `Tuner._write` — та же ошибка, что
    «модель убрали из config.yaml», при которой дальше идти нельзя: их надо развести.
  - найдено при 3б: llama-bench упал без строк на первом типе кэша (q8_0) — второй (f16) уже не меряется, весь
    основной замер — сбой (`Tuner._bench_all`).
  - ✓ 0.1.15, шаг 3в (слово — «начни», рекомендованное: вошёл в 3б, кода нет): «черновик не стирается, пока его не
    замерили» — сделано шагом 3б. Черновик стирает только `Installer.tuned` → `prune_drafts`, а подбор зовёт его лишь
    при `_drafts_measured` и без «Стоп»; удаление и отмена установки модели черновиков не трогают. Тест шага («сбой
    подбора — старый код удаляет файл») на коде после 3б не падает: это проверяет
    `test_a_draft_goes_only_once_measured_to_its_end_without_a_gain`.
  - найдено при 3в: черновик, который помогал, после «Снять ускорение» стирается в конце следующего подбора любой
    модели (`prune_drafts`: команда его больше не называет, никто не ждёт), и следующий «Ускорить» качает его заново —
    расходится со словом пользователя при 3б «удалять, только если замерен до конца и не помог».
  - ✓ 0.1.15, шаг 4 (слово — «начни»; время — от начала текущего замера, слова для экспертов и починка поля —
    рекомендованные; подсказка о выгрузке — слово пользователя): строка подбора минутами стояла на «Меряю: 0 из ?» —
    хаб сообщал окну число замеров только с первой строкой llama-bench, а до неё модель грузится. Теперь хаб говорит
    его до запуска llama-bench, с шагом (`step`: `bench`, `moe`, `spec`) и временем начала каждого замера (`since`), и
    повторяет каждые `TICK_S` = 15 с (`Tuner._watch`). Окно (`measuring_words`, `elapsed_words`): «Готовлю замер…»;
    «Замер настроек: 0 из 6, идёт 3 мин. Сначала модель загружается в память — первый результат бывает через несколько
    минут…»; «Замер ускорения (угадывание вперёд): 1 из 3, идёт 5 мин…»; «Замер, какую часть модели держать в обычной
    памяти: 1 из 4, идёт меньше минуты…»; строкой ниже — «Модели на это время выгружены из видеопамяти, после замера
    вернутся сами.». Заодно: каждый замер перерисовывал весь лист настроек — число, набираемое в поле запуска,
    возвращалось к сохранённому; теперь ход замера перерисовывает только строку (`tuneChanged`). Файлы —
    `hub/tuning.py`, `app/ui/model_setup.py`, `common/locale/en.json`. Тесты (на старом коде падают все) —
    `test_the_measuring_says_how_much_there_is_before_its_first_line` (настоящая программа вместо llama-bench молчит,
    потом шесть строк; на старом — до первой строки «0 из 0»),
    `test_the_measuring_line_says_what_is_measured_and_how_long`,
    `test_a_number_being_typed_stays_while_the_tuning_measures` (окно без экрана, набор клавишами; на старом — «32768»
    вместо набранного «65536»).
  - найдено при 4 (по коду): так же весь лист перерисовывает ход скачивания черновой модели (`install.draft` →
    `ModelSetup._set_drafting` → `changed`) — набираемое в поле запуска число, по-видимому, возвращается к сохранённому
    и пока качается черновик.
  - ✓ 0.1.15, шаг 5 (по правилу шагов 5–8, без «начни»): кусок большого файла, у которого оборвалась связь, просился
    заново с начала — уже скачанные байты (до 64 МБ на кусок) качались второй раз, а число в окне откатывалось на них.
    Теперь кусок продолжается с места обрыва (`Progress.partial` — байты каждого начатого куска на диске, живут между
    попытками той же загрузки; запрос `Range` — с них), число в окне не уменьшается (`Progress.top`, `_shown`); заново
    с нуля — только когда файл выброшен (`_forget_pieces`, sha256 не совпал). Файл — `hub/hf.py`. Тест —
    `test_a_broken_piece_goes_on_from_where_it_broke` (подставной сервер рвёт связь одного куска; на старом коде все
    запросы — с начала куска).
  - найдено при составлении плана 0.1.15: после перезапуска хаба недокачанные куски (до 64 МБ каждый) качаются с
    начала — `Progress` новый, байты начатых кусков не записаны в `.parts`.
  - ✓ 0.1.15, шаг 6 (по правилу шагов 5–8): каждый «Ускорить» запускал свою работу — второй и третий щелчок, пока
    черновик качался, качали его снова в тот же файл и трижды просили подбор; до начала загрузки хаб молчал. Теперь у
    модели один «Ускорить» за раз (`Installer._speedups`; щелчок человека поверх автоматического — подбор сразу, без
    ожидания тишины), во всём хабе одна загрузка черновика за раз (`_draft_lock`; дождавшийся снова смотрит, не скачан
    ли уже черновик), с первой секунды — `drafting` «looking»: окно пишет «Ищу черновую модель для ускорения на Hugging
    Face…», кнопка «Ускорить» недоступна (`speedBusy`). Ход загрузки идёт из своего словаря, а не из `drafting`, который
    к тому времени может быть чужим. Файлы — `hub/model_install.py`, `app/ui/model_setup.py`, `common/locale/en.json`.
    Тест — `test_a_second_speed_up_starts_no_second_download` (три щелчка подряд; на старом коде подбор попрошен
    трижды).
  - найдено при составлении плана 0.1.15: «Ускорить» второй модели, пока качается черновик первой, ждёт молча — её
    «Ищу…» затирает событие загрузки первой (`drafting` у хаба один).
  - ✓ 0.1.15, шаг 7 (по правилу шагов 5–8): «Ускорить» для модели, которая не лежит на видеокарте целиком, сразу
    качал черновик — сотни мегабайт ради ускорения, которого, скорее всего, не будет: черновику тоже нужно место на
    карте. Теперь по тону строки под пресетами (поля равны config.yaml — с несохранёнными правками кнопка недоступна)
    он сначала спрашивает «Модель не помещается в видеокарту целиком»: «Поместится не вся…» (часть в ОЗУ) или «Не
    поместится…» (больше карты), кнопка «Ускорить всё равно»; без неё хабу ничего не уходит. Поместится целиком или
    карта не известна — как раньше, сразу. Файлы — `app/ui/model_setup.py` (`speedUp`, `SPEED_UP_ASKS`),
    `common/locale/en.json`. Тест — `test_speeding_up_a_model_that_does_not_fit_asks_first` (на старом коде запрос ушёл
    сразу).
  - найдено при составлении плана 0.1.15: после установки AFO сама качает черновик и для модели, которая не
    помещается в видеокарту (`on_done` → `speed_up` с `automatic=True`) — спросить некого, вернее не качать.
  - ✓ 0.1.15, шаг 8а (по правилу шагов 5–8): нажатая кнопка выглядела так же, как под курсором, — щелчок не был виден
    на самой кнопке, а ответ хаба приходил через секунды. Теперь, пока кнопку держат, у неё свой фон: у основной —
    темнее её цвета под курсором, у остальных — цвет выбранной строки (`Theme.p.item_selected`); новых цветов нет.
    Отпущенная в стороне — не щелчок, как было. Файл — `app/ui/qml/PlainButton.qml` (все такие кнопки окна). Тест —
    `test_a_button_shows_it_is_pressed` (окно без экрана, мышь; на старом коде цвет нажатой — тот же, что под курсором).
  - ✓ 0.1.15, шаг 8б (по правилу шагов 5–8): кнопка листа «Модели», которая спрашивает хаб, оставалась прежней, пока
    не придёт ответ, — при медленном хабе секунды, — и каждый щелчок слал запрос заново. Теперь (`ModelSetup._asked`,
    `pending`, `pendingText`) она сразу гаснет и пишет слово: «Ускорить: спрашиваю хаб…», «Снимаю ускорение…», «Подбор:
    спрашиваю хаб…», «Подбор для всех моделей: спрашиваю хаб…», «Останавливаю замер…», «Сохраняю…» (автоподбор,
    «Сохранить»), «Возвращаю версию…», «Выгружаю настройки…», «Загружаю настройки…»; второй щелчок, пока ответа нет,
    ничего не шлёт; запрос, ждущий свободного из четырёх потоков окна, тоже сразу виден. Слово — под рядом своей
    кнопки: подбора и ускорения (`pendingText`), сохранения и версий (`pendingSaveText`), файла (`pendingFileText`); их
    сигнал свой (`pendingChanged`) — лист не перерисовывается (как в шаге 4). При открытии модели — «Читаю настройки
    модели…» (`modelLoading`). У «Ускорить» слово — после «да» на вопрос шага 7. Файлы — `app/ui/model_setup.py`,
    `app/ui/qml/ModelsSheet.qml`, `common/locale/en.json`. Тест —
    `test_the_models_sheet_answers_at_once_while_the_hub_is_slow` (хаб отвечает через 3 с; на старом коде кнопка после
    трёх щелчков активна, окно молчит; пауза окна — меньше 0,25 с).
  - Полный прогон порции 0.1.15 после шага 8б (без `tests/test_computer.py`, 2026-11-16): 1302 теста, 1301 прошёл.
    Упал `test_unload_stops_tuning_and_it_waits_while_the_card_is_the_persons` (`tests/test_together.py`): второй замер
    не начался за 20 с. Отдельно — 3 из 3, весь `test_together.py` — прошёл; подставной замер этого теста заменяет
    `Tuner._tune`, правки порции его пути не трогают.
  - плавающее, найдено при полном прогоне 0.1.15: `test_unload_stops_tuning_and_it_waits_while_the_card_is_the_persons`
    в полном прогоне не дождался второго замера. По коду подбор ждёт тишины настоящей видеокарты (`GpuCard.load` —
    монитор хаба, порог `BUSY_PCT`): занятая в это время карта или нагрузка машины всем прогоном растягивают ожидание
    дальше 20 с теста. Причина не установлена.
  - Порция 0.1.16 — шаги 9а–12 плана `D:\AFO-plans\plan-0.1.16.md` (вне репозитория). Слово пользователя
    2026-11-16: 9а–11 — подряд без «начни», один отчёт в конце; перед шагом 12 (какую сборку llama.cpp ставит AFO) —
    остановиться и показать разведку. Остановки — как в 0.1.15 (тест не падает на старом коде, настоящая модель,
    правка вне файлов шага, папка `data/`). Каждый шаг — отдельный коммит; весь прогон — в конце; в GitHub — ничего.
  - ✓ 0.1.16, шаг 9а: кнопки листа «Установка моделей», которые спрашивают хаб («Добавить», «Пробный запуск», его
    остановка, поиск на дисках и его остановка, кнопки строк установок), оставались прежними до ответа хаба, и каждый
    щелчок слал запрос заново. Теперь (`InstallView._doing`, `pending`, `pendingText`, сигнал `pendingChanged` — лист
    не перерисовывается) кнопка сразу гаснет, слово — под кнопками вкладки «На этом компьютере» и над списком
    «Установки» («Добавляю модель…», «Пробный запуск: спрашиваю хаб…», «Останавливаю пробные запуски…», «Начинаю
    поиск моделей на дисках…», «Останавливаю поиск…», «Выполняю…», «Начинаю установку…»); второй щелчок ничего не
    шлёт. «Установить» в каталоге и у варианта пишет на себе «Устанавливается…» (поправка пользователя). Запас памяти
    («Настройки → Модели», четвёртый файл — по его слову): «Сохраняю запас памяти…» под полями; число, набранное,
    пока первое в пути, уходит после ответа (последнее побеждает), то же самое — никуда. Общий запрет «одна установка
    за раз» из плана не ставился: он молча терял бы вторую модель мастера первого запуска; повтор той же кнопки
    отсекается и без него. Файлы — `app/ui/install_view.py`, `app/ui/qml/InstallSheet.qml`,
    `app/ui/qml/SettingsModelsSheet.qml`, `common/locale/en.json`. Тест —
    `test_the_install_sheet_answers_at_once_while_the_hub_is_slow` (хаб отвечает через 3 с; на старом коде «Добавить»
    после трёх щелчков активна, окно молчит).
  - ✓ 0.1.16, шаг 9б: у окна четыре места для запросов к хабу; четыре долгих («Проверить запуск» — до 330 с, запись
    настроек, каталог) занимали все, и любая другая кнопка ждала их. Теперь запрос, которому клиент дал срок больше
    обычных 30 с (`LONG_S`, `HubClient._waiting`), на время ожидания отдаёт своё место другим и берёт его обратно
    после (`Background.waiting` — штатные `QThreadPool.releaseThread` / `reserveThread`, только внутри задачи этого же
    пула; окно подключает его в `Controller.attach`: `client.long_wait`). Запрос в 30 с место держит, как раньше.
    Очередь терминала (свой пул на один поток, клавиши по порядку) не затронута. Файлы — `app/tasks.py`,
    `app/hub_client.py`, `app/ui/controller.py`. Тест — `tests/test_tasks.py`,
    `test_a_long_request_does_not_hold_the_quick_ones` (настоящий HTTP-сервер и настоящий клиент: на старом коде
    быстрый запрос за четырьмя долгими ждал 3 с; счёт мест пула сходится и после ответа, и после оборванной связи).
  - ✓ 0.1.16, шаг 10 (рассчитано на 300+ моделей — слово пользователя): приход списка моделей держал окно 0,3 с при
    327 моделях — после каждой записи настроек, при смене того, что в видеопамяти. Причин две. Главная, которой не было
    в плане: привязка `App.pickerItems.concat(…)` (`Main.qml`, меню выбора модели) читала из Python весь список заново
    на каждую свою строку — 330 сборок списка по 1 мс (Qt 6.12: `.concat` над списком-свойством; `.map`, `.length`,
    чтение в переменную — по одному разу, проверено опытом). Теперь список читается один раз в переменную: 0,285 →
    0,005 с. Вторая — по плану: `MenuPopup.qml` строил строку на каждый пункт даже закрытым; теперь `ListView`:
    закрытое меню строк не держит, открытое — только видимые и немного вокруг; при открытии выбранная строка — в
    середине видимой части; строки пунктов названы `menuItem_<id>`, меню выбора модели — `modelMenu`. Меню — общее
    для 23 мест окна, чинится сразу везде. Файлы — `app/ui/qml/MenuPopup.qml`, `app/ui/qml/Main.qml`. Тест —
    `test_a_long_list_of_models_does_not_hold_the_window` (325 моделей; на старом коде закрытое меню держит 332 строки).
  - ✓ 0.1.16, шаг 11а: «Установить» у варианта, который ляжет на видеокарту не целиком, ставил сразу — в разы
    медленнее того, что помещается. Теперь сначала вопрос «Модель не поместится в видеокарту целиком»
    (`smaller_advice`, `_offer_variant`, `_advise`): есть рекомендованный вариант того же хранилища, который помещается,
    — «Поставить Q4_K_M» / «Всё равно Q8_0» / «Отмена»; нет такого — «Найти меньшую» (поиск на Hugging Face, размер
    «поместится») / «Всё равно поставить»; не поместится совсем — «Не поместится — выберите модель меньше…». Так же —
    «Установить» в «Подборке», когда её лучший вариант не целиком, и «Добавить» у найденной на дисках («Всё равно
    добавить»). «Примерно до {count} млрд параметров» — из `fits_params` хаба (`fits_count`, общий с размерами
    поиска). Не спрашивают (по совету, не по букве плана, где — только «целиком»): Mixture-of-Experts с экспертами в
    обычной памяти (выбор пользователя в 0.1.15 — для неё это быстрее всего) и компьютер без видеокарты (там все
    модели в обычной памяти). Без изменений: модели для рисования, «Добавить файл…», мастер первого запуска. Файлы —
    `app/ui/install_view.py`, `common/locale/en.json`. Тест —
    `test_installing_a_model_that_does_not_fit_offers_a_smaller_one` (на старом коде Q8_0 ставится сразу).
  - ✓ 0.1.16, шаг 11б: модель, которая уже поставлена или добавлена и легла на видеокарту не вся (любым путём:
    «Добавить файл…», поиск на дисках, «всё равно»), об этом молчала. Теперь её строка в «Установках» говорит
    «Поместилась не вся: часть в обычной памяти — в несколько раз медленнее. Меньшие модели: «Поиск» → размер
    «поместится».» (`advice_of`, поле `advice` в `job_rows`, `jobAdvice` в `InstallSheet.qml`), те же слова — в
    сообщении «Модель … установлена» и новом ««…» добавлена. …» для добавленной с диска. Признак — `plan.where`
    записанных настроек запуска: хаб пишет его и для добавленных с диска (`Installer._configure`), правка хаба не
    понадобилась. Mixture-of-Experts (в сведениях модели есть эксперты) — без слов, как в 11а. Файлы —
    `app/ui/install_view.py`, `app/ui/qml/InstallSheet.qml`, `common/locale/en.json`. Тест —
    `test_an_installed_model_that_did_not_fit_says_so` (на старом коде строка молчит).
  - Полный прогон порции 0.1.16 после шага 11б (без `tests/test_computer.py`, 2026-11-17): 1307 тестов, все прошли.
    Шаг 12 (какую сборку llama.cpp ставит AFO) — по слову пользователя только после его взгляда на разведку.
  - Шаг 12 — по слову пользователя 2026-11-17 («Начни»): 12а–12д подряд, один отчёт; добавлен 12д (его решение:
    ошибка «llama.cpp не читает этот файл модели» и сообщение подбора ведут на нужную кнопку сборки); живая проверка —
    один раз после 12д, отдельный хаб в пустой папке вне проекта качает настоящую b11899, папка потом удаляется. Шаги
    1–12 выходят одной версией 0.1.15 — версии 0.1.16 не будет. Разрешение «шаги подряд, разведка — в отчёт» записано
    в начале `D:\AFO-plans\plan-0.1.16.md`.
  - ✓ 0.1.16, шаг 12а: новая установка движка и «Обновить движок» брали самую новую сборку llama.cpp — их выходит
    несколько в день, AFO на них никто не проверял. Теперь — проверенная `LLAMA_TAG = "b11899"` (`hub/engines.py`,
    `llama_cpp_release`: сначала GitHub спрашивается про неё); её нет на GitHub или в ней нет архива для этой
    видеокарты — самая новая, в задании `unchecked` (слова в окне — 12в); самая новая — только по просьбе (`newest`).
    Что стоит, хаб узнаёт по номеру в коде, не спрашивая GitHub (одобренное отступление от плана): `update_info` —
    `checked`, `state` (`checked` / `older` / `newer` — не проверялась / `unknown`), `newer` — только при `older`,
    `latest` (самая новая; GitHub молчит — пусто, ответ всё равно есть), `newest_newer`. Сама AFO ничего не заменяет.
    Подставной GitHub тестов отвечает про одну сборку (`releases/tags/…`) или 404. Файлы — `hub/engines.py`,
    `hub/setup.py`. Тест — `test_the_engine_is_the_build_afo_was_checked_on` (на старом коде ставится b120, самая
    новая).
  - ✓ 0.1.16, шаг 12б: окно не могло попросить хаб о нужной сборке — «Обновить» ставил ту, что хаб выбирал сам. Теперь
    `POST /v1/setup/update` принимает `{"target": "checked" | "newest"}` (без тела — проверенная, как просит прежнее
    окно); менять нечего — 409 «проверенная сборка уже стоит» / «новее сборки нет» (GitHub не сказал, какая новейшая
    — не отказ: задание само скажет, почему не скачалось). Клиент — `setup_update(target="checked")`. Файлы —
    `hub/api/setup.py`, `app/hub_client.py`, `common/locale/en.json`. Тест — `test_the_window_asks_which_build_to_put`
    (на старом коде тело не читается и снова ставится проверенная).
  - ✓ 0.1.16, шаг 12в: мастер писал «llama.cpp … — самая новая сборка» или «есть новее» про самую новую. Теперь — про
    проверенную (`SetupView.updateText` по `state`): «— проверенная сборка: AFO проверена на ней.», «— есть
    проверенная новее: …» (кнопка «Обновить движок»), «— новее проверенной …: AFO на ней не проверялась. Сама AFO её не
    заменит.» и кнопка «Поставить проверенную b11899» (`wizardCheckedEngine`, `updateBack`, `checkedText`). Щелчок —
    сразу «Скачиваю сборку…», кнопки гаснут, второй щелчок до ответа хаба ничего не шлёт (`_put`, `_updating`).
    Проверенной на GitHub не было — после «Движок установлен…» / «Движок обновлён» слова «Проверенной сборки … на
    GitHub уже нет — поставлена …, AFO на ней не проверялась.» (`job_text`, поле задания `unchecked`). Файлы —
    `app/ui/setup_view.py`, `app/ui/qml/WizardSheet.qml`, `common/locale/en.json`. Тест —
    `test_the_wizard_says_which_llama_cpp_build_runs` (на старом коде у b12318 — «самая новая сборка»).
  - ✓ 0.1.16, шаг 12г: самую новую сборку человек поставить не мог. Теперь, когда на GitHub есть новее той, что
    стоит, в мастере строка «Новейшим моделям иногда нужна новая сборка llama.cpp. Самая новая сейчас — …» и кнопка
    «Поставить самую новую» (`wizardNewest`, `wizardNewestEngine`, `newestAvailable`); щелчок — сначала вопрос
    «Поставить самую новую сборку llama.cpp?» (AFO на ней не проверялась, прежняя остаётся рядом, назад — «Поставить
    проверенную»), по «Поставить …» — `setup_update("newest")` с той же защитой от второго щелчка (`putNewest`).
    Файлы — `app/ui/setup_view.py`, `app/ui/qml/WizardSheet.qml`, `common/locale/en.json`. Тест —
    `test_the_newest_llama_cpp_build_is_put_only_after_a_warning` (на старом коде кнопки нет).
  - ✓ 0.1.16, шаг 12д (решение пользователя 2026-11-17): слова «обновите сборку llama.cpp в мастере первого запуска»
    не говорили, какую кнопку нажать. Теперь ошибка «llama.cpp не читает этот файл модели» (`common/plain_errors.py`)
    — «Этой модели нужна сборка новее проверенной: нажмите «Поставить самую новую» в «Первом запуске». Если это файл
    Ollama… — возьмите эту модель в GGUF с Hugging Face.»; подбор и настройки о том, чего сборка не умеет (llama-bench
    рядом — `tuning.bench_path`, угадывание вперёд — `_tune_spec` и `llama_config._check_spec`, кэш экспертов MoE),
    говорят по сборке, что стоит (`engines.advice`, путь — `llama_config.server_of`): старше проверенной —
    «поставьте проверенную сборку b11899: «Обновить движок» в мастере первого запуска», проверенная или новее — «нужна
    сборка новее: «Поставить самую новую» в мастере первого запуска», номер не прочитать — прежние слова. Окно берёт
    слова хаба (`advice` в ответе подбора «unsupported»; `model_setup.spec_text`). В карточке ошибки кнопки нет — слова
    называют кнопку и место. Файлы — `common/plain_errors.py`, `hub/engines.py`, `hub/llama_config.py`,
    `hub/tuning.py`, `app/ui/model_setup.py`, `common/locale/en.json`. Тест —
    `test_what_the_build_lacks_points_to_the_build_that_has_it` (настоящие папки сборок; на старом коде везде
    «обновите сборку…»), `tests/test_plain.py`, `test_guessing_ahead_in_the_models_sheet`.
  - Живая проверка шага 12 (по слову пользователя, 2026-11-17): хаб в этом же процессе (TestClient), всё — в пустой
    папке во временном каталоге вне проекта (изоляция как `isolated_data_dir`, свой ключ «Графики», без опроса
    видеокарты, сборка `cuda-13` названа), GitHub — настоящий. `POST /v1/setup/engine` за 1,7 мин поставил
    `llama.cpp/b11899-cuda-13` (612 МБ вместе с llama-swap v273, sha256 сверены), `unchecked` ложь; `GET
    /v1/setup/update` — `state: checked`, `latest: b12445`, `newest_newer: true`. Папка и ключ удалены, процессов не
    осталось. Запуск llama-swap этой проверкой не проверен: путь к конфигу из изоляции (`LOCALCHAT_LLAMA_SWAP_CONFIG`)
    сильнее выбора мастера, llama-swap получил несуществующий файл и закрылся (`started: slow`) — так только при
    изоляции; на компьютере человека этой переменной нет.
  - Полный прогон порции 0.1.16 после шага 12д (без `tests/test_computer.py`, 2026-11-17): 1312 тестов, 1311 прошли.
    Упал `test_dxgi_sees_the_cards_the_registry_knows` (`tests/test_hardware.py`) — правки порции `common/hardware.py`
    не трогают; утром при прогоне после 11б он проходил. После перезагрузки компьютера реестр Windows записывает
    встроенной графике Intel (драйвер 32.0.101.9033 от 24.09.2026, не новый) 37 ГБ своей памяти
    (`HardwareInformation.qwMemorySize`), DXGI — 0,1 ГБ. Первый прогон этой порции оборвался на 5 % — компьютер
    выключился ночью.
  - найдено при полном прогоне после 12д: `hardware.adapters` берёт память карты из реестра, и встроенная Intel с 37 ГБ
    в реестре встаёт первой: мастер первого запуска и «Проверить систему» пишут «Intel(R) Graphics — 37 ГБ
    видеопамяти»; на компьютере только с Intel она прошла бы порог `MIN_VRAM_GIB` (Vulkan вместо процессора). Сколько
    модели помещается — не затронуто (это `nvidia-smi`), выбор сборки при отвечающем драйвере NVIDIA — тоже. Не
    чинилось. Исправлено к 0.1.16 по слову пользователя (2026-11-17) — «Исправление после 0.1.15», «Видеокарты»;
    проверка в окне — за ним.
  - Найдено пользователем 2026-11-17 в AFO 0.1.15 (слово — «запиши в „Стабилизацию“, не чини сейчас»): режим
    проекта, Qwen2.5-Coder-14B-Instruct-abliterated Q3_K_XL, контекст 32K, задача «search
    `https://en.wikipedia.org/wiki/Artificial_intelligence` and collect all data and write this in txt document». Его
    слова: «Модель описала шаги и вывела вызов web_fetch как текст в блоке json ({"name":"web_fetch","arguments":
    {"url":…, "start_char":0}}), затем попросила подтверждения. Инструмент не выполнился, файл не создан». Пять
    пунктов ниже — его словами (по Правилу 5); разведка — по коду, журналу хаба, копиям config.yaml и заголовку файла
    модели; разговор не читался.
  - тяжесть «ломает функцию». Слова пользователя: «Проверить по журналу хаба и коду: в каком виде AFO ждёт вызов
    инструмента и что именно прислала модель. Если модель пишет вызов текстом — AFO должна распознать JSON с именем
    известного инструмента и выполнить его по обычным правилам разрешений, а не показывать человеку». Разведка: хаб
    берёт вызов только из поля `tool_calls` ответа llama-server (`hub/gateway/openai_compat.py` 270–275), остальное
    идёт в чат текстом (290–291); это поле llama-server заполняет сам, когда узнаёт вызов по формату шаблона модели.
    Шаблон в файле этой модели инструменты принимает и просит вызов в метках `<tool_call>{"name": …, "arguments":
    …}</tool_call>`; модель написала тот же JSON без меток, в блоке json, — b11899 вызовом его не сочла. Инструменты
    модели были переданы: имя `web_fetch` и поле `start_char` — из описания AFO (`hub/web.py` 54). Журнал хаба ответов
    модели не пишет (в `hub.log` за 15:24–15:43 о разговоре ни строки) — что именно пришло от llama-server, показать на
    стенде с этой моделью.
  - тяжесть «мешает человеку». Слова пользователя: «В списке моделей нет отметки, какие умеют работать с
    инструментами. В режиме проекта предупреждать, если выбранная модель не умеет». Разведка: у локальной модели хаб
    такого признака не знает (`capabilities` — `hub/gateway/openai_compat.py` 168–174). Опора: хаб уже читает шаблон
    чата каждой модели ради глубины (`llama_config.model_efforts`, 0.1.13) — по нему видно, принимает ли модель
    инструменты; без `--jinja` не принимает никакая. Но шаблон этой модели их принимает — отметка по шаблону её не
    выделила бы: сбой пункта выше — в том, как она пишет вызов.
  - тяжесть «мешает человеку». Слова пользователя: «Модель сама просит подтверждения словами („Please confirm“), хотя
    для этого есть режим „Спрашивать“. В указаниях модели: не просить подтверждения текстом, вызывать инструмент».
    Разведка: указание модели в чате с проектом (`TOOLS_NOTE`, `RIGHTS_NOTES["ask"]` — `hub/context.py` 52–66) говорит
    только «the user approves each»; слов «не проси подтверждения текстом, вызывай инструмент» нет — они есть у
    автономных задач (`hub/autonomous.py` 82, `hub/chat.py` 1671).
  - Слова пользователя: «Строка состояния показывает „llama-swap не отвечает“, когда модель только что ответила (после
    смены контекста в настройках). Проверить, обновляется ли индикатор». Разведка (смена контекста — 15:41): таких
    мест два. Строка состояния внизу окна по коду не застывает: хаб каждые 2 с спрашивает llama-swap (`/running`, ждёт 2 с)
    и шлёт окну новое, как только оно другое (`hub/system_status.py` 134–145, 166–187); «не отвечает» — порт закрыт
    или ответ дольше 2 с. Строка листа «Модели» после записи — «Записано, но не проверено: llama-swap не отвечает…»
    (`app/ui/model_setup.py` 867; хаб ждёт, пока llama-swap перечитает файл, до 15 с — `llama_config.reloaded`) —
    пишется один раз и стоит до следующего действия: ответ модели её не меняет. Какую из двух видел пользователь и
    почему llama-swap не ответил при записи — не установлено, показать на стенде.
  - Слова пользователя: «Контекст 8 тыс. по умолчанию в режиме проекта при свободной видеопамяти: описание
    инструментов занимает 2,1 тыс. до первого сообщения». Разведка: своего контекста у режима проекта нет — это
    контекст модели в config.yaml. По копиям config.yaml: до 16.11 16:24 у этой модели было 32768; запись в 16:24
    поставила 8192 и кэш q8_0 — ровно пресет «Быстрее» (`LAUNCH_PRESETS`, `app/ui/model_setup.py` 243); запись 17.11 в
    15:41 — 32768, f16 и слои «сколько поместится» (`--fit on` вместо `-ngl 99`; почему сменились слои — не
    смотрелось).
  - Жалоба пользователя, давняя (записана 2026-11-17; тяжесть — «ломает функцию», его слово). Его слова: «Режим
    проекта, чат „web“, выбран режим „Доверенный“, модель Flash-Next Coder (abliterated). На каждом шаге появляется
    окно разрешения: „Править scrape_wikipedia.py“ с кнопками „Разрешить“, „Разрешить: все edit_file в чате“,
    „Разрешить всегда в «web»“, „Отклонить“. Файл лежит внутри папки проекта». Дополнение: «Я нажал „Разрешить всегда в
    «web»“, но окна разрешений продолжили появляться на следующих шагах. … Режим проектов с вопросом на каждом шаге
    непригоден для работы „до результата“». Его решение: «„Доверенный“ не спрашивает ни о чём внутри папки проекта:
    чтение, запись, правка, запуск команд; спрашивает, только если действие выходит за папку проекта; при включении
    режима один раз словами: „Модель сможет менять файлы и запускать команды в проекте без вопросов. Команды могут
    затронуть и остальной компьютер“; смена режима действует сразу, в том числе на идущий ответ». «„Разрешить всегда в
    «web»“ для правки файлов снимает вопросы на все правки в этом проекте, для команд — на все команды в этом проекте.
    Название кнопки должно говорить, что именно разрешается».
    Диагноз (по коду; в TechDoc о «Доверенном» не было ни слова; разговор не читался). «Доверенный» (`trusted_skips` —
    `hub/approvals.py` 240; `hub/chat.py` 1102–1300) без вопроса правит файлы проекта, запускает команды (сетевые
    тоже), читает сайты и ищет, если в чате нет проекта «только локально»; спрашивает разрушительные команды
    (удаление, `git push`, `reset --hard`, команду, которая запускает не названное в строке), команды о данных AFO,
    запись в файл с несохранёнными правками редактора — и всё, правки и команды, после того как чат прочитал
    написанное чужими: страницу, поиск, письмо, ответ API, чужое окно (`read_the_web`, `hub/chat.py` 1312; D19). Тогда
    же ответы «для чата» и «всегда» не действуют: при запросе не читаются (`cover=False`), а «всегда» не записывается
    вовсе — `_close` делает его «один раз» (`hub/approvals.py` 663–676), хотя окно эти кнопки показывает
    (`app/ui/approvals.py` 170–177). Задача о Википедии почти наверняка читала сайт — отсюда окно на каждой правке. В
    чистом чате «всегда» пишется в `permission_rules` и читается при следующем запросе (`_rule_allows`), но ключ у
    правок — имя инструмента (`edit_file` и `write_file` — разные), у команд — точная команда (`grant_key`, 315–328):
    новая команда спрашивается заново. «Разрешить: все edit_file в чате» — все `edit_file` этого чата, пока он не
    читал сайт. Режим читается из базы при каждом действии (`store.context_settings`, `hub/chat.py` 1106, 1198):
    следующее действие идущего ответа берёт новый режим, но окно, открытое в момент смены, остаётся.
    Воспроизведение (2026-11-17; настоящий хаб, настоящий `web_fetch` на маленьком сайте этого компьютера, настоящие
    терминалы, модель — по сценарию; тесты в репозиторий не вошли): прошли «Доверенный» в чистом чате и «следующее
    действие идущего ответа берёт новый режим»; упали «Доверенный» после чтения сайта (окно `edit_file`), «всегда» на
    правки (`write_file` спрошен), «всегда» на команды (`Write-Output two` спрошена), «всегда» после чтения сайта
    (правил — 0, окно снова), «все edit_file в чате» после чтения сайта, окно, открытое при смене на «Доверенный».
    Не чинилось: шаги — по слову пользователя.
  - Жалоба пользователя 2026-11-17, AFO 0.1.15, основной ПК (RTX 5080). Его слова: «установить видеомодель через окно
    нельзя». «Студия, пустое состояние: „Модели для картинок ещё нет“ и кнопка „Поставить модель картинок“. Про видео
    ни слова, хотя Студия снимает видео». «Выбираю „Поиск на Hugging Face“, фильтр „Видео“. Вижу репозитории:
    unsloth/LTX-2.5-GGUF, QuantStack/Wan2.2-I2V-A14B-GGUF и другие, под ними зелёным „AFO ставит её сам — „Установить“ в
    „Рисовать““». «Щёлкаю по unsloth/LTX-2.5-GGUF. Вместо выбора варианта и установки открывается подборка с фильтром
    „Рисовать“: SDXS 512, FLUX.2 klein 4B, Z-Image-Turbo, Wan2.2 TI2V 5B Turbo. LTX-2.5 там нет. При этом подсвечена
    вкладка „Поиск на Hugging Face“». «У MiniMax-H3-Pruned написано „≈ 35,7 ГБ с кодировщиком и VAE — помещается в
    видеопамять — быстро“, у hyperflow-h3-flash — „≈ 32,0 ГБ… помещается“. Карта 16 ГБ, свободно 14,9 ГБ». Дополнение:
    «В 0.1.13 щелчок по такой строке вёл туда же, в подборку „Рисовать“, но тогда других моделей картинок и не было».
    Диагноз (окно без экрана на своём хабе в пустой папке, настоящий поиск Hugging Face, щелчки мышью): на месте пути
    47.7б–48.7д остался переход 0.1.13 — `openFound` (`app/ui/install_view.py` 1162–1167) сначала смотрит `picture`
    и ведёт в «Рисовать», ветка `family` (варианты) — ниже; хаб даёт `picture` всякому репозиторию главного файла
    семейства (`hub/model_install.py` 1118–1121), а 47.8 и 48.6 добавили 26 семейств без карточек — с v0.1.14 у 24
    репозиториев (самые скачиваемые: unsloth LTX, QuantStack Wan, city96 Wan2.1, leejet FLUX и Qwen) щелчок ведёт в
    «Рисовать», где их нет; v0.1.13 — тот же переход при четырёх наборах с карточками. Тесты окна подставляли ответ хаба
    с пустым `picture` у этих репозиториев. Вкладка: `Segmented` сам пишет `value` по щелчку
    (`app/ui/qml/Segmented.qml` 20) и рвёт связь `value: Installer.tab` — программа меняет вкладку, подсветка стоит.
    «35,7 ГБ помещается»: на карту по правилам 47.9 идёт одна модель (12,3 ГБ + 2 + 0,5 против 14,4), кодировщик 18,2 и
    VAE 5,2 — в ОЗУ; слова говорят о всех 35,7. Расчёт на границе оптимистичен: у LTX-2.3 distilled на 48.8 вердикт
    «быстро», движок положил всё в ОЗУ — место «карта минус 1 ГБ» при рабочем столе 1,3 ГБ, кусок кодировщика 0,5 ГБ
    при ≈ 0,8 у gemma. «14,9 ГБ» — не свободное, а «карта минус запас». 47.9 и 48.8 ставили модели через хаб (скрипт
    стенда `one.py`), окно не участвовало. Исправление — раздел «Исправление после 0.1.15: модели картинок и видео из
    поиска» ниже. Попутно: репозиторий кодировщика joeygambino/MiniMax-H3-encoder-GGUF поиск называет моделью
    MiniMax-H3 («≈ 43,5 ГБ»), как AtomicChat в 47 — не чинилось.
  - Жалоба пользователя 2026-11-18, AFO 0.1.16, этот компьютер (RTX 5080). Его слова: «вчём проблема» — снимок
    карточки «LTX-2.5 Q3_K_M — загружается — 0,3 ГБ из 22,1 ГБ · 1,0 МБ/с · ещё ≈ 359 мин». Диагноз (по событиям
    `install.changed` его `hub.db` — копия, только чтение; файлы `engines/downloads`; замеры с этого компьютера):
    установка начата в 14:36, отменена им в 14:39. Первые файлы задания — программа для видео с GitHub (три архива sd.cpp,
    0,94 ГБ); GitHub отдаёт этому компьютеру 0,6 МБ/с на соединение и 8,9 МБ/с на восемь, Hugging Face — 6,5 и 35
    (кусками по 16 МБ). Архив 363 МБ — шесть кусков по 64 МБ: пять пришли, шестой стоял на 56 МБ из 64 — к концу файла
    работало одно соединение. Окно считало время всех 22,1 ГБ по скорости GitHub. Второе, хуже: кодировщик Gemma 4 12B
    (8,4 ГБ) лежит в закрытом репозитории elix3r/gemma4-12b-with-proj-ltx-2.5-GGUF (`gated: auto`, без ключа — 401);
    ключа Hugging Face в его AFO нет — после программы и модели (≈ 12 ГБ) установка встала бы «модель закрыта». Хаб
    смотрит закрытость только главного репозитория найденной модели. Исправление — раздел «Исправление после 0.1.16»
    ниже (решение пользователя 2026-11-18: все три шага, вместе с 0.1.17).
- Найдено проходом по всем переключателям окна (шаг 2 исправления после 0.1.15, 2026-11-17), не чинилось: пока над
  экраном агента карточка одобрения, низ экрана (панель «когда» под вводом) не прокручивается в вид — высота
  содержимого `column.height + 48` (`app/ui/qml/AgentView.qml` 161) не считает места карточки, а колонка сдвинута на
  него (170; `reserveTop` — `Main.qml` 132). Переключатель «Когда» панели расписания (`scheduleWhen`,
  `SchedulePanel.qml` 31) не показывается нигде: единственная панель — под вводом агента, с `chooser: false`.
- Найдено перед 48.8 (2026-11-15), не чинилось — по слову пользователя: «Выход» в трее не закрывает AFO. Пользователь
  нажал его дважды — окно (`pythonw -m app --tray`, запущено из Проводника в 13:39), хаб (8766) и llama-swap (8080)
  живы, в `data/logs/app.log` после запуска нет «the window closed». Для 48.8 окно и хаб остановлены вручную, как
  делает «Выход» (окно, затем `POST /v1/shutdown`), llama-swap пользователя — только выгружен.
- Неудобно (из ревью, открыто): слова отказа у Wan2.1-FLF2V с одним первым кадром; кадры отменённого задания остаются
  до общей чистки; память окна (PNG картинок без предела, кадры плеера при скрытой Студии); поток окна (выбор кадра,
  «Сохранить как…»); цикл хаба (base64 готового видео и кадров); лента очереди пересобирается на каждый шаг; холст
  может остаться «в очереди»; удаление файлов после десяти отказов Windows сдаётся молча; остатки «Нарисовать» (палитра
  команд, настройки; «картинка не нарисована» у видео после перезапуска хаба — ✓ 48.10г); отмена во время загрузки модели и
  «вторая половина ждёт в ОЗУ» (по исходнику — перечитывается с диска) — покажет 48.8. Найдено при 48.8: описание
  блока видеомодели в config.yaml — «установлена AFO: рисует картинки» (`hub/model_install.py` 1896); у семейства
  `ltx-2.3-distilled` в репозитории unsloth/LTX-2.3-GGUF хаб не отмечает рекомендованный вариант — только у `-dev`;
  id установленной LTX — с двойной приставкой: `ltx-2.3-ltx-2.3-22b-distilled-1.1-q4_0` (имя в llama-swap — «LTX-2.3
  ltx-2.3-22b-distilled-1.1 Q4_0»).
- Полный прогон после 48.7 (1238 из 1240) нашёл две гонки: тест 48.1 ждал два запроса поиска по порядку, а они идут
  разом (`asyncio.gather`) — сравниваются без порядка; работник видео объявлял задание «running» раньше, чем держал
  карту (`_set` ждёт базу) — чат мог успеть взять карту: теперь `_free.clear()` — до «running», чтение кадров и всё до
  запроса — внутри `try`, кадр, что не прочитался, — ошибка задания, а не карта навсегда занята.
- Риски: LTX и MiniMax-H3 на 16 ГБ — только «медленнее», ролик — десятки минут: проба короткая, очередь и «Стоп» на
  виду; кодировщик LTX-2.5 в GGUF — чужой, подтвердит только ролик; пара экспертов — обе половины в одной установке,
  отмена и удаление — обеих; umt5 и VAE Wan делят многие семейства — счёт 47.3; новый режим окна — палитра команд,
  горячие клавиши и «Нарисовать» из чата ведут в Студию.

### Исправление после 0.1.15: модели картинок и видео из поиска

Жалоба пользователя 2026-11-17 («Стабилизация» выше). Решения пользователя 2026-11-17: шаги 1–5 подряд, без его
слова между ними, один отчёт в конце; выпуск 0.1.15 не трогается — это следующая версия. Остановка — только если тест
не падает на старом коде, нужна настоящая модель, правка выходит за файлы шага больше чем на один файл, затронуты его
`data` или GitHub. Тест каждого шага щёлкает мышью в окне без экрана и сначала падает на старом коде.

- Шаг 1. Щелчок по найденной модели — её варианты, вердикт по памяти и «Установить» прямо в «Поиске»: окно идёт по
  семейству (`family`), переход в «Рисовать» по `picture` убран — и у наборов с карточками. Подпись строки — «щелчок
  покажет варианты». Файлы: `app/ui/install_view.py`. Тест — `tests/test_together.py`: настоящий хаб, подставной
  Hugging Face, окно; щелчок по репозиторию двух семейств даёт оба варианта, «Установить» ставит выбранный.
  ✓ Сделано: `openFound` — по `family`, ветка `picture` и текст «„Установить“ в „Рисовать“» ушли; строка без оценки —
  «щелчок покажет её варианты». Наборы с карточками тоже открывают варианты (находка «три набора вели в другой
  раздел» закрыта шагом); вариант, который и есть модель карточки, хаб ставит как установку карточки — без `found`
  (`install_pictures`: `card` и главный файл набора), карточка видит её установленной; у семейств без карточек — как
  прежде, найденной. Второй файл — `hub/model_install.py`. Помощники тестов окна: `item_saying` и `click` —
  `tests/test_ui.py` (щелчок мышью; окно без экрана раскладывает элементы только при отрисовке — сначала
  `grabWindow`). Тест — `test_a_found_model_clicked_in_the_search_shows_its_variants_to_install` (на старом коде
  щелчок не открыл вариантов: «Подборка»). `test_the_search_s_filters_licences_and_pictures_in_the_window` — строка
  набора, как её шлёт хаб (`family` рядом с `picture`), щелчок — варианты.
- Шаг 2. Подсветка переключателя — там, где содержимое: `Segmented` не пишет `value` сам, владельцы без внешней связи
  (бюджеты — три, проект — один) пишут сами. Файлы: `app/ui/qml/Segmented.qml`, `BudgetsSheet.qml`,
  `ProjectSheet.qml`. Тест: щелчок по «Поиску», затем программа меняет вкладку — подсветка за ней; и проход по всем
  переключателям окна: щелчок меняет и подсветку, и выбор владельца.
  ✓ Сделано: `Segmented` по щелчку только говорит `picked`; бюджеты («Для чего», «Период», «После лимита») и облако
  проекта пишут выбор сами. Тесты — `tests/test_ui.py`: `test_a_switch_shows_the_programs_own_change_after_a_click`
  (щелчок по «Поиску», потом `showJobs` — подсвечены «Установки»; на старом коде — «Поиск») и
  `test_every_switch_in_the_window_follows_a_click`: чат, проект с файлом и его «История», агент с панелью пределов и
  расписанием, Студия с картинкой-образцом и все панели справа (модели, мастер, бюджеты, установка с поиском, «Ещё
  фильтрами» и вариантами) — каждый видимый переключатель щёлкается мышью по каждому варианту, и тех, что варианты
  открывают; подсвечен щёлкнутый, у владельца — он. Найдено переключателей в окне столько же, сколько их в QML (57),
  кроме `UNSHOWN_SWITCHES` — «Когда» расписания нигде не показан («Стабилизация»). Без своей записи бюджеты теряли
  выбор — тест это ловит (проверено: «Период» без `onPicked` — падение). Язык по щелчку перезапускает окно: в тесте
  `QuickApp.restart` только отмечается — иначе каждый прогон оставлял запущенный AFO без окна (его хаб занял 8765;
  остановлены 2026-11-17). Честно видно теперь: «Программы» снимают решение, язык ждёт перезапуска, «Быстрее ↔
  Качественнее» без открытого чата не меняется — раньше подсветка стояла на щелчке.
- Шаг 3. Пустая Студия предлагает и картинки, и видео: «Поставить модель картинок» — «Подборка», «Рисовать»; «Поставить
  модель для видео» — «Подборка» к проверенной Wan2.2 TI2V 5B Turbo (рамка `shownSet`); рядом ссылка «Другие модели
  для видео» — «Поиск», фильтр «Видео», «Поместится в видеопамять». Каждая кнопка сама ставит вкладку. Файлы:
  `app/ui/qml/StudioView.qml`, `app/ui/install_view.py`.
  ✓ Сделано: в пустой Студии — «Моделей для картинок и видео ещё нет», две кнопки и ссылка; все три зовут
  `Installer.offer` (картинки, видео, другие модели для видео), он сам ставит вкладку, назначение, фильтры и
  открывает окно установки. Рамка — у карточки набора с `kind: video` из каталога (`shownSet`; прежняя рамка 41.2
  после шага 1 не ставилась никем); окно прокручивается к ней один раз, когда открыто и карточка сделана
  (`shownAsked`, `revealFramed` — третий файл, `app/ui/qml/InstallSheet.qml`). Поиск сохраняет набранные слова и
  «Ещё фильтры» — они видны в окне. Тест — `test_the_empty_studio_offers_a_model_for_pictures_and_one_for_video`
  (`tests/test_ui.py`): окно оставлено на «Установках», щелчки мышью по трём местам — вкладка, назначение, рамка и
  прокрутка к Wan2.2 TI2V 5B Turbo в окне высотой 480, «Видео» и «Поместится» в поиске; на старом коде — падение:
  кнопка картинок открывала окно на «Установках».
- Шаг 4. Слова о памяти: сколько на видеокарте, сколько в памяти компьютера, сколько скачается на диск; кодировщик и VAE
  не входят ни на карту, ни в ОЗУ — «не хватит памяти компьютера»; у семейства, не проверенного вживую, — «по расчёту,
  не проверялась»; описание модели в каталоге о скорости не говорит (вердикт с ним не спорит); строка «для новой
  модели ≈ … видеопамяти» говорит, что это. Файлы: `hub/model_install.py`, `hub/model_catalog.json`,
  `app/ui/install_view.py`.
  ✓ Сделано: `picture_place` (и `variant_place` пары) — к вердикту `picture_fit` байты на карте (`card`: модель, и
  кодировщик с VAE, если остаток карты их держит) и в памяти компьютера (`ram`: остальное; у «медленнее», «без
  видеокарты», «не хватит» — всё). Строка поиска и каждый вариант: «на видеокарте ≈ 12,3 ГБ, в памяти компьютера ≈
  23,4 ГБ — быстро · скачается ≈ 35,7 ГБ» (скачается — без частей, что уже лежат у других моделей, 47.3); у
  «нет» — «не хватит памяти компьютера: нужно ≈ …, есть ≈ …»; слова вердиктов картинок — свои
  (`PICTURE_VERDICTS`), у чата прежние. Семейство без `checked` в каталоге — «(по расчёту, не проверялась)»;
  `checked` — у десяти, что запускались вживую: четыре карточки, Qwen-Image 2.1 Turbo, FLUX.3 klein, Qwen-Image
  (2512) — 47.9, Wan2.2 TI2V 5B, Wan2.2 I2V A14B, LTX-2.3 distilled — 48.8. Описания в каталоге — без скорости и
  памяти (у тринадцати убрано «за секунды», «на 16 ГБ — медленно», «быстрая», «от 6 ГБ» и т. п.; шаги — числом).
  Строка над окном: «RTX 5080: модели можно занять ≈ 14,9 ГБ видеопамяти из 15,9 ГБ — остальное оставлено рабочему
  столу и другим программам». Отказ установки найденной видеомодели — «…: не хватит памяти компьютера». Тест —
  `test_a_found_picture_model_says_what_goes_on_the_card_and_what_into_memory` (`tests/test_together.py`: настоящий
  хаб, подставной Hugging Face, окно, щелчки мышью: строка над окном, модель на карте и части в памяти, вариант, «не
  проверялась», проверенное семейство без пометки, «не хватит памяти компьютера»; каталог — без слов о скорости). На
  старом коде падает и на каталоге, и на окне (строка «для новой модели ≈ 14,9 ГБ»). Поправлены прежние тесты этих
  слов: `test_a_found_picture_model_says_what_it_needs_here`, `test_a_found_picture_model_installed_from_the_search`
  и строка над окном.
- Шаг 5. Расчёт на границе: запас под рабочий стол и другие программы — сколько они занимают на самом деле, не меньше
  запаса человека (1 ГБ); кусок кодировщика — его самый крупный тензор из заголовка файла на Hugging Face; запас меньше
  0,5 ГБ — «впритык». Тест — числа 48.8: LTX-2.3 distilled Q4_0 на 16 ГБ при рабочем столе 1,3 ГБ — не «быстро».
  Файлы: `hub/model_install.py`, `hub/hf.py`, `common/gguf.py`.
  ✓ Сделано. Запас «по факту» — только у моделей картинок и видео: их движок (auto-fit sd.cpp) сам раскладывает
  модель по тому, что свободно на карте при запуске. `room()` даёт `picture_gib` — карта минус то, что держат рабочий
  стол и другие программы (`Installer._others`: замер, пока llama-swap не держит модели, — его модель считалась бы с
  ними, а для картинки он её выгрузит; пока держит — прежний замер), не меньше запаса человека. У чата место прежнее —
  «по компьютеру» (38.1): иначе ноутбук с Ollama на 5,6 ГБ снова «ничего не помещается», а llama.cpp, в отличие от
  sd.cpp, по свободному не раскладывает (38.2 — слова «закройте лишние программы»). Кусок кодировщика —
  `Installer._staging`: самый крупный тензор кодировщиков набора (не VAE) из заголовка их файлов на Hugging Face —
  GGUF (`common/gguf.largest_tensor`) или safetensors (`hf.largest_tensor`), один раз за жизнь хаба; не прочёлся —
  прежние 0,5 ГиБ. Остаток меньше `TIGHT_GIB` (0,5 ГиБ) — вердикт `tight`: «впритык: если другие программы займут
  чуть больше видеопамяти — медленнее», рекомендованным такой вариант не становится, вопроса «найти меньшую» нет
  (`FITS` окна — четвёртый файл, `app/ui/install_view.py`). Тест —
  `test_a_found_video_model_at_the_cards_edge_is_not_called_fast` (`tests/test_together.py`): настоящий хаб, окно,
  щелчки мышью; размеры файлов — как у настоящих (Hugging Face стенда отдаёт килобайты), заголовок кодировщика —
  настоящий GGUF с `token_embd` 262144 × 3840 в Q6_K (≈ 0,8 ГБ, как у gemma-3-12b), рабочий стол 1,3 ГБ на 16 303
  МиБ: вариант — «в памяти компьютера ≈ … — медленнее», на старом коде — «на видеокарте ≈ 12,7 ГБ … — быстро»;
  у чата — 14,9 ГБ, у картинок — 14,6; замер держится, пока llama-swap держит модель; 11 500 МиБ — «впритык», 11 000
  — «быстро». Qwen-Image-2512 Q4_K_S на 14,9 ГиБ (`test_model_install.py`) — теперь «впритык» (0,4 ГиБ). Тесты,
  что обнуляют запасы под файлы стенда в килобайты, обнуляют и `TIGHT_GIB`.
- Полный прогон после шага 5 (без `tests/test_computer.py`, 2026-11-17): 1318 тестов, 1317 прошли. Упал
  `test_dxgi_sees_the_cards_the_registry_knows` — тот же, что после 12д: реестр записывает встроенной Intel 37 ГБ;
  `common/hardware.py` эти шаги не трогали.
- Решения пользователя 2026-11-17: эти пять шагов, починка видеокарт и `/scenes/` в `.gitignore` — версия 0.1.16
  (0.1.15 уже на GitHub); шаги по разрешениям «Доверенного» — 0.1.17.
- Видеокарты (слово пользователя: тест не зависит от железа этого компьютера; в мастере и «Проверить систему» первой
  — карта, на которой работает AFO; встроенной графике не приписывать общую память). ✓ Сделано:
  `hardware.with_dxgi` — у карты, которой DXGI насчитывает своей памяти меньше `INTEGRATED_GIB`, видеопамяти нет
  (`integrated`, мастер пишет «память общая с ОЗУ»); карту DXGI ищет по имени, иначе — единственную того же
  производителя; DXGI молчит — слово реестра. `hardware.running_first` — карта `build_for` первой: мастер
  (`hub/setup.py`) и «Проверить систему» (`hub/diagnostics.py`, «… (память общая с ОЗУ)»). Сборка llama.cpp при
  молчащем драйвере NVIDIA — Vulkan на NVIDIA, а не на встроенной; компьютер только со встроенной — процессор.
  Тесты: `test_integrated_graphics_has_no_video_memory_of_its_own` (числа основного ПК, железо не читается),
  `test_the_wizard_shows_the_card_afo_runs_on_first` (настоящий хаб), строка видеокарт в
  `test_the_system_check_says_what_to_do` — на старом коде падают все три; `test_dxgi_sees_the_cards_the_registry_
  knows` больше не сравнивает память реестра с DXGI на этом компьютере.

### Исправление после 0.1.16: загрузка моделей картинок и видео

Жалоба пользователя 2026-11-18 («Стабилизация» выше). Решение пользователя 2026-11-18: три шага, выходят вместе с
шагами по разрешениям «Доверенного» в 0.1.17; сначала эти три, потом разрешения (обе работы правят TechDoc и перевод).
Тест каждого шага сначала падает на старом коде; сервер загрузок в тестах — настоящий, на этом компьютере.

- Шаг 1. Закрытые части видны до загрузки: хаб спрашивает Hugging Face о каждом закрытом (`gated`) репозитории
  частей модели с ключом человека (или без него); не пускает — варианты говорят, на какой странице принять условия и
  куда вставить ключ, «Установить» ничего не начинает. Файлы: `hub/hf.py`, `hub/model_install.py`,
  `app/ui/install_view.py`, `app/ui/qml/InstallSheet.qml`.
  ✓ Сделано: `hf.readable` — первый байт файла с ключом (или без), переадресация не открывается; 401/403 — закрыто,
  нет ответа — «открыто» (скажет сама загрузка). `Installer._closed` спрашивает только репозитории с `gated` в их
  сведениях; «закрыто» помнит 60 с (`CLOSED_TTL_S`), «открыто» — час, каждое для своего ключа (в памяти — отпечаток
  ключа, не он сам); перед установкой «закрыто» спрашивается заново. `picture_details` отдаёт `closed` и слова
  (`closed_words`: без ключа — «войдите, примите условия на этой странице и вставьте ключ (тип Read) в „Настройки →
  Подключения“», с ключом — «ваш ключ туда не пускает…»); окно — заметка `installRepoClosed` и кнопка «Открыть
  страницу на Hugging Face» (прежняя заметка «Модель закрыта» — только о главном репозитории и не вместе с новой).
  `install_pictures` и `install` (у чата — закрытый главный репозиторий) отказывают 403 этими словами до программы и
  файлов. Тест — `test_a_part_closed_on_hugging_face_is_said_before_anything_downloads` (`tests/test_together.py`:
  настоящий хаб, окно, щелчки мышью; стенд Hugging Face — `CLOSED`, 401 без `hf_good`): заметка и страница видны,
  «Установить» — ошибка с этими словами, ни задания, ни архива с GitHub, ни файла; с ключом заметки нет, установка
  доходит до конца. На старом коде заметки нет.
- Шаг 2. Честное время: пока качается программа с GitHub, окно пишет время её, а не всей модели; время модели — по
  скорости её файлов. Файлы: `hub/model_install.py`, `app/ui/install_view.py`.
  ✓ Сделано: `_download` перед архивом программы (`role: engine`, они идут первыми) запоминает, где кончаются
  оставшиеся архивы (`_engine_end`), перед файлом модели — забывает (и в `finally`); `install.progress` несёт
  `engine_left` — сколько программы осталось. Окно (`time_left`): пока он не ноль — «программа для видео: ещё ≈ …»
  (у картинок — «программа для рисования») по нему и текущей скорости; потом — остаток всей модели по скорости её
  файлов, как прежде. И строка внизу окна, и карточка установки. Тест —
  `test_the_time_said_while_the_engine_comes_from_github_is_the_engines` (`tests/test_together.py`: настоящий хаб,
  его события по настоящему WebSocket — в `on_event` окна, как их отдаёт поток событий; GitHub стенда медленнее
  Hugging Face — `FakeHub.github_slow`): у каждого события, пока идёт программа, время — её остатка, после — модели.
  На старом коде хаб остатка программы не говорит.
- Шаг 3. Последний медленный кусок не тянется один: свободное соединение забирает вторую половину самого медленного
  куска; файл на диске и докачка после перезапуска — прежние. Файл: `hub/hf.py`.
  ✓ Сделано: кусок в пути — одна или несколько частей (`_Span`: начало, конец, сколько записано, когда пришёл последний
  байт). Соединение, которому нечего взять, не уходит, а раз в 0,1 с (`STEAL_LOOK_S`) ищет (`_share`) часть, которой
  её соединению нужно дольше `STEAL_AFTER_S` (2 с; судится не раньше, чем через столько же от начала), и забирает
  вторую половину её остатка — а если соединение стоит (2 с без байта), всё, кроме одного куска чтения (`CHUNK`);
  забранное — не меньше `STEAL_MIN` (4 МБ). Медленное соединение пишет только до своего конца (читается под замком
  перед каждой записью; оставленный ему кусок чтения — не меньше того, что оно может писать в этот миг) и закрывается.
  Кусок готов, когда готовы все его части; список готовых кусков (`.parts`), sha256 и файл — прежние. Обрыв части —
  как у куска (0.1.15, шаг 5): с места обрыва; не вышло за три попытки — следующая попытка загрузки идёт с того, что
  лежит от начала куска без пропуска (`_contiguous`). Число в окне — байты на диске всех частей (`_on_disk`), назад не
  идёт. Молчащее соединение куска рвётся через 30 с вместо 120 (`PIECE_READ_S`). Тест —
  `test_a_slow_piece_is_shared_with_a_free_connection` (`tests/test_model_install.py`, настоящий сервер стенда; одно
  соединение — секунда на 16 КБ, `FakeHub.slow_once`): файл целый за < 2,5 с (кусок один — 4 с), свободное соединение
  взяло его остаток; обрыв забранной части (`break_once`) — спрошено с места обрыва. На старом коде никто не берёт.
  Стенд: соединение с «localhost» на Windows ждёт ~2 с (сначала ::1) — тест идёт на 127.0.0.1.

### Исправление 0.1.17: «Доверенный» по правилам

Жалоба пользователя 2026-11-17 («Стабилизация» выше: окно разрешения на каждом шаге в «Доверенном»). Решения
пользователя 2026-11-18: «Доверенный» работает как режим auto в Claude Code, описание которого он прислал, — обычное в
проекте идёт молча, опасное не выполняется и не спрашивается (модель читает отказ и ищет другой путь), человека зовут
только в тупике; сначала на правилах, без модели, модель-проверяющий — второй очередью (после этих шагов, по его слову).
Пункт «Стабилизации» закрывает только его слово после проверки в окне.

Правила «Доверенного» после шагов:

- Молча: правки файлов проекта и обычные команды в нём, чтение сайтов и поиск (если в чате нет проекта «только
  локально») — и после того, как чат прочитал написанное чужими (страницу, поиск, письмо). Прежнее «после чтения сайта
  спрашивать всё» в «Доверенном» снято его решением; риск — текст страницы может подсказать модели действие — назван
  ему 2026-11-18.
- Отказ без окна, всегда: команда, которая читает данные самой AFO (ключи, база).
- Отказ без окна, а на втором отказе подряд — окно человеку: удаление и необратимое (`DESTRUCTIVE`), команда, которая
  запускает не названное в строке (`OPAQUE`), разрушительный инструмент чужого MCP-сервера.
- Окно, как прежде: запись в файл, открытый в редакторе с несохранёнными правками.
- «Спрашивать», «Песочница», «Только чтение», уровни автономного агента, ограничения «Цели» — не меняются, кроме
  шагов 3 и 4.

Шаги (тест каждого сначала падает на старом коде; настоящий хаб, настоящий `web_fetch` на сайте этого компьютера,
настоящие терминалы, модель — по сценарию):

- Шаг 1. После чтения сайта «Доверенный» не спрашивает правки и обычные команды. Файлы: `hub/chat.py`,
  `hub/mcp_server.py` (Claude Code), `hub/agents.py` (проверки пакета координатора).
  ✓ Сделано: «читал чужое» (`read_the_web`) больше не отменяет доверие — в `_write_file`, `_run_command`, `_mcp_tool`
  (`hub/chat.py`), в `approval_prompt` Claude Code (`hub/mcp_server.py`) и в `_allow_checks` (`hub/agents.py`); у
  остальных режимов оно по-прежнему выключает ответы «для чата» и «всегда» (до шага 4). Тест —
  `test_a_trusted_chat_that_read_the_web_goes_on_without_cards` (`tests/test_together.py`: настоящий хаб, страница с
  сайта этого компьютера, правка и команда в настоящем терминале — ни одной записи в `approvals`); на старом коде —
  окно `edit_file`. Прежние проверки «после сайта доверенный спрашивает» (`tests/test_web.py`, `test_websearch.py`,
  `test_claude_code.py`) переписаны на новое правило.
- Шаг 2. Опасное — отказ с причиной вместо окна; второй отказ подряд в чате — окно; данные AFO — отказ всегда. Отказ
  записывается в `approvals` (кто решил — правила). Файлы: `hub/chat.py`, `hub/approvals.py`, `hub/mcp_server.py`,
  `hub/context.py` (слова модели о её правах).
  ✓ Сделано: `approvals.trusted_refuses` — None (делается молча), слова отказа или '' (спросить): данные AFO —
  `TRUSTED_NEVER` всегда; разрушительное (`destructive`, у чужого MCP — класс `destructive`) — `TRUSTED_NOT_YET`, а
  сразу следом — вопрос. `ChatService.trusted_verdict` помнит чаты с последним отказом (`self.refused`; обычная
  команда или показанное окно счёт снимают, правка — нет), пишет отказ в очередь (`ApprovalQueue.refused`: `denied`,
  `decided_by = 'rules'`, слова — в `comment`; событие `approval.decided` без хранения). На полигоне и в «Полном»
  отказ говорит сам вопрос (`_decision`), как прежде. Им же идут команды (`_run_command`), чужие MCP (`_mcp_tool`) и
  Claude Code (`approval_prompt`). Слова прав в контексте модели (`RIGHTS_NOTES["trusted"]`) — по новым правилам.
  Тест — `test_a_trusted_chat_is_refused_what_cannot_be_undone_and_asked_only_when_it_insists`
  (`tests/test_together.py`): `git push` — отказ и запись правил, обычная команда идёт; два разрушительных подряд —
  окно на втором; данные AFO дважды подряд — два отказа, окна нет. На старом коде — окно на первом `git push`.
- Шаг 3. «Всегда в проекте»: для правки — все правки файлов проекта, для команды — все команды проекта (кроме тех, что
  спрашиваются каждый раз); кнопка называет, что разрешается. Прежние правила (точная команда, имя инструмента)
  действуют. Файлы: `hub/approvals.py`, `app/ui/approvals.py`, `app/ui/projects.py`.
- Шаг 4. Ответы «для чата» и «всегда» действуют и после чтения сайта; карточка говорит, что чат читал чужой текст.
  Файлы: `hub/chat.py`, `hub/approvals.py`, `app/ui/approvals.py`.
- Шаг 5. Смена режима действует сразу: открытое окно этого чата закрывается как разрешённое, если «Доверенный» его не
  спросил бы. Гонка: запрос записан в базу, но ещё не ждёт ответа — ждать начинает до записи. Файлы:
  `hub/api/conversations.py`, `hub/approvals.py`, `hub/chat.py`.
- Шаг 6. Слова при включении «Доверенного» в чате: «Модель сможет менять файлы и запускать команды в проекте без
  вопросов. Команды могут затронуть и остальной компьютер» — «Включить» и «Отмена»; описание режима — по новым
  правилам. Файлы: `app/ui/qml/ContextSheet.qml`, `app/ui/controller.py`, `app/ui/rows.py`.
- Шаг 7. «Недавно заблокировано»: отказы правил этого чата в окне, у каждого «Разрешить» — следующая такая же попытка
  модели пройдёт один раз. Файлы: `hub/approvals.py`, `hub/api/approvals.py`, окно разрешений.
- Конец: весь pytest, перевод (`--sync` и английский), строки карты файлов.

### План 2026-11-18: «Режиссёр», «Улучшить ролик», «Архитектор + исполнитель»

Слово пользователя 2026-11-18: три задумки — (1) «AI-Режиссёр» в «Студии»: фото товара, идея и длительность → модель
пишет сценарий по сценам → AFO снимает сцены по очереди на LTX-2.5, держит товар одинаковым, проверяет брак, склеивает
ролик, добавляет звук и голос; (2) «Улучшить ролик»: апскейл, 60 к/с, звук, губы под голос, замена предмета, дорисовка
краёв, камера; (3) «Архитектор + исполнитель»: облачная модель проектирует и проверяет, локальная пишет код маленькими
задачами. Его решение: сначала полный план по этапам и список движков; план утверждён 2026-11-18. Этапы 50–61 — ниже,
каждый начинается по его слову. Прежний этап 49 (видео по видео, образцам и звуку) расходится: звук — этап 52, видео
по видео — этап 59.

Найдено (разведка 2026-11-18; номера строк — на этот день).

- Видео: один `Visual._work` (`hub/visual.py` 1053–1096) — одна работа за раз; видеокарту берёт
  `card.take(STUDIO, chats=True)` и отдаёт после каждой работы (1076, 1094, 1274) — между сценами её может взять чат.
- Режимы LTX в каталоге — `text`, `picture`, `frames` (`init_image`, `end_image`; `hub/visual.py` 703–708, 1224).
  «Два кадра» у LTX вживую не проверялись; LTX-2.5 вживую не запускалась ни разу (выпуски 0.1.15–0.1.16).
- `Visual._source` принимает только PNG (761, 769): `from_job` ролика — отказ 422, сцепки по последнему кадру нет.
- Длина: `seconds` 0,5–4 (`hub/api/visual.py` 77), на экране 1/2/3 с (`app/ui/visual_view.py` 52); 121 кадр (5 с)
  не пройдёт. Шаги, cfg и метод — из флагов установки (`installed`, 503–521), запрос их не меняет.
- `hub/ffmpeg.py`: только `to_mp4` (WebP → H.264, `-an`); `ffprobe` из архива не распаковывается (79–84). Кадров из
  ролика, склейки, апскейла, интерполяции, звука и голоса в хабе нет. Число кадров в файле нигде не читается.
- `visual_jobs` (миграции 048–053): группы работ («фильм») нет. `hub/retention.py` (39, 197) чистит `data/media`
  старше 90 дней и сверх 2 ГБ — ролики фильма попадут под очистку.
- Вопрос модели самим хабом — `Judge.complete` (`hub/judge.py` 448): бюджет (461–467), учёт расхода (485–491),
  разбор и один повтор (`TRIES`, 49); только текст, без предела времени, отмена не ставится. Картинку в сообщении
  (`images`) понимают все провайдеры, кроме DeepSeek; OpenRouter и DeepSeek не ставят `vision`
  (`hub/gateway/cloud_openai.py` 117–121, 204–209). Строгого JSON (`response_format`, схема, принудительный вызов
  инструмента) нет ни у одного провайдера. Кэш запросов Anthropic включён (`anthropic_provider.py` 56, 161–177).
- Координатор (`hub/agents.py`, `hub/delegate.py`): пакет до 8 задач с зависимостями, у задачи свой профиль (своя
  модель), локальные — по одной, проверка командой в песочнице задачи, хвост ошибки — исполнителю, до 3 кругов
  (`ROUNDS`), затем судья по diff. Нет: координатор получает `board()` — имена файлов, не diff (418–445); файлов
  задаче не дать, хотя `store.set_context_settings(map, live, pinned)` есть (`hub/store.py` 221–236); модель — только
  профилем; после перезапуска хаба задачи `cancelled`/`escalated` (522–539); `run_command` подпрогона в песочнице
  спрашивает каждый раз (`hub/chat.py` 1208–1213); снимка веб-страницы нет (`web_fetch` — текст, без localhost);
  судья, «Цель» и чаты идут мимо `AgentRunner._gpu` (516).

Движки и зависимости (проверено по первоисточникам 2026-11-18; «проверить» — первым шагом своего этапа).

Без Python — отдельные программы, сменные части через `hub/engines.py`:

- stable-diffusion.cpp `master-1053` (стоит): LoRA — только полем запроса `lora: [{path, multiplier}]`, пути из
  `GET /sdcpp/v1/capabilities`; `sample_params` на запрос; звук LTX при `--audio-vae`, выход `webm` (VP8 + PCM) или
  `avi`, в `webp` звука нет, MP4 нет. Проверить: WebM в сборке для Windows (`output_formats_by_mode.vid_gen`), LoRA
  для LTX. Нет: IC-LoRA и `control_frames` у LTX, маска в `vid_gen` (у VACE зашита 1.0), продление по нескольким
  кадрам, цикл; `/sdcpp/v1/upscale` — только картинка.
- stable-diffusion.cpp ≥ `master-1066` (новейшая — `master-1106`, 2026-11-16): `sd-cli -M vid_upscale` — RelayVSR
  (×2/×4) и SeedVR2 3B/7B, вход — папка кадров, звук не переносит, по HTTP нет. cuda12 393 МБ + cudart 537 МБ.
- Веса: SeedVR2-3B fp8 3,4 ГБ + VAE 0,5 ГБ (Apache-2.0; формат для sd.cpp и память на 16 ГБ — проверить); RelayVSR
  (Apache-2.0); RealESRGAN_x4plus 64 МБ (BSD-3); звуковой VAE LTX (в каталоге AFO его нет); LoRA Lightricks —
  Cinemagraph и Slow-Motion для 2.5, камера (Dolly, Jib, Static) только для LTX-2 19B.
- rife-ncnn-vulkan, сборка TNTwise 20250112 (MIT, модели 4.25/4.26, 789 МБ, Vulkan, папка PNG) — 60 к/с и замедление.
- audio.cpp v0.9.5 (Apache-2.0; Vulkan 82 МБ или CUDA 448 + 579 МБ) — один exe: голос Qwen3-TTS 0.6B/1.7B
  (Apache-2.0, русский и английский, до 3,8 + 0,7 ГБ), музыка ACE-Step 1.5 (MIT, ≈ 10 ГБ), звуки (Stable Audio
  Small — бесплатно до $1 млн дохода; MOSS-SoundEffect — проверить), тайм-коды речи (GigaAM v3, whisper — MIT).
- ffmpeg BtbN n9.0 (уже качается): `xfade`, `acrossfade`, `concat`, `scdet`, `minterpolate` есть; `ffprobe.exe` в
  архиве — проверить.
- Microsoft Edge без окна (есть в Windows 11) — снимок страницы; проверить.

С Python и PyTorch — только с согласия пользователя (этап 56):

- uv + python-build-standalone (49 МБ); PyTorch 2.11 + cu128 … 2.15 + cu132 — 2,0–2,75 ГБ загрузки, ≈ 4–6 ГБ на
  диске на окружение; RTX 5080 — не ниже 2.7 + cu128, для CUDA 13 драйвер ≥ 580. Общее окружение невозможно: движки
  требуют разные версии — окружение на движок.
- MuseTalk 1.5 (MIT, 3,4 ГБ, от 4 ГБ видеопамяти) — губы под голос. SAM 2.1 (Apache-2.0, 0,9 ГБ) — маска на ролик.
  Wan2.1-VACE-1.3B (Apache-2.0, 19 ГБ) — замена предмета и дорисовка краёв. ltx-pipelines + IC-LoRA (код Apache,
  модели — лицензия LTX; LTX-2.5 ≈ 66 ГБ, 16 ГБ — проверить) — камера, перенос движения, пересъёмка отрезка.
  DWPose, Depth Anything V2 Small (Apache-2.0; Base и Large — CC-BY-NC).

Не берём — лицензия запрещает рекламу или движок заброшен: MMAudio (веса CC-BY-NC), CodeFormer (S-Lab), GFPGAN
(внутри некоммерческий код), 4x-UltraSharp (CC-BY-NC-SA), XTTS-v2, F5-TTS, Silero, Fish Speech, Wav2Lip, GIMM-VFI,
MusicGen, LivePortrait (части InsightFace), BasicVSR++ и RealBasicVSR (PyTorch 1.7), VEnhancer и STAR (нет лицензии,
от 24 ГБ), Video2X и upscayl-ncnn (AGPL). Озвучки готового видео (Foley) с чистой лицензией не найдено — вместо неё
звуки по описанию сцены (53). Свой апскейлер с обучением — вне плана. Модель LTX: по карточке — бесплатно при доходе
до $10 млн в год.

Порядок: шаги по разрешениям «Доверенного» (0.1.17) → 50 → 51 → 52 → 53 → 54 → 61 → 55 → 56 → 57–60. Шаг — 2–3 файла
кода и тест, падающий на старом коде; стенды — настоящий хаб, настоящий ffmpeg, `FakeDrawSwap` (`tests/test_visual.py`
242–343), `tests/fake_*` для облака; окно — настоящий QML без экрана.

### Этап 50. «Режиссёр»: сценарий, сцены, склейка

Без звука и без авто-проверки. Закрыт, когда из фото товара и одной фразы получается склеенный MP4 на 15 с и
пользователь проверил его в окне.

- 50.1 Замер LTX-2.5 вживую (по слову, после `nvidia-smi`): 73/97/121 кадр, 768×512, холодный и тёплый старт, «два
  кадра», кадры глазами. Решает длину сцены и время ролика; запись здесь.
- 50.2 Вопрос модели с картинками: `Judge.complete` принимает `images`, получает предел времени и отмену. Файл:
  `hub/judge.py`. Тест — настоящий хаб, подставной облачный сервер видит картинку, расход записан.
- 50.3 Кадры и склейка: `last_frame`, `frames_at`, `join` (жёсткий стык или `xfade`) в `hub/ffmpeg.py`;
  `Visual._source` принимает ролик как источник кадра. Тест — настоящий ffmpeg: число кадров и длина файла.
- 50.4 Сцена до 5 с и свои шаги, cfg и seed на запрос (`sample_params`). Файлы: `hub/api/visual.py`, `hub/visual.py`,
  `common/picture_sizes.py`. Предел — по замеру 50.1.
- 50.5 База: миграция 054 — `films` (идея, длительность, фото товара, модель-режиссёр, состояние, файл) и
  `film_scenes` (номер, режим, текст по-русски, английский промпт, кадры, seed, работа `visual_jobs`, состояние);
  ролики фильма очистка не трогает (`hub/retention.py`).
- 50.6 Сценарий: роль модели «Режиссёр» (как «Проверка цели»), промпт, разбор `read_storyboard` (кадры 8n+1, режим
  `picture | continue | frames`, проверка полей), один повтор. Новый `hub/director.py`.
- 50.7 Съёмка фильма: сцены по очереди; `continue` берёт последний кадр прошлой сцены, `picture` — фото товара,
  `frames` — два кадра; видеокарта между сценами не отдаётся; «Стоп», «Продолжить», после перезапуска хаба — с
  неснятых сцен. Файлы: `hub/director.py`, `hub/visual.py`, `hub/gpu_card.py` (свой держатель и слова для чата).
- 50.8 API и события: `hub/api/films.py` (`/v1/films`: сцены, снять всё, переснять, собрать), `film.changed`,
  `film.progress`; `app/hub_client.py`.
- 50.9 Окно: в «Студии» переключатель «Вручную | Режиссёр»; фото товара, идея, 15/30/60 с, «Собрать сценарий»;
  карточки сцен (кадр, русский текст, правка, «Переснять»); «Снять всё»; «Собрать ролик»; время «ещё ≈». Файлы:
  `app/ui/film_view.py`, `app/ui/qml/DirectorView.qml`, `StudioView.qml`. Тест — щелчки мышью.
- 50.10 Конец этапа: живая проверка — ролик 15 с из 4 сцен; весь pytest; карта файлов.

### Этап 51. Авто-проверка сцен

Три кадра сцены и фото товара → модель-режиссёр → JSON «годно / брак, причина, новый текст, seed, cfg». Пересъёмка
только этой сцены, не больше двух раз; предел денег и пересъёмок на фильм; выключатель. Причина брака — в карточке;
следующие `continue`-сцены переснимаются, если сменился их первый кадр. Файлы: `hub/director.py`, `hub/judge.py`,
`app/ui/film_view.py`.

### Этап 52. Звук в ролике

Часть прежнего этапа 49. Проверка: сборка sd-server для Windows отдаёт `webm`; звуковой VAE LTX — в каталог и
установку. Хаб просит `webm`, ffmpeg делает MP4 с AAC; плеер получает звук (`AudioOutput`) и громкость; поле
«Звуковое окружение» в «Студии» и в сцене; склейка фильма со звуком (`acrossfade`). Файлы: `hub/visual.py`,
`hub/ffmpeg.py`, `hub/model_catalog.json`, `app/ui/qml/VideoPlayer.qml`.

### Этап 53. Голос, музыка и звуки

Движок — audio.cpp.

- 53.1 Замер на RTX 5080: Qwen3-TTS по-русски, ACE-Step, звуки — память, время, качество на слух.
- 53.2 Движок как сменная часть: загрузка, sha256, запуск подпроцессом, очередь видеокарты.
- 53.3 Голос: текст диктора и тайм-коды в сценарии → дорожка; выбор голоса.
- 53.4 Музыка — по описанию или свой файл; звуки — по описанию сцены.
- 53.5 Сведение: голос поверх музыки (музыка тише под голосом) — в итоговый MP4.

### Этап 54. «Улучшить ролик» без Python

- 54.1 Панель «Улучшить ролик» для ролика из «Последних» и для фильма; результат — новый ролик рядом.
- 54.2 60 к/с и замедление ×2/×4 — rife-ncnn-vulkan; стыки сцен находит `scdet`, через стык не сглаживает.
- 54.3 sd.cpp — на версию с `vid_upscale`; повторная живая проверка видео.
- 54.4 Апскейл: быстрый (RelayVSR или RealESRGAN) и «с дорисовкой» (SeedVR2-3B); звук переносится.

### Этап 55. LoRA и движение камеры

Проверка: LoRA Lightricks для LTX-2.5 в sd.cpp. Работает — поле `lora`, установка LoRA, готовые варианты («Цикл»,
«Замедление»). Камера для 2.5 без PyTorch недоступна — уходит в этап 59.

### Этап 56. Движки на PyTorch — основа

Нужно согласие пользователя: +4–6 ГБ на диске на каждый движок. uv и свой Python на движок; установка по требованию с
честным размером; запуск подпроцессом; очередь видеокарты; удаление. Первый движок для проверки основы — MuseTalk.

### Этапы 57–60. Движки на PyTorch — по одному

После этапа 56, каждый — по слову пользователя.

- 57. Губы под голос — MuseTalk 1.5.
- 58. Замена предмета и фона, дорисовка краёв — SAM 2.1 и Wan2.1-VACE-1.3B.
- 59. Видео по видео и камера — ltx-pipelines с IC-LoRA (или Wan VACE); прежний этап 49.
- 60. Продление с движением и бесшовный цикл — когда появится у движка.

### Этап 61. «Архитектор + исполнитель»

Доделка координатора, а не второй оркестратор. «Найди и замени» (`edit_file`) и кэш запросов уже есть.

- 61.1 Архитектор видит изменения задачи: сжатый diff на доске или инструмент чтения diff. Файл: `hub/agents.py`.
- 61.2 Узкий контекст задачи: поле `files` (1–3 файла), карта проектов и «живое» выключены
  (`store.set_context_settings`). Файлы: `hub/delegate.py`, `hub/agents.py`.
- 61.3 Модель на задачу (поле `model`) вместо профиля на каждую модель.
- 61.4 Команды проверки подбираются по манифесту проекта (типы, линтер, сборка) и идут в песочнице без вопросов —
  после разрешений «Доверенного».
- 61.5 Пакет продолжается после перезапуска хаба.
- 61.6 Локальная модель пишет вызов инструмента текстом — хаб его понимает («Стабилизация», 2026-11-17).
- 61.7 Снимок страницы для архитектора (Edge без окна) — после проверки.
- 61.8 Одна очередь видеокарты для судьи, «Цели», чатов и задач.

### Потом

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
слову пользователя перед Strata; 46 (KTransformers) закрыт замером 46.1 (2026-11-14); 47 (рисование с Hugging Face)
— по слову пользователя 2026-11-15, затем 48 (видео с Hugging Face и «Студия»), потом 49 (видео по видео, образцам
и звуку; звук в ролике) — с 2026-11-18 он разошёлся по этапам 52 и 59, а дальше идут этапы 50–61 в порядке из «Плана
2026-11-18». Живая проверка на настоящих моделях — в конце этапа, по слову
пользователя и после `nvidia-smi`; весь pytest — один раз в конце этапа (Правило 4). Пункты аудита — между этапами,
по слову пользователя.

### Исправление 0.1.12: модели, которые не запускались

Найдено 2026-11-14 в AFO 0.1.11 пользователя (его снимок: «Модель не запустилась» у Qwen3.8-27B RVN Q8_0). llama-swap
пишет только «upstream command exited prematurely», и AFO гадало: «файл повреждён, слишком новый или не помещается».
Слова самого llama.cpp — в журнале модели, который держит llama-swap (`/logs/stream/<id>`; адрес `/upstream/<id>/…`
модель запускает — его не трогать): у Qwen3.8 RVN — `invalid argument: --no-mmap` (b11899 заменила флаг на
`--load-mode`, а `--help` о нём молчит — `match_build` 42.1 его не снял), у Kimi-Linear-48B — «V cache type q8_0 with
block size 32 does not divide n_embd_head_v=72» (голова V — 72), у gemma3:4b и qwen3.5 из Ollama — «key not found» и
«wrong array length» (свой формат Ollama). Пробный запуск убрал последние три, перебрав контексты зря.

- 0.1.12 ✓ (по слову пользователя «сделай полный исправление с patch note», версия — 0.1.12 по его выбору: 0.1.11
  читает версию только тремя числами). А — настоящая причина: `llama_config.said(url, id)` читает журнал модели
  `LOG_READ_S` (поток ждёт новых строк — что пришло, то и есть) и `cause()` берёт строку после последнего старта
  (`llama_server: initializing`), самую говорящую по `CAUSES` (флаг → кэш → файл → память → прочее), первую своего
  рода; её слова идут за «exited prematurely» — в чате и проверке (`openai_compat`), в проверке подбора
  (`Tuner._load`); пробный запуск не уменьшает настройки, когда они «безнадёжны» (`hopeless`: флаг, кэш, файл), и
  пишет их; `common/plain_errors.py` — три правила раньше общего: «Сборка llama.cpp не знает флаг из команды модели»,
  «Сжатие KV-кэша не подходит модели», «llama.cpp не читает этот файл модели» (Ollama, модель новее сборки). Б —
  флаги, убранные молча: `match_build` пишет известные переименования (`RENAMED`: `--no-mmap` → `--load-mode none`,
  `--mlock` → `mlock`, `--mmap` → `mmap`) и снимает прочие незнакомые сборке флаги со значением — только в командах
  её llama-server и только когда её `--help` прочитан целиком (`WHOLE`: `-m`, `-c`, `--host`, `--port`; разбор b11899
  — 417 флагов, ни одного пропущенного); `unknown_flags` — что не так; `Installer.fit_to_build` — при старте хаба, под
  замком писателей, с копией, llama-swap не принял — копия назад. На конфиге пользователя (в памяти, без записи) —
  ровно одна правка: `qwen27b-q8`, `--no-mmap` → `--load-mode none`. В — сжатие кэша по модели:
  `llama_memory.quantized_cache` (блоки по 32 должны делить `key_length` и `value_length`) — установщик пишет f16,
  подбор не меряет сжатые, заготовки окна дают f16, оценка в «Запуске» — ошибка с объяснением до записи. Тесты —
  `test_why_a_model_did_not_start_in_llama_cpps_own_words`, `test_why_a_local_model_did_not_start_in_plain_words`,
  `test_a_model_llama_cpp_cannot_read_is_not_tried_smaller`, `test_flags_a_build_took_away_without_a_word`,
  `test_the_commands_are_fitted_to_the_build_when_the_hub_starts`,
  `test_a_model_a_quantized_cache_does_not_fit_is_measured_with_f16`, `test_this_computer_against_a_model`,
  `test_faster_or_better_for_a_local_model`.

### Исправление 0.1.13: глубина размышлений, которую берёт сама модель

Найдено 2026-11-14 после 0.1.12 (снимки пользователя: Qwen3.8-27B RVN Q8_0 загрузилась и не ответила, на второе
сообщение — «HTTP 500 … Jinja Exception: Unexpected reasoning effort max»). Хаб отдавал llama.cpp глубину чата как
есть (`reasoning_effort`), а шаблон чата Qwen3.8 (RVN, и Flash-Next в llama.cpp — у того «high» сначала становится
«xhigh») берёт только `xhigh`, `medium`, `low`, на прочие — исключение; «высокая» и «максимальная» так не работали.
Первое сообщение пришло во время загрузки: llama-swap уже ответил 200 ради хода загрузки и отдал отказ модели после
него как есть — JSON без `data:`; хаб такие строки пропускал, ответ был пустым и без ошибки (повторено на стенде:
копия llama-swap v267 на своём порту, поддельный llama-server на Python).

- 0.1.13 ✓ (по слову пользователя «теперь нужно делать так чтобы глубина рассуждения была которую имеет сама модель»).
  А — уровни модели: `llama_config.template_efforts` — слова в скобках проверки `reasoning_effort … in (…)` шаблона,
  в именах хаба (`common/efforts.py`: `EFFORTS`, `own_efforts`, `nearest_effort` — перенесён из `cloud_openai`);
  `efforts_now`/`model_efforts` — шаблон, который запускает команда: `--chat-template-file`, иначе
  `--chat-template`, иначе из заголовка файла модели (`tokenizer.chat_template`, после словаря — несколько МБ), без
  Jinja (`--no-jinja`) — никаких; файл читается раз, пока не изменились размер и время (`_TEMPLATES`), в потоке. На
  конфиге пользователя — 19 моделей за 1,5 с, из кэша 4 мс; ограничены пять Qwen3.8 (`low`, `medium`, `xhigh`),
  остальные шаблоны уровней не называют. Б — хаб: провайдер llama-swap на этом компьютере (`efforts`, как `gate`)
  кладёт уровни в `capabilities.efforts` и перед каждым ответом спрашивает модель ближайший уровень не выше
  выбранного («максимальная» → `xhigh`, «высокая» → `medium`, псевдонимы llama-swap тоже); у OpenRouter —
  его `supported_efforts`, у OpenAI — таблица `MODELS`; Gemini, Claude и Claude Code переводят любой из пяти сами —
  у них все пять. В — окно: меню «Глубина» и лист «Настройка моделей» предлагают уровни модели
  (`presenter.model_efforts`), кнопка и сообщение — ближайший (`App._depth`), выбор человека остаётся для следующей
  модели. Г — отказ после хода загрузки: строка JSON с `error` вне `data:` — ошибка (`_refusal`), как ответ 500;
  `plain_errors` — «Шаблон чата модели не принял запрос». Тесты — `test_the_reasoning_depths_a_models_chat_template_takes`,
  `test_each_models_depths_from_its_file_or_its_template_file`,
  `test_a_local_model_is_asked_only_the_depths_its_template_takes`,
  `test_a_refusal_after_the_loading_banner_is_an_error_not_an_empty_answer`, `test_openrouter_takes_each_models_efforts`,
  `test_the_catalog` (OpenAI), `test_a_chat_template_that_refuses_in_plain_words`,
  `test_the_depth_menu_offers_what_the_model_takes`.

### Выпуски

Перед каждым выпуском:

- проверенная сборка llama.cpp (`LLAMA_TAG` в `hub/engines.py`, 12а) поднимается до новой — после живой проверки AFO
  на ней (модели запускаются, подбор и ускорение работают); не прошла — остаётся прежняя, и это пишется в заметки.

- AFO 0.1.11 — 2026-11-14 (`https://github.com/WorBHacker/AFO/releases/tag/v0.1.11`; слово пользователя — «тогда
  нужно выпустить новую версию в гит»): этапы 35–44 и пункты аудита #1–#7 — ускорение замером (эксперты MoE в ОЗУ,
  ngram, черновые модели, MTP, DFlash и MTP от ggml-org), трей, видеопамять — моделям, движок ставится сам, оценка по
  компьютеру, контекст на виду, поиск на Hugging Face. Перед отправкой: весь pytest после последнего кода — 1206, все
  прошли; после версии — тесты выпусков, установщика, лицензии и имени; 16 неотправленных коммитов — нет почты
  пользователя, строк соавтора и ключей, автор и коммиттер — адрес noreply. Коммит версии `9d8354a`, метка `v0.1.11`
  аннотированная «AFO 0.1.11». GitHub Actions собрал установщик за 4 мин («imports: ok», окно собрано, 0
  предупреждений QML), `AFO-Setup-0.1.11.exe` 89,2 МБ, sha256
  `34470938a60c133a5ba367c4284532e8f8eb61fc9fca11298aac1141a8109c80` — скачанный файл, `digest` GitHub и `.sha256`
  совпали. Заметки — по-русски (3153 знака из 4000) и по-английски; опубликован последним, `hub.release.latest` на
  настоящем GitHub видит 0.1.11 с этой sha256 и считает её новее 0.1.10. Найдено при заметках: «Выход» в меню трея
  делит строку каталога с ценой «Выход» (выходные токены) — в английском окне трей пишет «Output». Исправлено после
  выпуска (по слову пользователя «да исправь»; войдёт в следующий): у Python — контекст перевода, как у
  `qsTr("Свернуть", "window")` в QML: `i18n.tr_in("menu", "Выход")` — ключ `menu\x04Выход` («Exit»), без него —
  обычный; `i18n_catalog` находит `tr_in`. Тесты — `test_a_text_with_two_meanings_and_a_number`,
  `test_marked_and_unmarked_texts_are_found`, `test_the_trays_exit_is_not_a_prices_output`.
- AFO 0.1.12 — 2026-11-14 (`https://github.com/WorBHacker/AFO/releases/tag/v0.1.12`; слово пользователя — «сделай
  полный исправление с patch note и обнови версию», номер 0.1.12 — его выбор вместо «1.11a»: 0.1.11 читает версию
  только тремя числами и «a» не увидела бы): исправление 0.1.12 выше и «Exit» трея. Перед отправкой: весь pytest после
  последнего кода — 1213, все прошли; тесты выпусков, установщика, лицензии и имени после версии; неотправленные
  коммиты — без почты, соавторства и ключей, автор и коммиттер — noreply. Коммит версии `880155e`, метка `v0.1.12`
  аннотированная «AFO 0.1.12». Сборка GitHub Actions — «imports: ok», окно собрано, 0 предупреждений QML;
  `AFO-Setup-0.1.12.exe` 89,2 МБ, sha256 `3d620bff00b835ebdbd3856f54ba658a3feccabf261d02df1b398314655fc4e3` — скачанный
  файл, `digest` GitHub и `.sha256` совпали. Заметки — по-русски (2156 знаков из 4000) и по-английски; опубликован
  последним, `hub.release.latest` видит 0.1.12 с этой sha256 и считает её новее 0.1.11.
- AFO 0.1.13 — 2026-11-14 (`https://github.com/WorBHacker/AFO/releases/tag/v0.1.13`; слово пользователя — «да
  выпусти»): исправление 0.1.13 выше. Перед отправкой: весь pytest после последнего кода — 1219, ни один не упал; одна
  ошибка — страж реестра пользователя (`persons_registry_untouched`): за прогон (21:20–21:54) в «Графике» поменялась
  запись, которой нет в записи его AFO (`gpu.moved`; все 13 её записей на месте), страж вернул прежнее значение, какую
  именно — он не называет; код исправления реестр не трогает. После версии — тесты выпусков, установщика, лицензии и
  имени (21); неотправленные коммиты — без почты, соавторства и ключей, автор и коммиттер — noreply. Коммит версии
  `7c9b000`, метка `v0.1.13` аннотированная «AFO 0.1.13». Сборка GitHub Actions — «imports: ok», окно собрано, 0
  предупреждений QML; `AFO-Setup-0.1.13.exe` 89,2 МБ, sha256
  `b902818859b84423abcafb234b614151de77e7cec30e54d1b44ef75a5c68293a` — скачанный файл, `digest` GitHub и `.sha256`
  совпали. Заметки — по-русски (1769 знаков из 4000) и по-английски; опубликован последним, `hub.release.latest` видит
  0.1.13 с этой sha256 и считает её новее 0.1.12.
- AFO 0.1.14 — 2026-11-16 (`https://github.com/WorBHacker/AFO/releases/tag/v0.1.14`; слово пользователя —
  «Отправляй» после его проверки: 0.1.14 поверх 0.1.13 — ролик стартует один раз, перемотка плавная, чат отвечает):
  этапы 47–48 — модели для рисования и видео с Hugging Face, «Студия», один хозяин видеокарты, плеер на Qt Multimedia
  с FFmpeg из Qt, тексты LGPL для Qt и FFmpeg. Перед отправкой: весь pytest после последнего кода — 1287, все прошли;
  после версии — тесты выпусков, установщика, лицензии и имени (25); местная сборка — 92,15 МиБ, проверки папки
  прошли, LGPL 3 лежит у PySide6-Essentials, -Addons и shiboken6, LGPL 2.1 FFmpeg — у Addons, RECORD их называет;
  75 неотправленных коммитов — без почты, соавторства и ключей, автор и коммиттер — noreply. Коммит версии `c6857c1`,
  метка `v0.1.14` аннотированная «AFO 0.1.14». Сборка GitHub Actions за 4 мин — «imports: ok», окно собрано, 0
  предупреждений QML, «player: ok»; `AFO-Setup-0.1.14.exe` 92,13 МиБ, sha256
  `cf6fbf22e821210bedac730388564a100e9b4fcd25fd6d6f43e8f2528a1e3e96` — скачанный файл, `digest` GitHub и `.sha256`
  совпали. Заметки — по-русски (3451 знак из 4000) и по-английски; известные ограничения — «Выход» в трее, LTX-2.5 не
  проверена вживую, ffmpeg докачивается (~86 МБ) и, по слову пользователя, пресеты «Быстрее», «Баланс» и
  «Качественнее» ставят все слои на видеокарту, даже когда модель в неё не помещается (в разы медленнее; совет —
  «сколько поместится» и контекст 8K; исправить в 0.1.15). Опубликован последним, `hub.release.latest` видит 0.1.14 с
  этой sha256 и считает её новее 0.1.13.
- AFO 0.1.15 — 2026-11-17 (`https://github.com/WorBHacker/AFO/releases/tag/v0.1.15`; слово пользователя — «Да,
  отправляй И выпусти новую версию на гит»): «Стабилизация» — шаги 1–8 (порция 0.1.15) и 9–12 (порция 0.1.16;
  по слову пользователя одной версией, 0.1.16 не будет): модели, которые не помещаются в видеокарту (пресеты, вопрос
  при записи, совет при установке), подбор и «Ускорить», кнопки, которые отвечают сразу, длинный список моделей,
  докачка куска с места обрыва, проверенная сборка llama.cpp b11899. Перед отправкой: живая проверка шага 12 (b11899
  с настоящего GitHub в пустой папке); весь pytest после последнего кода — 1312, 1311 прошли (упал
  `test_dxgi_sees_the_cards_the_registry_knows`: Windows после перезагрузки записывает встроенной Intel 37 ГБ —
  «Стабилизация»); после версии — тесты выпусков, установщика, лицензии и имени (25); местная сборка — 92,16 МиБ,
  проверки папки прошли; 27 неотправленных коммитов — без почты, соавторства и ключей, автор и коммиттер — noreply.
  Коммит версии `f5c2d26`, метка `v0.1.15` аннотированная «AFO 0.1.15». Сборка GitHub Actions за 3,5 мин — «imports:
  ok», окно собрано, 0 предупреждений QML, «player: ok»; `AFO-Setup-0.1.15.exe` 92,16 МиБ, sha256
  `08d6ab92cb16286695a59c4f0301042227c6a225bcfbd519cfae1ef84d6a75a8` — скачанный файл, `digest` GitHub и `.sha256`
  совпали. Заметки — по-русски (3877 знаков из 4000) и по-английски; известные ограничения — «Выход» в трее, LTX-2.5
  не проверена вживую, черновая модель после установки и для модели, что не помещается, начатые куски после
  перезапуска — с начала, «Ускорить» второй модели ждёт молча, встроенная Intel в мастере с десятками ГБ, ffmpeg
  докачивается. Опубликован последним, `hub.release.latest` видит 0.1.15 с этой sha256 и считает её новее 0.1.14.
- AFO 0.1.16 — 2026-11-17 (`https://github.com/WorBHacker/AFO/releases/tag/v0.1.16`; слово пользователя —
  «Отправляй»): «Исправление после 0.1.15» — пять шагов установки моделей картинок и видео из
  поиска (варианты и «Установить» прямо в поиске, подсветка переключателей, видео в пустой «Студии», слова о памяти,
  граница видеокарты по реально занятой памяти и заголовку кодировщика), «Видеокарты» (встроенной графике — не её
  общая память, первой — карта, на которой работает AFO), `/scenes/` в `.gitignore`. Шаги по разрешениям
  «Доверенного» — 0.1.17. Перед сборкой: весь pytest после последнего кода (без `tests/test_computer.py`) — 1320, все
  прошли; местная сборка — «imports: ok», «player: ok», `AFO-Setup-0.1.16.exe` 92,18 МиБ, sha256
  `5597644b551c8d5639969dc16195926a386cf81205aa341296cba115097ba985`; 12 неотправленных коммитов — без почты,
  соавторства и ключей (единственное «sk-ant-» — поддельный ключ теста), автор и коммиттер — noreply. Коммит версии
  `2d7b990`, метка `v0.1.16` аннотированная «AFO 0.1.16». Сборка GitHub Actions за 4,5 мин — «imports: ok», окно
  собрано, 0 предупреждений QML, «player: ok»; `AFO-Setup-0.1.16.exe` 92,18 МиБ, sha256
  `8cb5b9892982c4e95574843356203876c733896a02dfbc0d99b1a13140812f4d` — скачанный файл, `digest` GitHub и `.sha256`
  совпали. Заметки — `build/dist/release-notes-0.1.16.md`, по-русски (2615 знаков из 4000) и по-английски; известные
  ограничения — «Доверенный» после чтения сайта спрашивает на каждом шаге (0.1.17), вызов инструмента текстом,
  «Выход» в трее, LTX-2.5 и семейства «по расчёту» не проверены вживую, «Ускорить» второй модели ждёт молча, начатые
  куски после перезапуска — с начала, ffmpeg докачивается. Опубликован последним, `hub.release.latest` видит 0.1.16 с
  этой sha256 и считает её новее 0.1.15.

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

### Правило 5. Жалоба пользователя — в «Стабилизацию» в тот же день

Слово пользователя 2026-11-17: любая жалоба пользователя в тот же день записывается в «Стабилизацию» его словами и с
датой. Пункт закрывается (✓) только его словом, после того как он проверил исправление в окне: коммит и прошедшие
тесты пункт не закрывают.

### Запреты

- В GitHub — только по слову пользователя. Тег выпуска аннотированный: `git tag -a -m "AFO X.Y.Z"`; отправленный тег
  не заменяют.
- В коммитах и PR нет строки соавтора Claude. Личную почту пользователя не писать в репозиторий.
- Новый этап начинается только по слову пользователя.
- `data/` пользователя — только чтение, его чаты не читать: поведение модели воспроизводи на стенде.
- Перед настоящими моделями проверь `nvidia-smi`: карта может быть занята игрой.
- На живой проверке смотри сам результат, а не только время и «готово»: у видео — число кадров в файле, меняются ли
  они, средний кадр глазами; у картинки — саму картинку (48.8: LTX снимал белым, замер записал только время).
- Хаб, запущенный вручную, — только с изоляцией как в `isolated_data_dir` из `tests/conftest.py`, иначе он запустит
  llama-swap пользователя.
- Пользователю отвечай по-русски.
