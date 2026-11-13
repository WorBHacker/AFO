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
