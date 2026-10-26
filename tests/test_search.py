"""Searching a project (stage 2, D26): ripgrep and the walk without it give the same answers."""

import pytest
from starlette.testclient import TestClient

from common.paths import data_paths
from hub import search
from hub.projects import ProjectError
from hub.server import create_app
from hub.settings import HubSettings

ENGINES = [
    pytest.param(True, id="ripgrep", marks=pytest.mark.skipif(search.ripgrep() is None, reason="no ripgrep")),
    pytest.param(False, id="walk"),
]


def found(result):
    return {f["path"]: [(m["line"], m["text"], m["ranges"]) for m in f["matches"]] for f in result["files"]}


@pytest.fixture
def tree(repo):
    (repo / "node_modules" / "lib").mkdir(parents=True)
    (repo / "node_modules" / "lib" / "x.js").write_text("print('in a cache')\n", encoding="utf-8")
    (repo / "long.txt").write_text("x" * 500 + " needle " + "y" * 500 + "\n", encoding="utf-8")
    return repo.resolve()


@pytest.mark.parametrize("use_ripgrep", ENGINES)
def test_search(tree, use_ripgrep):
    def run(text, **kw):
        return search.search(tree, text, use_ripgrep=use_ripgrep, **kw)

    result = run("print")
    assert result["engine"] == ("ripgrep" if use_ripgrep else "walk")
    assert found(result) == {"src/app.py": [(1, "print('hi')", [[0, 5]]), (2, "print('new line')", [[0, 5]])]}
    assert result["matches"] == 2 and not result["truncated"]  # not in node_modules, not in what git ignores
    assert run("token")["files"] == []  # secret.txt is ignored by git
    assert found(run("файл")) == {"notes.txt": [(1, "свежий файл", [[7, 11]])]}  # characters, not bytes

    assert run("Print")["files"] == []  # smart case: a capital asks for exact case
    assert len(found(run("Print", case="insensitive"))["src/app.py"]) == 2
    assert [m[0] for m in found(run(r"print\('n.*'\)", regex=True))["src/app.py"]] == [2]
    assert found(run("Проект", glob="*.md")) == {"README.md": [(1, "# Проект", [[2, 8]])]}
    assert run("print", limit=1)["truncated"] is True

    (line,) = found(run("needle"))["long.txt"]
    text, ranges = line[1], line[2]
    assert len(text) <= search.MAX_LINE + 2 and text.startswith("…") and text.endswith("…")
    assert text[ranges[0][0] : ranges[0][1]] == "needle"  # the cut line still points at the match

    with pytest.raises(ProjectError):
        run("(", regex=True)
    with pytest.raises(ProjectError):
        run("   ")


def test_search_api(tree):
    app = create_app(
        HubSettings(paths=data_paths(), llama_swap_url="http://127.0.0.1:9", status_interval_s=0, process_interval_s=0)
    )
    with TestClient(app, base_url="http://127.0.0.1:8765") as client:
        client.headers["Authorization"] = "Bearer " + (data_paths().tokens / "app.token").read_text(encoding="utf-8")
        pid = client.post("/v1/projects", json={"path": str(tree)}).json()["id"]
        result = client.get(f"/v1/projects/{pid}/search", params={"q": "print"}).json()
        assert result["matches"] == 2 and result["files"][0]["path"] == "src/app.py"
        assert client.get(f"/v1/projects/{pid}/search", params={"q": "(", "regex": "true"}).status_code == 400
        assert client.get(f"/v1/projects/{pid}/search", params={"q": "x", "case": "loud"}).status_code == 400
