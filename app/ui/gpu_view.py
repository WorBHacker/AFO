"""«Видеокарта — моделям» in the window (43.7): the switch, the programs on the card - each moved, waiting for its
restart, or kept there and why - the person's choice for one, «Вернуть как было»; a notice when programs were moved
(the setting holds from their next start: the person restarts them). The hub decides and writes (hub/gpu_prefs.py).
"""

from __future__ import annotations

from typing import Any

from PySide6.QtCore import Property, QObject, Signal, Slot

from common.i18n import N_, tr

WHERE = {
    "moved": N_("на встроенной графике"),
    "waits": N_("переведена — освободит видеопамять после перезапуска"),
    "kept": N_("на видеокарте"),
    "card": N_("на видеокарте — переключатель выключен"),
}
WHY = {
    "person": N_("вы так выбрали"),
    "windows": N_("часть Windows"),
    "tool": N_("ей нужна видеокарта"),
    "game": N_("игра"),
    "chosen": N_("графику ей уже выбрали в Windows"),
    "never": N_("AFO её не трогает"),
}


def program_rows(programs: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """The programs as the sheet shows them: their words, and which choice the button offers."""
    out = []
    for p in programs:
        where = tr(WHERE.get(p.get("where", ""), "")) if p.get("where") in WHERE else ""
        why = tr(WHY[p["why"]]) if p.get("why") in WHY else ""
        on_card = p.get("where") in ("kept", "card")
        offer = tr("Перевести на встроенную") if on_card else tr("Оставить на видеокарте")
        out.append({**p, "text": where + (f" ({why})" if why else ""),
                    "size": tr("{mib} МБ", mib=f"{p.get('mib') or 0:.0f}"),
                    "offer": "integrated" if on_card else "card", "offerText": offer})  # fmt: skip
    return out


class GpuView(QObject):
    changed = Signal()
    noticed = Signal(str, str)  # programs were moved: a notice outside the window (the tray)

    def __init__(self, app) -> None:
        super().__init__(app)
        self.app = app
        self._state: dict[str, Any] = {}

    @Property("QVariantMap", notify=changed)
    def state(self) -> dict:
        return dict(self._state)

    @Property(bool, notify=changed)
    def available(self) -> bool:
        return bool(self._state.get("available"))

    @Property(bool, notify=changed)
    def on(self) -> bool:
        return bool(self._state.get("on"))

    @Property(list, notify=changed)
    def programs(self) -> list:
        return program_rows(self._state.get("programs") or [])

    @Property(str, notify=changed)
    def summary(self) -> str:
        """One line: what goes where, and what it freed."""
        if not self._state:
            return ""
        if not self.available:
            return tr("Встроенной графики рядом с видеокартой нет — переводить программы некуда.")
        moved = [p for p in self._state.get("programs") or [] if p.get("where") in ("moved", "waits")]
        waits = sum(1 for p in moved if p.get("where") == "waits")
        text = tr("Встроенная: {integrated} · видеокарта: {card}", integrated=self._state.get("integrated", ""),
                  card=self._state.get("card", ""))  # fmt: skip
        if moved:
            text += " · " + tr("переведено программ: {n}, освобождено ~{mib} МБ", n=len(moved),
                               mib=f"{self._state.get('freed_mib') or 0:.0f}")  # fmt: skip
        if waits:
            text += " · " + tr("ждут перезапуска: {n}", n=waits)
        return text

    @Property(str, notify=changed)
    def trayText(self) -> str:
        freed = self._state.get("freed_mib") or 0
        return tr("Видеокарта — моделям (освобождено ~{mib} МБ)", mib=f"{freed:.0f}") if freed else tr(
            "Видеокарта — моделям")  # fmt: skip

    def _set(self, state: dict[str, Any]) -> None:
        if state:
            self._state = state
            self.changed.emit()

    @Slot()
    def load(self) -> None:
        self.app._call(lambda: self.app.client.gpu(), self._set, lambda _e: None)

    @Slot(bool)
    def setOn(self, on: bool) -> None:
        self.app._call(lambda: self.app.client.set_gpu(on), self._set,
                       lambda e: self.app.show_error(tr("Не вышло: {error}", error=e)))  # fmt: skip

    @Slot(str, str)
    def choose(self, exe: str, where: str) -> None:
        self.app._call(lambda: self.app.client.gpu_choose(exe, where), self._set,
                       lambda e: self.app.show_error(tr("Не вышло: {error}", error=e)))  # fmt: skip

    @Slot()
    def giveBack(self) -> None:
        """«Вернуть как было» - asked first: the programs go back to Windows' own choice, the switch goes off."""

        def decide(yes: bool) -> None:
            if not yes:
                return

            def done(found: dict[str, Any]) -> None:
                self._set(found.get("state") or {})
                left = found.get("left") or []
                said = tr("Графика программ возвращена как была: {n}", n=len(found.get("given_back") or []))
                if left:
                    said += " · " + tr("ваши изменения не тронуты: {n}", n=len(left))
                self.app.flash(said)

            self.app._call(lambda: self.app.client.gpu_give_back(), done,
                           lambda e: self.app.show_error(tr("Не вышло: {error}", error=e)))  # fmt: skip

        self.app._ask(tr("Вернуть графику программ как было?"),
                      tr("Программы, которые AFO перевёл на встроенную графику, снова будут на выбор Windows — после "
                         "их перезапуска. Переключатель «Видеокарта — моделям» выключится."),
                      tr("Вернуть"), decide)  # fmt: skip

    def on_event(self, kind: str, payload: dict[str, Any]) -> None:
        if kind == "gpu.prefs":
            self._set(payload.get("state") or {})
        elif kind == "gpu.moved":
            names = ", ".join(p.get("name") or "" for p in payload.get("programs") or [])
            text = tr("Перезапустите их, чтобы освободить видеопамять для моделей: {names}", names=names)
            self.app.flash(tr("Переведены на встроенную графику: {names}", names=names))
            self.noticed.emit(tr("Видеокарта — моделям"), text)
            self.load()
