"""Projects (stage 2): the registry, staying inside the project, trees with .gitignore, files, git."""

import pytest
from starlette.testclient import TestClient

from common.paths import data_paths
from hub import cards, projects
from hub.auth import create_client
from hub.db import Database, migrate
from hub.projects import ProjectError
from hub.server import create_app
from hub.settings import HubSettings
from tests.conftest import run_git

BASE = "http://127.0.0.1:8765"


@pytest.fixture
def db():
    database = Database(data_paths().ensure().db)
    database.call(migrate)
    yield database
    database.close()


# --- the registry ---


def test_registry(db, repo, tmp_path):
    project = db.call(lambda c: projects.add_project(c, str(repo / "src" / "..")))
    assert project.path == str(repo.resolve()) and project.name == "proj" and project.cloud_ok is False
    with pytest.raises(ProjectError, match="already"):
        db.call(lambda c: projects.add_project(c, str(repo).upper()))  # Windows paths ignore case
    with pytest.raises(ProjectError, match="no such folder"):
        db.call(lambda c: projects.add_project(c, str(tmp_path / "missing")))
    with pytest.raises(ProjectError, match="not a folder"):
        db.call(lambda c: projects.add_project(c, str(repo / "README.md")))
    with pytest.raises(ProjectError, match="whole drive"):
        db.call(lambda c: projects.add_project(c, repo.anchor))
    changed = db.call(lambda c: projects.update_project(c, project.id, name="Мой", cloud_ok=True))
    assert (changed.name, changed.cloud_ok) == ("Мой", True)
    assert [p.id for p in db.call(projects.list_projects)] == [project.id]
    assert db.call(lambda c: projects.remove_project(c, project.id))
    assert repo.is_dir()  # forgetting a project never touches its folder


def test_paths_stay_inside_the_project(repo):
    root = repo.resolve()
    assert projects.resolve_inside(root, "src/app.py") == root / "src" / "app.py"
    assert projects.resolve_inside(root, "\\src\\app.py") == root / "src" / "app.py"
    # "C:" alone would be the current folder of the project's own drive - inside; another drive is not
    for escape in ("../outside.txt", "src/../../outside.txt", str(root.parent / "outside.txt"), "Z:/elsewhere"):
        with pytest.raises(ProjectError, match="leaves the project"):
            projects.resolve_inside(root, escape)


# --- trees and files ---


def test_tree_follows_gitignore(repo):
    root = repo.resolve()
    top = projects.tree(root)
    assert [(e["name"], e["dir"]) for e in top] == [
        ("src", True),
        (".gitignore", False),
        ("notes.txt", False),  # untracked but not ignored: shown
        ("README.md", False),
    ]
    assert [e["path"] for e in projects.tree(root, "src")] == ["src/app.py"]
    with pytest.raises(ProjectError):
        projects.tree(root, "README.md")


def test_a_git_tree_hides_caches_git_does_not_keep(repo):
    """31.4 (live, 2026-11-08): a git project with no .gitignore showed `__pycache__` and `.pytest_cache` in its tree,
    and coder's card draft described them. A cache folder git keeps nothing of is left out there too; one whose
    files git tracks (a committed `.idea/`) stays."""
    root = repo.resolve()
    (root / "__pycache__").mkdir()
    (root / "__pycache__" / "app.cpython-314.pyc").write_bytes(b"\0pyc")
    (root / "src" / "__pycache__").mkdir()
    (root / "src" / "__pycache__" / "app.cpython-314.pyc").write_bytes(b"\0pyc")
    (root / ".pytest_cache" / "v").mkdir(parents=True)
    (root / ".pytest_cache" / "v" / "nodeids").write_text("[]", encoding="utf-8")
    (root / ".idea").mkdir()
    (root / ".idea" / "project.xml").write_text("<project/>\n", encoding="utf-8")
    run_git(root, "add", ".idea/project.xml")
    run_git(root, "commit", "-q", "-m", "ide settings")
    assert [e["name"] for e in projects.tree(root) if e["dir"]] == [".idea", "src"]
    assert [e["path"] for e in projects.tree(root, "src")] == ["src/app.py"]
    assert "__pycache__/" not in "\n".join(cards._tree(root))


def test_tree_without_git_hides_noise(tmp_path):
    root = tmp_path / "plain"
    (root / "node_modules" / "x").mkdir(parents=True)
    (root / ".venv").mkdir()
    (root / "main.py").write_text("x = 1\n", encoding="utf-8", newline="\n")
    assert [e["name"] for e in projects.tree(root.resolve())] == ["main.py"]


def test_secret_files_are_marked_in_the_tree(repo):
    (repo / "deploy.pem").write_text("-----BEGIN PRIVATE KEY-----", encoding="utf-8")
    (repo / ".env.example").write_text("API_KEY=", encoding="utf-8")
    marks = {e["name"]: e["secret"] for e in projects.tree(repo.resolve())}
    assert marks["deploy.pem"] is True and marks[".env.example"] is False and marks["README.md"] is False


def test_reading_files(repo):
    root = repo.resolve()
    readme = projects.read_file(root, "README.md")
    assert readme["text"] == "# Проект\n" and readme["encoding"] == "utf-8" and not readme["binary"]
    (repo / "old.txt").write_bytes("привет".encode("cp1251"))
    assert projects.read_file(root, "old.txt")["text"] == "привет"
    assert projects.read_file(root, "build/out.bin")["binary"] is True
    big = projects.read_file(root, "src/app.py", max_bytes=5)
    assert big["truncated"] is True and big["text"] == "print"
    with pytest.raises(ProjectError):
        projects.read_file(root, "src")


# --- git ---


def test_git_status_log_and_diff(repo, tmp_path):
    root = repo.resolve()
    status = projects.git_status(root)
    assert status["repo"] and status["branch"] == "main"
    assert {(c["path"], c["index"] + c["worktree"]) for c in status["changes"]} == {
        ("src/app.py", " M"),
        ("notes.txt", "??"),
    }
    (log,) = projects.git_log(root)
    assert log["subject"] == "first" and log["author"] == "Test"
    diff = projects.git_diff(root, "src/app.py")["diff"]
    assert "+print('new line')" in diff
    assert "+свежий файл" in projects.git_diff(root, "notes.txt")["diff"]  # untracked: all new
    assert projects.git_diff(root, "src/app.py", staged=True)["diff"] == ""
    plain = tmp_path / "plain"
    plain.mkdir()
    assert projects.git_status(plain.resolve())["repo"] is False
    assert projects.git_log(plain.resolve()) == []


# --- the API ---


@pytest.fixture
def api():
    app = create_app(HubSettings(paths=data_paths(), llama_swap_url="http://127.0.0.1:9", status_interval_s=0))
    with TestClient(app, base_url=BASE) as client:
        client.headers["Authorization"] = "Bearer " + (data_paths().tokens / "app.token").read_text(encoding="utf-8")
        yield client


def test_projects_api(api, repo):
    added = api.post("/v1/projects", json={"path": str(repo)})
    assert added.status_code == 201
    pid = added.json()["id"]
    assert added.json()["exists"] is True
    assert api.post("/v1/projects", json={"path": str(repo)}).status_code == 422
    assert [p["id"] for p in api.get("/v1/projects").json()["projects"]] == [pid]
    assert next(e["name"] for e in api.get(f"/v1/projects/{pid}/tree").json()["entries"]) == "src"
    assert api.get(f"/v1/projects/{pid}/file", params={"path": "README.md"}).json()["text"] == "# Проект\n"
    assert api.get(f"/v1/projects/{pid}/file", params={"path": "../x"}).status_code == 400
    git = api.get(f"/v1/projects/{pid}/git").json()
    assert git["branch"] == "main" and git["log"][0]["subject"] == "first"
    assert "+print('new line')" in api.get(f"/v1/projects/{pid}/diff", params={"path": "src/app.py"}).json()["diff"]
    assert api.patch(f"/v1/projects/{pid}", json={"name": "Хаб"}).json()["name"] == "Хаб"
    assert api.get("/v1/projects/nope/tree").status_code == 404


def test_only_the_app_changes_the_list(api, repo):
    cline = api.app.state.db.call(lambda c: create_client(c, "cline"))
    other = {"Authorization": f"Bearer {cline}"}
    assert api.post("/v1/projects", json={"path": str(repo)}, headers=other).status_code == 403
    pid = api.post("/v1/projects", json={"path": str(repo)}).json()["id"]
    # reading is open as MCP has it (D32, D46): a cloud client does not see a local-only project, a local one does
    assert api.get(f"/v1/projects/{pid}/tree", headers=other).status_code == 404
    local = api.app.state.db.call(lambda c: create_client(c, "local-agent", cloud=False))
    assert api.get(f"/v1/projects/{pid}/tree", headers={"Authorization": f"Bearer {local}"}).status_code == 200
    assert api.delete(f"/v1/projects/{pid}", headers=other).status_code == 403
    assert api.delete(f"/v1/projects/{pid}").status_code == 204
    assert api.delete(f"/v1/projects/{pid}").status_code == 404
    (removed,) = api.get("/v1/feed", params={"types": "project.removed"}).json()["events"]
    assert removed["payload"] == {"id": pid, "name": "proj"}  # the log keeps what it was called
