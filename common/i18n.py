"""The words people read, in their language (step 10.5). Russian is the source: the text in the code, as written.
Every other language is a catalog, common/locale/<code>.json, from each Russian text to its translation; a text the
catalog lacks stays Russian (tests/test_i18n.py makes sure none does).

- tr("Удалить «{name}»?", name=x): the text in the current language, then its fields (str.format, only when given).
- plural(n, "файл", "файла", "файлов"): the word for n. A catalog keeps a language's forms in one entry under
  "файл|файла|файлов" - English: "file|files".
- N_("..."): marks a text kept in a constant; it is translated where it is shown, with tr() - the language is chosen
  after the modules are imported.
- QML: qsTr("...") through the same catalog (app/ui/translator.py).

The language: LOCALCHAT_LANGUAGE, else the person's choice (Settings: the window keeps it, the hub a copy), else the
installer's (installer/afo.iss), else the first of Windows' preferred languages AFO speaks, else English."""

from __future__ import annotations

import json
import os
import sys
from functools import cache
from pathlib import Path

LANGUAGES = {"ru": "Русский", "en": "English"}  # each in its own words, whatever the window's  # i18n: keep
SOURCE = "ru"
FALLBACK = "en"
ENV = "LOCALCHAT_LANGUAGE"
LOCALE = Path(__file__).with_name("locale")
INSTALLER_KEY = r"Software\AFO"  # HKCU: the "Language" the installer was run in

_language = SOURCE


@cache
def catalog(code: str) -> dict[str, str]:
    path = LOCALE / f"{code}.json"
    return json.loads(path.read_text(encoding="utf-8")) if path.is_file() else {}


def language() -> str:
    return _language


def set_language(code: str) -> str:
    global _language
    if code not in LANGUAGES:
        raise ValueError(f"AFO does not speak {code!r}")
    _language = code
    return code


def tr(text: str, /, **fields: object) -> str:
    if _language != SOURCE:
        text = catalog(_language).get(text) or text
    return text.format(**fields) if fields else text


def N_(text: str) -> str:
    return text


def plural(n: int, one: str, few: str, many: str) -> str:
    """Russian: 1 сообщение, 2 сообщения, 5 сообщений, 21 сообщение; other languages - the catalog's forms."""
    if _language != SOURCE:
        forms = catalog(_language).get(f"{one}|{few}|{many}")
        if forms:
            single, other = forms.split("|")
            return single if n == 1 else other
    if n % 10 == 1 and n % 100 != 11:
        return one
    if 2 <= n % 10 <= 4 and not 12 <= n % 100 <= 14:
        return few
    return many


def speaks(tag: str | None) -> str | None:
    """'ru-RU', 'en_US.UTF-8', 'en' -> the language AFO speaks, or None."""
    code = (tag or "").replace("_", "-").split("-")[0].split(".")[0].lower()
    return code if code in LANGUAGES else None


def chosen(saved: str | None = None) -> str:
    """The language to speak: see the module's docstring."""
    for found in (os.environ.get(ENV), saved):
        if speaks(found):
            return speaks(found)
    for tag in (installer_choice(), *preferred_languages()):
        if speaks(tag):
            return speaks(tag)
    return FALLBACK


def installer_choice() -> str | None:
    """The language the installer was run in (installer/afo.iss writes it)."""
    if sys.platform != "win32":
        return None
    import winreg

    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, INSTALLER_KEY) as key:
            return str(winreg.QueryValueEx(key, "Language")[0])
    except OSError:
        return None


def preferred_languages() -> list[str]:
    """The person's display languages in their order, as Windows keeps them ('ru-RU', 'en-US')."""
    if sys.platform != "win32":
        found = os.environ.get("LC_ALL") or os.environ.get("LANG")
        return [found] if found else []
    import ctypes
    from ctypes import wintypes

    count, size = wintypes.ULONG(0), wintypes.ULONG(0)
    kernel = ctypes.windll.kernel32
    names = 0x8  # MUI_LANGUAGE_NAME
    if not kernel.GetUserPreferredUILanguages(names, ctypes.byref(count), None, ctypes.byref(size)):
        return []
    buffer = ctypes.create_unicode_buffer(size.value)
    if not kernel.GetUserPreferredUILanguages(names, ctypes.byref(count), buffer, ctypes.byref(size)):
        return []
    return [tag for tag in buffer[: size.value].split("\0") if tag]
