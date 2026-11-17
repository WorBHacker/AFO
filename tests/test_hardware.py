"""What decides pictures and videos on this computer (stage 29.2, D55): each card's dedicated and shared memory as
DXGI sees it, NVIDIA's compute capability, Vulkan's loader, the processor's vector units."""

import sys

import pytest

from common import hardware

GIB = 1024**3
windows = pytest.mark.skipif(sys.platform != "win32", reason="Windows' own interfaces")


def test_a_card_as_dxgi_describes_it():
    card = hardware._dxgi_card("Intel(R) Arc(TM) 140V GPU (16GB)", 0x8086, 128 * 1024**2, int(15.8 * GIB), 0)
    assert card == {"name": "Intel(R) Arc(TM) 140V GPU (16GB)", "vendor": "intel", "dedicated_gib": 0.1,
                    "shared_gib": 15.8}  # fmt: skip
    assert hardware._dxgi_card("NVIDIA GeForce RTX 5080", 0x10DE, 16 * GIB, 32 * GIB, 0)["vendor"] == "nvidia"
    assert hardware._dxgi_card("Some card", 0x1AB8, GIB, GIB, 0)["vendor"] == "other"
    # Windows' own software renderer is no card: it draws on the processor
    assert hardware._dxgi_card("Microsoft Basic Render Driver", 0x1414, 0, 8 * GIB, 2) is None


@windows
def test_dxgi_sees_the_cards_the_registry_knows():
    cards = hardware.dxgi_adapters()
    for card in cards:
        assert card["vendor"] in {"nvidia", "amd", "intel", "other"}
        assert card["dedicated_gib"] >= 0 and card["shared_gib"] >= 0
        assert "Basic Render" not in card["name"]
    if not cards:
        return  # a machine without a hardware adapter (a build server)
    assert len({(c["name"], c["dedicated_gib"]) for c in cards}) == len(cards)  # DXGI lists a card twice at times
    known = hardware.adapters()
    for card in known:
        assert any(c["vendor"] == card["vendor"] for c in cards), (card, cards)
    # whatever this computer's registry says of an integrated card's memory (the fix after 0.1.15: the Intel's 37 GB),
    # it has none of its own; the numbers themselves are this computer's - test_integrated_graphics_has_no_video_...
    assert all(c["vram_gib"] == 0.0 for c in known if c.get("integrated"))


def test_integrated_graphics_has_no_video_memory_of_its_own():
    """The fix after 0.1.15, the user's main computer: after a restart Windows' registry gives its integrated Intel(R)
    Graphics 37 GB of «its own» memory - the shared memory of 64 GB of RAM - where DXGI says 0.1. It came first in
    the first-run wizard and in «Проверить систему» («Intel(R) Graphics — 37 ГБ»), and had NVIDIA's driver not
    answered, llama.cpp's build would have been Vulkan on it. DXGI's word decides: integrated, no video memory of its
    own; the card AFO runs on is shown first. The numbers are that computer's - the test does not read this one."""
    from app.ui.setup_view import card_rows

    registry = [
        {"name": "Intel(R) Graphics", "vendor": "intel", "vram_gib": 37.0, "driver": "32.0.101.9033"},
        {"name": "NVIDIA GeForce RTX 5080", "vendor": "nvidia", "vram_gib": 15.9, "driver": "32.0.16.2114"},
    ]
    dxgi = [{"name": "NVIDIA GeForce RTX 5080", "vendor": "nvidia", "dedicated_gib": 15.6, "shared_gib": 47.4},
            {"name": "Intel(R) Graphics", "vendor": "intel", "dedicated_gib": 0.1, "shared_gib": 47.4}]  # fmt: skip
    cards = hardware.with_dxgi(registry, dxgi)
    assert [(c["name"], c["vram_gib"], c.get("integrated", False)) for c in cards] == [
        ("NVIDIA GeForce RTX 5080", 15.9, False), ("Intel(R) Graphics", 0.0, True)]  # fmt: skip
    assert card_rows(cards) == ["NVIDIA GeForce RTX 5080 — 15,9 ГБ видеопамяти",
                                "Intel(R) Graphics — память общая с ОЗУ"]  # fmt: skip
    nvidia = {"version": "596.14", "cuda": 13030, "compute": [12, 0]}
    assert hardware.build_for(cards, nvidia)["card"] == "NVIDIA GeForce RTX 5080"
    unanswered = hardware.build_for(cards, None)  # NVIDIA's driver did not answer: Vulkan - on the NVIDIA card
    assert (unanswered["build"], unanswered["card"]) == ("vulkan", "NVIDIA GeForce RTX 5080")
    alone = hardware.with_dxgi(registry[:1], dxgi[1:])  # a computer with the integrated graphics alone
    assert hardware.build_for(alone, None)["build"] == "cpu"
    assert hardware.with_dxgi(registry, [])[0]["vram_gib"] == 37.0  # DXGI did not answer: the registry's word
    other = {"name": "Intel(R) UHD Graphics 770", "vendor": "intel", "vram_gib": 2.0, "driver": ""}  # named otherwise
    assert hardware.with_dxgi([other], dxgi[1:])[0].get("integrated")  # the only Intel card DXGI sees

    # the card AFO runs on first, though another has more memory
    radeon = {"name": "Radeon RX 7900 XTX", "vendor": "amd", "vram_gib": 24.0, "driver": ""}
    both = hardware.with_dxgi([registry[1], radeon], [])
    assert [c["name"] for c in both] == ["Radeon RX 7900 XTX", "NVIDIA GeForce RTX 5080"]
    shown = hardware.running_first(both, hardware.build_for(both, nvidia)["card"])
    assert [c["name"] for c in shown] == ["NVIDIA GeForce RTX 5080", "Radeon RX 7900 XTX"]


@windows
def test_nvidia_says_its_cards_compute_capability():
    driver = hardware.nvidia_driver()
    if driver is None:
        pytest.skip("no NVIDIA driver here")
    major, minor = driver["compute"]
    assert major >= 3 and minor >= 0  # Kepler and newer: what a driver of today still runs


@windows
def test_the_processors_vector_units():
    units = hardware.cpu_features()
    assert set(units) == {"avx", "avx2", "avx512"}
    assert all(isinstance(v, bool) for v in units.values())
    assert units["avx"] >= units["avx2"] >= units["avx512"]  # each one comes with the one before it


RTX4060 = {"name": "NVIDIA GeForce RTX 4060 Laptop GPU", "vendor": "nvidia", "dedicated_gib": 8.0, "shared_gib": 7.9}
GTX1650 = {"name": "NVIDIA GeForce GTX 1650", "vendor": "nvidia", "dedicated_gib": 4.0, "shared_gib": 7.9}
MX130 = {"name": "NVIDIA GeForce MX130", "vendor": "nvidia", "dedicated_gib": 2.0, "shared_gib": 3.9}
RX580 = {"name": "Radeon RX 580", "vendor": "amd", "dedicated_gib": 8.0, "shared_gib": 7.9}
IRIS = {"name": "Intel(R) Iris(R) Xe Graphics", "vendor": "intel", "dedicated_gib": 0.1, "shared_gib": 7.9}
NEW_DRIVER = {"version": "610.88", "cuda": 13030, "compute": [8, 9]}


def test_what_pictures_and_videos_a_computer_gets():
    plan = hardware.pictures_for
    laptop = plan([IRIS, RTX4060], NEW_DRIVER, vulkan=True, ram_gib=16)
    assert laptop | {"why": ""} == {"build": "cuda12", "card": RTX4060["name"], "memory_gib": 8.0, "pictures": "main",
                                    "quality": True, "video": "local", "tiles": True, "why": ""}  # fmt: skip
    # 4 GB: pictures at full size, a video only to try - minutes per second of it (29.1)
    old = plan([GTX1650], {**NEW_DRIVER, "compute": [7, 5]}, vulkan=True, ram_gib=16)
    assert (old["build"], old["pictures"], old["quality"], old["video"]) == ("cuda12", "main", False, "slow")
    assert plan([GTX1650], {**NEW_DRIVER, "compute": [7, 5]}, vulkan=True, ram_gib=8)["video"] == "cloud"
    # Maxwell has no CUDA build of the engine: Vulkan; so has a driver older than CUDA 12
    maxwell = plan([MX130], {**NEW_DRIVER, "compute": [5, 0]}, vulkan=True, ram_gib=8)
    assert (maxwell["build"], maxwell["pictures"], maxwell["video"]) == ("vulkan", "light", "cloud")
    assert plan([RTX4060], {**NEW_DRIVER, "cuda": 11080}, vulkan=True, ram_gib=16)["build"] == "vulkan"
    assert plan([RTX4060], None, vulkan=True, ram_gib=16)["build"] == "vulkan"  # the driver did not answer
    # AMD: Vulkan; a video only to try, its tiles always (without them Vulkan's decoding falls over, 29.1)
    amd = plan([RX580], None, vulkan=True, ram_gib=16)
    assert (amd["build"], amd["pictures"], amd["video"], amd["tiles"]) == ("vulkan", "main", "slow", True)
    # an integrated card works in shared memory - the trial run decides between it and the processor
    thin = plan([IRIS], None, vulkan=True, ram_gib=16)
    assert (thin["build"], thin["card"], thin["memory_gib"], thin["pictures"], thin["video"]) == \
        ("vulkan", IRIS["name"], 7.9, "light", "cloud")  # fmt: skip
    bare = plan([], None, vulkan=False, ram_gib=8)
    assert (bare["build"], bare["card"], bare["memory_gib"], bare["pictures"], bare["video"]) == \
        ("cpu", "", 8, "light", "cloud")  # fmt: skip
    assert plan([RX580], None, vulkan=False, ram_gib=16)["build"] == "cpu"  # no Vulkan loader: nothing to run it
    # the whole of a big card: no tiles for a video (twice as fast there, 29.1)
    big = plan([{**RTX4060, "dedicated_gib": 15.6}], NEW_DRIVER, vulkan=True, ram_gib=64)
    assert (big["video"], big["tiles"]) == ("local", False)
    for p in (laptop, old, maxwell, amd, thin, bare, big):
        assert p["why"]  # said in words


@windows
def test_vulkans_loader():
    found = hardware.vulkan_loader()
    assert isinstance(found, bool)
    if any(c["vendor"] in {"nvidia", "amd"} and c["vram_gib"] >= 1 for c in hardware.adapters()):
        assert found  # their drivers install it
