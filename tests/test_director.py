"""«Режиссёр» (stage 50): a film - an idea, a length, the product's photo - and its scenes kept in the database; the
cleanup of old pictures leaves a film's files alone while the film is there."""

from hub import director, retention
from hub.db import Database, migrate
from tests.test_retention import aged

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
