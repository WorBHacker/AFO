"""The engines for a computer that has none yet (stage 10.2, D37, Q19): which llama.cpp build its graphics card wants,
which GitHub release holds it, the archives unpacked safely, and llama-swap's first config."""

import zipfile

import pytest

from common.hardware import build_for
from hub import engines, hf
from hub import llama_config as lc


def release(tag, *names, draft=False):
    return {"tag_name": tag, "draft": draft, "assets": [
        {"name": n, "browser_download_url": f"https://github.com/x/{n}", "size": 10, "digest": "sha256:" + "a" * 64}
        for n in names]}  # fmt: skip


def test_the_build_a_graphics_card_wants():
    rtx = {"name": "RTX 5080", "vendor": "nvidia", "vram_gib": 15.9}
    igpu = {"name": "Intel(R) Graphics", "vendor": "intel", "vram_gib": 2.0}
    radeon = {"name": "Radeon RX 9070", "vendor": "amd", "vram_gib": 16.0}
    assert build_for([rtx, igpu], {"cuda": 13030})["build"] == "cuda-13"
    assert build_for([rtx], {"cuda": 12080})["build"] == "cuda-12"
    assert build_for([rtx], None)["build"] == "vulkan"  # the driver did not answer
    assert build_for([radeon, igpu], None) == {"build": "vulkan", "card": "Radeon RX 9070",
                                               "why": "видеокарта AMD или Intel: сборка с Vulkan"}  # fmt: skip
    assert build_for([igpu], None)["build"] == "cpu"  # an integrated card sharing 2 GB of RAM: no faster
    assert build_for([], None)["build"] == "cpu"


def test_the_release_that_holds_the_build():
    releases = [
        {"tag_name": "v0.7.0", "assets": []},  # not a build
        release("b11950", "llama-b11950-bin-win-cpu-x64.zip"),  # the CUDA archives are not up yet
        release("b11946", "llama-b11946-bin-win-cuda-12.4-x64.zip", "llama-b11946-bin-win-cuda-13.4-x64.zip",
                "llama-b11946-bin-win-cuda-13.4-arm64.zip", "cudart-llama-bin-win-cuda-13.4-x64.zip",
                "cudart-llama-bin-win-cuda-12.4-x64.zip", "llama-b11946-bin-win-vulkan-x64.zip"),
    ]  # fmt: skip
    cuda = engines.pick_llama_cpp(releases, "cuda-13")
    assert cuda["tag"] == "b11946" and [a["name"] for a in cuda["assets"]] == [
        "llama-b11946-bin-win-cuda-13.4-x64.zip", "cudart-llama-bin-win-cuda-13.4-x64.zip"]  # fmt: skip
    assert cuda["assets"][0]["sha256"] == "a" * 64
    assert engines.pick_llama_cpp(releases, "cpu")["tag"] == "b11950"  # the newest that has it
    assert [a["name"] for a in engines.pick_llama_cpp(releases, "vulkan")["assets"]] == [
        "llama-b11946-bin-win-vulkan-x64.zip"]  # fmt: skip
    no_runtime = [release("b1", "llama-b1-bin-win-cuda-12.4-x64.zip")]
    assert engines.pick_llama_cpp(no_runtime, "cuda-12") is None  # useless without the CUDA runtime
    swap = engines.pick_llama_swap(release("v268", "llama-swap_268_linux_amd64.tar.gz",
                                           "llama-swap_268_windows_amd64.zip"))  # fmt: skip
    assert swap["tag"] == "v268" and swap["assets"][0]["name"] == "llama-swap_268_windows_amd64.zip"


def test_the_picture_engines_pinned_build():
    """29.3 (D55): stable-diffusion.cpp's build AFO was checked on (29.1) - not its newest: it publishes several a day,
    and some are broken. CUDA comes with its runtime; Vulkan and the processor's alone."""
    sha = engines.SD_TAG.rsplit("-", 1)[1]
    pinned = release(engines.SD_TAG, f"sd-master-{sha}-bin-win-cuda12-x64.zip", "cudart-sd-bin-win-cu12-x64.zip",
                     f"sd-master-{sha}-bin-win-vulkan-x64.zip", f"sd-master-{sha}-bin-win-cpu-x64.zip",
                     f"sd-master-{sha}-bin-win-rocm-10.1.0-x64.zip",
                     f"sd-master-{sha}-bin-Linux-Ubuntu-26.04-x86_64.zip")  # fmt: skip
    cuda = engines.pick_sd_cpp(pinned, "cuda12")
    assert cuda["tag"] == engines.SD_TAG and [a["name"] for a in cuda["assets"]] == [
        f"sd-master-{sha}-bin-win-cuda12-x64.zip", "cudart-sd-bin-win-cu12-x64.zip"]  # fmt: skip
    assert [a["name"] for a in engines.pick_sd_cpp(pinned, "vulkan")["assets"]] == [
        f"sd-master-{sha}-bin-win-vulkan-x64.zip"]  # fmt: skip
    assert [a["name"] for a in engines.pick_sd_cpp(pinned, "cpu")["assets"]] == [f"sd-master-{sha}-bin-win-cpu-x64.zip"]
    assert engines.pick_sd_cpp(release(engines.SD_TAG, f"sd-master-{sha}-bin-win-cuda12-x64.zip"), "cuda12") is None
    assert engines.pick_sd_cpp(release("master-1060-0000000", "sd-master-0000000-bin-win-cpu-x64.zip"), "cpu") is None


def test_archives_unpack_inside_their_folder_and_the_first_config(tmp_path):
    good = tmp_path / "good.zip"
    with zipfile.ZipFile(good, "w") as zf:
        zf.writestr("llama-server.exe", b"MZ")
        zf.writestr("ggml-cuda.dll", b"MZ")
    folder = tmp_path / "engines" / "llama.cpp" / "b1-cuda-13"
    assert sorted(engines.unpack(good, folder)) == ["ggml-cuda.dll", "llama-server.exe"]
    assert engines.server_in(folder) == folder / "llama-server.exe"
    evil = tmp_path / "evil.zip"
    with zipfile.ZipFile(evil, "w") as zf:
        zf.writestr("../../outside.txt", b"x")
    with pytest.raises(engines.EngineError, match="вне папки"):
        engines.unpack(evil, tmp_path / "x")
    assert not (tmp_path / "outside.txt").exists()

    config = tmp_path / "engines" / "llama-swap" / "config.yaml"
    engines.write_config(config, folder / "llama-server.exe")
    text = config.read_text(encoding="utf-8")
    assert lc.model_blocks(text) == {} and lc.server_command(text) == "${llama_server}"
    assert lc.macros(text)["llama_server"].endswith('b1-cuda-13/llama-server.exe"')
    with pytest.raises(engines.EngineError, match="не трогаю"):
        engines.write_config(config, folder / "llama-server.exe")


def test_an_archive_without_its_sha256_is_not_put_in(tmp_path):
    """The audit (D46): a release GitHub gave no digest for is refused, as ripgrep's is - nothing is downloaded."""
    asset = {"name": "llama-b1-bin-win-cpu-x64.zip", "url": "https://github.com/x/a.zip", "size": 10, "sha256": None}
    with pytest.raises(engines.EngineError, match="контрольную сумму"):
        engines.fetch([asset], tmp_path / "dl", tmp_path / "engine", report=lambda n, t: None, cancel=None)
    assert not (tmp_path / "dl").exists()


@pytest.mark.parametrize(
    ("override", "used"),
    [(None, "https://huggingface.co"), ("https://mirror.example/", "https://mirror.example"),
     ("http://127.0.0.1:9001", "http://127.0.0.1:9001"), ("http://mirror.example", "https://huggingface.co"),
     ("ftp://x", "https://huggingface.co")],
)  # fmt: skip
def test_download_addresses_from_the_environment_are_https_or_this_computer(override, used):
    assert hf.safe_base(override, "https://huggingface.co") == used
