# LocalChat (рабочее название)

Локальная AI-экосистема: **хаб** знает проекты, процессы, терминалы и модели; **лёгкое приложение** на Qt Widgets (рисуется процессором, видеокарта остаётся моделям); VS Code и агенты подключаются к хабу. Все решения и этапы — в [ARCHITECTURE.md](ARCHITECTURE.md).

## Структура

| Папка | Что это |
|---|---|
| `hub/` | хаб — фоновый процесс на `127.0.0.1:8765`: API, база, шлюз моделей (llama-swap, Claude) |
| `app/` | приложение на PySide6 |
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

Запуск:

```bash
.venv/Scripts/python.exe -m hub
.venv/Scripts/python.exe -m hub stop
.venv/Scripts/python.exe -m app
```

Ключ Anthropic (для моделей Claude) хранится в диспетчере учётных данных Windows, ввод скрыт:

```bash
.venv/Scripts/python.exe -m hub key set anthropic_api_key
.venv/Scripts/python.exe -m hub key status
```

Логи пишутся в `data/logs/<компонент>.log`. Папку данных можно переопределить переменной `LOCALCHAT_DATA_DIR`.
