"""AFO's own updates in the window (step 10.4d): Settings → «О программе» says which AFO this is and whether a newer
one is out, downloads it on a click and installs it after a question - the window closes and opens again on the new
version. A newer version is said once in the status line; a checkout of the code is told to update with git."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from PySide6.QtCore import Property, QObject, Signal, Slot

from common.i18n import tr

if TYPE_CHECKING:
    from .controller import Controller

TOLD_KEY = "release/told"  # in app.ini: the version the status line has already told of


def status_text(state: dict[str, Any], size: str) -> str:
    """The line under the version in Settings."""
    job = state.get("job") or {}
    status, current, latest = job.get("status"), state.get("current", ""), state.get("latest", "")
    if status == "updated" and not state.get("newer"):  # a newer one out already says itself (below)
        return tr("AFO обновлена до {version}", version=job.get("version", current))
    if status == "failed":
        return job.get("error", "")
    if status == "downloading":
        return tr("Скачивается AFO {version}…", version=job.get("version", latest))
    if status == "ready":
        return tr("AFO {version} скачана — можно ставить", version=job.get("version", latest))
    if status == "installing":
        return tr("Ставится AFO {version}: окно закроется и откроется снова", version=job.get("version", latest))
    if state.get("newer"):
        return tr("Есть AFO {latest} ({size}) — у вас {current}", latest=latest, size=size, current=current)
    if state.get("error"):
        return tr("Не удалось проверить обновления: {error}", error=state["error"])
    if state.get("checked"):
        return tr("AFO {version} — самая новая версия", version=current)
    return f"AFO {current}" if current else ""


class ReleaseView(QObject):
    changed = Signal()

    def __init__(self, app: Controller):
        super().__init__(app)
        self.app = app
        self._state: dict[str, Any] = {}

    # ================= for QML =================

    @Property(str, notify=changed)
    def text(self) -> str:
        from .controller import file_size

        return status_text(self._state, file_size(int(self._state.get("size") or 0)))

    @Property(str, notify=changed)
    def note(self) -> str:
        if self._state.get("newer") and self._state.get("installed") is False:
            return tr("Это рабочая копия AFO: обновите её через git pull.")
        return ""

    @Property(str, notify=changed)
    def latest(self) -> str:
        return self._state.get("latest", "")

    @Property(str, notify=changed)
    def notesUrl(self) -> str:
        return self._state.get("url", "") if self._state.get("newer") else ""

    def _status(self) -> str:
        return (self._state.get("job") or {}).get("status", "idle")

    @Property(bool, notify=changed)
    def canDownload(self) -> bool:
        return bool(self._state.get("newer") and self._state.get("installed")) and self._status() in (
            "idle", "failed", "cancelled", "updated")  # updated: to the version before this newer one  # fmt: skip

    @Property(bool, notify=changed)
    def canInstall(self) -> bool:
        return bool(self._state.get("installed")) and self._status() == "ready"

    @Property(bool, notify=changed)
    def busy(self) -> bool:
        return self._status() in ("downloading", "installing")

    @Property(bool, notify=changed)
    def downloading(self) -> bool:
        return self._status() == "downloading"

    @Property(bool, notify=changed)
    def failed(self) -> bool:
        return self._status() == "failed"

    @Property(float, notify=changed)
    def progress(self) -> float:
        job = self._state.get("job") or {}
        return job.get("done", 0) / job["total"] if job.get("total") else 0.0

    @Property(bool, notify=changed)
    def auto(self) -> bool:
        return self._state.get("auto", True) is not False

    # ================= the hub =================

    def _set(self, state: dict[str, Any]) -> None:
        self._state = state
        self.changed.emit()
        job = state.get("job") or {}
        told = str(self.app.settings.value(TOLD_KEY, "") or "")
        if job.get("status") == "updated" and told != "updated " + job.get("version", ""):
            self.app.settings.setValue(TOLD_KEY, "updated " + job.get("version", ""))
            self.app.flash(tr("AFO обновлена до {version}", version=job.get("version", "")))
        elif state.get("newer") and state.get("auto") is not False and told != state.get("latest"):
            self.app.settings.setValue(TOLD_KEY, state.get("latest", ""))
            self.app.flash(tr("Есть AFO {version} — «Настройки → О программе»", version=state.get("latest", "")))

    def _ask_hub(self, call) -> None:
        if self.app.client is not None:
            self.app.call_or_fail(
                call, self._set, lambda e: self.app.show_error(tr("Обновление AFO: {error}", error=e))
            )

    @Slot()
    def load(self) -> None:
        if self.app.client is not None:
            self.app._call(lambda: self.app.client.release(), self._set, lambda _e: None)

    @Slot()
    def check(self) -> None:
        self._ask_hub(lambda: self.app.client.release_check())

    @Slot()
    def download(self) -> None:
        self._ask_hub(lambda: self.app.client.release_download())

    @Slot()
    def cancel(self) -> None:
        self._ask_hub(lambda: self.app.client.release_cancel())

    @Slot()
    def install(self) -> None:
        version = self._state.get("latest") or (self._state.get("job") or {}).get("version", "")

        def decide(yes: bool | None) -> None:
            if yes:
                self._ask_hub(lambda: self.app.client.release_install())

        words = tr("AFO закроется, поставит новую версию и откроется снова. Разговоры, проекты и модели останутся; "
                   "задачи агента, которые идут сейчас, прервутся.")  # fmt: skip
        self.app._ask(tr("Установить AFO {version}?", version=version), words, tr("Установить"), decide)

    @Slot(bool)
    def setAuto(self, on: bool) -> None:
        self._ask_hub(lambda: self.app.client.set_release_auto(on))

    def on_event(self, kind: str, payload: dict[str, Any]) -> None:
        job = payload.get("job")
        if kind == "release.changed" and job and job.get("status") == "downloading" and self._state:
            self._state = {**self._state, "job": job}  # progress: no call to the hub for it
            self.changed.emit()
        else:
            self.load()
