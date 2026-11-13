"""Stage 43.5-43.6: Windows' «Графика» setting written for programs, and given back exactly; the graphics adapters and
each program's video memory, as Windows has them. The registry is the real one, in a key of the test's own."""

import contextlib
import uuid
import winreg

import pytest

from hub import gpu_prefs
from tests.test_tools import hub  # noqa: F401 - the hub fixture


@pytest.fixture
def key(monkeypatch):
    """A key of this test's own instead of HKCU\\...\\DirectX\\UserGpuPreferences; gone afterwards."""
    path = rf"Software\AFO-tests\{uuid.uuid4().hex}\UserGpuPreferences"
    monkeypatch.setenv(gpu_prefs.KEY_ENV, path)
    yield path
    parent = path.rsplit("\\", 1)[0]
    for sub in (path, parent, parent.rsplit("\\", 1)[0]):  # the last only when no other test's key is in it
        with contextlib.suppress(OSError):
            winreg.DeleteKey(winreg.HKEY_CURRENT_USER, sub)


def test_the_value_keeps_windows_fields():
    """The value is Windows' own: «Key=Value;» fields - only GpuPreference is AFO's to change."""
    assert gpu_prefs.with_preference(None, 1) == "GpuPreference=1;"
    assert gpu_prefs.with_preference("AppStatus=0;", 1) == "AppStatus=0;GpuPreference=1;"
    assert gpu_prefs.with_preference(";SwapEffectUpgradeEnable=1;", 1) == "SwapEffectUpgradeEnable=1;GpuPreference=1;"
    assert gpu_prefs.with_preference("GpuPreference=2;AppStatus=0;", 1) == "GpuPreference=1;AppStatus=0;"
    assert gpu_prefs.with_preference("GpuPreference=1;", None) is None  # nothing left: no value
    assert gpu_prefs.preference("AppStatus=0;GpuPreference=2;") == 2 and gpu_prefs.preference("AppStatus=0;") is None
    assert gpu_prefs.preference(None) is None


def test_programs_go_to_the_integrated_graphics_and_come_back_exactly(key, hub):  # noqa: F811
    """43.5: a program goes to the integrated graphics (GpuPreference=1) - Windows' own fields beside it stay; one the
    person (or Windows) gave a choice is left alone. What was there before AFO is kept in the hub's database; «Вернуть
    как было» gives back exactly that, and only AFO's own values - one the person changed since stays theirs."""
    store = gpu_prefs.Registry()
    store.write(r"C:\Games\game.exe", "AppStatus=0;")  # Windows' own field
    store.write(r"C:\Tools\chosen.exe", "GpuPreference=2;")  # the person's choice
    prefs = gpu_prefs.Prefs(hub.app.state.db, store)

    assert hub.portal.call(prefs.move, r"C:\Apps\app.exe")
    assert hub.portal.call(prefs.move, r"C:\Games\game.exe")
    assert not hub.portal.call(prefs.move, r"C:\Tools\chosen.exe")  # a choice already: not AFO's to change
    assert hub.portal.call(prefs.move, r"C:\Apps\later.exe")
    assert store.read(r"C:\Apps\app.exe") == "GpuPreference=1;"
    assert store.read(r"C:\Games\game.exe") == "AppStatus=0;GpuPreference=1;"
    assert store.read(r"C:\Tools\chosen.exe") == "GpuPreference=2;"
    kept = hub.app.state.db.call(gpu_prefs.load_moved)
    assert kept[r"C:\Games\game.exe"] == {"before": "AppStatus=0;", "wrote": "AppStatus=0;GpuPreference=1;"}
    assert kept[r"C:\Apps\app.exe"] == {"before": None, "wrote": "GpuPreference=1;"}
    assert not hub.portal.call(prefs.move, r"C:\Apps\app.exe")  # moved already: nothing new

    store.write(r"C:\Apps\later.exe", "GpuPreference=2;")  # the person changed it after AFO
    back = hub.portal.call(prefs.give_back)
    assert sorted(back["given_back"]) == [r"C:\Apps\app.exe", r"C:\Games\game.exe"]
    assert back["left"] == [r"C:\Apps\later.exe"]
    assert store.read(r"C:\Apps\app.exe") is None and store.read(r"C:\Games\game.exe") == "AppStatus=0;"
    assert store.read(r"C:\Apps\later.exe") == "GpuPreference=2;" and store.read(r"C:\Tools\chosen.exe") == (
        "GpuPreference=2;")  # fmt: skip
    assert hub.app.state.db.call(gpu_prefs.load_moved) == {}


def test_the_adapters_and_the_programs_video_memory():
    """43.5: the graphics adapters as Windows has them (DXGI): name, maker, LUID, their own memory; which one Windows
    gives a program on «Энергосбережение» and which on «Высокая производительность» - moving programs is worth it only
    when they are two. Each program's video memory on each adapter: Windows' GPU Process Memory counters."""
    adapters = gpu_prefs.adapters()
    assert adapters and all({"name", "vendor", "luid", "dedicated_mib"} <= set(a) for a in adapters)
    choice = gpu_prefs.choice()
    assert set(choice) == {"power_saving", "high_performance", "two"}
    assert choice["two"] == (choice["power_saving"]["luid"] != choice["high_performance"]["luid"])
    memory = gpu_prefs.process_memory()
    assert all(isinstance(pid, int) and luid and mib >= 0 for (pid, luid), mib in memory.items())
    big = [a for a in adapters if a["dedicated_mib"] >= 1024 and a["vendor"] != gpu_prefs.MICROSOFT]
    if big:  # a graphics card: Windows' desktop (dwm) holds memory on the adapter that shows it, or another does
        assert any(luid in {a["luid"] for a in adapters} for (_pid, luid) in memory)


def program(exe, name="", mib=50.0, busy_3d=0.0):
    return {"exe": exe, "name": name or exe.rsplit("\\", 1)[-1], "mib": mib, "busy_3d": busy_3d, "pids": [1]}


FACTS = {"choices": {}, "games": {r"e:\games\delta\delta.exe"}, "values": {}, "moved": set(),
         "game_folders": [r"e:\steamlibrary\steamapps\common" + "\\"], "own": [r"d:\afo" + "\\"],
         "windows": r"c:\windows" + "\\"}  # fmt: skip


def test_which_programs_stay_on_the_card(tmp_path):
    """43.6 (the user's word: every program that is not a game): what stays on the graphics card - games (Windows'
    own list of them, the stores' folders, a program busy drawing 3D now), programs that need the card (CUDA, NVENC,
    3D work, AFO's own engines), Windows' own parts, a program with a choice already; the person's choice first."""
    why = gpu_prefs.why_kept
    assert why(program(r"C:\Users\u\AppData\Roaming\Telegram Desktop\Telegram.exe"), FACTS) is None  # it goes
    assert why(program(r"C:\Program Files\Microsoft VS Code\Code.exe"), FACTS) is None
    assert why(program(r"E:\Games\Delta\delta.exe"), FACTS) == "game"  # Windows knows it as a game
    assert why(program(r"E:\SteamLibrary\steamapps\common\AION2\aion2.exe"), FACTS) == "game"  # a store's folder
    assert why(program(r"E:\Dev\build\Stress.exe", busy_3d=40.0), FACTS) == "game"  # drawing 3D hard now
    assert why(program(r"E:\GameDevelop\atmos\build\Atmos.exe"), FACTS) == "game"  # idle, but its folder says so
    assert why(program(r"E:\X\Binaries\Win64\X-Win64-Shipping.exe"), FACTS) == "game"  # Unreal's build
    unity = tmp_path / "Some" / "Thing.exe"
    unity.parent.mkdir()
    (unity.parent / "UnityPlayer.dll").write_bytes(b"")
    assert why(program(str(unity)), FACTS) == "game"  # Unity's player beside it
    wallpaper = r"E:\SteamLibrary\steamapps\common\wallpaper_engine\wallpaper64.exe"
    assert why(program(wallpaper), FACTS) is None  # Steam's software, not a game
    assert why(program(r"C:\Program Files\obs-studio\bin\64bit\obs64.exe"), FACTS) == "tool"  # NVENC
    assert why(program(r"C:\Program Files\NVIDIA Corporation\NVIDIA App\CEF\NVIDIA Overlay.exe"), FACTS) == "tool"
    assert why(program(r"C:\ComfyUI\python_embeded\python.exe"), FACTS) == "tool"  # CUDA, most likely
    assert why(program(r"D:\AFO\engines\llama.cpp\llama-server.exe"), FACTS) == "tool"
    assert why(program(r"C:\Windows\explorer.exe"), FACTS) == "windows"
    assert why(program(r"C:\Program Files\WindowsApps\Microsoft.WindowsTerminal_1.24\WindowsTerminal.exe"),
               FACTS) == "windows"  # a packaged app: the setting goes by its package, not its file  # fmt: skip
    chosen = {**FACTS, "values": {r"c:\tools\chosen.exe": "GpuPreference=2;", r"e:\x\game.exe": "AppStatus=0;"}}
    assert why(program(r"C:\Tools\chosen.exe"), chosen) == "chosen"
    assert why(program(r"E:\X\game.exe"), chosen) == "game"  # Windows marked it (AppStatus)
    mine = {**FACTS, "choices": {r"c:\apps\keep.exe": "card", r"e:\games\delta\delta.exe": "integrated"}}
    assert why(program(r"C:\Apps\keep.exe"), mine) == "person"
    assert why(program(r"E:\Games\Delta\delta.exe"), mine) is None  # the person moves even a game


def test_the_watcher_moves_programs_on_the_card_and_says_which_wait(key, hub):  # noqa: F811
    """43.6: while «Видеокарта — моделям» is on and there are two adapters, each look at the card moves the programs
    that go (the setting is Windows' own: it holds from their next start - till then they wait); a game stays; the
    switch off moves nothing more and gives nothing back - «Вернуть как было» does; the person's choice of a moved
    program gives that one back at once. AFO's state says what each program is and what the moved ones held."""
    seen = [program(r"C:\Apps\Telegram.exe", "Telegram.exe", 34.0),
            program(r"E:\Games\Delta\delta.exe", "delta.exe", 900.0, busy_3d=60.0)]  # fmt: skip
    watcher = gpu_prefs.Watcher(hub.app.state.db, hub.app.state.bus, gpu_prefs.Prefs(hub.app.state.db),
                                interval_s=0, look=lambda luids: list(seen), facts=lambda: dict(FACTS),
                                choice=lambda: {"two": True, "power_saving": {"name": "Intel"},
                                                "high_performance": {"name": "RTX", "luid": "x", "vendor": 1}},
                                adapters=lambda: [])  # fmt: skip
    state = hub.portal.call(watcher.check)
    assert state["available"] and state["on"] and (state["integrated"], state["card"]) == ("Intel", "RTX")
    rows = {p["exe"]: p for p in state["programs"]}
    assert rows[r"C:\Apps\Telegram.exe"]["where"] == "waits"  # moved, still on the card: a restart frees it
    assert rows[r"E:\Games\Delta\delta.exe"]["where"] == "kept" and rows[r"E:\Games\Delta\delta.exe"]["why"] == "game"
    assert gpu_prefs.Registry().read(r"C:\Apps\Telegram.exe") == "GpuPreference=1;"
    assert gpu_prefs.Registry().read(r"E:\Games\Delta\delta.exe") is None

    seen[:] = [seen[1]]  # Telegram restarted: on the integrated graphics now
    state = hub.portal.call(watcher.check)
    rows = {p["exe"]: p for p in state["programs"]}
    assert rows[r"C:\Apps\Telegram.exe"]["where"] == "moved" and state["freed_mib"] == 34.0

    hub.portal.call(watcher.set_on, False)
    seen.append(program(r"C:\Apps\Code.exe", "Code.exe", 144.0))
    state = hub.portal.call(watcher.check)
    assert not state["on"] and gpu_prefs.Registry().read(r"C:\Apps\Code.exe") is None  # off: nothing more moves
    assert gpu_prefs.Registry().read(r"C:\Apps\Telegram.exe") == "GpuPreference=1;"  # nor given back

    hub.portal.call(watcher.set_on, True)
    hub.portal.call(watcher.choose, r"C:\Apps\Telegram.exe", "card")  # the person: keep it on the card
    assert gpu_prefs.Registry().read(r"C:\Apps\Telegram.exe") is None  # given back at once
    state = hub.portal.call(watcher.check)
    rows = {p["exe"]: p for p in state["programs"]}
    assert rows[r"C:\Apps\Code.exe"]["where"] == "waits" and rows.get(r"C:\Apps\Telegram.exe", {}).get("why") in (
        None, "person")  # fmt: skip

    back = hub.portal.call(watcher.give_back)
    assert back["given_back"] == [r"C:\Apps\Code.exe"] and not back["state"]["on"]  # back, and off
    assert gpu_prefs.Registry().read(r"C:\Apps\Code.exe") is None


def test_the_api(hub):  # noqa: F811
    """43.6: GET /v1/gpu - the state; PUT - the switch; PUT /v1/gpu/programs - the person's choice for a program;
    POST /v1/gpu/give-back - «Вернуть как было». The desktop app only."""
    db = hub.app.state.db  # what is on this computer's card is not this test's business
    hub.app.state.gpu = gpu_prefs.Watcher(db, hub.app.state.bus, gpu_prefs.Prefs(db), interval_s=0,
                                          look=lambda luids: [], facts=lambda: dict(FACTS))  # fmt: skip
    state = hub.get("/v1/gpu").json()
    assert {"available", "on", "programs", "freed_mib"} <= set(state)
    assert hub.put("/v1/gpu", json={"on": False}).json()["on"] is False
    assert hub.put("/v1/gpu", json={"on": True}).json()["on"] is True
    chosen = hub.put("/v1/gpu/programs", json={"exe": r"C:\Apps\x.exe", "where": "card"})
    assert chosen.status_code == 200
    assert hub.put("/v1/gpu/programs", json={"exe": r"C:\Apps\x.exe", "where": "moon"}).status_code == 422
    back = hub.post("/v1/gpu/give-back").json()
    assert back["state"]["on"] is False and back["given_back"] == []
