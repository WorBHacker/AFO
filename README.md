# LocalChat (рабочее название)

Локальная AI-экосистема: **хаб** знает проекты, процессы, терминалы и модели; **лёгкое приложение** на Qt Widgets (рисуется процессором, видеокарта остаётся моделям); VS Code и агенты подключаются к хабу. Все решения и этапы — в [ARCHITECTURE.md](ARCHITECTURE.md).

## Структура

| Папка | Что это |
|---|---|
| `hub/` | хаб — фоновый процесс на `127.0.0.1:8765`: API, база, шлюз моделей (llama-swap, Claude) |
| `app/` | окно чата на PySide6: `app/ui/` — Qt Quick (QML), рисует процессор; клиент хаба, сам запускает хаб, если тот не работает |
| `common/` | общий код: пути к данным, логирование |
| `tests/` | тесты (pytest) |
| `data/` | база, логи, снимки — создаётся сама, в git не попадает |
| `localchat/` | прототип чата, разбирается на части в этапе 1 |

## Разработка

Подготовка окружения (Python 3.14):

```bash
py -3.14 -m venv .venv
.venv/Scripts/python.exe -m pip install -e ".[hub,app,dev]"
```

Проверки:

```bash
.venv/Scripts/ruff.exe check .
.venv/Scripts/ruff.exe format --check .
.venv/Scripts/python.exe -m pytest
```

Запуск окна: двойной щелчок по `LocalChat.cmd` (без консоли) или

```bash
.venv/Scripts/python.exe -m app
```

Окно само запускает хаб, если он не работает; после закрытия окна хаб продолжает работать. Хаб вручную:

```bash
.venv/Scripts/python.exe -m hub
.venv/Scripts/python.exe -m hub stop
```

Ключи API (Anthropic, Google Gemini, Brave Search), адрес своего SearXNG и токен Telegram-бота задаются в окне:
«Подключения» (Ctrl+Shift+K или «Настройки → Открыть подключения…»). В первый раз окно просит придумать пароль
подключений — без него ключи не добавить, не заменить и не удалить. Ключи хранятся в диспетчере учётных данных
Windows и из хаба не выходят. То же из консоли (спросит пароль, если он задан):

```bash
.venv/Scripts/python.exe -m hub key set anthropic_api_key
.venv/Scripts/python.exe -m hub key status
```

Забыли пароль — `python -m hub connections reset`: сбрасывает его вместе со всеми сохранёнными подключениями.

Логи пишутся в `data/logs/<компонент>.log`. Папку данных можно переопределить переменной `LOCALCHAT_DATA_DIR`.
