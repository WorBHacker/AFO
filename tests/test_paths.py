from common.paths import DATA_DIR_ENV, PROJECT_ROOT, data_paths


def test_env_override_is_used(isolated_data_dir):
    assert data_paths().root == isolated_data_dir


def test_default_is_project_data_folder(monkeypatch):
    monkeypatch.delenv(DATA_DIR_ENV)
    assert data_paths().root == PROJECT_ROOT / "data"


def test_ensure_creates_every_folder(isolated_data_dir):
    paths = data_paths().ensure()
    for folder in (paths.root, paths.logs, paths.terminals, paths.snapshots, paths.blobs, paths.backups, paths.tokens):
        assert folder.is_dir()
    assert paths.db == isolated_data_dir / "hub.db"
    assert not paths.db.exists()  # the database is created by the hub, not by ensure()
    assert paths.hub_info.parent == paths.hub_lock.parent == isolated_data_dir


def test_ensure_is_repeatable():
    paths = data_paths()
    assert paths.ensure() == paths.ensure()
