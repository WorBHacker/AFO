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
    # the biggest card's memory agrees; an integrated card's does not - the registry gives it a reserve it lacks
    if known and known[0]["vram_gib"] >= 4:
        assert abs(max(c["dedicated_gib"] for c in cards) - known[0]["vram_gib"]) <= 0.5


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


@windows
def test_vulkans_loader():
    found = hardware.vulkan_loader()
    assert isinstance(found, bool)
    if any(c["vendor"] in {"nvidia", "amd"} and c["vram_gib"] >= 1 for c in hardware.adapters()):
        assert found  # their drivers install it
