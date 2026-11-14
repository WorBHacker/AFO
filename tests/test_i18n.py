"""The words people read, in their language (step 10.5): Russian is the source - the text in the code - and English a
catalog. The language is the person's choice in Settings, else the installer's, else the first of Windows' preferred
languages AFO speaks, else English. The catalog has every text the code marks and nothing else, with the same fields,
and the translated code has no Russian text left unmarked."""

import pytest

from common import i18n, i18n_catalog
from tests.test_tools import hub  # noqa: F401 - the hub fixture

TRANSLATED = ["app", "hub", "common"]  # everything people read (10.5a-10.5c)


@pytest.fixture
def english(monkeypatch):
    """English, with a catalog of the test's own (the real one is checked as a whole below)."""
    words = {}
    monkeypatch.setattr(i18n, "catalog", lambda code: words)
    i18n.set_language("en")
    return words


def test_russian_is_the_text_itself_and_english_comes_from_the_catalog(english):
    english["Удалить «{name}»?"] = "Delete “{name}”?"
    assert i18n.tr("Удалить «{name}»?", name="a.txt") == "Delete “a.txt”?"
    assert i18n.tr("Нет в каталоге") == "Нет в каталоге"  # missing: stays Russian, never empty
    assert i18n.N_("Тема") == "Тема"  # kept in a constant: translated where it is shown
    i18n.set_language("ru")
    assert i18n.tr("Удалить «{name}»?", name="a.txt") == "Удалить «a.txt»?"
    assert i18n.tr("{n:.1f} с", n=2.25) == "2.2 с" and i18n.tr("{} {{x}}") == "{} {{x}}"  # fields only with values


def test_a_text_with_two_meanings_and_a_number(english):
    """qsTr("Свернуть", "window"): the window's button minimizes, a card's link collapses - one Russian word, two
    English ones; the one with a context has gettext's key. A number's decimal point follows the language."""
    assert i18n.in_context("window", "Свернуть") == "window\x04Свернуть"
    # Python too (found 2026-11-14 at release 0.1.11): the tray's «Выход» is Exit, a price's «Выход» - its output
    english.update({"Выход": "Output", "menu\x04Выход": "Exit"})
    assert (i18n.tr_in("menu", "Выход"), i18n.tr("Выход"), i18n.tr_in("menu", "Вход")) == ("Exit", "Output", "Вход")
    english["menu\x04Удалить {n}"] = "Delete {n}"
    assert i18n.tr_in("menu", "Удалить {n}", n=2) == "Delete 2"
    assert i18n.decimal("1.5") == "1.5"
    i18n.set_language("ru")
    assert i18n.decimal("1.5") == "1,5"


def test_the_trays_exit_is_not_a_prices_output(monkeypatch):
    """Found at release 0.1.11: the tray's «Выход» shared its key with a price's «Выход» and read «Output» in English;
    the real catalog keeps them apart."""
    monkeypatch.setattr(i18n, "_language", "en")
    assert (i18n.tr_in("menu", "Выход"), i18n.tr("Выход")) == ("Exit", "Output")


def test_plural_forms_per_language(english):
    forms = ("файл", "файла", "файлов")
    english["файл|файла|файлов"] = "file|files"
    assert [i18n.plural(n, *forms) for n in (0, 1, 2, 21)] == ["files", "file", "files", "files"]
    i18n.set_language("ru")
    numbers = (1, 2, 5, 11, 12, 21, 22, 25, 111, 112)
    assert [i18n.plural(n, *forms) for n in numbers] == ["файл", "файла", "файлов", "файлов", "файлов", "файл", "файла",
                                                         "файлов", "файлов", "файлов"]  # fmt: skip


def test_the_language_chosen(monkeypatch):
    monkeypatch.delenv(i18n.ENV, raising=False)
    monkeypatch.setattr(i18n, "installer_choice", lambda: None)
    monkeypatch.setattr(i18n, "preferred_languages", lambda: ["uz-Latn-UZ", "ru-RU", "en-US"])
    assert i18n.chosen() == "ru"  # the first of Windows' languages AFO speaks
    monkeypatch.setattr(i18n, "preferred_languages", lambda: ["de-DE"])
    assert i18n.chosen() == "en"  # none of them: English
    monkeypatch.setattr(i18n, "installer_choice", lambda: "ru")
    assert i18n.chosen() == "ru"  # the language the installer was run in
    assert i18n.chosen("en") == "en"  # the person's choice in Settings comes before it
    assert i18n.chosen("xx") == "ru"  # a language AFO does not speak: as if none was chosen
    monkeypatch.setenv(i18n.ENV, "en")
    assert i18n.chosen("ru") == "en"  # the environment first: the tests pin Russian so
    with pytest.raises(ValueError):
        i18n.set_language("xx")


def test_windows_says_which_languages_the_person_reads():
    found = i18n.preferred_languages()
    assert isinstance(found, list) and all(isinstance(tag, str) and tag for tag in found)


def test_the_catalog_has_every_marked_text_and_nothing_else():
    used = i18n_catalog.keys()
    for code in i18n.LANGUAGES:
        if code == i18n.SOURCE:
            continue
        found = i18n_catalog.load(code)
        missing = sorted(set(used) - set(found))
        assert missing == [], f"{code}: not translated - {[(k, used[k][0]) for k in missing[:20]]}"
        assert sorted(set(found) - set(used)) == [], f"{code}: kept for nothing (python -m common.i18n_catalog)"
        assert i18n_catalog.problems(found) == []


def test_a_translation_keeps_the_texts_fields():
    wrong = i18n_catalog.problems({"Удалить {name}?": "Delete {file}?"})
    assert wrong == ["Удалить {name}?: fields ['name'] ≠ ['file']"]
    assert i18n_catalog.problems({"Открыть %1 из %2": "Open %1"}) == ["Открыть %1 из %2: fields ['%1', '%2'] ≠ ['%1']"]
    assert i18n_catalog.problems({"Тема": ""}) == ["Тема: not translated"]
    assert i18n_catalog.problems({"Тема": "Тема"}) == ["Тема: Russian left"]
    assert i18n_catalog.problems({"файл|файла|файлов": "files"}) == ["файл|файла|файлов: not 2 forms"]
    assert i18n_catalog.problems({"Тема": "Theme", "файл|файла|файлов": "file|files"}) == []


def test_marked_and_unmarked_texts_are_found(tmp_path):
    (tmp_path / "view.py").write_text(
        '"""Модуль: докстроки не переводятся."""\n'
        "from common.i18n import N_, plural, tr, tr_in\n"
        'LABELS = {"low": N_("низкая"), "quit": tr_in("menu", "Выход")}\n'
        "def f(n, name):\n"
        '    log.info("журнал — не для людей %s", n)\n'
        '    words = plural(n, "файл", "файла", "файлов")\n'
        '    pattern = "да|нет"  # i18n: keep\n'
        '    return tr("Удалить «{name}»?", name=name) + f"ещё {n} штук" + "забыто"\n',
        encoding="utf-8",
    )
    (tmp_path / "Sheet.qml").write_text(
        "Item {\n"
        '    // комментарий: "не текст"\n'
        '    property string a: qsTr("Настройки")\n'
        "    property string b: qsTr(\"Удалить «%1»?\").arg(name) + ' забыто'\n"
        '    property string url: "https://example.org/путь"  // i18n: keep\n'
        "    /* и «блок» */\n"
        '    property string tip: qsTr("Свернуть", "window") + qsTr("«%1» — %2").arg(x)\n'
        "}\n",
        encoding="utf-8",
    )
    keys = i18n_catalog.keys([tmp_path])
    assert sorted(keys) == ["menu\x04Выход", "window\x04Свернуть", "«%1» — %2", "Настройки", "Удалить «%1»?",
                            "Удалить «{name}»?", "низкая", "файл|файла|файлов"]  # fmt: skip
    assert keys["Настройки"] == [f"{tmp_path / 'Sheet.qml'}:3"]
    left = i18n_catalog.unmarked([tmp_path / "view.py", tmp_path / "Sheet.qml"])
    assert [place.rsplit(":", 1)[1] for place in left] == ["8", "8", "4"]


def test_the_translated_files_have_no_russian_text_left_unmarked():
    assert i18n_catalog.unmarked([i18n_catalog.ROOT / name for name in TRANSLATED]) == []


def test_qml_texts_come_through_the_catalog():
    """qsTr() in QML asks Qt's translators; AFO's answers from the same catalog - Russian stays the source."""
    from PySide6.QtCore import QUrl
    from PySide6.QtQml import QQmlComponent, QQmlEngine
    from PySide6.QtWidgets import QApplication

    from app.ui import translator

    qt = QApplication.instance() or QApplication([])
    catalog = translator.Catalog()
    qt.installTranslator(catalog)
    try:
        engine = QQmlEngine()
        component = QQmlComponent(engine)
        component.setData('import QtQuick\nItem { property string a: qsTr("Настройки"); '
                          'property string b: qsTr("Сейчас: %1").arg("5 ГБ"); property string c: qsTr("Нет такого"); '
                          'property string d: qsTr("Свернуть", "window"); property string e: qsTr("Свернуть") }'
                          .encode(), QUrl("file:///Probe.qml"))  # fmt: skip
        item = component.create()
        assert (item.property("a"), item.property("b"), item.property("c")) == ("Настройки", "Сейчас: 5 ГБ",
                                                                                 "Нет такого")  # fmt: skip
        i18n.set_language("en")
        engine.retranslate()
        assert (item.property("a"), item.property("b"), item.property("c")) == ("Settings", "Now: 5 ГБ", "Нет такого")
        assert (item.property("d"), item.property("e")) == ("Minimize", "Collapse")  # one Russian word, two meanings
    finally:
        qt.removeTranslator(catalog)


def test_the_window_starts_in_the_language_kept_in_its_settings(tmp_path, monkeypatch):
    from PySide6.QtCore import QSettings

    from app.ui import translator

    monkeypatch.delenv(i18n.ENV)
    monkeypatch.setattr(translator, "install", lambda app: None)
    settings = QSettings(str(tmp_path / "app.ini"), QSettings.Format.IniFormat)
    settings.setValue(translator.LANGUAGE_KEY, "en")
    assert translator.use(None, settings) == "en" == i18n.language()


def test_the_hub_speaks_the_windows_language(hub, monkeypatch):  # noqa: F811
    """/v1/language: the window chooses, the hub keeps the choice - a hub started at login speaks it before the window
    opens - and writes in it from then on; no other client chooses it."""
    from hub import language
    from hub.auth import create_client

    assert hub.get("/v1/language").json() == {"language": "ru", "languages": i18n.LANGUAGES}
    assert hub.put("/v1/language", json={"language": "en"}).json()["language"] == "en" == i18n.language()
    assert hub.app.state.db.call(language.saved) == "en"
    assert hub.put("/v1/language", json={"language": "xx"}).status_code == 422
    other = hub.app.state.db.call(lambda c: create_client(c, "cline"))
    refused = hub.put("/v1/language", json={"language": "ru"}, headers={"Authorization": f"Bearer {other}"})
    assert refused.status_code == 403 and hub.get("/v1/language").json()["language"] == "en"
    i18n.set_language("ru")
    monkeypatch.delenv(i18n.ENV)
    monkeypatch.setattr(i18n, "preferred_languages", lambda: ["ru-RU"])
    assert hub.app.state.db.call(language.load) == "en"  # the hub starting again: the choice, not Windows' language


def test_the_vscode_extension_speaks_vs_codes_language():
    """VS Code's own way (10.5d): the extension's texts are English in its code - vscode.l10n.t(), %keys% in
    package.json - and Russian in l10n/bundle.l10n.ru.json and package.nls.ru.json; VS Code shows its own language."""
    import json
    import re

    folder = i18n_catalog.ROOT / "vscode"
    script = (folder / "extension.js").read_text(encoding="utf-8")
    said = {m[1].replace("'", "'") for m in re.findall(r"\bt\((['\"])((?:\.|(?!\1).)*)\1", script)}
    russian = json.loads((folder / "l10n" / "bundle.l10n.ru.json").read_text(encoding="utf-8"))
    assert said and said == set(russian)
    for english, words in russian.items():
        assert set(re.findall(r"\{\d\}", english)) == set(re.findall(r"\{\d\}", words)), english
        assert i18n_catalog.RUSSIAN.search(words) and not i18n_catalog.RUSSIAN.search(english)
    manifest = (folder / "package.json").read_text(encoding="utf-8")
    keys = set(re.findall(r'"%([\w.]+)%"', manifest))
    for name in ("package.nls.json", "package.nls.ru.json"):
        assert set(json.loads((folder / name).read_text(encoding="utf-8"))) == keys, name
    assert keys and not i18n_catalog.RUSSIAN.search(manifest + script)


def test_the_readme_in_both_languages():
    """GitHub shows README.md (Russian); README.en.md is the English one; each links the other."""
    russian = (i18n_catalog.ROOT / "README.md").read_text(encoding="utf-8")
    english = (i18n_catalog.ROOT / "README.en.md").read_text(encoding="utf-8")
    assert russian.startswith("# AFO\n") and english.startswith("# AFO\n")
    assert "(README.en.md)" in russian and "(README.md)" in english
    assert "python -m common.i18n_catalog" in english  # how a new text gets its English


def test_the_proving_grounds_tasks_in_both_languages():
    """People read a task's title, level, about and each hidden check's why in the window's language; the objective
    and the constraints are the model's task and stay the same in every language, so exams stay comparable (10.5c)."""
    import json

    from hub import proving

    for folder in proving.TASKS_DIR.iterdir():
        spec = json.loads((folder / "task.json").read_text(encoding="utf-8"))
        english = spec["en"]
        assert len(english["why"]) == len(spec["exam"]), folder.name
        words = [english["title"], english["level"], english["about"], *english["why"]]
        assert all(words) and not any(i18n_catalog.RUSSIAN.search(w) for w in words), folder.name
    russian = {t["id"]: t for t in proving.tasks()}
    i18n.set_language("en")
    english = {t["id"]: t for t in proving.tasks()}
    assert russian.keys() == english.keys() and len(english) >= 6
    for name, task in english.items():
        assert not i18n_catalog.RUSSIAN.search(task["title"] + task["level"] + task["about"]), name
        assert task["objective"] == russian[name]["objective"] and task["grade"] == russian[name]["grade"]
        assert proving.check_why(proving._spec(proving.TASKS_DIR / name), 1).isascii()
    assert {t["grade"] for t in english.values()} == {"easy", "medium", "hard"}  # the window colours by it


def test_an_error_is_explained_in_the_hubs_language():
    """The plain words for an error the hub wrote in English find it too (the patterns know both languages)."""
    from common.plain_errors import explain

    i18n.set_language("en")
    title, advice = explain("llama-swap is not answering")
    assert title == "Local models are not running" and "Settings → Startup" in advice
    assert explain("the budget is used up")[0] == "Budget used up"
