"""The window in its language (step 10.5): QML's qsTr() through common.i18n's catalog, Qt's own words (its dialogs'
buttons) through Qt's translations. Installed before the window loads; another language - the window opens again."""

from __future__ import annotations

from PySide6.QtCore import QCoreApplication, QLibraryInfo, QLocale, QSettings, QTranslator

from common import i18n

LANGUAGE_KEY = "ui/language"  # in the window's app.ini: the person's choice in Settings


class Catalog(QTranslator):
    """qsTr("Настройки") -> "Settings": the catalog's text, or None - Qt then keeps the source."""

    def translate(self, context: str, source: str, disambiguation: str | None = None, n: int = -1) -> str | None:
        if i18n.language() == i18n.SOURCE:
            return None
        return i18n.catalog(i18n.language()).get(source) or None

    def isEmpty(self) -> bool:
        return False


def use(app: QCoreApplication, settings: QSettings) -> str:
    """The window's language, chosen as common.i18n says, and its translators: before any text is made."""
    i18n.set_language(i18n.chosen(settings.value(LANGUAGE_KEY)))
    install(app)
    return i18n.language()


def install(app: QCoreApplication) -> None:
    """Qt's own translations first, then AFO's: the last one installed is asked first."""
    qt = QTranslator(app)
    if qt.load(QLocale(i18n.language()), "qtbase", "_", QLibraryInfo.path(QLibraryInfo.LibraryPath.TranslationsPath)):
        app.installTranslator(qt)
    app.installTranslator(Catalog(app))
