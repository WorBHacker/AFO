"""A project's own checks (stage 61.4): found by its files, with their programs on this computer - real folders, and
PATH a folder of real (empty) program files, so `shutil.which` looks as it does on the user's computer."""

from hub import checks


def test_a_projects_checks_are_found_by_its_files(tmp_path, monkeypatch):
    on_path = tmp_path / "bin"
    on_path.mkdir()
    for name in ("cargo.exe", "go.exe"):
        (on_path / name).write_bytes(b"")
    monkeypatch.setenv("PATH", str(on_path))

    python = tmp_path / "py"
    scripts = python / ".venv" / "Scripts"
    scripts.mkdir(parents=True)
    for name in ("python.exe", "pytest.exe", "ruff.exe"):  # its own environment's programs; no mypy among them
        (scripts / name).write_bytes(b"")
    (python / "pyproject.toml").write_text("[tool.ruff]\nline-length = 100\n\n[tool.mypy]\nstrict = true\n",
                                           encoding="utf-8")  # fmt: skip
    (python / "tests").mkdir()
    assert checks.found(python) == [f'& "{scripts / "ruff.exe"}" check .', f'& "{scripts / "python.exe"}" -m pytest -q']
    (python / "tests").rmdir()
    assert checks.found(python) == [f'& "{scripts / "ruff.exe"}" check .']  # no tests: no pytest

    rust, go, node, bare = (tmp_path / n for n in ("rust", "go", "node", "bare"))
    for folder in (rust, go, node, bare):
        folder.mkdir()
    (rust / "Cargo.toml").write_text('[package]\nname = "x"\n', encoding="utf-8")
    (go / "go.mod").write_text("module x\n", encoding="utf-8")
    (node / "package.json").write_text('{"scripts": {"test": "jest"}}', encoding="utf-8")
    (bare / "pyproject.toml").write_text("[tool.ruff\nbroken", encoding="utf-8")  # not TOML: nothing, no failure
    (bare / "tests").mkdir()
    assert checks.found(rust) == ["cargo test"]
    assert checks.found(go) == ["go vet ./...", "go test ./..."]
    assert checks.found(node) == []  # its copy has no node_modules (61.4)
    assert checks.found(bare) == []  # no environment of its own, no python or pytest on PATH
    monkeypatch.setenv("PATH", str(tmp_path / "nowhere"))
    assert checks.found(rust) == [] and checks.found(go) == []  # their programs are not on this computer
