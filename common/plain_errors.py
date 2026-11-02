"""Errors in plain words (stage 10.6, D37): what a person without technical knowledge can do about the usual ones -
a model that does not fit the memory, llama-swap that is not running, a key the provider refuses, a provider that
asks to wait or wants money. The technical text stays beside it, for whoever helps.
"""

from __future__ import annotations

import re

# (what the technical text says, a short title, what to do) - the first that matches wins
RULES: list[tuple[re.Pattern[str], str, str]] = [
    (
        re.compile(
            r"(?i)out of memory|cudaMalloc failed|failed to allocate|ErrorOutOfDeviceMemory|not enough memory|"
            r"unable to allocate|insufficient memory|std::bad_alloc|VK_ERROR_OUT_OF"
        ),
        "Не хватает памяти",
        "Модели не хватило видеопамяти или ОЗУ. Закройте браузер и игры, выберите вариант модели поменьше или "
        "уменьшите контекст в «Настройке моделей → Запуск в llama-swap».",
    ),
    (
        re.compile(
            r"(?i)llama-swap[^\n]*(не отвечает|cannot list models|connection attempts failed|connection refused|"
            r"ConnectError)"
        ),
        "Локальные модели не запущены",
        "llama-swap не отвечает. Включите «Хаб сам запускает llama-swap» в «Настройки → Запуск» и перезапустите хаб, "
        "или откройте «Первый запуск», если движок ещё не установлен.",
    ),
    (  # llama-swap could not run the model's program at all (found live 2026-10-28)
        re.compile(r"(?i)failed to start command|fork/exec"),  # not a model's own file: that is the next rule
        "Программа модели не найдена",
        "llama-swap не нашёл программу, которой запускается модель: путь в config.yaml неверный или движок удалили. "
        "Установите движок в «Первом запуске» или поправьте путь в config.yaml; «Проверить систему» покажет, "
        "где он.",
    ),
    (
        re.compile(
            r"(?i)upstream command exited|exited prematurely|exit status|failed to load model|"
            r"error loading model|unknown model architecture|invalid magic|failed to open"
        ),
        "Модель не запустилась",
        "llama.cpp не смог загрузить файл модели: он повреждён, слишком новый для этой сборки или не помещается "
        "в память. Переустановите модель или обновите движок в «Первом запуске».",
    ),
    (
        re.compile(r"(?i)timed? ?out|ReadTimeout|no answer in"),
        "Модель отвечает слишком долго",
        "Большая модель долго загружается, особенно с жёсткого диска. Подождите и спросите ещё раз; если так "
        "каждый раз — переложите модель на SSD или выберите модель поменьше.",
    ),
    (
        re.compile(
            r"(?i)\b401\b|invalid[_ ]api[_ ]key|authentication|unauthori[sz]ed|incorrect api key|"
            r"API key not valid|permission[_ ]denied"
        ),
        "Ключ не подошёл",
        "Поставщик не принял ключ API: он неверный, отозван или без нужных прав. Замените его в «Подключениях» и "
        "нажмите «Проверить».",
    ),
    (
        re.compile(
            r"(?i)\b402\b|insufficient[_ ]?(quota|credit|balance|funds)|credit balance|billing|"
            r"payment required"
        ),
        "Закончились деньги на счёте",
        "У поставщика кончился баланс или лимит. Пополните счёт на его сайте или выберите локальную модель.",
    ),
    (
        re.compile(r"(?i)\b429\b|rate[_ ]limit|too many requests|overloaded|\b529\b|resource[_ ]exhausted"),
        "Поставщик просит подождать",
        "Слишком много запросов или серверы перегружены. Подождите минуту и спросите ещё раз.",
    ),
    (
        re.compile(r"(?i)budget|бюджет"),
        "Бюджет исчерпан",
        "Для этой модели закончился бюджет. Поднимите его в «Бюджетах» (Ctrl+B) или выберите другую модель.",
    ),
    (
        re.compile(r"(?i)context (length|window)|too many tokens|maximum context|prompt is too long|exceeds the"),
        "Разговор не помещается в модель",
        "Разговор стал длиннее, чем модель может прочитать. Начните новый чат или выберите модель с большим "
        "контекстом; у локальной модели контекст можно поднять: «Настройка моделей → Запуск в llama-swap → Контекст» "
        "(агенту нужно не меньше 32768).",
    ),
    (  # git's lock files: another program works with the repository right now (D46)
        re.compile(r"(?i)index\.lock|\.lock'?: File exists|cannot lock ref|Unable to create '[^']*\.lock'"),
        "Git занят другой программой",
        "С этим репозиторием сейчас работает другая программа — терминал, VS Code или git в фоне. Подождите минуту и "
        "повторите. Если это повторяется, а git нигде не запущен, удалите файл .git/index.lock.",
    ),
    (
        re.compile(
            r"(?i)name resolution|getaddrinfo|Network is unreachable|no route to host|SSL|certificate|"
            r"connection attempts failed|connection refused|ConnectError|ConnectTimeout"
        ),
        "Нет связи с интернетом",
        "Облачная модель недоступна: проверьте интернет. Локальные модели работают и без него.",
    ),
]


def explain(text: str) -> tuple[str, str] | None:
    """(title, what to do) for a technical error text, or None when it is not one of the usual."""
    for pattern, title, hint in RULES:
        if pattern.search(text or ""):
            return title, hint
    return None
