"""Project cards (stage 3, D24): what a draft is made from, versions, outdated paths, the API, the card as L1."""

import asyncio

import pytest
from conftest import run_git
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


def code_of(repo):
    (repo / "auth.py").write_text("class AccessGuard:\n    def check(self, user):\n        return user.admin\n\n"
                                  "    def deny(self):\n        raise PermissionError\n\n\n"
                                  "def require_permission(user):\n    return AccessGuard().check(user)\n",
                                  encoding="utf-8")  # fmt: skip
    (repo / "settings.py").write_text("TIMEOUT = 5\nRETRIES = 3\n", encoding="utf-8")  # constants only: nothing to say
    (repo / "tests").mkdir(exist_ok=True)
    (repo / "tests" / "test_auth.py").write_text("def test_guard():\n    assert True\n", encoding="utf-8")


def test_a_card_says_its_mechanisms_from_the_codes_definitions(repo):
    """18.2 (D52): «Ключевые механизмы и где они» - a model writes it from the code's definitions (the symbol index:
    each file with what it defines, a class with its methods, tests left out), not from a guess at the tree."""
    from hub.symbols import SymbolIndex

    code_of(repo)
    p, index = project(repo.resolve()), SymbolIndex()
    said = cards.overview(index, p)
    assert "auth.py: AccessGuard(check, deny), require_permission" in said
    assert "test_auth" not in said and "settings.py" not in said
    material = cards.gather(p, index)
    assert "## Определения в коде" in material and material.index("## Определения в коде") < material.index("## README")
    assert "## Определения в коде" not in cards.gather(p)  # no index: as before
    system = cards.prompt(p, index)[0]["content"]
    assert "Ключевые механизмы и где они" in system and "Определения в коде" in system


def test_an_older_card_gets_its_mechanisms_on_an_update(repo):
    """A card confirmed before the section was in the template lacks it: the window says so, and an update writes it
    from the code's definitions along with what changed."""
    from hub.symbols import SymbolIndex

    code_of(repo)
    p, index = project(repo.resolve()), SymbolIndex()
    assert cards.missing_sections(CARD) == ["Ключевые механизмы и где они"]
    full = CARD + "\n## Ключевые механизмы и где они\n- права: `auth.py`, `require_permission`"
    assert cards.missing_sections(full) == []
    then = cards.snapshot(p)
    system, asked = (m["content"] for m in cards.update_prompt(p, CARD, then, index))
    assert "Ключевые механизмы и где они" in system and "auth.py: AccessGuard" in asked
    system, asked = (m["content"] for m in cards.update_prompt(p, full, then, index))
    assert "Ключевые механизмы и где они" not in system and "auth.py: AccessGuard" not in asked


def test_what_changed_since_the_card(repo):
    p = project(repo.resolve())
    (repo / "pyproject.toml").write_text("[project]\nname = 'x'\n", encoding="utf-8")
    then = cards.snapshot(p)
    assert then["head"] and then["folders"] == ["src"] and set(then["deps"]) == {"pyproject.toml"}  # build/: ignored
    assert cards.changes_since(p, then)["significant"] is False
    assert cards.changes_since(p, None) is None  # a version confirmed before baselines were kept
    (repo / "pyproject.toml").write_text("[project]\nname = 'x'\ndependencies = ['httpx']\n", encoding="utf-8")
    (repo / "Dockerfile").write_text("FROM python\n", encoding="utf-8")
    changes = cards.changes_since(p, then)
    assert changes["dependencies"] == ["Dockerfile", "pyproject.toml"] and changes["significant"]
    for n in range(cards.SIGNIFICANT_COMMITS):
        run_git(repo, "commit", "-q", "--allow-empty", "-m", f"step {n}")
    many = cards.changes_since(p, {**cards.snapshot(p), "head": then["head"]})
    assert many["commits"] == cards.SIGNIFICANT_COMMITS and many["significant"] and many["dependencies"] == []
    assert cards.changes_since(p, {**then, "head": "0" * 40})["commits"] is None  # a commit that is not there

    asked = cards.update_prompt(p, CARD, then)[1]["content"]
    assert "изменились файлы зависимостей и сборки: Dockerfile, pyproject.toml" in asked
    old_kind = {k: v for k, v in then.items() if k != "facts"}  # a version confirmed before 2026-10-28: by bytes
    assert cards.changes_since(p, old_kind)["dependencies"] == ["Dockerfile", "pyproject.toml"]
    assert "## pyproject.toml (сейчас)\n[project]" in asked and "dependencies = ['httpx']" in asked
    assert "step 19" in asked and "## Дерево папок сейчас" in asked
    assert "## Проект сейчас\n# Проект «proj»" in cards.update_prompt(p, CARD, None)[1]["content"]  # no baseline
    # what models wrap an answer in comes off
    assert cards.tidy_update("<card>\n## А\nраз\n</card>") == "## А\nраз"
    assert cards.tidy_update("```markdown\n## А\nраз\n```") == "## А\nраз"
    assert cards.tidy_update("  ## А\nраз  ") == "## А\nраз"


def test_an_update_touches_only_the_sections_the_model_rewrote():
    card = "Вступление.\n\n## Назначение\nХаб и окно.\n\n## Устройство\n`hub/` и `app/`.\n\n## Зависимости\nfastapi"
    # a model that garbles what it should copy: only what it rewrote is taken; a misspelt heading finds its section
    answer = "Вот правка:\n## Зависимасти\nfastapi, tree-sitter\n\n## Индекс символов\nПо tree-sitter."
    merged = cards.merge_update(card, answer)
    assert merged == (
        "Вступление.\n\n## Назначение\nХаб и окно.\n\n## Устройство\n`hub/` и `app/`.\n\n"
        "## Зависимости\nfastapi, tree-sitter\n\n## Индекс символов\nПо tree-sitter."
    )
    assert cards.merge_update(card, cards.NO_CHANGES) is None
    assert cards.merge_update(card, "<card>\n## назначение\nТолько хаб.\n</card>").startswith(
        "Вступление.\n\n## Назначение\nТолько хаб.\n\n## Устройство"
    )


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
                                                         "missing": [], "changes": None, "sections": [],
                                                         "drafting": False}  # fmt: skip
    cline = hub.app.state.db.call(lambda c: create_client(c, "cline"))
    other = {"Authorization": f"Bearer {cline}"}
    assert hub.post(f"/v1/projects/{pid}/card/draft", json={"model_id": "fake/echo"}, headers=other).status_code == 403
    assert hub.post(f"/v1/projects/{pid}/card/draft", json={"model_id": "fake/echo"}).status_code == 202
    # the draft is saved a moment before "drafting" goes off: wait for both
    state = wait(hub, lambda: (s := hub.get(f"/v1/projects/{pid}/card").json())["draft"] and not s["drafting"] and s)
    assert state["draft"]["text"] == CARD and state["drafting"] is False
    assert "# Проект «proj»" in writer.seen[0][1]["content"]
    usage = hub.app.state.db.call(lambda c: c.execute("SELECT meta FROM usage").fetchone()["meta"])
    assert '"purpose": "card"' in usage

    confirmed = hub.put(f"/v1/projects/{pid}/card", json={"text": CARD + "\n- правка"}).json()
    assert confirmed["confirmed"]["version"] == 1 and confirmed["draft"] is None
    assert confirmed["missing"] == ["lib/old.py"]
    assert confirmed["sections"] == ["Ключевые механизмы и где они"]  # 18.2: a card older than the section
    unchanged = {"commits": 0, "new_folders": [], "gone_folders": [], "dependencies": [], "significant": False}
    assert confirmed["changes"] == unchanged  # nothing has moved since

    # the project moves on: the card may be outdated, and an update touches only what changed (D24)
    (repo / "docs").mkdir()
    (repo / "docs" / "guide.md").write_text("# guide\n", encoding="utf-8")
    run_git(repo, "add", "docs")
    run_git(repo, "commit", "-q", "-m", "docs: a guide")
    changes = hub.get(f"/v1/projects/{pid}/card").json()["changes"]
    assert changes == {**unchanged, "commits": 1, "new_folders": ["docs"], "significant": True}
    assert hub.post(f"/v1/projects/{pid}/card/draft", json={"model_id": "fake/echo", "update": True}).status_code == 202
    wait(hub, lambda: hub.get(f"/v1/projects/{pid}/card").json()["draft"])
    asked = writer.seen[-1]
    assert "Выведи только те разделы" in asked[0]["content"] and cards.NO_CHANGES in asked[0]["content"]
    assert asked[1]["content"].startswith('<changes project="proj">') and "новые папки: docs" in asked[1]["content"]
    assert asked[1]["content"].endswith("<card>\n" + CARD + "\n- правка\n</card>")  # the confirmed card, last
    assert "docs: a guide" in asked[1]["content"]  # the commits since
    hub.delete(f"/v1/projects/{pid}/card/draft")
    hub.put(f"/v1/projects/{pid}/card", json={"text": CARD})
    assert hub.get(f"/v1/projects/{pid}/card").json()["changes"]["significant"] is False  # confirmed anew
    assert hub.post(f"/v1/projects/{pid}/card/restore", json={"version": 5}).status_code == 404
    assert hub.delete(f"/v1/projects/{pid}/card/draft").status_code == 404
    assert hub.put(f"/v1/projects/{pid}/card", json={"text": " "}).status_code == 422

    # a cloud model may not read a local-only project
    hub.app.state.gateway._providers.pop("fake", None)
    hub.app.state.gateway.register(CardWriter(locality="cloud"))
    hub.portal.call(hub.app.state.gateway.sync_catalog)
    refused = hub.post(f"/v1/projects/{pid}/card/draft", json={"model_id": "fake/echo"})
    assert refused.status_code == 409 and "local-only" in refused.text


def test_what_in_a_dependency_file_is_worth_a_look_at_the_card(repo):
    """Q3, 2026-10-28: a version bump or a reformatted file does not make the card look outdated; a new package, a
    new command, a changed entry point does."""
    p = project(repo.resolve())
    files = {
        "package.json": '{"name": "web", "version": "1.0.0", "dependencies": {"react": "^18.0.0"},\n'
        ' "scripts": {"dev": "vite"}}',
        "pyproject.toml": '[project]\nname = "x"\nversion = "1"\ndependencies = ["httpx>=0.27", "Pydantic_Core"]\n',
        "requirements.txt": "# pinned\nfastapi==0.110\n-r base.txt\n",
        "Cargo.toml": '[package]\nname = "x"\nversion = "0.1.0"\n[dependencies]\nserde = "1"\n',
        "go.mod": "module example.com/x\n\ngo 1.22\n\nrequire (\n\tgithub.com/gin-gonic/gin v1.9.1\n)\n",
        "Dockerfile": "FROM python:3.12\nRUN pip install .\n",
    }
    for name, text in files.items():
        (repo / name).write_text(text, encoding="utf-8")
    then = cards.snapshot(p)
    same = {
        "package.json": '{\n  "name": "web",\n  "version": "1.4.2",\n  "dependencies": {"react": "^18.3.1"},\n'
        '  "scripts": {"dev": "vite"}\n}\n',
        "pyproject.toml": '[project]\nname = "x"\nversion = "2"\ndependencies = ["httpx>=0.28", "pydantic-core"]\n',
        "requirements.txt": "fastapi==0.115  # newer\n",
        "Cargo.toml": '[package]\nname = "x"\nversion = "0.2.0"\n[dependencies]\nserde = { version = "1.0.200" }\n',
        "go.mod": "module example.com/x\n\ngo 1.22\n\nrequire (\n\tgithub.com/gin-gonic/gin v1.10.0\n)\n",
        "Dockerfile": "# the image\nFROM python:3.12\n\nRUN pip install .\n",
    }
    for name, text in same.items():
        (repo / name).write_text(text, encoding="utf-8")
    quiet = cards.changes_since(p, then)
    assert quiet["dependencies"] == [] and quiet["significant"] is False
    worth = {
        "package.json": same["package.json"].replace('"vite"', '"vite --host"'),  # how it is run
        "pyproject.toml": same["pyproject.toml"] + '[project.scripts]\nx = "x.cli:main"\n',  # a new command
        "requirements.txt": "fastapi==0.115\nuvicorn\n",  # a new package
        "Cargo.toml": same["Cargo.toml"] + "tokio = '1'\n",
        "go.mod": same["go.mod"].replace(")", "\tgorm.io/gorm v1.25.0\n)"),
        "Dockerfile": same["Dockerfile"].replace("3.12", "3.13"),
    }
    for name, text in worth.items():
        (repo / name).write_text(text, encoding="utf-8")
    loud = cards.changes_since(p, then)
    assert loud["dependencies"] == sorted(files) and loud["significant"]
    assert cards.facts("package.json", b"{not json") == "{not json"  # unreadable: its text, so any change counts
