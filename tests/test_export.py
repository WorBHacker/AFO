"""Exporting the card into the project (stage 4.4, D23): the block, how each file changes, the API."""

import pytest
from starlette.testclient import TestClient

from common.paths import data_paths
from hub import export
from hub.auth import create_client
from hub.export import BEGIN, END, ExportError
from hub.projects import Project
from hub.server import create_app
from hub.settings import HubSettings

BASE = "http://127.0.0.1:8765"
CARD = "## Назначение\nДемо-проект.\n\n## Как запускать\n`python -m demo`"
SECRET = "sk-ant-api03-" + "q" * 40


def project(root):
    return Project(id="p1", name="demo", path=str(root), description="", cloud_ok=False, created_at="", updated_at="")


def test_the_block(tmp_path):
    text = export.block(project(tmp_path), CARD + f"\nключ {SECRET}", [{"text": "тесты: pytest -q"}])
    assert text.startswith(BEGIN) and text.rstrip().endswith(END)
    assert "## Карточка проекта «demo» (LocalChat)" in text
    assert "### Назначение\nДемо-проект." in text and "### Как запускать" in text  # one level down, under the block
    assert "### Заметки для моделей\n- тесты: pytest -q" in text
    assert "MCP" in text and SECRET not in text  # the file may be committed: secrets are masked


def test_each_file_changes_only_where_it_should(tmp_path):
    p = project(tmp_path)
    text = export.block(p, CARD, [])
    created = export.plan(p, "claude", text)
    assert created.mode == "create" and created.after == text and created.path == tmp_path / "CLAUDE.md"

    (tmp_path / "AGENTS.md").write_bytes("# Мои правила\r\nНе трогать.\r\n".encode())
    appended = export.plan(p, "agents", text)
    assert appended.mode == "append"
    assert appended.after.startswith("# Мои правила\r\nНе трогать.\r\n\r\n" + BEGIN)  # the user's part first, CRLF kept
    assert "\r\n" in appended.after.split(BEGIN)[1] and "\n\n" not in appended.after.replace("\r\n", "")
    export.write(appended, tmp_path / "snapshots")

    newer = export.block(p, CARD.replace("Демо-проект.", "Демо-проект, версия 2."), [])
    replaced = export.plan(p, "agents", newer)
    assert replaced.mode == "replace" and replaced.after.count(BEGIN) == 1
    assert "версия 2" in replaced.after and "# Мои правила\r\nНе трогать." in replaced.after
    export.write(replaced, tmp_path / "snapshots")
    assert export.plan(p, "agents", newer).mode == "unchanged"

    (tmp_path / "AGENTS.md").write_text(replaced.after.replace("Не трогать.", "Не трогать!") + "\r\nХвост.\r\n",
                                        encoding="utf-8", newline="")  # fmt: skip
    again = export.plan(p, "agents", text)
    assert again.mode == "replace" and "Не трогать!" in again.after and again.after.endswith("Хвост.\r\n")


def test_a_clinerules_folder_gets_a_file_of_its_own(tmp_path):
    (tmp_path / ".clinerules").mkdir()
    (tmp_path / ".clinerules" / "style.md").write_text("свои правила\n", encoding="utf-8")
    text = export.block(project(tmp_path), CARD, [])
    plan = export.plan(project(tmp_path), "cline", text)
    assert plan.path == tmp_path / ".clinerules" / "localchat.md" and plan.mode == "create"
    export.write(plan, tmp_path / "snapshots")
    assert export.plan(project(tmp_path), "cline", text).mode == "unchanged"
    assert (tmp_path / ".clinerules" / "style.md").read_text(encoding="utf-8") == "свои правила\n"


def test_what_is_refused(tmp_path):
    p = project(tmp_path)
    with pytest.raises(ExportError, match="unknown target"):
        export.plan(p, "readme", "x")
    (tmp_path / "CLAUDE.md").write_bytes(b"\xff\xfe\x00broken")
    with pytest.raises(ExportError, match="not UTF-8"):
        export.plan(p, "claude", "x")


def test_the_api(repo):
    app = create_app(HubSettings(paths=data_paths(), llama_swap_url="http://127.0.0.1:9", status_interval_s=0,
                                 process_interval_s=0))  # fmt: skip
    with TestClient(app, base_url=BASE) as client:
        client.headers["Authorization"] = "Bearer " + (data_paths().tokens / "app.token").read_text(encoding="utf-8")
        pid = client.post("/v1/projects", json={"path": str(repo)}).json()["id"]
        assert client.get(f"/v1/projects/{pid}/card/export").status_code == 409  # no confirmed card yet
        client.put(f"/v1/projects/{pid}/card", json={"text": CARD})
        client.post(f"/v1/projects/{pid}/notes", json={"text": "тесты: pytest"})
        (repo / "AGENTS.md").write_text("# Свои правила\n", encoding="utf-8")

        preview = client.get(f"/v1/projects/{pid}/card/export").json()
        modes = {t["target"]: (t["path"], t["mode"]) for t in preview["targets"]}
        assert modes == {"claude": ("CLAUDE.md", "create"), "agents": ("AGENTS.md", "append"),
                         "cline": (".clinerules", "create")}  # fmt: skip
        assert "- тесты: pytest" in preview["block"]
        assert not (repo / "CLAUDE.md").exists()  # a preview writes nothing (D23)

        written = client.post(f"/v1/projects/{pid}/card/export", json={"targets": ["claude", "agents"]})
        assert written.status_code == 200, written.text
        files = {f["target"]: f for f in written.json()["files"]}
        assert files["claude"]["snapshot"] is None and files["agents"]["snapshot"]
        assert (data_paths().snapshots / files["agents"]["snapshot"]).read_text(encoding="utf-8") == "# Свои правила\n"
        assert (repo / "CLAUDE.md").read_text(encoding="utf-8").startswith(BEGIN)
        assert (repo / "AGENTS.md").read_text(encoding="utf-8").startswith("# Свои правила\n\n" + BEGIN)
        assert not (repo / ".clinerules").exists()
        feed = client.get("/v1/feed", params={"types": "card.exported"}).json()["events"]
        assert [f["path"] for f in feed[0]["payload"]["files"]] == ["CLAUDE.md", "AGENTS.md"]

        assert client.post(f"/v1/projects/{pid}/card/export", json={"targets": ["nope"]}).status_code == 422
        other = app.state.db.call(lambda c: create_client(c, "cline", cloud=False))
        refused = client.post(f"/v1/projects/{pid}/card/export", json={"targets": ["claude"]},
                              headers={"Authorization": f"Bearer {other}"})  # fmt: skip
        assert refused.status_code == 403
