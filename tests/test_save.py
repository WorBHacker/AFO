"""The user's save from the window's editor (stage 14.1, D47): through the hub, as a model's write goes (D29) - the
version the editing began from checked, the file's encoding, byte order mark and line ends kept, the change kept
and undone in «Изменения» as the user's."""

from hub.auth import create_client
from tests.test_tools import hub  # noqa: F401 - the hub fixture


def opened(hub, pid, path):  # noqa: F811 - the fixture's name
    found = hub.get(f"/v1/projects/{pid}/file", params={"path": path})
    assert found.status_code == 200, found.text
    return found.json()


def save(hub, pid, path, text, base, encoding="utf-8"):  # noqa: F811
    return hub.put(f"/v1/projects/{pid}/file", json={"path": path, "text": text, "encoding": encoding, "base": base})


def test_a_save_writes_the_file_as_it_was_and_can_be_undone(hub, repo):  # noqa: F811
    pid = hub.post("/v1/projects", json={"path": str(repo)}).json()["id"]
    (repo / "win.txt").write_bytes(b"\xef\xbb\xbfone\r\ntwo\r\n")
    got = opened(hub, pid, "win.txt")
    assert (got["encoding"], got["eol"]) == ("utf-8 BOM", "CRLF")
    saved = save(hub, pid, "win.txt", "one\nтри\n", got["sha256"], got["encoding"])  # the editor has \n
    assert saved.status_code == 200, saved.text
    written = (repo / "win.txt").read_bytes()
    assert written == b"\xef\xbb\xbfone\r\n" + "три".encode() + b"\r\n"  # its mark and its line ends stay
    result = saved.json()
    assert result["sha256"] == opened(hub, pid, "win.txt")["sha256"] and not result["created"]
    change = result["change"]
    assert change["tool"] == "save" and change["conversation_id"] is None and change["path"] == "win.txt"
    feed = hub.get("/v1/feed", params={"types": "file.saved"}).json()["events"]
    assert feed[-1]["payload"]["path"] == "win.txt" and feed[-1]["payload"]["change"]["id"] == change["id"]
    diff = hub.get(f"/v1/changes/{change['id']}").json()["diff"]
    assert "-two" in diff and "+три" in diff

    # the version on disk is not the one the editing began from: 409, with what is there now
    (repo / "win.txt").write_bytes(b"changed by another program\r\n")
    stale = save(hub, pid, "win.txt", "моё\n", result["sha256"], "utf-8 BOM")
    assert stale.status_code == 409
    detail = stale.json()["detail"]
    assert detail["reason"] == "changed" and detail["file"]["text"] == "changed by another program\r\n"
    assert (repo / "win.txt").read_bytes() == b"changed by another program\r\n"  # nothing written
    undo = hub.post(f"/v1/changes/{change['id']}/revert", json={})
    assert undo.status_code == 409  # undoing would lose the other program's text
    (repo / "win.txt").write_bytes(written)
    assert hub.post(f"/v1/changes/{change['id']}/revert", json={}).status_code == 200
    assert (repo / "win.txt").read_bytes() == b"\xef\xbb\xbfone\r\ntwo\r\n"


def test_the_files_own_encoding_and_what_has_no_place_in_it(hub, repo):  # noqa: F811
    pid = hub.post("/v1/projects", json={"path": str(repo)}).json()["id"]
    (repo / "старый.txt").write_bytes("привет\r\nмир\r\n".encode("cp1251"))
    got = opened(hub, pid, "старый.txt")
    assert got["encoding"] == "cp1251"
    assert save(hub, pid, "старый.txt", "привет\nвесь мир\n", got["sha256"], "cp1251").status_code == 200
    assert (repo / "старый.txt").read_bytes() == "привет\r\nвесь мир\r\n".encode("cp1251")  # cp1251 stays cp1251
    base = opened(hub, pid, "старый.txt")["sha256"]
    refused = save(hub, pid, "старый.txt", "привет\nмир ✓\n", base, "cp1251")
    assert refused.status_code == 422 and "«✓» (line 2) has no place in cp1251" in refused.json()["detail"]
    assert (repo / "старый.txt").read_bytes() == "привет\r\nвесь мир\r\n".encode("cp1251")

    (repo / "wide.txt").write_bytes(b"\xff\xfe" + "a\r\nб\r\n".encode("utf-16-le"))
    got = opened(hub, pid, "wide.txt")
    assert got["encoding"] == "utf-16" and save(hub, pid, "wide.txt", "a\nбв\n", got["sha256"], "utf-16").is_success
    assert (repo / "wide.txt").read_bytes() == b"\xff\xfe" + "a\r\nбв\r\n".encode("utf-16-le")
    assert save(hub, pid, "wide.txt", "x", "", "koi8-r").status_code == 422  # not one the editor knows


def test_new_gone_secret_and_whose_the_save_is(hub, repo):  # noqa: F811
    pid = hub.post("/v1/projects", json={"path": str(repo)}).json()["id"]
    made = save(hub, pid, "docs/новый.md", "# Новый\n", "")
    assert made.status_code == 200 and made.json()["created"] and made.json()["change"]["before_hash"] is None
    assert (repo / "docs" / "новый.md").read_bytes() == "# Новый\n".encode()
    again = save(hub, pid, "docs/новый.md", "# Другой\n", "")
    assert again.status_code == 409 and again.json()["detail"]["reason"] == "exists"
    base = opened(hub, pid, "docs/новый.md")["sha256"]
    (repo / "docs" / "новый.md").unlink()
    gone = save(hub, pid, "docs/новый.md", "# Новый\n", base)
    said = "docs/новый.md is gone from the disk since it was opened"
    assert gone.status_code == 409 and gone.json()["detail"] == {"reason": "gone", "message": said, "file": None}

    # a secret file: the user may save it, but its content is kept nowhere - no change, no snapshot
    (repo / ".env").write_text("KEY=old-value-1234\n", encoding="utf-8")
    snapshots = hub.app.state.settings.paths.snapshots
    before = set(snapshots.iterdir()) if snapshots.exists() else set()
    secret = save(hub, pid, ".env", "KEY=new-value-5678\n", opened(hub, pid, ".env")["sha256"])
    assert secret.status_code == 200 and secret.json()["change"] is None
    assert (repo / ".env").read_text(encoding="utf-8") == "KEY=new-value-5678\n"
    assert (set(snapshots.iterdir()) if snapshots.exists() else set()) == before
    assert all(c["path"] != ".env" for c in hub.get(f"/v1/projects/{pid}/changes").json()["changes"])

    assert save(hub, pid, ".git/config", "x", "").status_code == 422  # git's own folder
    assert save(hub, pid, "../outside.txt", "x", "").status_code == 422  # out of the project
    token = hub.app.state.db.call(lambda c: create_client(c, "cline", cloud=False))
    other = hub.put(f"/v1/projects/{pid}/file", json={"path": "README.md", "text": "x", "base": ""},
                    headers={"Authorization": f"Bearer {token}"})  # fmt: skip
    assert other.status_code == 403  # the window's alone
