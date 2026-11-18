"""«Режиссёр» (stage 50): a film - an idea, a length, the product's photo - and its scenes kept in the database; the
cleanup of old pictures leaves a film's files alone while the film is there."""

import base64
import json

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
