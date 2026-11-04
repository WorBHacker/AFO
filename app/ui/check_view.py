"""«Проверить систему» in the window (stage 10.6, D37): each line of the hub's check with a mark - works, worth a look,
does not work - and what to do; «Собрать журналы» makes the zip for whoever helps and opens its folder.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from PySide6.QtCore import Property, QObject, Signal, Slot

from common.i18n import N_, tr

if TYPE_CHECKING:
    from .controller import Controller

MARKS = {True: ("ok", N_("в порядке")), None: ("warn", N_("стоит взглянуть")), False: ("error", N_("не работает"))}


def check_rows(lines: list[dict[str, Any]]) -> list[dict[str, Any]]:
    out = []
    for line in lines:
        tone, word = MARKS.get(line.get("ok"), ("", ""))
        out.append({"id": line["id"], "title": line["title"], "text": line.get("text", ""),
                    "advice": line.get("advice", ""), "tone": tone, "mark": tr(word) if word else ""})  # fmt: skip
    return out


def summary(lines: list[dict[str, Any]]) -> str:
    bad = sum(1 for line in lines if line.get("ok") is False)
    warn = sum(1 for line in lines if line.get("ok") is None)
    if not lines:
        return ""
    if not bad and not warn:
        return tr("Всё в порядке")
    parts = []
    if bad:
        parts.append(tr("не работает: {count}", count=bad))
    if warn:
        parts.append(tr("стоит взглянуть: {count}", count=warn))
    return ", ".join(parts).capitalize()


class CheckView(QObject):
    changed = Signal()

    def __init__(self, app: Controller):
        super().__init__(app)
        self.app = app
        self._lines: list[dict[str, Any]] = []
        self._busy = False
        self._report = ""

    @Property(list, notify=changed)
    def lines(self) -> list:
        return check_rows(self._lines)

    @Property(str, notify=changed)
    def summary(self) -> str:
        return tr("Проверяю…") if self._busy and not self._lines else summary(self._lines)

    @Property(bool, notify=changed)
    def busy(self) -> bool:
        return self._busy

    @Property(str, notify=changed)
    def report(self) -> str:
        return self._report

    @Slot()
    def run(self) -> None:
        if self.app.client is None or self._busy:
            return
        self._busy, self._lines = True, []
        self.changed.emit()

        def done(data: dict[str, Any]) -> None:
            self._busy, self._lines = False, data.get("lines") or []
            self.changed.emit()

        def failed(error: Exception) -> None:
            self._busy = False
            advice = tr("Хаб не ответил: закройте окно и откройте снова — оно запустит хаб.")
            self._lines = [{"id": "hub", "title": tr("Хаб"), "ok": False, "text": str(error), "advice": advice}]
            self.changed.emit()

        self.app.call_or_fail(lambda: self.app.client.support_check(), done, failed)

    @Slot()
    def collect(self) -> None:
        if self.app.client is None or self._busy:
            return
        self._busy = True
        self.changed.emit()

        def done(data: dict[str, Any]) -> None:
            self._busy, self._lines, self._report = False, data.get("lines") or self._lines, data.get("path", "")
            self.changed.emit()
            self.app.flash(tr("Журналы собраны: {path}", path=self._report))
            self.app.openFolder(data.get("folder", ""))

        def failed(error: Exception) -> None:
            self._busy = False
            self.changed.emit()
            self.app.show_error(tr("Журналы не собраны: {error}", error=error))

        self.app.call_or_fail(lambda: self.app.client.support_report(), done, failed)
