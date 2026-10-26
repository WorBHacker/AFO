"""Project cards (stage 3, D24): what a draft is made from, versions, outdated paths, the API, the card as L1."""

import asyncio

import pytest
from starlette.testclient import TestClient

from common.paths import data_paths
from hub import cards, context
from hub.auth import create_client
from hub.cards import CardError
from hub.db import Database, migrate
from hub.gateway.base import Model, StreamEvent
from hub.projects import Project
from hub.server import create_app
from hub.settings import HubSettings

CARD = "## Назначение\nДемо.\n## Ключевые папки и файлы\n- `src/app.py` - вход\n- `lib/old.py` - старое\n- `pytest`"


def project(root, cloud_ok=False):
    return Project(id="p1", name="proj", path=str(root), description="демо", cloud_ok=cloud_ok, created_at="",
                   updated_at="")  # fmt: skip


def test_what_a_draft_is_made_from(repo):
    (repo / ".env").write_text("TOKEN=x\n", encoding="utf-8")
    (repo / "pyproject.toml").write_text("[project]\nname='x'\n# key = 'sk-ant-api03-" + "k" * 40 + "'\n",
                                         encoding="utf-8")  # fmt: skip
    material = cards.gather(project(repo.resolve()))
    assert "# Проект «proj»" in material and "Описание от пользователя: демо" in material
    assert "## Дерево папок" in material and "  app.py" in material and ".env" not in material
    assert "## README.md\n# Проект" in material and "## pyproject.toml" in material
    assert "sk-ant" not in material and "[REDACTED]" in material
    assert "## Последние коммиты\n- " in material and "first" in material
    prompt = cards.prompt(project(repo.resolve()))
    assert prompt[0]["role"] == "system" and "Назначение" in prompt[0]["content"]


def test_outdated_paths(repo):
    assert cards.missing_paths(project(repo.resolve()), CARD) == ["lib/old.py"]  # `pytest` is a word, not a path
    # the first version of this check flagged these on a real card: names under a folder's heading, settings
    listed = "## `src/`\n- `app.py`\n- `app/`\n## Сборка\n`packages.find`, `app.ui`, `hub.db`\n## `notes.txt`"
    assert cards.missing_paths(project(repo.resolve()), listed) == []


@pytest.fixture
def db():
    database = Database(data_paths().ensure().db)
    database.call(migrate)
    database.call(lambda c: c.execute(
        "INSERT INTO projects (id, name, path) VALUES ('p1', 'proj', 'C:\\\\nowhere')"))  # fmt: skip
    yield database
    database.close()


def test_versions(db):
    db.call(lambda c: cards.save_draft(c, "p1", "черновик", "llama-swap/coder"))
    state = db.call(lambda c: cards.card_state(c, "p1"))
    assert state["draft"]["text"] == "черновик" and state["confirmed"] is None
    first = db.call(lambda c: cards.confirm(c, "p1", "версия один"))
    assert first["confirmed"]["version"] == 1 and first["confirmed"]["model_id"] == "llama-swap/coder"
    assert first["draft"] is None
    db.call(lambda c: cards.confirm(c, "p1", "версия два"))
    restored = db.call(lambda c: cards.restore(c, "p1", 1))
    assert restored["confirmed"]["version"] == 3 and restored["confirmed"]["text"] == "версия один"
    assert [v["version"] for v in restored["versions"]] == [3, 2, 1]
    assert db.call(lambda c: cards.confirmed_text(c, "p1")) == "версия один"
    with pytest.raises(CardError):
        db.call(lambda c: cards.restore(c, "p1", 9))
    with pytest.raises(CardError):
        db.call(lambda c: cards.confirm(c, "p1", "  "))
    assert db.call(lambda c: cards.drop_draft(c, "p1")) is False


def test_the_card_is_the_projects_layer(repo):
    p = project(repo.resolve())
    layer = context.project_layer(p, CARD, ["lib/old.py"])
    assert "The project's card (confirmed by the user):" in layer and "## Назначение" in layer
    assert "[The card is outdated here: it names lib/old.py, which are gone.]" in layer
    assert "README.md (start)" not in layer  # the card takes the place of the automatic summary


class CardWriter:
    name = "fake"

    def __init__(self, locality="local"):
        self.models = [Model("fake/echo", "fake", "echo", "Echo", locality, {}, {})]
        self.seen = []

    async def list_models(self):
        return self.models

    async def stream_chat(self, model_ref, messages, effort, cancel, tools=None):
        self.seen.append(messages)
        yield StreamEvent("content", CARD)
        yield StreamEvent("end", usage={"input_tokens": 900, "output_tokens": 120}, stop_reason="end_turn")


@pytest.fixture
def hub():
    app = create_app(
        HubSettings(paths=data_paths(), llama_swap_url="http://127.0.0.1:9", status_interval_s=0, process_interval_s=0)
    )
    with TestClient(app, base_url="http://127.0.0.1:8765") as client:
        client.headers["Authorization"] = "Bearer " + (data_paths().tokens / "app.token").read_text(encoding="utf-8")
        yield client


def wait(hub, check):
    for _ in range(300):
        result = check()
        if result:
            return result
        hub.portal.call(asyncio.sleep, 0.02)
    raise AssertionError("condition not met")


def test_the_card_api(hub, repo):
    writer = CardWriter()
    hub.app.state.gateway.register(writer)
    hub.portal.call(hub.app.state.gateway.sync_catalog)
    pid = hub.post("/v1/projects", json={"path": str(repo)}).json()["id"]
    assert hub.get(f"/v1/projects/{pid}/card").json() == {"confirmed": None, "draft": None, "versions": [],
                                                         "missing": [], "drafting": False}  # fmt: skip
    cline = hub.app.state.db.call(lambda c: create_client(c, "cline"))
    other = {"Authorization": f"Bearer {cline}"}
    assert hub.post(f"/v1/projects/{pid}/card/draft", json={"model_id": "fake/echo"}, headers=other).status_code == 403
    assert hub.post(f"/v1/projects/{pid}/card/draft", json={"model_id": "fake/echo"}).status_code == 202
    state = wait(hub, lambda: (s := hub.get(f"/v1/projects/{pid}/card").json())["draft"] and s)
    assert state["draft"]["text"] == CARD and state["drafting"] is False
    assert "# Проект «proj»" in writer.seen[0][1]["content"]
    usage = hub.app.state.db.call(lambda c: c.execute("SELECT meta FROM usage").fetchone()["meta"])
    assert '"purpose": "card"' in usage

    confirmed = hub.put(f"/v1/projects/{pid}/card", json={"text": CARD + "\n- правка"}).json()
    assert confirmed["confirmed"]["version"] == 1 and confirmed["draft"] is None
    assert confirmed["missing"] == ["lib/old.py"]
    assert hub.post(f"/v1/projects/{pid}/card/restore", json={"version": 5}).status_code == 404
    assert hub.delete(f"/v1/projects/{pid}/card/draft").status_code == 404
    assert hub.put(f"/v1/projects/{pid}/card", json={"text": " "}).status_code == 422

    # a cloud model may not read a local-only project
    hub.app.state.gateway._providers.pop("fake", None)
    hub.app.state.gateway.register(CardWriter(locality="cloud"))
    hub.portal.call(hub.app.state.gateway.sync_catalog)
    refused = hub.post(f"/v1/projects/{pid}/card/draft", json={"model_id": "fake/echo"})
    assert refused.status_code == 409 and "local-only" in refused.text
