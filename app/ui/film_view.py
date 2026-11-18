"""«Режиссёр» in the Studio (stage 50.9): a film of scenes. Left - the product's photo, the idea, the length, the shape,
«Собрать сценарий»; the centre - the scenes' cards (the clip's first frame, what happens, «Изменить», «Переснять»),
the player - a scene's clip or the joined film -, «Снять всё», «Собрать ролик», and how long is left. (51.4) «Проверять
сцены»: the director's model looks at each scene filmed - a card says what it said, and why a scene is filmed anew.

The hub does it all (hub/director.py): this asks and shows. A film's changes come with `film.changed`. A scene's clip
is a video of the Studio's: its step, its time and its MP4 for the player are the Studio view's
(app/ui/visual_view.py), whose changes are this view's too.
"""

from __future__ import annotations

import base64
import shutil
from pathlib import Path
from typing import TYPE_CHECKING, Any

from PySide6.QtCore import Property, QObject, QUrl, Signal, Slot

from common.i18n import N_, tr
from common.picture_sizes import clip_of, scene_modes

from ..hub_client import HubError
from .visual_view import seconds_text

if TYPE_CHECKING:
    from .controller import Controller

KINDS = [("manual", N_("Вручную")), ("director", N_("Режиссёр"))]
LENGTHS = [("15", N_("15 с")), ("30", N_("30 с")), ("60", N_("60 с"))]
SHAPE_NAMES = [("wide", N_("Широкая")), ("tall", N_("Высокая")), ("square", N_("Квадрат"))]
SCENE_MODES = {"picture": N_("по фото"), "continue": N_("продолжение"), "frames": N_("к фото"),
               "text": N_("по описанию")}  # fmt: skip
SCENE_LENGTHS = [("2", N_("2 с")), ("3", N_("3 с")), ("4", N_("4 с")), ("5", N_("5 с"))]
JOINTS = [("cut", N_("Встык")), ("fade", N_("Плавно"))]
FADE_S = 0.5  # «Плавно»: a cross-fade at each joint
BUSY = ("writing", "filming", "joining")


def length_text(seconds: float) -> str:
    """A scene's length: «4 с», «4,7 с»."""
    whole = round(seconds)
    return tr("{seconds} с", seconds=whole if abs(seconds - whole) < 0.05 else f"{seconds:.1f}".replace(".", ","))


def left_text(seconds: float) -> str:
    """How long is left, roughly: «меньше минуты», «3 мин», «1 ч 5 мин»."""
    minutes = round(seconds / 60)
    if minutes < 1:
        return tr("меньше минуты")
    if minutes < 60:
        return tr("{minutes} мин", minutes=minutes)
    return tr("{hours} ч {minutes} мин", hours=minutes // 60, minutes=minutes % 60)


class FilmView(QObject):
    changed = Signal()
    scenesChanged = Signal()  # the cards alone: a list given anew takes the row back to its start

    def __init__(self, app: Controller):
        super().__init__(app)
        self.app = app
        self.studio = app.visual_view
        self.studio.changed.connect(self.changed.emit)  # a scene's step, its MP4 made, the video models
        self.changed.connect(self._sync_cards)
        self._kind = "manual"
        self._films: list[dict[str, Any]] = []
        self._film: dict[str, Any] = {}  # the film open; {} - a new one, not begun in the hub
        self._idea = ""
        self._length = "15"
        self._shape = "wide"
        self._photo: dict[str, str] = {}  # a photo chosen, not sent yet: {png: base64, shown: url}
        self._no_photo = False  # the film's photo taken away, not sent yet
        self._joint = "cut"
        self._checks = True  # «Проверять сцены» of a film not begun yet; one begun has its own
        self._shown = 0  # the scene in the player; 0 - the joined film
        self._run: set[int] = set()  # the scenes of the shooting going on: «ещё ≈» counts them
        self._asking = False  # a request of this view's is on its way
        self._cards: list[dict[str, Any]] = []

    # ================= for QML =================

    @Property(bool, notify=changed)
    def available(self) -> bool:
        """A film is filmed by a video model: without one there is nothing to switch to."""
        return bool(self.studio.video_models())

    @Property(list, notify=changed)
    def kinds(self) -> list:
        return [{"id": k, "name": tr(n)} for k, n in KINDS]

    @Property(str, notify=changed)
    def kind(self) -> str:
        return self._kind if self.available else "manual"

    @Property(str, notify=changed)
    def idea(self) -> str:
        return self._idea

    @Property(list, notify=changed)
    def lengths(self) -> list:
        return [{"id": k, "name": tr(n)} for k, n in LENGTHS]

    @Property(str, notify=changed)
    def length(self) -> str:
        return self._length

    @Property(list, notify=changed)
    def shapes(self) -> list:
        return [{"id": k, "name": tr(n)} for k, n in SHAPE_NAMES]

    @Property(str, notify=changed)
    def shape(self) -> str:
        return self._shape

    def _photo_file(self) -> Path | None:
        """The film's photo as the hub keeps it (data/media, this computer), unless taken away here."""
        name = "" if self._no_photo else self._film.get("photo", "")
        path = self.app.paths.media / name if name else None
        return path if path is not None and path.is_file() else None

    @Property(bool, notify=changed)
    def hasPhoto(self) -> bool:
        return bool(self._photo) or self._photo_file() is not None

    @Property(str, notify=changed)
    def photoSource(self) -> str:
        if self._photo:
            return self._photo["shown"]
        path = self._photo_file()
        return QUrl.fromLocalFile(str(path)).toString() if path is not None else ""

    @Property(bool, notify=changed)
    def hasFilm(self) -> bool:
        return bool(self._film.get("id"))

    @Property(list, notify=changed)
    def filmChoices(self) -> list:
        """The films, the newest first, and a new one before them."""
        new = {"id": "", "name": tr("Новый ролик"), "note": "", "icon": "plus", "selected": not self.hasFilm}
        return [new, *({"id": f["id"], "name": " ".join(f["idea"].split())[:60],
                        "selected": f["id"] == self._film.get("id"),
                        "note": tr("{seconds} с · сцен: {scenes}", seconds=f["seconds"], scenes=len(f["scenes"]))}
                       for f in self._films)]  # fmt: skip

    @Property(str, notify=changed)
    def filmName(self) -> str:
        return " ".join(self._film["idea"].split())[:40] if self.hasFilm else tr("Новый ролик")

    def _scenes(self) -> list[dict[str, Any]]:
        return self._film.get("scenes") or []

    @Property(bool, notify=changed)
    def busy(self) -> bool:
        return self._asking or self._film.get("status") in BUSY

    @Property(str, notify=changed)
    def state(self) -> str:
        """draft, writing, ready, filming, joining; "" - a film not begun."""
        return self._film.get("status", "")

    @Property(bool, notify=changed)
    def canWrite(self) -> bool:
        return bool(self._idea.strip()) and not self.busy

    @Property(str, notify=changed)
    def writeText(self) -> str:
        return tr("Переписать сценарий") if self._scenes() else tr("Собрать сценарий")

    @Property(bool, notify=changed)
    def hasScenes(self) -> bool:
        return bool(self._scenes())

    @Property(bool, notify=changed)
    def canShoot(self) -> bool:
        return not self.busy and any(s["status"] != "done" for s in self._scenes())

    @Property(str, notify=changed)
    def shootText(self) -> str:
        """«Снять всё» - or, some scenes filmed already, «Продолжить»."""
        return tr("Продолжить") if any(s["status"] == "done" for s in self._scenes()) else tr("Снять всё")

    @Property(bool, notify=changed)
    def canJoin(self) -> bool:
        scenes = self._scenes()
        return not self.busy and bool(scenes) and all(s["status"] == "done" for s in scenes)

    def _joined(self) -> Path | None:
        name = self._film.get("file", "")
        path = self.app.paths.media / name if name else None
        return path if path is not None and path.is_file() else None

    @Property(bool, notify=changed)
    def joined(self) -> bool:
        return self._joined() is not None

    @Property(list, notify=changed)
    def joints(self) -> list:
        return [{"id": k, "name": tr(n)} for k, n in JOINTS]

    @Property(str, notify=changed)
    def joint(self) -> str:
        return self._joint

    @Property(bool, notify=changed)
    def checks(self) -> bool:
        """«Проверять сцены» (51.4): the director's model looks at each scene filmed; one not good is filmed anew."""
        return bool(self._film.get("checks", self._checks)) if self.hasFilm else self._checks

    # ---- the film's words ----

    def _model(self) -> dict[str, Any] | None:
        """The video model that films this film: the one it names, else the first installed."""
        models = self.studio.video_models()
        named = next((m for m in models if m["model_id"] == self._film.get("model_id")), None)
        return named or (models[0] if models else None)

    def _fps(self) -> int:
        model = self._model()
        return int(clip_of(model.get("clip") if model else None)["fps"])

    def _left(self) -> float | None:
        """Seconds the shooting going on still takes: the frames left to film by what a frame took here - in this
        film's scenes, else in the Studio's latest videos; None - nothing filmed here yet to tell by."""
        scenes = self._scenes()
        took = [(float(s["took"]), int(s["frames"])) for s in scenes if s.get("took") and s["status"] == "done"]
        took = took or self.studio.video_times()
        if not took:
            return None
        rate, left = sum(t for t, _f in took) / sum(f for _t, f in took), 0.0
        for s in scenes:
            if s["status"] == "done" or (self._run and s["n"] not in self._run):
                continue
            step, steps = self.studio.step_of(s.get("job_id") or "") if s["status"] == "filming" else (0, 0)
            left += s["frames"] * rate * (1 - step / steps if steps else 1.0)
        return left

    @Property(str, notify=changed)
    def statusText(self) -> str:
        film, scenes = self._film, self._scenes()
        status = film.get("status", "")
        if status == "writing":
            return tr("режиссёр пишет сценарий…")
        if status == "joining":
            return tr("склеиваю ролик…")
        if status == "filming":
            if film.get("checking"):
                return tr("режиссёр смотрит сцену {n} из {all}…", n=film["checking"], all=len(scenes))
            now = next((s for s in scenes if s["status"] == "filming"), None)
            if now is None:
                return tr("снимаю…")
            text = tr("снимаю сцену {n} из {all}", n=now["n"], all=len(scenes))
            if now.get("tries"):
                text = tr("переснимаю сцену {n} из {all}", n=now["n"], all=len(scenes))
            step, steps = self.studio.step_of(now.get("job_id") or "")
            clock = self.studio.clock_of(now.get("job_id") or "")
            if steps:
                text = tr("{text}: шаг {step} из {steps}", text=text, step=step, steps=steps)
            elif clock is not None:
                text = tr("{text}: {time}", text=text, time=f"{int(clock) // 60}:{int(clock) % 60:02d}")
            left = self._left()
            return tr("{text} · ещё ≈ {time}", text=text, time=left_text(left)) if left is not None else text
        if film.get("error"):
            return film["error"]
        if not scenes:
            return ""
        done = sum(s["status"] == "done" for s in scenes)
        if done < len(scenes):
            return tr("снято сцен: {done} из {all}", done=done, all=len(scenes))
        if self._joined() is None:
            bad = ", ".join(str(s["n"]) for s in scenes if s.get("verdict") == "reject")
            return tr("все сцены сняты; проверку не прошли: {scenes} — посмотрите их", scenes=bad) if bad else tr(
                "все сцены сняты — соберите ролик")  # fmt: skip
        seconds = sum(s["frames"] - 1 for s in scenes) / self._fps() - film.get("fade", 0) * (len(scenes) - 1)
        return tr("ролик готов: {length}", length=length_text(seconds))

    @Property(bool, notify=changed)
    def failed(self) -> bool:
        """The status line says what did not come out."""
        return self._film.get("status") not in BUSY and bool(self._film.get("error"))

    @Property(float, notify=changed)
    def percent(self) -> float:
        """How far the shooting is: the scenes filmed and the share of the one being filmed."""
        scenes = self._scenes()
        if self._film.get("status") != "filming" or not scenes:
            return 0.0
        done = float(sum(s["status"] == "done" for s in scenes))
        now = next((s for s in scenes if s["status"] == "filming"), None)
        step, steps = self.studio.step_of(now.get("job_id") or "") if now else (0, 0)
        return round(100 * (done + (step / steps if steps else 0.0)) / len(scenes), 1)

    # ---- the scenes ----

    def _clip(self, scene: dict[str, Any]) -> Path | None:
        """A scene's clip: the hub's WebP on this computer (data/media)."""
        path = self.app.paths.media / scene["clip"] if scene.get("clip") else None
        return path if path is not None and path.is_file() else None

    def _card(self, scene: dict[str, Any], fps: int) -> dict[str, Any]:
        status, clip = scene["status"], self._clip(scene)
        seconds = (scene["frames"] - 1) / fps
        verdict, reason, tries = scene.get("verdict", ""), scene.get("reason", ""), scene.get("tries", 0)
        rejected = status == "done" and verdict == "reject"
        if self._film.get("checking") == scene["n"]:
            words = tr("режиссёр смотрит…")
        elif rejected:  # the take that stays is not good: the look's retakes are used up
            words = tr("не прошла проверку: {reason}", reason=reason)
        elif status == "done":
            took = scene.get("took")
            words = tr("снята за {time}", time=seconds_text(took) if took < 60 else left_text(took)) if took else tr(
                "снята")  # fmt: skip
            if verdict == "ok":
                words = tr("{words} · проверена, пересъёмок: {tries}", words=words, tries=tries) if tries else tr(
                    "{words} · проверена", words=words)  # fmt: skip
        elif status == "filming" and tries and reason:
            words = tr("переснимается: {reason}", reason=reason)
        elif status == "failed":
            words = tr("не вышла: {error}", error=scene.get("error") or tr("видео не снято"))
        else:
            words = tr({"new": N_("не снята"), "filming": N_("снимается…"),
                        "stale": N_("устарела — переснимите")}[status])  # fmt: skip
        whole = round(seconds)
        return {"n": scene["n"], "mode": scene["mode"], "words": scene["words"], "prompt": scene["prompt"],
                "title": tr("{n} · {mode} · {length}", n=scene["n"], mode=tr(SCENE_MODES[scene["mode"]]),
                            length=length_text(seconds)),
                "length": str(whole) if abs(seconds - whole) < 0.05 else "", "status": status, "statusText": words,
                "failed": status == "failed" or rejected, "filmed": clip is not None,
                "thumb": QUrl.fromLocalFile(str(clip)).toString() if clip is not None else "",
                "shootText": tr("Переснять") if clip is not None else tr("Снять")}  # fmt: skip

    def _sync_cards(self) -> None:
        fps = self._fps()
        cards = [self._card(s, fps) for s in self._scenes()]
        if cards != self._cards:
            self._cards = cards
            self.scenesChanged.emit()

    @Property(list, notify=scenesChanged)
    def scenes(self) -> list:
        return self._cards

    @Slot(int, result=list)
    def sceneModes(self, n: int) -> list:
        """How scene `n` may begin: as the film's video model films and whether there is a photo; the first scene
        goes on from nothing."""
        model = self._model()
        can = scene_modes((model or {}).get("modes") or ["text"], self.hasPhoto)
        return [{"id": m, "name": tr(SCENE_MODES[m])} for m in can if not (n == 1 and m in ("continue", "frames"))]

    @Property(list, notify=changed)
    def sceneLengths(self) -> list:
        return [{"id": k, "name": tr(name)} for k, name in SCENE_LENGTHS]

    # ---- the player ----

    @Property(int, notify=changed)
    def shownScene(self) -> int:
        return self._shown

    def _shown_scene(self) -> dict[str, Any] | None:
        return next((s for s in self._scenes() if s["n"] == self._shown), None)

    @Property(str, notify=changed)
    def playSource(self) -> str:
        """What the player plays: the joined film - or the scene chosen: its MP4, which the hub makes as the scene
        is filmed, an older one's when first shown ("" until then; the WebP's own if none can be made)."""
        scene = self._shown_scene()
        if scene is None:
            path = self._joined()
            return QUrl.fromLocalFile(str(path)).toString() if path is not None else ""
        clip, job_id = self._clip(scene), scene.get("job_id") or ""
        if clip is None:
            return ""
        return self.studio.playable(job_id, clip.with_suffix(".mp4"), lambda: self.app.client.visual_mp4(job_id),
                                    QUrl.fromLocalFile(str(clip)).toString())  # fmt: skip

    @Property(str, notify=changed)
    def playFallback(self) -> str:
        """The scene's WebP: its first frame stands until the MP4 is there."""
        scene = self._shown_scene()
        clip = self._clip(scene) if scene is not None else None
        return QUrl.fromLocalFile(str(clip)).toString() if clip is not None else ""

    @Property(str, notify=changed)
    def playNote(self) -> str:
        scene = self._shown_scene()
        return self.studio.play_note(scene.get("job_id") or "") if scene is not None else ""

    # ================= loading =================

    @Slot()
    def load(self) -> None:
        """The Studio shown: the films as the hub has them; the first time - the newest one is opened."""
        if self.app.client is None:
            return

        def films(data: dict[str, Any]) -> None:
            first, self._films = not self._films and not self.hasFilm, data.get("films") or []
            now = next((f for f in self._films if f["id"] == self._film.get("id")), None)
            if now is not None:
                self._film = now
            elif self.hasFilm:  # deleted meanwhile
                self._open({})
            elif first and self._films and not self._idea.strip():
                self._open(self._films[0])
            self.changed.emit()

        self.app._call(lambda: self.app.client.films(), films, lambda _e: None)

    def _open(self, film: dict[str, Any]) -> None:
        """A film on the screen - or {}: a new one; its own values in the fields."""
        self._film, self._photo, self._no_photo, self._shown, self._run = film, {}, False, 0, set()
        self._idea = film.get("idea", "")
        self._length = str(film.get("seconds", 15))
        self._shape = film.get("shape", "wide")
        self._joint = "fade" if film.get("fade") else "cut"
        self._checks = bool(film.get("checks", True))
        if not film.get("file"):  # not joined yet: its latest scene filmed plays
            filmed = [s["n"] for s in self._scenes() if s["status"] == "done" and self._clip(s) is not None]
            self._shown = filmed[-1] if filmed else 0

    def _take(self, film: dict[str, Any]) -> None:
        """A film as the hub has it now: among the films, and - the one open - on the screen. What is typed in the
        fields stays: the hub's changes come while the person writes."""
        at = next((i for i, f in enumerate(self._films) if f["id"] == film["id"]), None)
        if at is None:
            self._films.insert(0, film)
        elif film.get("updated_at", "") < self._films[at].get("updated_at", ""):
            return  # an answer that came after the event of a later change
        else:
            self._films[at] = film
        if film["id"] != self._film.get("id"):
            return
        before = {s["n"]: s["status"] for s in self._scenes()}
        was, self._film = self._film, film
        for scene in film["scenes"]:  # a scene just filmed is shown; the film just joined - instead of any
            if scene["status"] == "done" and before.get(scene["n"], "done") != "done":
                self._shown = scene["n"]
        if (film["file"] and film["file"] != was.get("file")) or self._shown_scene() is None:
            self._shown = 0
        if film["status"] != "filming":
            self._run = set()

    def _request(self, call, failed: str, then=None) -> None:
        """A request about the film open: its answer - the film - is taken; what the hub refuses is said."""
        if self.app.client is None or self._asking:
            return
        self._asking = True
        self.changed.emit()

        def done(result: dict[str, Any]) -> None:
            self._asking = False
            if result.get("film"):
                self._take(result["film"])
            if then is not None:
                then(result)
            self.changed.emit()

        def error(exc: Exception) -> None:
            self._asking = False
            self.app.show_error(tr(failed, error=exc))
            self.changed.emit()

        self.app.call_or_fail(call, done, error)

    # ================= what the person does =================

    @Slot(str)
    def setKind(self, kind: str) -> None:
        self._kind = kind if kind in {k for k, _n in KINDS} else "manual"
        self.changed.emit()  # «Режиссёр» shown: its view asks for the films (`load`)

    @Slot(str)
    def setIdea(self, idea: str) -> None:
        self._idea = idea
        self.changed.emit()

    @Slot(str)
    def setLength(self, length: str) -> None:
        self._length = length if length in {k for k, _n in LENGTHS} else "15"
        self.changed.emit()

    @Slot(str)
    def setShape(self, shape: str) -> None:
        self._shape = shape if shape in {k for k, _n in SHAPE_NAMES} else "wide"
        self.changed.emit()

    @Slot(str)
    def setJoint(self, joint: str) -> None:
        self._joint = joint if joint in {k for k, _n in JOINTS} else "cut"
        self.changed.emit()

    @Slot(bool)
    def setChecks(self, on: bool) -> None:
        """«Проверять сцены»: a film begun is told at once - the next shooting goes by it; one not begun keeps it
        for «Собрать сценарий»."""
        if on == self.checks:
            return
        self._checks = on
        if self.hasFilm:
            film_id = self._film["id"]
            self._request(lambda: self.app.client.update_film(film_id, {"checks": on}),
                          N_("Проверку сцен не переключить: {error}"))  # fmt: skip
        else:
            self.changed.emit()

    @Slot(str)
    def choosePhoto(self, url: str) -> None:
        """«Фото товара…»: any picture Qt reads, sent as PNG with «Собрать сценарий»."""
        data = self.studio.png_of(url)
        if data is not None:
            shown = url if url.startswith("file:") else QUrl.fromLocalFile(url).toString()
            self._photo, self._no_photo = {"png": base64.b64encode(data).decode(), "shown": shown}, False
            self.changed.emit()

    @Slot()
    def clearPhoto(self) -> None:
        self._photo, self._no_photo = {}, True
        self.changed.emit()

    @Slot(str)
    def pick(self, film_id: str) -> None:
        """A film from the list, or "" - a new one."""
        if self.busy:
            return
        self._open(next((f for f in self._films if f["id"] == film_id), {}))
        self.changed.emit()

    @Slot()
    def write(self) -> None:
        """«Собрать сценарий»: the film begun (or its own changed) and the director asked. Scenes filmed already
        would be lost - asked first."""
        if not self.canWrite:
            return
        if any(s["status"] != "new" for s in self._scenes()):
            self.app._ask(tr("Переписать сценарий?"),
                          tr("Режиссёр напишет сцены заново — снятые сцены пропадут из ролика."), tr("Переписать"),
                          lambda yes: self._write() if yes else None)  # fmt: skip
        else:
            self._write()

    def _write(self) -> None:
        client, film = self.app.client, self._film
        fields: dict[str, Any] = {"idea": self._idea.strip(), "seconds": int(self._length), "shape": self._shape,
                                  "checks": self.checks}  # fmt: skip
        if self._photo:
            fields["photo_png"] = self._photo["png"]
        elif self._no_photo:
            fields["photo"] = ""

        def ask() -> dict[str, Any]:
            if not film.get("id"):
                made = client.new_film({k: v for k, v in fields.items() if k != "photo"})["film"]
            else:
                new = {k: v for k, v in fields.items() if k in ("photo_png", "photo") or v != film.get(k)}
                made = client.update_film(film["id"], new)["film"] if new else film
            try:  # the film is there whatever the director says: the next «Собрать сценарий» is about it
                return {"film": client.write_film(made["id"])["film"]}
            except HubError as exc:
                return {"film": made, "error": exc}

        def then(result: dict[str, Any]) -> None:
            if not self.hasFilm:  # begun just now: as the hub has it by now
                self._film = next((f for f in self._films if f["id"] == result["film"]["id"]), result["film"])
            self._photo, self._no_photo = {}, False
            if result.get("error"):
                self.app.show_error(tr("Сценарий не собрать: {error}", error=result["error"]))

        self._request(ask, N_("Сценарий не собрать: {error}"), then)

    @Slot()
    def shoot(self) -> None:
        """«Снять всё» and «Продолжить»: the scenes not filmed, in order."""
        if not self.canShoot:
            return
        film_id, self._run = self._film["id"], {s["n"] for s in self._scenes() if s["status"] != "done"}
        self._request(lambda: self.app.client.shoot_film(film_id), N_("Не снять: {error}"))

    @Slot(int)
    def shootScene(self, n: int) -> None:
        """«Переснять» (a scene not filmed: «Снять») - that scene alone."""
        if self.busy or not self.hasFilm:
            return
        film_id, self._run = self._film["id"], {n}
        self._request(lambda: self.app.client.shoot_film(film_id, n), N_("Не снять: {error}"))

    @Slot()
    def stop(self) -> None:
        """«Стоп»: the director's question let go, the scene being filmed stopped."""
        if self._film.get("status") in ("writing", "filming"):
            film_id = self._film["id"]
            self.app._call(lambda: self.app.client.stop_film(film_id), lambda r: (self._take(r["film"]),
                                                                                  self.changed.emit()),
                           lambda e: self.app.show_error(tr("Не остановить: {error}", error=e)))  # fmt: skip

    @Slot()
    def join(self) -> None:
        """«Собрать ролик»: the scenes into one MP4 - cut to cut, or «Плавно»."""
        if not self.canJoin:
            return
        film_id, fade = self._film["id"], FADE_S if self._joint == "fade" else 0.0
        self._request(lambda: self.app.client.join_film(film_id, fade), N_("Не собрать ролик: {error}"))

    @Slot(int, str, str, str, str)
    def saveScene(self, n: int, words: str, prompt: str, mode: str, length: str) -> None:
        """«Изменить» → «Сохранить»: what has changed of the scene - what happens, the description for the video
        model, how it begins, its length (`length`: whole seconds, "" - as it is)."""
        scene = next((s for s in self._scenes() if s["n"] == n), None)
        if scene is None or self.busy:
            return
        fields: dict[str, Any] = {}
        if " ".join(words.split()) != scene["words"]:
            fields["words"] = words
        if " ".join(prompt.split()) != scene["prompt"]:
            fields["prompt"] = prompt
        if mode and mode != scene["mode"]:
            fields["mode"] = mode
        if length and round((scene["frames"] - 1) / self._fps(), 2) != float(length):
            fields["seconds"] = float(length)
        if fields:
            film_id = self._film["id"]
            self._request(lambda: self.app.client.update_scene(film_id, n, fields), N_("Сцену не изменить: {error}"))

    @Slot(int)
    def show(self, n: int) -> None:
        """A scene's clip into the player; 0 - the joined film."""
        scene = next((s for s in self._scenes() if s["n"] == n), None)
        self._shown = n if scene is not None and self._clip(scene) is not None else 0
        self.changed.emit()

    @Slot(str)
    def saveAs(self, url: str) -> None:
        """«Сохранить как…»: the joined film, an MP4."""
        source, target = self._joined(), QUrl(url).toLocalFile() if url.startswith("file:") else url
        if source is None or not target:
            return
        if not target.lower().endswith(".mp4"):
            target += ".mp4"
        try:
            shutil.copyfile(source, target)
        except OSError as exc:
            self.app.show_error(tr("Не сохранить: {error}", error=exc.strerror or exc))
            return
        self.app.flash(tr("Сохранено: {path}", path=target))

    @Slot()
    def remove(self) -> None:
        """«Удалить ролик» - asked first."""
        if not self.hasFilm or self.busy:
            return
        film_id = self._film["id"]

        def gone(_result: dict[str, Any]) -> None:
            self._films = [f for f in self._films if f["id"] != film_id]
            if self._film.get("id") == film_id:
                self._open({})

        self.app._ask(tr("Удалить ролик?"),
                      tr("Сценарий и склеенный ролик удалятся. Клипы сцен останутся в «Последних» Студии."),
                      tr("Удалить"),
                      lambda yes: self._request(lambda: self.app.client.delete_film(film_id) or {},
                                            N_("Не удалить ролик: {error}"), gone) if yes else None)  # fmt: skip

    # ================= the hub's events =================

    def on_event(self, kind: str, payload: dict[str, Any]) -> None:
        if kind == "film.changed" and (payload.get("film") or {}).get("id"):
            self._take(payload["film"])
        elif kind == "film.deleted":
            self._films = [f for f in self._films if f["id"] != payload.get("id")]
            if self.hasFilm and self._film["id"] == payload.get("id"):
                self._open({})
        else:
            return
        self.changed.emit()
