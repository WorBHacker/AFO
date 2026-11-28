from common import paths as paths_module
from common.paths import DATA_DIR_ENV, app_home, data_paths


def test_env_override_is_used(isolated_data_dir):
    assert data_paths().root == isolated_data_dir


def test_default_is_the_checkouts_data_folder_or_afos_own(monkeypatch, tmp_path):
    """A checkout that keeps its data in <project>/data goes on with it; one without - a new clone, a git worktree -
    has AFO's own folder's (the test expected <project>/data always and failed in every worktree, 2026-11-28)."""
    monkeypatch.delenv(DATA_DIR_ENV)
    checkout = tmp_path / "checkout"
    monkeypatch.setattr(paths_module, "PROJECT_ROOT", checkout)
    assert data_paths().root == app_home() / "data"
    (checkout / "data").mkdir(parents=True)
    assert data_paths().root == checkout / "data"


def test_ensure_creates_every_folder(isolated_data_dir):
    paths = data_paths().ensure()
    for folder in (paths.root, paths.logs, paths.terminals, paths.snapshots, paths.blobs, paths.backups, paths.tokens):
        assert folder.is_dir()
    assert paths.db == isolated_data_dir / "hub.db"
    assert not paths.db.exists()  # the database is created by the hub, not by ensure()
    assert paths.hub_info.parent == paths.hub_lock.parent == isolated_data_dir
