# LocalChat (рабочее название)

Локальная AI-экосистема: **хаб** знает проекты, процессы, терминалы и модели; **лёгкое приложение** на Qt Widgets (рисуется процессором, видеокарта остаётся моделям); VS Code и агенты подключаются к хабу. Все решения и этапы — в [ARCHITECTURE.md](ARCHITECTURE.md).

## Структура

| Папка | Что это |
|---|---|
| `hub/` | хаб — фоновый процесс (сервер появится в этапе 1) |
| `app/` | приложение на PySide6 |
| `common/` | общий код: пути к данным, логирование |
| `tests/` | тесты (pytest) |
| `data/` | база, логи, снимки — создаётся сама, в git не попадает |
| `localchat/` | прототип чата, разбирается на части в этапе 1 |

## Разработка

Подготовка окружения (Python 3.14):

```bash
py -3.14 -m venv .venv
.venv/Scripts/python.exe -m pip install -e ".[app,dev]"
```

Проверки:

```bash
.venv/Scripts/ruff.exe check .
.venv/Scripts/ruff.exe format --check .
.venv/Scripts/python.exe -m pytest
```

Запуск (пока только проверка связей):

```bash
.venv/Scripts/python.exe -m hub
.venv/Scripts/python.exe -m app
```

Логи пишутся в `data/logs/<компонент>.log`. Папку данных можно переопределить переменной `LOCALCHAT_DATA_DIR`.
