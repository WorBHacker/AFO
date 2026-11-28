"""«Аудио» in the Studio («Стабилизация», portion 5; the user's complaint 15 of 2026-11-27, «Нет в Студии
"Аудио"»): a voice reading a text, music by its description, a sound by its description - each a file of its own
(hub/sound_pieces.py). Left: what to make, the voice or the length, «Скачать» for the part it needs; the centre:
the latest pieces, each played by a click, «Сохранить как…», ✕ and «Удалить».

The parts are those of a film's sound (53.6): «Скачать» goes the same way as in «Звук фильма…» (the licence asked
when it forbids commercial use). A piece plays from the hub's file in data/media, as the Studio's videos do; it is
let go before it is deleted - Windows keeps a file being played.
"""

from __future__ import annotations

import shutil
from pathlib import Path
from typing import TYPE_CHECKING, Any

from PySide6.QtCore import Property, QObject, QUrl, Signal, Slot

from common.i18n import N_, decimal, tr

if TYPE_CHECKING:
    from .controller import Controller

KINDS = [("voice", N_("Голос")), ("music", N_("Музыка")), ("effect", N_("Звуки"))]
PARTS = {"voice": "voice", "music": "music", "effect": "effects"}  # the hub's sound_pieces.PARTS
# a length: the same three choices for music and a sound - one switch -, each its own seconds
LENGTHS = {"music": [("short", N_("15 с"), 15.0), ("middle", N_("30 с"), 30.0), ("long", N_("1 мин"), 60.0)],
           "effect": [("short", N_("3 с"), 3.0), ("middle", N_("5 с"), 5.0), ("long", N_("10 с"), 10.0)]}  # fmt: skip
HINTS = {"voice": N_("Текст, который прочитать голосом"),
         "music": N_("Какая музыка? Например: спокойное пианино, лёгкий джаз. Лучше по-английски"),
         "effect": N_("Какой звук? Лучше по-английски: rain on a tin roof, birds in a forest")}  # fmt: skip
MAKE = {"voice": N_("Прочитать"), "music": N_("Сделать музыку"), "effect": N_("Сделать звук")}
STATUS = {"queued": N_("в очереди"), "running": N_("делаю…"), "failed": N_("не вышло"), "cancelled": N_("остановлено")}
RECENT = 50


def length_text(seconds: float | None) -> str:
    if not seconds:
        return ""
    whole = round(float(seconds))
    return f"{whole // 60}:{whole % 60:02d}"


class AudioView(QObject):
    changed = Signal()

    def __init__(self, app: Controller):
        super().__init__(app)
        self.app = app
        self._kind = "voice"
        self._words = {k: "" for k, _n in KINDS}
        self._voice = ""
        self._length = {"music": "middle", "effect": "middle"}
        self._pieces: list[dict[str, Any]] = []
        self._sounds: dict[str, Any] = {}  # the engine of sound and its parts (/v1/sound)
        self._chosen = ""  # the piece in the player

    # ================= for QML =================

    @Property(list, notify=changed)
    def kinds(self) -> list:
        return [{"id": k, "name": tr(n)} for k, n in KINDS]

    @Property(str, notify=changed)
    def kind(self) -> str:
        return self._kind

    @Property(str, notify=changed)
    def words(self) -> str:
        return self._words[self._kind]

    @Property(str, notify=changed)
    def hint(self) -> str:
        return tr(HINTS[self._kind])

    @Property(str, notify=changed)
    def makeText(self) -> str:
        return tr(MAKE[self._kind])

    def _voices(self) -> list[dict[str, Any]]:
        return self._sounds.get("voices") or []

    def _voice_id(self) -> str:
        known = [v["id"] for v in self._voices()]
        return self._voice if self._voice in known else (known[0] if known else "")

    @Property(list, notify=changed)
    def voiceChoices(self) -> list:
        now = self._voice_id()
        return [{"id": v["id"], "name": v["name"], "selected": v["id"] == now} for v in self._voices()]

    @Property(str, notify=changed)
    def voiceName(self) -> str:
        now = self._voice_id()
        return next((v["name"] for v in self._voices() if v["id"] == now), "")

    @Property(list, notify=changed)
    def lengths(self) -> list:
        return [{"id": k, "name": tr(n)} for k, n, _s in LENGTHS.get(self._kind, [])]

    @Property(str, notify=changed)
    def length(self) -> str:
        return self._length.get(self._kind, "")

    def _part(self) -> dict[str, Any]:
        return next((p for p in self._sounds.get("parts") or [] if p["id"] == PARTS[self._kind]), {})

    @Property("QVariantMap", notify=changed)
    def part(self) -> dict:
        """The part of the engine this kind needs: its name, ready, loading (how far) or missing (its size)."""
        found = self._part()
        if not found:
            return {}
        total, got = int(found.get("total") or found.get("size") or 0), int(found.get("got") or 0)
        return {"id": found["id"], "name": found["name"], "state": found["state"], "error": found.get("error") or "",
                "size": decimal(f"{int(found.get('size') or 0) / 2**30:.1f}"),
                "percent": round(100.0 * got / total) if total else 0}  # fmt: skip

    @Property(bool, notify=changed)
    def canMake(self) -> bool:
        return self._part().get("state") == "ready" and bool(self._words[self._kind].strip())

    @Property(list, notify=changed)
    def pieces(self) -> list:
        """The latest pieces, the newest first: what each is, its words, how long - or how it goes."""
        names = dict(KINDS)
        out = []
        for p in self._pieces:
            status = p["status"]
            note = length_text(p.get("seconds")) if status == "done" else tr(STATUS.get(status, status))
            if status == "failed" and p.get("error"):
                note = f"{note}: {p['error']}"
            voice = next((v["name"] for v in self._voices() if v["id"] == p.get("voice")), "")
            out.append({"id": p["id"], "kind": tr(names.get(p["kind"], p["kind"])), "words": p["words"],
                        "voice": voice, "note": note, "status": status, "done": status == "done",
                        "going": status in ("queued", "running"), "chosen": p["id"] == self._chosen})  # fmt: skip
        return out

    def _file(self, found: dict[str, Any] | None) -> Path | None:
        if not found or found.get("status") != "done" or not found.get("file"):
            return None
        path = self.app.paths.media / found["file"]
        return path if path.is_file() else None

    def _chosen_piece(self) -> dict[str, Any] | None:
        return next((p for p in self._pieces if p["id"] == self._chosen), None)

    @Property(str, notify=changed)
    def playing(self) -> str:
        """The chosen piece's file, for the player; "" - none."""
        path = self._file(self._chosen_piece())
        return QUrl.fromLocalFile(str(path)).toString() if path is not None else ""

    @Property(str, notify=changed)
    def playingWords(self) -> str:
        found = self._chosen_piece()
        return found["words"] if found and self.playing else ""

    # ================= what to make =================

    @Slot(str)
    def setKind(self, kind: str) -> None:
        self._kind = kind if kind in dict(KINDS) else "voice"
        self.changed.emit()

    @Slot(str)
    def setWords(self, words: str) -> None:
        if words != self._words[self._kind]:
            self._words[self._kind] = words
            self.changed.emit()

    @Slot(str)
    def setVoice(self, voice: str) -> None:
        self._voice = voice
        self.changed.emit()

    @Slot(str)
    def setLength(self, length: str) -> None:
        if length in {k for k, _n, _s in LENGTHS.get(self._kind, [])}:
            self._length[self._kind] = length
            self.changed.emit()

    @Slot()
    def make(self) -> None:
        """«Прочитать» / «Сделать музыку» / «Сделать звук»: into the hub's queue; the list shows it."""
        words = " ".join(self._words[self._kind].split())
        if not words or self.app.client is None:
            return
        spec: dict[str, Any] = {"kind": self._kind, "words": words}
        if self._kind == "voice":
            spec["voice"] = self._voice_id()
        else:
            spec["seconds"] = next(s for k, _n, s in LENGTHS[self._kind] if k == self._length[self._kind])

        def made(result: dict[str, Any]) -> None:
            self._keep(result.get("piece") or {})
            self.changed.emit()

        self.app._call(lambda: self.app.client.make_sound(spec), made,
                       lambda e: self.app.show_error(tr("Не сделать звук: {error}", error=e)))  # fmt: skip

    # ================= the parts =================

    @Slot()
    def getPart(self) -> None:
        """«Скачать»: the part this kind needs - as «Звук фильма…» fetches it."""
        if (found := self._part()) and found.get("state") == "missing":
            self.app.film_view.getSound(found["id"])

    @Slot()
    def stopPart(self) -> None:
        if (found := self._part()) and found.get("state") == "loading":
            self.app.film_view.stopSound(found["id"])

    # ================= the pieces =================

    def _keep(self, found: dict[str, Any]) -> None:
        if not found.get("id"):
            return
        at = next((i for i, p in enumerate(self._pieces) if p["id"] == found["id"]), None)
        if found.get("status") == "removed":
            if at is not None:
                del self._pieces[at]
            if self._chosen == found["id"]:
                self._chosen = ""
            return
        if at is None:
            self._pieces = [found, *self._pieces][:RECENT]
        else:
            self._pieces[at] = found
        if found.get("status") == "done" and not self._chosen:  # the first one done: in the player
            self._chosen = found["id"]

    @Slot(str)
    def pick(self, piece_id: str) -> None:
        """A piece into the player (a click on it): it plays."""
        if self._file(next((p for p in self._pieces if p["id"] == piece_id), None)) is not None:
            self._chosen = piece_id
            self.changed.emit()

    @Slot(str)
    def cancel(self, piece_id: str) -> None:
        """✕ on one waiting or going on."""
        if self.app.client is None:
            return

        def done(result: dict[str, Any]) -> None:
            self._keep(result.get("piece") or {})
            self.changed.emit()

        self.app._call(lambda: self.app.client.cancel_sound(piece_id), done,
                       lambda e: self.app.show_error(tr("Не остановить: {error}", error=e)))  # fmt: skip

    @Slot(str)
    def remove(self, piece_id: str) -> None:
        """«Удалить»: the piece and its file for good, asked first; the player lets it go before the hub deletes it."""
        found = next((p for p in self._pieces if p["id"] == piece_id), None)
        if found is None or self.app.client is None:
            return

        def decide(yes: bool | None) -> None:
            if not yes:
                return
            if self._chosen == piece_id:  # the player lets its file go
                self._chosen = ""
                self.changed.emit()

            def gone(_result: Any) -> None:
                self._keep({"id": piece_id, "status": "removed"})
                self.changed.emit()

            self.app._call(lambda: self.app.client.remove_sound_piece(piece_id), gone,
                           lambda e: self.app.show_error(tr("Не удалено: {error}", error=e)))  # fmt: skip

        self.app._ask(tr("Удалить звук?"), tr("Файл удалится с диска — вернуть его будет нельзя."), tr("Удалить"),
                      decide)  # fmt: skip

    @Slot(str)
    def saveAs(self, url: str) -> None:
        """«Сохранить как…»: the piece in the player, a WAV, where the person said."""
        source = self._file(self._chosen_piece())
        target = QUrl(url).toLocalFile() if url.startswith("file:") else url
        if source is None or not target:
            return
        if not target.lower().endswith(".wav"):
            target += ".wav"
        try:
            shutil.copyfile(source, target)
        except OSError as exc:
            self.app.show_error(tr("Не сохранить: {error}", error=exc.strerror or exc))
            return
        self.app.flash(tr("Сохранено: {path}", path=target))

    # ================= loading =================

    @Slot()
    def load(self) -> None:
        if self.app.client is None:
            return

        def pieces(data: dict[str, Any]) -> None:
            self._pieces = list(data.get("pieces") or [])[:RECENT]
            if not self._chosen_piece():
                self._chosen = next((p["id"] for p in self._pieces if p["status"] == "done"), "")
            self.changed.emit()

        self.app._call(lambda: self.app.client.sound(), self._take_sound, lambda _e: None)
        self.app._call(lambda: self.app.client.sound_pieces(RECENT), pieces, lambda _e: None)

    def _take_sound(self, state: dict[str, Any]) -> None:
        self._sounds = state or {}
        self.changed.emit()

    def on_event(self, kind: str, payload: dict[str, Any]) -> None:
        if kind == "sound.changed":  # a part's download: how far, done, stopped
            self._take_sound(payload)
        elif kind == "sound.piece":
            self._keep(payload.get("piece") or {})
            self.changed.emit()
