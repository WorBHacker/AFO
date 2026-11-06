"""A big project: 20 000 files in git - the symbol index, search, the tree, a storm of changes under the watcher -
a 50 MB file in the viewer, and colouring a big file (the editor's size limit, stage 14, D47, comes from it)."""

import asyncio
import time

import pytest

from app.ui import highlighter, project_rows
from hub import cards, projects, search
from hub.projects import Project
from hub.symbols import SymbolIndex
from hub.watch import ProjectWatcher

from .stand import Clock, note

FOLDERS, PER_FOLDER = 200, 100


def module_text(i: int, j: int) -> str:
    needle = "    # needle_word: the search finds it\n" if j == 7 else ""
    return (f'"""Module {i}/{j}."""\n\n\nclass Thing{j}:\n    def run(self, value):\n{needle}'
            f"        return value + {j}\n\n    def stop(self):\n        return None\n\n\n"
            f"def helper_{i}_{j}(x):\n    return x * 2\n")  # fmt: skip


@pytest.fixture(scope="module")
def big(tmp_path_factory):
    from conftest import run_git

    root = tmp_path_factory.mktemp("big") / "proj"
    for i in range(FOLDERS):
        folder = root / "src" / f"pkg{i:03d}"
        folder.mkdir(parents=True)
        for j in range(PER_FOLDER):
            (folder / f"mod{j:03d}.py").write_bytes(module_text(i, j).encode())
    run_git(root, "init", "-q", "-b", "main")
    run_git(root, "add", ".")
    run_git(root, "commit", "-q", "-m", "first")
    return root.resolve()


def project(root) -> Project:
    return Project(id="big", name="big", path=str(root), description="", cloud_ok=False, created_at="", updated_at="")


class Count:
    """Counts the git processes the hub starts (they all go through projects.git)."""

    def __init__(self, monkeypatch):
        self.calls: list[tuple[str, ...]] = []
        real = projects.git

        def counted(root, *args, **kw):
            self.calls.append(args)
            return real(root, *args, **kw)

        monkeypatch.setattr(projects, "git", counted)


def test_the_symbol_index(big):
    # files just written are scanned by the antivirus when first opened (~2 ms each on the developer's machine):
    # a fresh clone pays that once, a project in use does not - both are measured
    with Clock() as fresh:
        SymbolIndex(budget_s=600).refresh(project(big))
    note("индекс символов: 20 000 свежих файлов (антивирус)", fresh.s, "с", 300)
    index = SymbolIndex(budget_s=600, fresh_s=0)
    with Clock() as cold:
        found = index.find(project(big), "helper_7_7")
    assert found["symbols"][0]["path"] == "src/pkg007/mod007.py" and found["complete"]
    note("индекс символов: 20 000 файлов с нуля", cold.s, "с", 20)
    with Clock() as warm:
        index.find(project(big), "Thing3")
    note("индекс символов: проверка без изменений", warm.s * 1000, "мс", 5000)


def test_search(big):
    for engine, use in (("ripgrep", True), ("без ripgrep", False)):
        with Clock() as clock:
            found = search.search(big, "needle_word", use_ripgrep=use)
        assert found["matches"] == FOLDERS, found["engine"]
        note(f"поиск ({engine})", clock.s * 1000, "мс", 1500 if use else 6000)


def test_the_tree(big, monkeypatch):
    count = Count(monkeypatch)
    with Clock() as top:
        projects.tree(big)
    note("дерево: корень проекта", top.s * 1000, "мс", 500)
    count.calls.clear()
    with Clock() as drawn:
        lines = cards._tree(big)
    assert len(lines) >= 200
    note("дерево для карточки (3 уровня)", drawn.s * 1000, "мс", 3000)
    note("  его процессов git", len(count.calls), "шт", 40)


def test_a_storm_of_changes_under_the_watcher(big, monkeypatch):
    count = Count(monkeypatch)
    files = sorted((big / "src").rglob("*.py"))[:5000]

    class Bus:
        def __init__(self):
            self.events: list[tuple[float, str, dict]] = []

        async def publish(self, event_type, payload=None, *, project_id=None, persist=True):
            self.events.append((time.perf_counter(), event_type, payload))

    async def scenario():
        bus = Bus()
        watcher = ProjectWatcher(bus)
        watcher.start([project(big)])
        deadline = time.perf_counter() + 30
        while not any(t == "project.git" for _, t, _ in bus.events):
            assert time.perf_counter() < deadline
            await asyncio.sleep(0.05)
        bus.events.clear()
        count.calls.clear()

        def storm() -> float:  # 5000 files over about 2 s, the way a checkout or a formatter goes
            for group in range(20):
                for path in files[group * 250 : (group + 1) * 250]:
                    path.write_bytes(path.read_bytes() + b"# changed\n")
                time.sleep(0.1)
            return time.perf_counter()

        with Clock() as cpu:
            last_write = await asyncio.to_thread(storm)
            while not bus.events or time.perf_counter() - bus.events[-1][0] < 3:  # quiet for 3 s: settled
                await asyncio.sleep(0.05)
        await watcher.stop()
        return bus.events, last_write, cpu

    events, last_write, cpu = asyncio.run(scenario())
    kinds = [t for _, t, _ in events]
    last = max(at for at, t, _ in events)
    assert kinds.count("project.git") >= 1
    assert sum(len(p["changed"]) for _, t, p in events if t == "project.files") > 0
    note("наблюдатель: 5000 изменений, успокоился после последнего", (last - last_write) * 1000, "мс", 3000)
    note("  событий project.files", kinds.count("project.files"), "шт", 10)
    note("  событий project.git", kinds.count("project.git"), "шт", 10)
    note("  процессов git", len(count.calls), "шт", 40)
    note("  CPU хаба на всё", cpu.cpu, "с", 20)


def test_a_50_mb_file_in_the_viewer(tmp_path):
    line = b"value = compute(alpha, beta, gamma)  # a line of a big generated file\n"
    (tmp_path / "big.py").write_bytes(line * (50 * 1024 * 1024 // len(line)))
    with Clock() as read:
        data = projects.read_file(tmp_path, "big.py")
    assert data["truncated"]
    note("файл 50 МБ: хаб читает начало", read.s * 1000, "мс", 300)
    with Clock() as laid:
        view = project_rows.file_view(data)
    assert "chunks" in view and view["lexer"] == ""  # over the editor's limit: in pieces, plain
    note("файл 50 МБ: просмотрщик готовит куски", laid.s * 1000, "мс", 500)


def test_colouring_a_big_file():
    text = "".join(module_text(i, j) for i in range(30) for j in range(100))
    one_mb = (text * (1024 * 1024 // len(text) + 1))[: 1024 * 1024]
    with Clock() as coloured:
        highlighter.lex_lines(highlighter.lexer_for("big.py"), one_mb)
    note("подсветка: разбор 1 МБ Python (в фоне)", coloured.s, "с", 6)
    with Clock() as viewer:
        view = project_rows.code_view(one_mb, "big.py")
    assert view["lexer"] == "python" and "text" in view  # one text, its colours the highlighter's (24.1)
    note("просмотрщик: 1 МБ — текст для окна", viewer.s * 1000, "мс", 300)
