"""«Режиссёр» (stage 50): a film - an idea, a length, the product's photo - and its scenes kept in the database; the
cleanup of old pictures leaves a film's files alone while the film is there."""

import base64
import json
import shutil
import time
from pathlib import Path

import pytest

from hub import director, retention
from hub.db import Database, migrate
from hub.gateway.openai_compat import OpenAICompatProvider
from tests.test_autonomous import use
from tests.test_judge import SeeingCloud, roles
from tests.test_retention import aged
from tests.test_visual import hub  # noqa: F401 - the Studio's stand

SCENES = [
    {"mode": "picture", "words": "Лампа на столе", "prompt": "a lamp on a desk, slow push in", "frames": 97},
    {"mode": "continue", "words": "Лампа загорается", "prompt": "the lamp lights up", "frames": 121, "seed": 7},
    {"mode": "frames", "words": "Возврат к лампе", "prompt": "the camera returns to the lamp", "frames": 73},
]


def test_a_film_and_its_scenes_are_kept_and_its_files_outlive_the_cleanup(tmp_path):
    """50.5: a film with its scenes in order; a scene's own change; a storyboard written anew replaces the scenes;
    the old pictures' cleanup (D30) leaves a film's photo, its scenes' clips with what lies beside them and the
    joined film - others of that age go, and once the film is deleted its files go too."""
    db = Database(tmp_path / "hub.db")
    db.call(migrate)
    made = db.call(lambda conn: director.new_film(conn, idea="Ролик о лампе", seconds=15, shape="tall",
                                                  director="cloud/eye"))  # fmt: skip
    assert (made["idea"], made["seconds"], made["shape"], made["status"], made["scenes"]) == (
        "Ролик о лампе", 15, "tall", "draft", [])  # fmt: skip
    film_id = made["id"]
    db.call(lambda conn: director.set_scenes(conn, film_id, SCENES))
    db.call(lambda conn: director.update_film(conn, film_id, status="ready", photo=f"2026-01/film-{film_id}-photo.png",
                                              file=f"2026-01/film-{film_id}.mp4"))  # fmt: skip
    db.call(lambda conn: director.update_scene(conn, film_id, 2, job_id="aaaaaaaaaaaa", status="done"))
    found = db.call(lambda conn: director.film(conn, film_id))
    assert [(s["n"], s["mode"], s["frames"], s["seed"], s["status"]) for s in found["scenes"]] == [
        (1, "picture", 97, None, "new"), (2, "continue", 121, 7, "done"), (3, "frames", 73, None, "new")]  # fmt: skip
    assert found["scenes"][1]["job_id"] == "aaaaaaaaaaaa" and found["status"] == "ready"
    assert [f["id"] for f in db.call(director.films)] == [film_id]

    month = tmp_path / "media" / "2026-01"
    mine = [aged(month / name, 120) for name in (
        f"film-{film_id}-photo.png", f"film-{film_id}.mp4", "aaaaaaaaaaaa.webp", "aaaaaaaaaaaa.mp4",
        "aaaaaaaaaaaa.end.png", "aaaaaaaaaaaa-source.png")]  # fmt: skip
    others = [aged(month / name, 120) for name in ("bbbbbbbbbbbb.webp", "bbbbbbbbbbbb.end.png", "film-gone.mp4")]
    assert retention.prune_all(db, tmp_path)["pictures"] == 3
    assert all(f.exists() for f in mine) and not any(f.exists() for f in others)

    db.call(lambda conn: director.set_scenes(conn, film_id, SCENES[:1]))  # written anew: one scene, its clip not kept
    assert [s["n"] for s in db.call(lambda conn: director.film(conn, film_id))["scenes"]] == [1]
    assert retention.prune_all(db, tmp_path)["pictures"] == 4  # the old scene's clip and what lay beside it
    db.call(lambda conn: director.delete_film(conn, film_id))
    assert db.call(lambda conn: director.film(conn, film_id)) is None
    assert db.call(lambda conn: conn.execute("SELECT count(*) FROM film_scenes").fetchone()[0]) == 0
    assert retention.prune_all(db, tmp_path)["pictures"] == 2 and not month.exists()
    db.close()


LTX = {"engine": "sd", "set": "film", "kind": "video", "tier": "video", "size": 768,
       "flags": ["--cfg-scale", "1", "--sampling-method", "euler", "--steps", "8"],
       "modes": ["text", "picture", "frames"],
       "clip": {"fps": 24, "step": 8, "grid": 32, "sizes": {"wide": [768, 512], "tall": [512, 768],
                                                            "square": [640, 640]}}}  # fmt: skip
CLIP = {"fps": 24, "step": 8}
BOARD = {"scenes": [
    {"mode": "picture", "seconds": 4, "words": "Лампа на столе", "prompt": "a brass lamp on a desk, slow push in"},
    {"mode": "continue", "seconds": 5, "words": "Лампа загорается", "prompt": "the lamp lights up, warm glow"},
    {"mode": "frames", "seconds": 3, "words": "Камера возвращается", "prompt": "the camera pulls back to the lamp"},
    {"mode": "continue", "seconds": 3, "words": "Финал", "prompt": "the lamp glows, static shot"}]}  # fmt: skip


def test_a_storyboard_is_read_made_to_fit_or_asked_again():
    """50.6: the director's answer made into scenes - the length in seconds into frames the family's VAE takes
    (LTX's 8k+1), a mode the film cannot film put right (no photo: by words; the first scene goes on from nothing),
    a scene too long cut to 5 s; what is no storyboard - no JSON, no scenes, a scene without a prompt, a length far
    from the one asked (the first time) - is asked again."""
    read = director.reader(clip=CLIP, modes=["text", "picture", "frames"], photo=True, seconds=15)
    found = read("Here it is:\n```json\n" + json.dumps(BOARD, ensure_ascii=False) + "\n```")
    assert [(s["mode"], s["frames"]) for s in found["scenes"]] == [
        ("picture", 97), ("continue", 121), ("frames", 73), ("continue", 73)]  # fmt: skip
    assert found["scenes"][0]["words"] == "Лампа на столе" and found["notes"] == []

    bare = director.reader(clip=CLIP, modes=["text"], photo=False, seconds=8)
    odd = {"scenes": [{"mode": "continue", "seconds": 9, "prompt": "a lamp"},
                      {"mode": "frames", "seconds": 2.6, "prompt": "it glows"}]}  # fmt: skip
    fit = bare(json.dumps(odd))
    assert [(s["mode"], s["frames"], s["words"]) for s in fit["scenes"]] == [
        ("text", 121, "a lamp"), ("text", 65, "it glows")]  # fmt: skip

    def strict():
        return director.reader(clip=CLIP, modes=["text", "picture", "frames"], photo=True, seconds=30)

    for wrong in ("I would film a lamp.", "{}", '{"scenes": []}', '{"scenes": [{"mode": "picture", "seconds": 4}]}'):
        assert strict()(wrong) is None, wrong
    strict = strict()
    assert strict(json.dumps(BOARD)) is None  # 15 s where 30 were asked: once more
    late = strict(json.dumps(BOARD))  # the second answer is taken as it is, and said
    assert len(late["scenes"]) == 4 and "15" in late["notes"][0] and "30" in late["notes"][0]


def test_the_director_writes_a_films_storyboard_from_the_idea_and_the_photo(hub):  # noqa: F811
    """50.6: «Собрать сценарий» - the model of the role «Режиссёр» gets the idea, the length and the product's photo
    and answers with the scenes; they are kept with the film, the question's cost with them; an answer that is no
    storyboard is asked once more; with no director chosen, or no video model, it says so in words."""
    from tests.test_visual import PICTURE, _install

    cloud = SeeingCloud(["Let me think about it.", json.dumps(BOARD, ensure_ascii=False)])
    try:
        use(hub, OpenAICompatProvider("studio-cloud", cloud.url, locality="cloud", api_key="k"))
        visual, db = hub.app.state.visual, hub.app.state.db
        (visual.media / "2026-11").mkdir(parents=True, exist_ok=True)
        made = db.call(lambda conn: director.new_film(conn, idea="Ролик о латунной лампе", seconds=15))
        film_id = made["id"]
        photo = f"2026-11/film-{film_id}-photo.png"
        (visual.media / photo).write_bytes(PICTURE)
        db.call(lambda conn: director.update_film(conn, film_id, photo=photo))
        write = hub.app.state.director.write

        with pytest.raises(director.DirectorError, match="модели видео нет"):
            hub.portal.call(write, film_id)
        _install(hub, "film", LTX)
        with pytest.raises(director.DirectorError, match="Режиссёр"):
            hub.portal.call(write, film_id)
        roles(hub, director="studio-cloud/eye")

        found = hub.portal.call(write, film_id)
        assert found["status"] == "ready" and found["error"] == "" and found["director"] == "studio-cloud/eye"
        assert [(s["n"], s["mode"], s["frames"], s["status"]) for s in found["scenes"]] == [
            (1, "picture", 97, "new"), (2, "continue", 121, "new"), (3, "frames", 73, "new"),
            (4, "continue", 73, "new")]  # fmt: skip
        assert found["tokens"] == 1840 and len(cloud.asked) == 2  # asked once more after the first answer
        system, user = cloud.asked[0]["messages"][0]["content"], cloud.asked[0]["messages"][1]["content"]
        assert "director" in system and '"continue"' in system
        assert "Ролик о латунной лампе" in user[0]["text"] and "15 seconds" in user[0]["text"]
        assert user[1]["image_url"]["url"] == "data:image/png;base64," + base64.b64encode(PICTURE).decode()
        metas = db.call(lambda c: [json.loads(r[0]) for r in c.execute(
            "SELECT meta FROM usage WHERE model_id = 'studio-cloud/eye'")])  # fmt: skip
        assert metas == [{"purpose": "director", "film_id": film_id}] * 2

        with pytest.raises(director.DirectorError, match="сценарий не получился"):  # the cloud has no answer left
            hub.portal.call(lambda: write(film_id, timeout_s=1))
        kept = db.call(lambda conn: director.film(conn, film_id))
        assert kept["status"] == "ready" and len(kept["scenes"]) == 4 and "сценарий не получился" in kept["error"]
    finally:
        cloud.close()


def _film_of(hub, scenes, **fields):  # noqa: F811
    """A film with the product's photo and these scenes, ready to be filmed."""
    from tests.test_visual import PICTURE

    visual, db = hub.app.state.visual, hub.app.state.db
    (visual.media / "2026-11").mkdir(parents=True, exist_ok=True)
    film_id = db.call(lambda conn: director.new_film(conn, idea="Ролик о лампе", seconds=15, **fields))["id"]
    photo = f"2026-11/film-{film_id}-photo.png"
    (visual.media / photo).write_bytes(PICTURE)

    def keep(conn):
        director.set_scenes(conn, film_id, scenes)
        director.update_film(conn, film_id, photo=photo, status="ready")

    db.call(keep)
    return film_id


def _until(hub, film_id, done, timeout=30.0):  # noqa: F811
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        found = hub.app.state.db.call(lambda conn: director.film(conn, film_id))
        if done(found):
            return found
        time.sleep(0.05)
    raise AssertionError(f"the film did not get there: {found['status']}, {[s['status'] for s in found['scenes']]}")


def _colour(b64: str) -> tuple[int, int, int]:
    from PySide6.QtGui import QImage

    pixel = QImage.fromData(base64.b64decode(b64), "PNG").pixelColor(32, 32)
    return pixel.red(), pixel.green(), pixel.blue()


@pytest.mark.skipif(shutil.which("ffmpeg") is None, reason="no ffmpeg")
def test_a_film_is_filmed_scene_after_scene_and_the_card_is_kept_between(hub, monkeypatch):  # noqa: F811
    """50.7: «Снять всё» - the scenes go one after another: «по фото» begins with the product's photo, «продолжение»
    with the last frame of the scene before (a real ffmpeg takes it), «к фото» goes from that frame to the photo;
    every scene has the film's shape, whatever the photo's. The card stays the Studio's between the scenes - a
    local chat's model would be loaded there and the video model after it, anew for each scene - and a waiting chat
    is told a film is being shot. A scene filmed anew leaves those that go on from it «stale», the joined film
    forgotten."""
    from hub import ffmpeg
    from tests.test_ffmpeg import GREEN, ORANGE, _near, _solid, animated_webp
    from tests.test_visual import PICTURE, FakeDrawSwap, _install

    _install(hub, "film", LTX)
    FakeDrawSwap.filmed = animated_webp([_solid(ORANGE), _solid((200, 120, 40)), _solid(GREEN)], 64, 64)
    program = Path(shutil.which("ffmpeg"))
    monkeypatch.setattr(ffmpeg, "find", lambda _tools: program)
    film_id = _film_of(hub, [{**s, "frames": f} for s, f in zip(BOARD["scenes"], (97, 121, 73, 73), strict=True)])
    state = hub.app.state
    seen, video = [], state.visual.video

    async def watched(spec, client):
        seen.append((state.card.holder, state.card.free.is_set(), state.card.words()))
        return await video(spec, client)

    monkeypatch.setattr(state.visual, "video", watched)
    began = hub.portal.call(state.director.shoot, film_id)
    assert began["status"] == "filming"
    with pytest.raises(director.DirectorError, match="фильм занят"):
        hub.portal.call(state.director.shoot, film_id)
    found = _until(hub, film_id, lambda f: f["status"] == "ready")
    assert [(s["n"], s["status"], s["error"]) for s in found["scenes"]] == [(n, "done", "") for n in (1, 2, 3, 4)]
    assert found["error"] == "" and all(s["job_id"] for s in found["scenes"])

    one, two, three, four = FakeDrawSwap.video
    assert base64.b64decode(one["init_image"]) == PICTURE and "end_image" not in one
    assert (one["width"], one["height"], one["video_frames"]) == (768, 512, 97)  # the film's shape, not the photo's
    assert _near(_colour(two["init_image"]), GREEN) and two["video_frames"] == 121 and "end_image" not in two
    assert _near(_colour(three["init_image"]), GREEN) and base64.b64decode(three["end_image"]) == PICTURE
    assert _near(_colour(four["init_image"]), GREEN) and (four["width"], four["height"]) == (768, 512)
    assert [p["prompt"] for p in FakeDrawSwap.video] == [s["prompt"] for s in BOARD["scenes"]]
    # between the scenes the card is the Studio's, held from local chats, and a chat is told why
    assert [(holder, free) for holder, free, _words in seen[1:]] == [("studio", False)] * 3
    assert all("фильм" in words for _h, _f, words in seen[1:])
    assert state.card.holder == "" and state.card.free.is_set() and state.card.words() == ""  # given when it ended

    state.db.call(lambda conn: director.update_film(conn, film_id, file="2026-11/film-x.mp4"))
    hub.portal.call(lambda: state.director.shoot(film_id, only=1))
    again = _until(hub, film_id, lambda f: f["status"] == "ready")
    assert [s["status"] for s in again["scenes"]] == ["done", "stale", "stale", "stale"] and again["file"] == ""
    assert again["scenes"][0]["job_id"] != found["scenes"][0]["job_id"] and len(FakeDrawSwap.video) == 5


def test_a_film_stops_at_once_and_a_scene_that_fails_stops_it_with_words(hub, monkeypatch):  # noqa: F811
    """50.7: «Стоп» while a scene is filmed - its job is stopped as the Studio's ✕ stops one (the model unloaded),
    the scene is «not filmed» again, the card is given; «Продолжить» films what is left. A scene that fails says why
    and the film stops there: the next one would go on from nothing. A hub that restarts mid-film leaves it ready to
    go on from the scenes not filmed."""
    from tests.test_visual import FakeDrawSwap, _install

    _install(hub, "film", LTX)
    state = hub.app.state
    plain = [{"mode": "picture", "words": "Лампа", "prompt": "a lamp", "frames": 25},
             {"mode": "picture", "words": "Свет", "prompt": "it lights", "frames": 25}]  # fmt: skip
    film_id = _film_of(hub, plain)
    FakeDrawSwap.video_polls = 10_000  # the scene does not end by itself
    hub.portal.call(state.director.shoot, film_id)
    _until(hub, film_id, lambda f: f["scenes"][0]["status"] == "filming" and FakeDrawSwap.video)
    stopped = hub.portal.call(state.director.stop, film_id)
    assert stopped["status"] == "ready" and [s["status"] for s in stopped["scenes"]] == ["new", "new"]
    assert FakeDrawSwap.unloaded == ["film"] and state.card.holder == "" and stopped["error"] == ""

    FakeDrawSwap.video_polls = 3
    hub.portal.call(state.director.shoot, film_id)  # «Продолжить»: what is not filmed
    done = _until(hub, film_id, lambda f: f["status"] == "ready")
    assert [s["status"] for s in done["scenes"]] == ["done", "done"] and len(FakeDrawSwap.video) == 3

    FakeDrawSwap.fails = {"code": "generation_failed", "message": "CUDA error: out of memory"}
    hub.portal.call(lambda: state.director.shoot(film_id, only=1))
    failed = _until(hub, film_id, lambda f: f["status"] == "ready")
    assert failed["scenes"][0]["status"] == "failed" and failed["scenes"][0]["error"]
    assert "сцена 1" in failed["error"] and state.card.holder == ""

    state.db.call(lambda conn: (director.update_film(conn, film_id, status="filming"),
                                director.update_scene(conn, film_id, 2, status="filming")))  # fmt: skip
    hub.portal.call(state.director.start)  # the hub comes up again
    after = state.db.call(lambda conn: director.film(conn, film_id))
    assert after["status"] == "ready" and after["scenes"][1]["status"] == "new" and "перезапуск" in after["error"]


@pytest.mark.skipif(shutil.which("ffmpeg") is None, reason="no ffmpeg")
def test_the_window_makes_a_film_through_the_hub_from_the_photo_to_the_joined_file(hub, monkeypatch, tmp_path):  # noqa: F811
    """50.8: the window's client against the real hub - a film begun with the idea and the product's photo, its
    storyboard written in the background (the answer comes at once, the scenes with `film.changed`), a scene changed
    by hand, «Снять всё», «Собрать ролик» - one MP4 of every scene's frames (a real ffmpeg), given as a file. A scene
    changed after it was filmed is stale, those that go on from it too, the joined file forgotten; «Стоп» lets a
    hung director go; a film deleted takes its photo and its joined file with it. What cannot be done is said."""
    from app.hub_client import HubClient, HubError
    from hub import ffmpeg
    from tests.test_ffmpeg import GREEN, ORANGE, _frames_of, _solid, animated_webp
    from tests.test_visual import PICTURE, FakeDrawSwap, _install

    _install(hub, "film", LTX)
    FakeDrawSwap.filmed = animated_webp([_solid(ORANGE), _solid((200, 120, 40)), _solid(GREEN)], 64, 64)
    program = Path(shutil.which("ffmpeg"))
    monkeypatch.setattr(ffmpeg, "find", lambda _tools: program)
    cloud = SeeingCloud([json.dumps(BOARD, ensure_ascii=False)])
    try:
        use(hub, OpenAICompatProvider("studio-cloud", cloud.url, locality="cloud", api_key="k"))
        roles(hub, director="studio-cloud/eye")
        client = HubClient("http://127.0.0.1:8765", hub.headers["Authorization"].removeprefix("Bearer "), http=hub)
        media = hub.app.state.visual.media
        photo = base64.b64encode(PICTURE).decode()

        with pytest.raises(HubError, match="идея"):
            client.new_film({"idea": "   ", "seconds": 15})
        with pytest.raises(HubError, match="PNG"):
            client.new_film({"idea": "Ролик", "seconds": 15, "photo_png": base64.b64encode(b"not a picture").decode()})
        assert hub.post("/v1/films", json={"idea": "Ролик", "seconds": 900}).status_code == 422
        assert hub.get("/v1/films/nope").status_code == 404 and client.films()["films"] == []

        made = client.new_film({"idea": "Ролик о латунной лампе", "seconds": 15, "photo_png": photo})["film"]
        film_id = made["id"]
        assert (made["status"], made["shape"], made["scenes"]) == ("draft", "wide", [])
        assert made["photo"].endswith(f"film-{film_id}-photo.png") and client.film_photo(film_id) == PICTURE
        assert [f["id"] for f in client.films()["films"]] == [film_id]
        changed = client.update_film(film_id, {"idea": "Ролик о лампе", "fade": 0.25})["film"]
        assert (changed["idea"], changed["fade"], changed["seconds"]) == ("Ролик о лампе", 0.25, 15)
        with pytest.raises(HubError, match="сценарий"):
            client.shoot_film(film_id)

        assert client.write_film(film_id)["film"]["status"] == "writing"  # at once: the director answers later
        written = _until(hub, film_id, lambda f: f["status"] == "ready")
        assert [(s["mode"], s["frames"]) for s in written["scenes"]] == [
            ("picture", 97), ("continue", 121), ("frames", 73), ("continue", 73)]  # fmt: skip
        assert "Ролик о лампе" in cloud.asked[0]["messages"][1]["content"][0]["text"]
        states = [p["film"]["status"] for kind, p in hub.seen if kind == "film.changed" and p["film"]["id"] == film_id]
        assert "writing" in states and states[-1] == "ready"

        edited = client.update_scene(film_id, 4, {"words": "Финал: лампа светит", "seconds": 2, "seed": 11,
                                                  "prompt": "the lamp glows  brighter, static shot"})  # fmt: skip
        edited = edited["film"]
        assert [edited["scenes"][3][k] for k in ("words", "prompt", "frames", "seed", "status")] == [
            "Финал: лампа светит", "the lamp glows brighter, static shot", 49, 11, "new"]  # fmt: skip
        with pytest.raises(HubError, match="первой сцене"):
            client.update_scene(film_id, 1, {"mode": "continue"})
        with pytest.raises(HubError, match="нет такой сцены"):
            client.update_scene(film_id, 9, {"words": "Нет"})
        with pytest.raises(HubError, match="описание"):
            client.update_scene(film_id, 2, {"prompt": "  "})
        with pytest.raises(HubError, match="снимите все сцены"):
            client.join_film(film_id)

        assert client.shoot_film(film_id)["film"]["status"] == "filming"
        with pytest.raises(HubError, match="фильм занят"):
            client.update_film(film_id, {"idea": "Другая"})
        filmed = _until(hub, film_id, lambda f: f["status"] == "ready")
        assert [s["status"] for s in filmed["scenes"]] == ["done"] * 4 and filmed["error"] == ""
        assert FakeDrawSwap.video[3]["video_frames"] == 49 and FakeDrawSwap.video[3]["seed"] == 11
        began = [p["scene"] for kind, p in hub.seen if kind == "film.progress" and p["id"] == film_id]
        assert began == [1, 2, 3, 4]

        assert hub.post(f"/v1/films/{film_id}/join", json={"fade": 9}).status_code == 422
        assert client.join_film(film_id, 0)["film"]["status"] == "joining"
        joined = _until(hub, film_id, lambda f: f["status"] == "ready")
        assert joined["error"] == "" and Path(joined["file"]).name.startswith(f"film-{film_id}-")
        assert joined["fade"] == 0 and (media / joined["file"]).is_file()
        (tmp_path / "film.mp4").write_bytes(client.film_file(film_id))
        assert len(_frames_of(program, tmp_path / "film.mp4", tmp_path / "frames")) == 12  # four scenes, three frames

        first = (media / joined["file"]).name
        client.join_film(film_id, 0)  # joined anew: another file, the one before gone
        anew = _until(hub, film_id, lambda f: f["status"] == "ready")
        assert Path(anew["file"]).name != first and [f.name for f in media.glob(f"*/film-{film_id}-*.mp4")] == [
            Path(anew["file"]).name]  # fmt: skip

        stale = client.update_scene(film_id, 2, {"prompt": "the lamp flickers, then lights up"})["film"]
        assert [s["status"] for s in stale["scenes"]] == ["done", "stale", "stale", "stale"] and stale["file"] == ""
        with pytest.raises(HubError):
            client.film_file(film_id)
        with pytest.raises(HubError, match="снимите все сцены"):
            client.join_film(film_id)
        worded = client.update_scene(film_id, 1, {"words": "Лампа на столе, утро"})["film"]  # the words alone
        assert worded["scenes"][0]["status"] == "done"

        assert client.write_film(film_id)["film"]["status"] == "writing"  # the cloud has no answer left: it hangs
        let_go = client.stop_film(film_id)["film"]
        assert (let_go["status"], let_go["error"], len(let_go["scenes"])) == ("ready", "", 4)

        client.delete_film(film_id)
        assert client.films()["films"] == [] and hub.get(f"/v1/films/{film_id}").status_code == 404
        assert list(media.glob(f"*/film-{film_id}-*")) == []
        assert ("film.deleted", {"id": film_id}) in [(kind, p) for kind, p in hub.seen]
    finally:
        cloud.close()


def test_the_look_at_a_scene_is_asked_and_its_answer_read():
    """51.2: what the look at a filmed scene asks - the scene (what happens, how it begins, its prompt), the faults a
    take is rejected for, the product's photo when there is one, the guidance only for a model that takes one - and
    how its answer is read: «stays», or why not with the prompt written anew; a guidance out of the room is brought
    into it; what is no verdict is asked again. The storyboard's own rule: a prompt begins with the motion. The
    frames of the engine's WebP are counted as ffmpeg numbers them."""
    from hub import visual
    from tests.test_ffmpeg import GREEN, ORANGE, _solid, animated_webp
    from tests.test_visual import PICTURE, STILL

    scene = {"mode": "continue", "words": "Лампа загорается", "prompt": "the lamp lights up, warm glow", "cfg": None}
    system, prompt, form = director.look_question(scene, photo=True, cfg=None)
    assert all(f'"{why}"' in system for why in director.REASONS) and "product's photo" in system
    assert "3 frames" in system and "cfg" not in system and "cfg" not in form and form in system
    assert "Лампа загорается" in prompt and "the lamp lights up, warm glow" in prompt
    assert "last frame of the scene before" in prompt and "product's photo" in prompt
    bare = director.look_question({**scene, "mode": "text"}, photo=False, cfg=None)
    assert "photo" not in bare[0] and "photo" not in bare[1] and "by the prompt alone" in bare[1]

    fast = {"cfg": 1.0}  # a distilled model: no guidance to ask for
    assert director.cfg_room(fast, scene) is None and director.cfg_room({}, scene) is None
    room = director.cfg_room({"cfg": 4.0}, {**scene, "cfg": 5.0})
    assert room == (2.0, 6.0, 5.0) and director.cfg_room({"cfg": 2.5}, scene) == (1.5, 4.5, 2.5)
    guided = director.look_question(scene, photo=True, cfg=room)
    assert '"cfg": <number>' in guided[2] and "2 to 6 (it was 5)" in guided[0]

    read = director.look_reader(None)
    assert read('Fine.\n{"ok": true}') == {"ok": True, "why": "", "reason": "", "prompt": "", "cfg": None}
    no = read(json.dumps({"ok": False, "why": "motion", "reason": "  Лампа  не загорается ", "cfg": 9,
                          "prompt": "The lamp's glow  swells from dim to bright"}, ensure_ascii=False))  # fmt: skip
    assert no == {"ok": False, "why": "motion", "reason": "Лампа не загорается", "cfg": None,
                  "prompt": "The lamp's glow swells from dim to bright"}  # fmt: skip
    worded = read('{"ok": false, "why": "blurry"}')  # no words of its own, a fault not in the list
    assert (worded["why"], worded["prompt"]) == ("scene", "") and "описании" in worded["reason"]
    assert "движения" in read('{"ok": false, "why": "motion"}')["reason"]
    for wrong in ("It looks good to me.", "{}", '{"ok": "yes"}', '{"verdict": true}', "{not json}"):
        assert read(wrong) is None, wrong
    within = director.look_reader(room)
    assert within('{"ok": false, "why": "product", "cfg": 9}')["cfg"] == 6.0
    assert within('{"ok": false, "why": "product", "cfg": 3.14159}')["cfg"] == 3.14
    assert within('{"ok": false, "why": "product", "cfg": "high"}')["cfg"] is None

    storyboard = director.question("Ролик о лампе", 15, "wide", ["text", "picture", "frames"], True)[0]
    assert "Begin with the motion" in storyboard and '"static"' in storyboard

    three = animated_webp([_solid(ORANGE), _solid((200, 120, 40)), _solid(GREEN)], 64, 64)
    assert visual.frames_in(three) == 3 and visual.moving(three)
    assert visual.frames_in(STILL) == 0 and visual.frames_in(PICTURE) == 0 and not visual.moving(STILL)


def test_a_films_look_at_its_scenes_is_a_switch_and_a_scene_keeps_what_the_look_said(hub):  # noqa: F811
    """51.1: a film's scenes are looked at once filmed - its own switch, on from the start, set through the hub; a
    scene keeps what the look said of it (the verdict, the reason, how many times it was filmed anew, the guidance
    asked for) - until the scene is changed by hand: then it is another scene. A hub that restarts mid-look leaves
    no scene «being looked at»."""
    from app.hub_client import HubClient
    from tests.test_visual import _install

    _install(hub, "film", LTX)
    client = HubClient("http://127.0.0.1:8765", hub.headers["Authorization"].removeprefix("Bearer "), http=hub)
    made = client.new_film({"idea": "Ролик о лампе", "seconds": 15})["film"]
    assert made["checks"] is True and made["checking"] == 0
    assert client.new_film({"idea": "Без проверки", "seconds": 15, "checks": False})["film"]["checks"] is False
    film_id, db = made["id"], hub.app.state.db
    assert client.update_film(film_id, {"checks": False})["film"]["checks"] is False
    assert client.update_film(film_id, {"idea": "Ролик о латунной лампе"})["film"]["checks"] is False  # not named
    assert client.update_film(film_id, {"checks": True})["film"]["checks"] is True

    db.call(lambda conn: director.set_scenes(conn, film_id, SCENES))
    db.call(lambda conn: director.update_film(conn, film_id, status="ready"))
    assert [(s["tries"], s["verdict"], s["reason"], s["cfg"]) for s in client.film(film_id)["film"]["scenes"]] == [
        (0, "", "", None)] * 3  # fmt: skip
    db.call(lambda conn: director.update_scene(conn, film_id, 1, tries=2, verdict="reject", cfg=4.5,
                                               reason="лампа другого цвета"))  # fmt: skip
    kept = client.update_scene(film_id, 1, {"words": "Лампа на столе, утро"})["film"]["scenes"][0]  # the words alone
    assert (kept["tries"], kept["verdict"], kept["reason"], kept["cfg"]) == (2, "reject", "лампа другого цвета", 4.5)
    anew = client.update_scene(film_id, 1, {"prompt": "a brass lamp on a desk, the camera orbits"})["film"]["scenes"][0]
    assert (anew["tries"], anew["verdict"], anew["reason"], anew["cfg"]) == (0, "", "", None)

    db.call(lambda conn: director.update_film(conn, film_id, status="filming", checking=2))
    hub.portal.call(hub.app.state.director.start)  # the hub comes up again
    after = client.film(film_id)["film"]
    assert (after["status"], after["checking"], after["checks"]) == ("ready", 0, True)
