"""«Режиссёр» in the Studio (stage 50.9): a film of scenes. Left - the product's photo, the idea, the length, the shape,
«Собрать сценарий»; the centre - the scenes' cards (the clip's first frame, what happens, «Изменить», «Переснять»),
the player - a scene's clip or the joined film -, «Снять всё», «Собрать ролик», and how long is left. (51.4) «Проверять
сцены»: the director's model looks at each scene filmed - a card says what it said, and why a scene is filmed anew.
(53.6) «Звук фильма…»: the sound added when the film is joined - the narrator's voice, the music (by its description
or the user's own file) - and the parts of the engine of sound, each fetched by a click; a scene's sheet has the
narrator's line.

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
from .enhance_view import improvement
from .install_view import gb, part_loading
from .visual_view import STATUS, improving, seconds_text, still_of

if TYPE_CHECKING:
    from .controller import Controller

KINDS = [("manual", N_("Вручную")), ("director", N_("Режиссёр"))]
LENGTHS = [("15", N_("15 с")), ("30", N_("30 с")), ("60", N_("60 с"))]
SHAPE_NAMES = [("wide", N_("Широкая")), ("tall", N_("Высокая")), ("square", N_("Квадрат"))]
SCENE_MODES = {"picture": N_("по фото"), "continue": N_("продолжение"), "frames": N_("к фото"),
               "text": N_("по описанию")}  # fmt: skip
SCENE_LENGTHS = [("2", N_("2 с")), ("3", N_("3 с")), ("4", N_("4 с")), ("5", N_("5 с"))]
JOINTS = [("cut", N_("Встык")), ("fade", N_("Плавно"))]
MUSICS = [("", N_("Нет")), ("made", N_("По описанию")), ("file", N_("Свой файл"))]  # (53.6) the film's music
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
    partsChanged = Signal()  # the parts of the engine of sound alone: their rows are not built anew at each change

    def __init__(self, app: Controller):
        super().__init__(app)
        self.app = app
        self.studio = app.visual_view
        self.studio.changed.connect(self.changed.emit)  # a scene's step, its MP4 made, the video models
        app.modelChanged.connect(self.changed.emit)  # the models that may write the script
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
        self._director = ""  # «Сценарий пишет» of a film not begun yet: a model, "" - the role's
        self._sound = True  # «Со звуком» (52.6), the same way
        # (53.6) the added sound of a film not begun yet: the narrator's voice, the music - its kind, its
        # description, the user's own file (a path: sent once the film is begun)
        self._voice, self._music, self._music_words, self._music_path = "", "", "", ""
        self._sounds: dict[str, Any] = {}  # the engine of sound and its parts, as the hub has them (/v1/sound)
        self._parts: list[dict[str, Any]] = []
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
        return bool(self._idea.strip()) and not self.busy and bool(self._writer())

    # ---- who writes the script («Стабилизация» 2026-11-27: «во вкладке "Режиссёр" … нет выбора роли») ----

    @Property(str, notify=changed)
    def director(self) -> str:
        """The model this film's script is written by; "" - the role «Режиссёр»'s."""
        return str(self._film.get("director") or "") if self.hasFilm else self._director

    def _writer(self) -> str:
        """Who writes it now: the film's model while it is offered, else the role's; "" - nobody."""
        chosen = self.director
        return chosen if chosen in self.app.models else self.app.role_model("director")

    @Property(str, notify=changed)
    def directorName(self) -> str:
        writer = self._writer()
        return (self.app.models.get(writer) or {}).get("name") or writer if writer else tr("модель не выбрана")

    @Property(list, notify=changed)
    def directorItems(self) -> list:
        """«Сценарий пишет»: the role's model first, then every model offered (Claude Code - an agent - not)."""
        role = self.app.role_model("director")
        named = (self.app.models.get(role) or {}).get("name", "")
        chosen = self.director if self.director in self.app.models else ""
        items = [{"id": "", "name": tr("Как в «Ролях»: {name}", name=named or tr("не выбрана")), "icon": "check",
                  "note": tr("модель роли «Режиссёр»") if named else tr("роль «Режиссёр» в «Моделях» не выбрана"),
                  "selected": not chosen}]  # fmt: skip
        for model_id, model in self.app.models.items():
            if model.get("provider") == "claude-code" or not model.get("enabled", True):
                continue
            sees = (model.get("capabilities") or {}).get("vision")
            items.append({"id": model_id, "name": model.get("name") or model_id,
                          "icon": "cloud" if model.get("locality") == "cloud" else "cpu",
                          "note": tr("видит картинки") if sees else "", "selected": chosen == model_id})  # fmt: skip
        return items

    @Property(str, notify=changed)
    def directorNote(self) -> str:
        if not self._writer():
            return tr("Выберите модель, которая напишет сценарий: ей уходят идея и фото товара. Снимает модель видео "
                      "— сцена за сценой.")  # fmt: skip
        cloud = (self.app.models.get(self._writer()) or {}).get("locality") == "cloud"
        return tr("Модели уходят идея и фото товара{where}. Снимает модель видео — сцена за сценой.",
                  where=tr(" — в облако") if cloud else "")  # fmt: skip

    @Slot(str)
    def setDirector(self, model_id: str) -> None:
        """«Сценарий пишет»: a model, or "" - the role's. A film begun is told at once: its next script, and the look
        at each scene filmed (51.4), are that model's."""
        if (model_id and model_id not in self.app.models) or model_id == self.director or self.busy:
            return
        self._director = model_id
        if self.hasFilm:
            film_id = self._film["id"]
            self._request(lambda: self.app.client.update_film(film_id, {"director": model_id}),
                          N_("Модель сценария не выбрать: {error}"))  # fmt: skip
        else:
            self.changed.emit()

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

    def _films_sound(self) -> bool:
        return bool((self._model() or {}).get("sound"))

    def _part_ready(self, part: str) -> bool:
        return any(p["id"] == part and p["state"] == "ready" for p in self._sounds.get("parts") or [])

    @Property(bool, notify=changed)
    def canSound(self) -> bool:
        """Whether the film's scenes can have sound: its video model films it (52.6) - or the engine of sound makes
        it for a scene filmed without («Звуки сцен» installed, 53.6)."""
        return self._films_sound() or self._part_ready("effects")

    @Property(bool, notify=changed)
    def sound(self) -> bool:
        """«Со звуком» (52.6): the scenes are filmed with sound, the director says what is heard in each."""
        return bool(self._film.get("sound", self._sound)) if self.hasFilm else self._sound

    @Property(str, notify=changed)
    def soundNote(self) -> str:
        """What «Со звуком» does - by who makes the scenes' sound."""
        if self._films_sound():
            return tr("Модель видео снимает сцены со звуком. Что слышно в сцене, пишет режиссёр; поправить — "
                      "«Изменить» у сцены. Выключатель действует на сцены, снятые после него.")  # fmt: skip
        return tr("Модель видео снимает без звука — звук каждой сцены делается по её «что слышно», когда ролик "
                  "собирается. Что слышно, пишет режиссёр; поправить — «Изменить» у сцены.")  # fmt: skip

    # ---- the added sound (53.6): the narrator, the music, the parts of the engine ----

    @Property(str, notify=changed)
    def voice(self) -> str:
        """The narrator's voice; "" - the film has no narrator."""
        return self._film.get("voice") or "" if self.hasFilm else self._voice

    @Property(list, notify=changed)
    def voiceChoices(self) -> list:
        now = self.voice
        none = {"id": "", "name": tr("Без диктора"), "note": "", "selected": not now}
        return [none, *({"id": v["id"], "name": v["name"], "note": "", "selected": v["id"] == now}
                        for v in self._sounds.get("voices") or [])]  # fmt: skip

    @Property(str, notify=changed)
    def voiceName(self) -> str:
        now = self.voice
        named = next((v["name"] for v in self._sounds.get("voices") or [] if v["id"] == now), now)
        return named if now else tr("Без диктора")

    @Property(list, notify=changed)
    def musics(self) -> list:
        return [{"id": k, "name": tr(n)} for k, n in MUSICS]

    @Property(str, notify=changed)
    def music(self) -> str:
        """The film's music: "" - none, "made" - by its description, "file" - the user's own."""
        return self._film.get("music") or "" if self.hasFilm else self._music

    @Property(str, notify=changed)
    def musicWords(self) -> str:
        return self._film.get("music_words") or "" if self.hasFilm else self._music_words

    @Property(bool, notify=changed)
    def hasMusicFile(self) -> bool:
        """The user's own music is chosen: the hub keeps its copy - or, a film not begun, the path is here."""
        return bool(self._film.get("music_file")) if self.hasFilm else bool(self._music_path)

    @Property(str, notify=changed)
    def musicFileName(self) -> str:
        if not self.hasMusicFile:
            return tr("файл не выбран")
        return tr("файл выбран") if self.hasFilm else Path(self._music_path).name

    @Property(str, notify=changed)
    def addedText(self) -> str:
        """The film's added sound in a line, beside «Звук фильма…»."""
        said = tr("диктор: {voice}", voice=self.voiceName) if self.voice else tr("без диктора")
        music = tr({"": N_("без музыки"), "made": N_("музыка по описанию"), "file": N_("своя музыка")}[self.music])
        return f"{said} · {music}"

    @Property(bool, notify=changed)
    def addsSound(self) -> bool:
        """«Собрать ролик» makes sound too - a voice, music, the sounds of scenes filmed without: it takes longer,
        and it can be stopped."""
        made = self.sound and self._part_ready("effects") and not self._films_sound()
        return bool(self.voice or self.music) or made

    def _parts_now(self) -> list[dict[str, Any]]:
        """The parts of the engine of sound: what each is for, whether it is here, how far its download is."""
        parts = []
        for p in self._sounds.get("parts") or []:
            size = gb(p["size"])
            if p["state"] == "ready":
                note = tr("установлено · {size}", size=size)
            elif p["state"] == "loading":
                note = part_loading(p)  # its speed and time left too (2026-11-27)
            else:
                note = p.get("error") or tr("не установлено · {size}", size=size)
            parts.append({"id": p["id"], "name": p["name"], "about": p["about"], "state": p["state"], "note": note,
                          "percent": round(100 * p["got"] / p["total"]) if p.get("total") else 0,
                          "failed": p["state"] == "missing" and bool(p.get("error")),
                          "getText": tr("Скачать {size}", size=size)})  # fmt: skip
        return parts

    @Property(list, notify=partsChanged)
    def soundParts(self) -> list:
        return self._parts

    @Property(bool, notify=changed)
    def soundEngine(self) -> bool:
        """The program of sound is here already; not yet - it comes with the first part."""
        return bool((self._sounds.get("engine") or {}).get("ready"))

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
            if self.voice and any(s.get("speaks") and s.get("say") for s in scenes):  # (57.4) MuseTalk: minutes
                return tr("делаю звук, губы под голос и склеиваю ролик…")
            return tr("делаю звук и склеиваю ролик…") if self.addsSound else tr("склеиваю ролик…")
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

    @staticmethod
    def _heard(scene: dict[str, Any]) -> tuple[str, str, bool]:
        """What sounds in a scene, for its card (53.15): (the line, the whole of it for a hint, whether it is not
        right). The hub's listener heard the sound the video model filmed (`own`) and the one the engine of sound
        made (`made`); the made one is in the film where there was no own sound, or in place of one that was not
        right (`replaced`). ('', '', False) - nothing was heard: no listener, a scene not filmed."""
        heard = scene.get("heard") or {}
        own, made = heard.get("own") or {}, heard.get("made") or {}
        if not own and not made:
            return "", "", False
        if own and not own.get("replaced"):  # the scene's own sound is the film's
            if not own.get("bad"):
                line = tr("Слышно: {heard}", heard=own["heard"])
                return line, line, False
            line = tr("Слышно: {heard} — не то: {bad}", heard=own["heard"], bad=own["bad"])
            if "replaced" not in own:
                hint = tr("{line}. При сборке ролика звук сцены будет сделан заново.", line=line)
            else:
                hint = tr("{line}. Сделанный заново звук вышел не лучше — оставлен звук модели видео.", line=line)
            return line, hint, True
        line = tr("Слышно: {heard}", heard=made["heard"])
        if made.get("bad"):
            line = tr("Слышно: {heard} — не то: {bad}", heard=made["heard"], bad=made["bad"])
        hint = line
        if own:
            line = tr("{line} · звук заменён", line=line)
            hint = tr("{line}. Модель видео сняла: {heard} — {bad}. Звук сцены сделан заново.", line=hint,
                      heard=own["heard"], bad=own["bad"])  # fmt: skip
        return line, hint, bool(made.get("bad"))

    def _card(self, scene: dict[str, Any], fps: int) -> dict[str, Any]:
        status, clip = scene["status"], self._clip(scene)
        heard, hint, amiss = self._heard(scene)
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
        title = tr("{n} · {mode} · {length}", n=scene["n"], mode=tr(SCENE_MODES[scene["mode"]]),
                   length=length_text(seconds))  # fmt: skip
        speaks = bool(scene.get("speaks")) and bool(self.voice)  # (57.4) the person in the frame says the line
        if speaks:
            title = tr("{title} · говорит в кадре", title=title)
        return {"n": scene["n"], "mode": scene["mode"], "words": scene["words"], "prompt": scene["prompt"],
                "title": title, "speaks": bool(scene.get("speaks")),
                "length": str(whole) if abs(seconds - whole) < 0.05 else "", "status": status, "statusText": words,
                "failed": status == "failed" or rejected, "filmed": clip is not None,
                "sound": scene.get("sound_words") or "", "say": scene.get("say") or "",
                "heard": heard, "heardHint": hint, "heardBad": amiss,
                "thumb": QUrl.fromLocalFile(str(still_of(clip))).toString() if clip is not None else "",
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

    @Property(bool, notify=scenesChanged)
    def anyHeard(self) -> bool:
        """Whether a scene's card says what sounds in it (53.15): the cards are a line taller then."""
        return any(card["heard"] for card in self._cards)

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
                                    QUrl.fromLocalFile(str(still_of(clip))).toString())  # fmt: skip

    @Property(str, notify=changed)
    def playFallback(self) -> str:
        """The scene's WebP - of a scene with sound, its poster (52.5): its first frame stands until the MP4 is
        there."""
        scene = self._shown_scene()
        clip = self._clip(scene) if scene is not None else None
        return QUrl.fromLocalFile(str(still_of(clip))).toString() if clip is not None else ""

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
        self.app._call(lambda: self.app.client.sound(), self._take_sound, lambda _e: None)

    def _take_sound(self, state: dict[str, Any]) -> None:
        """The engine of sound and its parts as the hub has them now (53.6)."""
        self._sounds = state or {}
        parts = self._parts_now()
        if parts != self._parts:
            self._parts = parts
            self.partsChanged.emit()
        self.changed.emit()

    def _open(self, film: dict[str, Any]) -> None:
        """A film on the screen - or {}: a new one; its own values in the fields."""
        self._film, self._photo, self._no_photo, self._shown, self._run = film, {}, False, 0, set()
        self._idea = film.get("idea", "")
        self._length = str(film.get("seconds", 15))
        self._shape = film.get("shape", "wide")
        self._joint = "fade" if film.get("fade") else "cut"
        self._checks = bool(film.get("checks", True))
        self._director = str(film.get("director") or "")
        self._sound = bool(film.get("sound", True))
        self._voice, self._music = film.get("voice") or "", film.get("music") or ""
        self._music_words, self._music_path = film.get("music_words") or "", ""
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

    @Slot(bool)
    def setSound(self, on: bool) -> None:
        """«Со звуком» (52.6): a film begun is told at once - the scenes filmed stay as they are, the next ones are
        filmed so; one not begun keeps it for «Собрать сценарий»."""
        if on == self.sound:
            return
        self._sound = on
        if self.hasFilm:
            film_id = self._film["id"]
            self._request(lambda: self.app.client.update_film(film_id, {"sound": on}),
                          N_("Звук не переключить: {error}"))  # fmt: skip
        else:
            self.changed.emit()

    def _added(self, fields: dict[str, Any], failed: str) -> None:
        """The film's added sound changed (53.6): a film begun is told at once - its joined file is another film
        now, the scenes stay; one not begun keeps it for «Собрать сценарий»."""
        if not self.hasFilm:
            self.changed.emit()
            return
        film_id = self._film["id"]
        self.app._call(lambda: self.app.client.update_film(film_id, fields),
                       lambda r: (self._take(r["film"]), self.changed.emit()),
                       lambda e: self.app.show_error(tr(failed, error=e)))  # fmt: skip

    @Slot(str)
    def setVoice(self, voice: str) -> None:
        """«Диктор»: a voice, or "" - no narrator."""
        if voice == self.voice or self.busy:
            return
        self._voice = voice
        self._added({"voice": voice}, N_("Диктора не выбрать: {error}"))

    @Slot(str)
    def setMusic(self, music: str) -> None:
        """«Музыка»: none, by its description, the user's own file - that one when a file is chosen
        (`chooseMusic`)."""
        if music not in {k for k, _n in MUSICS} or music == self.music or self.busy:
            return
        if music == "file" and not self.hasMusicFile:
            return
        self._music = music
        self._added({"music": music}, N_("Музыку не переключить: {error}"))

    @Slot(str)
    def setMusicWords(self, words: str) -> None:
        """What music the film needs, in words: the engine makes it by them."""
        words = " ".join(words.split())
        if words == self.musicWords or self.busy:
            return
        self._music_words = words
        self._added({"music_words": words}, N_("Описание музыки не сохранить: {error}"))

    @Slot(str)
    def chooseMusic(self, url: str) -> None:
        """«Свой файл»: a sound file of this computer; the hub keeps its own copy beside the film."""
        path = QUrl(url).toLocalFile() if url.startswith("file:") else url
        if not path or self.busy:
            return
        path = str(Path(path))  # as this computer writes it
        self._music, self._music_path = "file", path
        self._added({"music_path": path}, N_("Файл музыки не взять: {error}"))

    def _sound_part(self, call, failed: str) -> None:
        self.app._call(call, self._take_sound, lambda e: self.app.show_error(tr(failed, error=e)))

    @Slot(str)
    def getSound(self, part: str) -> None:
        """«Скачать»: the part's model - and, the first time, the program of sound."""
        self._get_sound(part, agreed=False)

    def _get_sound(self, part: str, *, agreed: bool) -> None:
        def failed(error: Exception) -> None:  # (62) a licence that forbids commercial use: asked first
            if not self.app.asked_licence(error, lambda: self._get_sound(part, agreed=True)):
                self.app.show_error(tr("Не скачать: {error}", error=error))

        self.app._call(lambda: self.app.client.install_sound(part, licence_ok=agreed), self._take_sound, failed)

    @Slot(str)
    def stopSound(self, part: str) -> None:
        self._sound_part(lambda: self.app.client.stop_sound(part), N_("Не остановить: {error}"))

    @Slot(str)
    def removeSound(self, part: str) -> None:
        """A part's model off the disk - asked first."""
        name = next((p["name"] for p in self._sounds.get("parts") or [] if p["id"] == part), part)
        self.app._ask(tr("Удалить «{name}»?", name=name),
                      tr("Файл модели удалится с диска. Понадобится снова — скачается заново."), tr("Удалить"),
                      lambda yes: self._sound_part(lambda: self.app.client.remove_sound(part),
                                                   N_("Не удалить: {error}")) if yes else None)  # fmt: skip

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
                                  "checks": self.checks, "sound": self.sound, "director": self.director}  # fmt: skip
        if self._photo:
            fields["photo_png"] = self._photo["png"]
        elif self._no_photo:
            fields["photo"] = ""
        # (53.6) a film not begun: its narrator and music go with it; its own music file - once it is begun
        added = {"voice": self._voice, "music": self._music if self._music != "file" else "",
                 "music_words": self._music_words}  # fmt: skip
        own = self._music_path if self._music == "file" else ""

        def ask() -> dict[str, Any]:
            if not film.get("id"):
                made = client.new_film({k: v for k, v in (fields | added).items() if k != "photo"})["film"]
                if own:
                    made = client.update_film(made["id"], {"music_path": own})["film"]
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
        """«Стоп»: the director's question let go, the scene being filmed stopped - or (53.6) the making of the
        film's added sound: the pieces made stay for the next «Собрать ролик»."""
        status = self._film.get("status")
        if status in ("writing", "filming") or (status == "joining" and self.addsSound):
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

    @Slot(int, str, str, str, str, str, str, bool)
    def saveScene(self, n: int, words: str, prompt: str, mode: str, length: str, sound: str, say: str,
                  speaks: bool = False) -> None:  # fmt: skip
        """«Изменить» → «Сохранить»: what has changed of the scene - what happens, the description for the video
        model, how it begins, its length (`length`: whole seconds, "" - as it is), what is heard in it (52.6),
        what the narrator says over it (53.6), whether the person in its frame says it (57.4)."""
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
        if " ".join(sound.split()) != (scene.get("sound_words") or ""):
            fields["sound_words"] = sound
        if " ".join(say.split()) != (scene.get("say") or ""):
            fields["say"] = say
        if self.voice and bool(speaks) != bool(scene.get("speaks")):
            fields["speaks"] = bool(speaks)
        if fields:
            film_id = self._film["id"]
            self._request(lambda: self.app.client.update_scene(film_id, n, fields), N_("Сцену не изменить: {error}"))

    @Slot(int)
    def show(self, n: int) -> None:
        """A scene's clip into the player; 0 - the joined film."""
        scene = next((s for s in self._scenes() if s["n"] == n), None)
        self._shown = n if scene is not None and self._clip(scene) is not None else 0
        self.changed.emit()

    # ---- (54.6) its joined video improved: the Studio's jobs of this film ----

    def open_film(self) -> dict[str, Any]:
        """The film open, joined (54.6: «Улучшить…» improves its video); {} - none."""
        return self._film if self._joined() is not None else {}

    def _improved(self) -> list[dict[str, Any]]:
        """The film's improved videos, the newest first: those the hub listed with the film, each as the Studio's
        queue has it now, and those sent since."""
        film_id = self._film.get("id")
        if not film_id:
            return []
        live = {j["id"]: j for j in self.studio.latest() if j.get("film_id") == film_id}
        listed = [live.pop(j["id"], j) for j in self._film.get("improved") or []]
        return [*live.values(), *listed]

    @Property(list, notify=changed)
    def improved(self) -> list:
        """Each improved video in a line: what was done, how far (✕ while it goes), done - opened in the Studio."""
        out = []
        for j in self._improved():
            status, step = j.get("status", ""), self.studio.step_of(j["id"])
            if status == "running":
                state = improving(step)
            elif status == "failed":
                state = tr("не вышло: {error}", error=j.get("error") or "")
            else:
                state = tr(STATUS[status]) if status in STATUS else status
            here = bool(j.get("file")) and (self.app.paths.media / j["file"]).is_file()
            out.append({"id": j["id"], "what": improvement(j.get("enhance")) or tr("Улучшенный"), "state": state,
                        "going": status in ("queued", "running"), "done": status == "done" and here,
                        "failed": status == "failed",
                        "percent": round(100.0 * step[0] / step[1], 1) if step[1] else 0.0})  # fmt: skip
        return out

    @Slot(str)
    def openImproved(self, job_id: str) -> None:
        """«Открыть в «Студии»»: the improved video on the Studio's canvas - played, saved, improved again."""
        self.setKind("manual")
        self.studio.open_job(job_id)

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
        elif kind == "sound.changed":  # (53.6) a part's download: how far, done, stopped
            self._take_sound(payload)
            return
        else:
            return
        self.changed.emit()
