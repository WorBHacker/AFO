"""Models already on this computer (step 15.1, D49): GGUF files on the drives, found in the background - the system's
own folders and links to other folders skipped, a model's parts one model, a vision projector with the model beside
it, Ollama's models through its manifests - listed, and added to llama-swap where they lie, without a copy; removing
such a model takes its config entry away and never its file."""

import json
import sys
import threading
import time
from pathlib import Path

import pytest

from hub import llama_config as lc
from hub import model_install as mi
from hub import model_scan
from tests.test_model_install import CONFIG, fake_hub, hub, tiny_gguf, wait_for  # noqa: F401 - the fixtures


def put(path: Path, data: bytes) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(data)
    return path


def test_the_models_on_a_drive(tmp_path):
    root = tmp_path / "D"
    put(root / "models" / "Qwen3-8B-Q4_K_M.gguf", tiny_gguf(120_000))
    put(root / "models" / "deep" / "Big-Q8_0-00001-of-00002.gguf", tiny_gguf(100_000))
    put(root / "models" / "deep" / "Big-Q8_0-00002-of-00002.gguf", b"\1" * 100_000)
    put(root / "lmstudio" / "gemma-3-4b-it-Q4_K_M.gguf", tiny_gguf(110_000))
    put(root / "lmstudio" / "mmproj-gemma-3-4b-it-F16.gguf", tiny_gguf(60_000))
    put(root / "small" / "vocab.gguf", tiny_gguf(2_000))  # too small: a vocabulary, not a model
    put(root / "Windows" / "System32" / "x.gguf", tiny_gguf(120_000))  # the system's own folders are skipped
    put(root / "$Recycle.Bin" / "S-1-5" / "old.gguf", tiny_gguf(120_000))
    put(root / "models" / "broken.gguf", b"not a gguf" * 20_000)  # not GGUF inside
    put(root / "models" / "mtp-Qwen3-8B-Q4_K_M.gguf", tiny_gguf(60_000))  # a speculative draft: no model alone
    put(root / "embed" / "nomic-embed-text-v1.5.Q4_K_M.gguf", tiny_gguf(80_000))  # embeddings: not for chats
    put(root / "work" / ".venv" / "Lib" / "site-packages" / "x" / "y.gguf", tiny_gguf(120_000))  # Python's own
    # a picture model's files (29.3): its diffusion GGUF has no layers in the header, umt5 is a text encoder
    put(root / "pictures" / "z_image_turbo-Q4_K.gguf", tiny_gguf(120_000, layers=0))
    put(root / "pictures" / "umt5-xxl-encoder-Q4_K_M.gguf", tiny_gguf(120_000, layers=24, architecture="t5encoder"))
    seen = []
    found = model_scan.scan([root], min_bytes=50_000, report=lambda folders, models, drive: seen.append(drive))
    by = {m["name"]: m for m in found}
    assert sorted(by) == ["Big-Q8_0", "Qwen3-8B-Q4_K_M", "gemma-3-4b-it-Q4_K_M"]
    big = by["Big-Q8_0"]
    assert Path(big["path"]).name == "Big-Q8_0-00001-of-00002.gguf" and big["size"] == 200_000
    assert len(big["files"]) == 2 and big["quant"] == "Q8_0" and big["layers"] == 8 and big["source"] == "file"
    assert Path(by["gemma-3-4b-it-Q4_K_M"]["projector"]).name == "mmproj-gemma-3-4b-it-F16.gguf"
    assert by["Qwen3-8B-Q4_K_M"]["projector"] == "" and by["Qwen3-8B-Q4_K_M"]["context_trained"] == 16384
    assert seen and set(seen) == {str(root)}  # the window sees it go, and on which drive
    stop = threading.Event()
    stop.set()
    assert model_scan.scan([root], min_bytes=50_000, cancel=stop) == []


@pytest.mark.skipif(sys.platform != "win32", reason="junctions are Windows'")
def test_a_link_to_another_folder_is_not_followed(tmp_path):
    import _winapi

    root = tmp_path / "D"
    put(root / "models" / "One-Q4_K_M.gguf", tiny_gguf(120_000))
    _winapi.CreateJunction(str(root / "models"), str(root / "link"))  # the same folder once more, by a link
    assert [m["name"] for m in model_scan.scan([root], min_bytes=50_000)] == ["One-Q4_K_M"]


def test_ollamas_models_by_their_manifests(tmp_path):
    home = tmp_path / ".ollama" / "models"
    model, lens, other = ("sha256:" + c * 64 for c in "abc")
    put(home / "blobs" / model.replace(":", "-"), tiny_gguf(120_000))
    put(home / "blobs" / lens.replace(":", "-"), tiny_gguf(60_000))
    put(home / "blobs" / other.replace(":", "-"), tiny_gguf(130_000))

    def manifest(*layers):
        return json.dumps({"layers": [{"mediaType": f"application/vnd.ollama.image.{kind}", "digest": digest}
                                      for kind, digest in layers]}).encode()  # fmt: skip

    put(home / "manifests" / "registry.ollama.ai" / "library" / "gemma3" / "4b",
        manifest(("model", model), ("projector", lens), ("template", other)))  # fmt: skip
    put(home / "manifests" / "hf.co" / "unsloth" / "Qwen3-GGUF" / "Q4_K_M", manifest(("model", other)))
    put(
        home / "manifests" / "registry.ollama.ai" / "library" / "gone" / "latest",
        manifest(("model", "sha256:" + "f" * 64)),
    )
    found = {m["name"]: m for m in model_scan.ollama(home)}
    assert sorted(found) == ["gemma3:4b", "hf.co/unsloth/Qwen3-GGUF:Q4_K_M"]  # a blob not there: not a model
    gemma = found["gemma3:4b"]
    assert Path(gemma["path"]).name == "sha256-" + "a" * 64 and Path(gemma["projector"]).name == "sha256-" + "b" * 64
    assert gemma["source"] == "ollama" and gemma["size"] == 120_000 and gemma["layers"] == 8
    assert found["hf.co/unsloth/Qwen3-GGUF:Q4_K_M"]["quant"] == "Q4_K_M"


def test_found_models_are_listed_and_added_where_they_lie(hub, tmp_path, monkeypatch):  # noqa: F811
    drive = tmp_path / "E"
    model = put(drive / "LLM" / "Mine-Q8_0.gguf", tiny_gguf(340_000))
    put(drive / "LLM" / "notes.txt", b"x")
    monkeypatch.setattr(model_scan, "default_roots", lambda: [drive])
    monkeypatch.setattr(model_scan, "MIN_BYTES", 50_000)
    monkeypatch.setattr(model_scan, "ollama_home", lambda: None)
    assert hub.get("/v1/install/found").json()["models"] == []
    assert hub.post("/v1/install/found/scan").json()["job"]["status"] == "scanning"
    deadline = time.monotonic() + 20
    while (shown := hub.get("/v1/install/found").json())["job"]["status"] == "scanning":
        assert time.monotonic() < deadline
        time.sleep(0.05)
    assert shown["job"]["status"] == "done" and [m["name"] for m in shown["models"]] == ["Mine-Q8_0"]
    entry = shown["models"][0]
    assert entry["verdict"] == "gpu" and entry["in_config"] == "" and entry["path"] == str(model)

    job = hub.post("/v1/install/local", json={"path": str(model)}).json()["job"]
    assert job["files"][0]["done"] and not job["own_files"]
    done = wait_for(hub, job["id"], "done", "failed")
    assert done["status"] == "done", done["error"]
    assert done["trial"]["tries"][0]["ok"]
    view = lc.model_view(hub.config.read_text(encoding="utf-8"), done["model_id"])
    assert view["file"] == str(model).replace("\\", "/")  # where it lies: no copy
    assert hub.get("/v1/install/found").json()["models"][0]["in_config"] == done["model_id"]
    again = hub.post("/v1/install/local", json={"path": str(model)})
    assert again.status_code == 409 and "уже" in again.json()["detail"]
    for wrong in (str(drive / "LLM" / "notes.txt"), str(drive / "none.gguf"), "relative.gguf"):
        assert hub.post("/v1/install/local", json={"path": wrong}).status_code == 422

    gone = hub.delete(f"/v1/install/models/{done['model_id']}").json()["job"]
    assert gone["status"] == "removed" and model.read_bytes()[:4] == b"GGUF"  # the person's file stays
    assert hub.config.read_text(encoding="utf-8") == CONFIG
    # what was found is kept until the next search: a hub started again lists it
    hub.app.state.scanner.models = []
    hub.app.state.scanner.load(hub.app.state.db.call(model_scan.saved))
    assert [m["name"] for m in hub.get("/v1/install/found").json()["models"]] == ["Mine-Q8_0"]


def test_an_ollama_model_is_named_by_ollama_not_by_its_file(hub, tmp_path):  # noqa: F811
    """34.2 (2026-11-08, the user's screenshot): Ollama keeps a model as a blob named by its sha256, and an id made of
    the file's name was «sha256-» and 64 characters - in llama-swap's config and in the window. The id comes from the
    name the list shows; a blob added as a file (no name from the list) - from the name in its header."""
    assert mi.local_id("hf.co/IlyaGusev/saiga_nemo_12b-GGUF:Q4_K_M") == "saiga_nemo_12b-gguf-q4_k_m"
    assert mi.local_id("mistral-nemo:latest") == "mistral-nemo" and mi.local_id("gemma3:4b") == "gemma3-4b"
    assert mi.local_id("Qwen3-8B-Q4_K_M") == "qwen3-8b-q4_k_m" and mi.local_id("··") == "model"
    blobs = tmp_path / ".ollama" / "models" / "blobs"
    saiga = put(blobs / ("sha256-" + "a" * 64), tiny_gguf(120_000))
    other = put(blobs / ("sha256-" + "b" * 64), tiny_gguf(120_000))
    name = "hf.co/IlyaGusev/saiga_nemo_12b-GGUF:Q4_K_M"
    job = hub.post("/v1/install/local", json={"path": str(saiga), "name": name}).json()["job"]
    assert (job["model_id"], job["name"]) == ("saiga_nemo_12b-gguf-q4_k_m", name)
    plain = hub.post("/v1/install/local", json={"path": str(other)}).json()["job"]  # «Добавить файл…»: no name
    assert plain["model_id"] == "tiny"  # the header's name, not «sha256-…»
    for each in (job, plain):
        assert wait_for(hub, each["id"], "done", "failed")["status"] == "done"
    assert {"saiga_nemo_12b-gguf-q4_k_m", "tiny"} <= set(lc.model_blocks(hub.config.read_text(encoding="utf-8")))
