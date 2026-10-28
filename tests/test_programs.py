"""The programs of this computer and what the autonomous agent may control (D43): what a look finds in the Start
menu and the registry, the catalog with its decisions, new programs after the first look, and the API (the desktop
app's alone). The real look at Windows is never run here: a stand-in source is."""

import json

from starlette.testclient import TestClient

from common.paths import data_paths
from hub import programs
from hub.auth import create_client
from hub.db import Database, migrate
from hub.programs import Found
from hub.server import create_app
from hub.settings import HubSettings

PF = "C:\\Program Files"


def resolver(paths):
    return lambda app_id: paths.get(app_id, "")


def test_what_the_start_menu_holds(tmp_path):
    viber = tmp_path / "Viber.exe"
    viber.write_bytes(b"MZ")
    entries = [
        {"Name": "7-Zip File Manager", "AppID": "{6D809377-6AF0-444B-8957-A3773F02200E}\\7-Zip\\7zFM.exe"},
        {"Name": "7-Zip Help", "AppID": "{6D809377-6AF0-444B-8957-A3773F02200E}\\7-Zip\\7-zip.chm"},  # help
        {"Name": "Блокнот", "AppID": "Microsoft.WindowsNotepad_8wekyb3d8bbwe!App"},
        {"Name": "Witcher 3", "AppID": "steam://rungameid/292030"},  # a link
        {"Name": "Settings", "AppID": "ms-settings:"},  # a link too
        {"Name": "Word", "AppID": "Microsoft.Office.WINWORD.EXE.15"},
        {"Name": "Telegram", "AppID": "Telegram.TelegramDesktop"},  # an id only
        {"Name": "Viber", "AppID": "Viber.Desktop", "Target": str(viber)},  # an id, and its shortcut's .exe
        {"Name": "Old tool", "AppID": "C:\\Gone\\tool.exe"},  # left behind by an uninstall
        {"Name": "7-Zip again", "AppID": "{6D809377-6AF0-444B-8957-A3773F02200E}\\7-Zip\\7zFM.exe"},  # the same
        {"Name": "", "AppID": "x"},
    ]
    packages = [{"PackageFamilyName": "Microsoft.WindowsNotepad_8wekyb3d8bbwe", "Version": "11.2607.14.0",
                 "InstallLocation": "C:\\Program Files\\WindowsApps\\Microsoft.WindowsNotepad_11.2607.14.0_x64__8wek",
                 "Publisher": "CN=Microsoft Corporation, O=Microsoft Corporation, L=Redmond"}]  # fmt: skip
    paths = {
        "{6D809377-6AF0-444B-8957-A3773F02200E}\\7-Zip\\7zFM.exe": f"{PF}\\7-Zip\\7zFM.exe",
        "{6D809377-6AF0-444B-8957-A3773F02200E}\\7-Zip\\7-zip.chm": f"{PF}\\7-Zip\\7-zip.chm",
        "Microsoft.Office.WINWORD.EXE.15": f"{PF}\\Microsoft Office\\root\\Office16\\WINWORD.EXE",
    }
    found = programs.from_start(entries, packages, resolver(paths))
    by_name = {f.name: f for f in found}
    assert set(by_name) == {"7-Zip File Manager", "Блокнот", "Word", "Telegram", "Viber"}
    assert (by_name["Viber"].aumid, by_name["Viber"].exe) == ("Viber.Desktop", str(viber))  # its windows carry no id
    assert by_name["7-Zip File Manager"].kind == "desktop" and by_name["7-Zip File Manager"].key.startswith("exe:")
    notepad = by_name["Блокнот"]
    assert (notepad.kind, notepad.publisher, notepad.version) == ("store", "Microsoft Corporation", "11.2607.14.0")
    assert notepad.folder.endswith("__8wek") and notepad.aumid == "Microsoft.WindowsNotepad_8wekyb3d8bbwe!App"
    assert by_name["Word"].exe.endswith("WINWORD.EXE")
    assert by_name["Telegram"].aumid == "Telegram.TelegramDesktop" and not by_name["Telegram"].exe
    assert programs.from_start([{"Name": "x", "AppID": "Microsoft.Office.WINWORD.EXE.15"}], [], resolver({})) != []


def test_publisher_and_version_from_the_registry_and_the_file(tmp_path):
    chrome = tmp_path / "chrome.exe"
    chrome.write_bytes(b"MZ")
    studio = f"{PF}\\Android\\bin\\studio64.exe"
    found = [
        Found(f"exe:{studio.lower()}", "Android Studio", "desktop", exe=studio),
        Found(f"exe:{PF}\\7-zip\\7zfm.exe", "7-Zip File Manager", "desktop", exe=f"{PF}\\7-Zip\\7zFM.exe"),
        Found("aumid:chrome", "Google Chrome", "desktop", aumid="Chrome"),
        Found("exe:c:\\windows\\notepad.exe", "Notepad", "desktop", exe="C:\\Windows\\notepad.exe"),
    ]  # fmt: skip
    installed = [
        {"name": "Android Studio", "publisher": "Google LLC", "version": "2026.1", "location": "",
         "icon": f"{PF}\\Android\\bin\\studio.exe", "uninstall": ""},  # the icon's folder holds studio64.exe
        {"name": "7-Zip 26.02", "publisher": "Igor Pavlov", "version": "26.02", "location": "", "icon": "",
         "uninstall": f'"{PF}\\7-Zip\\Uninstall.exe"'},  # by the uninstaller's folder
        {"name": "Google Chrome", "publisher": "Google LLC", "version": "156", "location": "",
         "icon": f'"{chrome}",0', "uninstall": ""},  # by name; its icon is a real .exe, so it becomes the exe
        {"name": "Windows thing", "publisher": "Nobody", "version": "1", "location": "C:\\Windows", "icon": "",
         "uninstall": "C:\\Windows\\system32\\msiexec.exe /x"},  # Windows' own folder says nothing
    ]  # fmt: skip
    enriched = {f.name: f for f in programs.enrich(found, installed)}
    assert (enriched["Android Studio"].publisher, enriched["Android Studio"].version) == ("Google LLC", "2026.1")
    assert enriched["7-Zip File Manager"].publisher == "Igor Pavlov"
    assert enriched["Google Chrome"].publisher == "Google LLC" and enriched["Google Chrome"].exe == str(chrome)
    assert enriched["Notepad"].publisher == ""
    filled = programs.with_file_info(
        [enriched["Notepad"]], lambda path: {"CompanyName": "Microsoft Corporation", "ProductVersion": "10.0"}
    )
    assert (filled[0].publisher, filled[0].version) == ("Microsoft Corporation", "10.0")
    assert programs.installed_exe('"C:\\A b\\app.exe",0') == "C:\\A b\\app.exe"
    assert programs.installed_exe("C:\\x\\uninst.exe /S") == "C:\\x\\uninst.exe"
    assert programs.installed_exe("MsiExec.exe /I{1234}") == "MsiExec.exe"
    assert programs.installed_exe("C:\\x\\icon.ico") == ""
    template = programs.enrich([Found("exe:g", "Game", "desktop", exe="C:\\G\\game.exe")],
                               [{"name": "Game", "publisher": "P", "version": "%version%"}])  # fmt: skip
    assert template[0].version == ""  # an installer's unfilled template is no version
    assert programs.real_version("0.0.0.0") == "" and programs.real_version(" 2.1 ") == "2.1"


def db_with_tables(tmp_path):
    db = Database(tmp_path / "hub.db")
    db.call(migrate)
    return db


def test_the_catalog_remembers_decisions_and_asks_only_about_new_programs(tmp_path):
    db = db_with_tables(tmp_path)
    seven = Found(f"exe:{PF}\\7-zip\\7zfm.exe", "7-Zip", "desktop", exe=f"{PF}\\7-Zip\\7zFM.exe")
    notepad = Found(
        "aumid:notepad!app", "Блокнот", "store", folder=f"{PF}\\WindowsApps\\Notepad_1", aumid="Notepad!App"
    )
    first = db.call(lambda c: programs.apply_scan(c, [seven, notepad], "t1"))
    assert first["first"] and first["new"] == [] and first["count"] == 2  # the first look asks nothing...
    assert db.call(programs.offer) == {"count": 2}  # ...it offers the list once
    db.call(programs.dismiss_offer)
    assert db.call(programs.offer) is None

    telegram = Found("exe:c:\\t\\telegram.exe", "Telegram", "desktop", exe="C:\\T\\Telegram.exe")
    second = db.call(lambda c: programs.apply_scan(c, [seven, telegram], "t2"))
    assert [p["name"] for p in second["new"]] == ["Telegram"] and second["new"][0]["fresh"]
    assert second["removed"] == 1  # Блокнот is gone
    listed = {p["name"]: p for p in db.call(programs.list_programs)}
    assert listed["Блокнот"]["removed"] and listed["Telegram"]["decision"] is None

    nid = listed["Блокнот"]["id"]
    db.call(lambda c: programs.decide(c, nid, "allow", "app", "t3"))
    back = db.call(lambda c: programs.apply_scan(c, [seven, telegram, notepad], "t4"))
    assert back["new"] == []  # installed again: not new, and decided already
    again = db.call(lambda c: programs.get_program(c, nid))
    assert again["decision"] == "allow" and not again["removed"] and again["id"] == listed["Блокнот"]["id"]

    tid = listed["Telegram"]["id"]
    assert db.call(lambda c: programs.put_off(c, tid))["fresh"] is False  # later: not asked again, still no
    assert db.call(lambda c: programs.get_program(c, tid))["decision"] is None
    try:
        db.call(lambda c: programs.decide(c, tid, "maybe", "app", "t5"))
        raise AssertionError("an unknown decision was taken")
    except ValueError:
        pass


def test_what_the_agent_may_control(tmp_path):
    """A desktop program by its exact .exe (allowing Notepad does not allow System32); a Store app by its folder or
    its windows' id; denied and undecided are no."""
    db = db_with_tables(tmp_path)
    notepad = Found("exe:c:\\windows\\system32\\notepad.exe", "Notepad", "desktop",
                    exe="C:\\Windows\\System32\\notepad.exe", folder="C:\\Windows\\System32")  # fmt: skip
    store = Found("aumid:calc!app", "Калькулятор", "store", folder=f"{PF}\\WindowsApps\\Calc_1", aumid="Calc!App")
    word = Found("exe:word", "Word", "desktop", exe=f"{PF}\\Office\\WINWORD.EXE")
    db.call(lambda c: programs.apply_scan(c, [notepad, store, word], "t1"))
    ids = {p["name"]: p["id"] for p in db.call(programs.list_programs)}
    db.call(lambda c: programs.decide(c, ids["Notepad"], "allow", "app", "t2"))
    db.call(lambda c: programs.decide(c, ids["Калькулятор"], "allow", "app", "t2"))
    db.call(lambda c: programs.decide(c, ids["Word"], "deny", "app", "t2"))

    def allowed(**kwargs):
        found = db.call(lambda c: programs.allowed_program(c, **kwargs))
        return found["name"] if found else None

    assert allowed(exe="c:\\WINDOWS\\system32\\NOTEPAD.EXE") == "Notepad"  # Windows paths ignore case
    assert allowed(exe="C:\\Windows\\System32\\cmd.exe") is None  # the same folder, another program
    assert allowed(exe=f"{PF}\\WindowsApps\\Calc_1\\Calculator.exe") == "Калькулятор"
    assert allowed(aumid="calc!APP") == "Калькулятор"
    assert allowed(exe=f"{PF}\\Office\\WINWORD.EXE") is None  # denied
    assert allowed(exe="C:\\Other\\app.exe") is None  # not in the catalog at all

    # a part of Windows' own package beside an allowed app of it (the input host beside «Архивация Windows»)
    cbs = "C:\\Windows\\SystemApps\\MicrosoftWindows.Client.CBS_cw5n1h2txyewy"
    backup = Found("aumid:cbs!backup", "Архивация Windows", "store", folder=cbs,
                   aumid="MicrosoftWindows.Client.CBS_cw5n1h2txyewy!WindowsBackup")  # fmt: skip
    telegram = Found("aumid:telegram.telegramdesktop", "Telegram", "desktop", aumid="Telegram.TelegramDesktop",
                     exe="C:\\Users\\u\\AppData\\Roaming\\Telegram Desktop\\Telegram.exe")  # fmt: skip
    db.call(lambda c: programs.apply_scan(c, [notepad, store, word, backup, telegram], "t3"))
    ids = {p["name"]: p["id"] for p in db.call(programs.list_programs)}
    for name in ("Архивация Windows", "Telegram"):
        db.call(lambda c, n=name: programs.decide(c, ids[n], "allow", "app", "t4"))
    assert allowed(exe=cbs + "\\TextInputHost.exe") is None
    assert allowed(aumid="MicrosoftWindows.Client.CBS_cw5n1h2txyewy!WindowsBackup") == "Архивация Windows"
    assert allowed(exe="c:\\users\\u\\appdata\\roaming\\telegram desktop\\telegram.exe") == "Telegram"


def stand_in(found):
    return lambda: list(found)


def test_the_api_is_the_apps_alone_and_says_what_is_new(tmp_path):
    app = create_app(HubSettings(paths=data_paths(), llama_swap_url="http://127.0.0.1:9", status_interval_s=0,
                                 process_interval_s=0))  # fmt: skip
    seven = Found(f"exe:{PF}\\7-zip\\7zfm.exe", "7-Zip", "desktop", exe=f"{PF}\\7-Zip\\7zFM.exe", publisher="Igor")
    telegram = Found("exe:c:\\t\\telegram.exe", "Telegram", "desktop", exe="C:\\T\\Telegram.exe")
    with TestClient(app, base_url="http://127.0.0.1:8765") as client:
        client.headers["Authorization"] = "Bearer " + (data_paths().tokens / "app.token").read_text(encoding="utf-8")
        catalog = app.state.programs
        catalog.scan_source = stand_in([seven])
        cline = app.state.db.call(lambda c: create_client(c, "cline"))
        other = {"Authorization": f"Bearer {cline}"}
        assert client.get("/v1/programs", headers=other).status_code == 403  # not even reading the list
        assert client.get("/v1/programs").json()["programs"] == []

        client.portal.call(catalog.scan)
        listed = client.get("/v1/programs").json()
        assert [p["name"] for p in listed["programs"]] == ["7-Zip"] and listed["offer"] == {"count": 1}
        assert listed["scanned_at"]
        assert client.post("/v1/programs/offer/dismiss").status_code == 204
        assert client.get("/v1/programs").json()["offer"] is None

        catalog.scan_source = stand_in([seven, telegram])
        client.portal.call(catalog.scan)
        found = app.state.db.call(
            lambda c: c.execute("SELECT payload FROM events WHERE type = 'programs.found'").fetchall()
        )
        assert [p["name"] for p in json.loads(found[0]["payload"])["programs"]] == ["Telegram"]
        tid = next(p["id"] for p in client.get("/v1/programs").json()["programs"] if p["name"] == "Telegram")

        assert client.put(f"/v1/programs/{tid}", json={"decision": "allow"}, headers=other).status_code == 403
        assert client.put(f"/v1/programs/{tid}", json={"decision": "maybe"}).status_code == 422
        decided = client.put(f"/v1/programs/{tid}", json={"decision": "allow"}).json()
        assert decided["decision"] == "allow" and not decided["fresh"]
        assert client.put(f"/v1/programs/{tid}", json={"decision": None}).json()["decision"] is None
        assert client.put(f"/v1/programs/{tid}", json={"later": True}).json()["fresh"] is False
        assert client.put("/v1/programs/nope", json={"decision": "deny"}).status_code == 404
        logged = app.state.db.call(
            lambda c: c.execute("SELECT payload FROM events WHERE type = 'program.decided'").fetchall()
        )
        assert [json.loads(r["payload"])["decision"] for r in logged] == ["allow", None]

        portable = tmp_path / "Portable Tool.exe"
        portable.write_bytes(b"MZ")  # not a real program: its own name is the file's
        assert client.post("/v1/programs", json={"path": str(tmp_path / "x.txt")}).status_code == 422
        added = client.post("/v1/programs", json={"path": str(portable)})
        assert added.status_code == 201 and added.json()["name"] == "Portable Tool" and added.json()["kind"] == "manual"
        assert client.post("/v1/programs", json={"path": str(portable)}).status_code == 200  # already there
        client.portal.call(catalog.scan)  # a look does not remove a hand-added program
        assert any(p["id"] == added.json()["id"] for p in client.get("/v1/programs").json()["programs"])
        assert client.delete(f"/v1/programs/{tid}").status_code == 404  # only hand-added ones go
        assert client.delete(f"/v1/programs/{added.json()['id']}").status_code == 204

        catalog.scan_source = stand_in([])  # a look that found nothing failed: the catalog stays
        assert client.portal.call(catalog.scan) is None
        assert len(client.get("/v1/programs").json()["programs"]) == 2

        def broken():
            raise RuntimeError("no PowerShell")

        catalog.scan_source = broken
        assert client.portal.call(catalog.scan) is None and catalog.error == "no PowerShell"
        assert client.post("/v1/programs/scan").status_code == 202
