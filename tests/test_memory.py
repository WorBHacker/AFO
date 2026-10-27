"""The autonomous agent's memory (stage 9.3, D40, Q24): a run's notes, facts kept for good - found by their stems,
personal ones only with the user's approval and never for a cloud model, seen, changed and deleted by the user -
and skills: a finished task's plan kept and given to a similar goal."""

from hub import memory
from hub.auth import create_client
from hub.db import Database, migrate
from tests.test_autonomous import PLAN, Scripted, ended, results, start, step, use
from tests.test_changes import wait
from tests.test_tools import hub  # noqa: F401 - the hub fixture


def test_facts_found_by_their_stems_and_kept_from_the_cloud(tmp_path):
    db = Database(tmp_path / "hub.db")
    db.call(migrate)
    add = lambda text, **kw: db.call(lambda c: memory.add_fact(c, text, source="user", **{  # noqa: E731
        "personal": False, "cloud_ok": False, **kw}))  # fmt: skip
    coffee = add("Пользователь пьёт кофе без сахара", about="вкусы", cloud_ok=True)
    add("Мама живёт в Ташкенте", personal=True, cloud_ok=True)  # personal: never for the cloud, whatever is asked
    add("Отчёты кладутся в папку D:/Отчёты")
    assert add("Пользователь пьёт кофе без сахара")["id"] == coffee["id"]  # known already: not twice
    found = lambda q, cloud=False: [f["text"] for f in db.call(lambda c: memory.facts(c, q, cloud=cloud))]  # noqa: E731
    assert found("ташкентская мама") == ["Мама живёт в Ташкенте"]  # «ташкен*» finds «Ташкенте»
    assert found("отчётов") == ["Отчёты кладутся в папку D:/Отчёты"]
    assert found("ташкент", cloud=True) == [] and found("кофе", cloud=True) == ["Пользователь пьёт кофе без сахара"]
    assert found("") == ["Отчёты кладутся в папку D:/Отчёты", "Мама живёт в Ташкенте",
                         "Пользователь пьёт кофе без сахара"]  # newest first  # fmt: skip
    changed = db.call(lambda c: memory.change_fact(c, coffee["id"], text="Кофе — с молоком", personal=True))
    assert changed["personal"] and not changed["cloud_ok"] and found("молоко") == ["Кофе — с молоком"]
    assert found("сахара") == []  # the search follows the change
    assert db.call(lambda c: memory.delete_fact(c, coffee["id"])) and found("молоко") == []
    for text in ("пишите на ivan@mail.ru", "телефон +998 90 123-45-67", "ул. Навои 12", "карта 4111 1111 1111 1111"):
        assert memory.looks_personal(text), text
    assert not memory.looks_personal("сервер на порту 8000 отвечает за 12 мс")
    db.close()


def test_the_agent_notes_remembers_and_recalls(hub):  # noqa: F811
    agent = use(hub, Scripted("agent", [
        [("note", {"text": "В README 9 строк, из них 6 непустых"})],
        [("remember", {"fact": "Пользователь хочет отчёты в Markdown", "about": "отчёты"})],
        [("remember", {"fact": "Почта пользователя ivan@mail.ru", "about": "контакты"})],  # personal: asked
        [("remember", {"fact": "Телефон мамы +998 90 123 45 67", "about": "контакты"})],  # asked, and refused
        [("recall", {"query": "непустых строк отчёты"})],
        [("finish", {"summary": "Запомнил.", "goal_met": False})],
    ]))  # fmt: skip
    run_id = start(hub)
    mail = wait(lambda: [a for a in hub.get("/v1/approvals").json()["approvals"] if a["tool"] == "remember"])[0]
    assert mail["preview"]["text"] == "Почта пользователя ivan@mail.ru" and "облачной модели" in mail["preview"]["note"]
    hub.post(f"/v1/approvals/{mail['id']}", json={"allow": True, "scope": "once"})
    phone = wait(lambda: [a for a in hub.get("/v1/approvals").json()["approvals"] if a["id"] != mail["id"]])[0]
    hub.post(f"/v1/approvals/{phone['id']}", json={"allow": False})
    ended(hub, run_id)
    said = results(agent)
    assert said[0] == "Noted." and said[1].startswith("Kept for good") and said[2].startswith("Kept for good")
    assert said[3] == "not kept: the user did not allow keeping this personal data"
    assert "В README 9 строк, из них 6 непустых" in said[4] and "Пользователь хочет отчёты в Markdown" in said[4]
    facts = {f["text"]: f for f in hub.get("/v1/memory/facts").json()["facts"]}
    assert set(facts) == {"Пользователь хочет отчёты в Markdown", "Почта пользователя ivan@mail.ru"}
    assert facts["Почта пользователя ivan@mail.ru"]["personal"] and facts["Почта пользователя ivan@mail.ru"][
        "source"] == f"run:{run_id}"  # fmt: skip
    assert hub.get(f"/v1/runs/{run_id}/notes").json()["notes"][0]["text"].startswith("В README")
    cline = hub.app.state.db.call(lambda c: create_client(c, "cline"))
    outside = {"Authorization": f"Bearer {cline}"}
    assert hub.get("/v1/memory/facts", headers=outside).json()["facts"] == []  # a local model's facts: not shared
    assert hub.post("/v1/memory/facts", json={"text": "x"}, headers=outside).status_code == 403


def test_a_skill_kept_and_given_to_a_similar_goal(hub):  # noqa: F811
    use(hub, Scripted("agent", [[PLAN], [step(1, "running")], [step(1, "done", result="9", check="read_file")],
                                [step(2, "skipped", result="уже ясно")],
                                [("finish", {"summary": "9 строк.", "goal_met": True})]]))  # fmt: skip
    first = start(hub, goal="Посчитать строки в README проекта")
    ended(hub, first)
    kept = hub.post("/v1/skills", json={"run_id": first, "name": "Подсчёт строк"}).json()
    assert kept["steps"] == [{"title": "Прочитать README", "check": "файл прочитан"}]  # the skipped step left out
    assert "update_step" in kept["tools"] and "set_plan" in kept["tools"]
    assert hub.get("/v1/skills", params={"q": "строки посчитать"}).json()["skills"][0]["id"] == kept["id"]
    later = use(hub, Scripted("later", [[("finish", {"summary": "—", "goal_met": False})]]))
    second = start(hub, goal="Посчитать строки в CHANGELOG", model_id="later/m")
    ended(hub, second)
    role = later.prompts[0][1]["content"]
    assert "A similar goal was reached before («Подсчёт строк»)" in role and "1. Прочитать README" in role
    assert hub.get("/v1/skills").json()["skills"][0]["uses"] == 1
    assert hub.post("/v1/skills", json={"run_id": "nope"}).status_code == 422
    assert hub.delete(f"/v1/skills/{kept['id']}").status_code == 204
    assert hub.get("/v1/skills").json()["skills"] == []
