"""Engines on PyTorch (stage 56): programs on Python that AFO fetches when the person asks - each in an environment
of its own, all on one Python and one PyTorch, all in AFO's folder (<engines>/torch). Separate programs AFO runs,
never imported: the hub has no PyTorch.

What the probe found (56.1, TechDoc «Этап 56»):
- uv (astral-sh/uv) makes the environments, with its own Python (python-build-standalone) - not the person's. Each
  call is given its cache and its Pythons' folder (UV_CACHE_DIR, UV_PYTHON_INSTALL_DIR), `python install` gets
  `--no-bin --no-registry` (else uv puts python3.10.exe in %USERPROFILE%\\.local\\bin and writes the registry), and it
  runs in this folder with `--no-project` (else it takes a pyproject.toml found above it for its project). The
  person's own uv settings (UV_*, uv.toml) are not read.
- PyTorch 2.8.0 + cu128: the RTX 50s need 2.7 + cu128 at least. Its wheels are asked by their URL with their sha256
  (uv checks it): uv keeps what it unpacked in its cache by that URL, and an environment links its files from there -
  a second engine on the same PyTorch costs only its own files (56.1: 0.01 GiB more).
- An engine's caches (Hugging Face, PyTorch's hub, matplotlib) lie in its own folder and it runs offline: else
  transformers writes into the person's profile (56.1).
- What AFO starts here runs in a Windows job: whatever it starts goes with it - at «Стоп», and when the hub stops.

uv's archive and the weights come by AFO's own download (sha256, «Стоп» goes on from where it was), each byte told to
the window; what uv fetches (Python, PyTorch, the libraries, the engine's code) is told by how much the disk took.

The engines (`ENGINES`):
- «Выделение предмета» (`select`): SAM 2.1 large (facebookresearch/sam2, Apache-2.0, code and weights) - an object
  followed through a video from one point (hub/torch_scripts/select.py). Its code - the archive of its commit,
  built without its CUDA extension (`SAM2_BUILD_CUDA=0`: only a mask's small holes are not filled; no compiler), its
  weights from Hugging Face by revision.
- «Замена и дорисовка» (`replace`, stage 58): Wan2.1-VACE-1.3B (Wan-AI, Apache-2.0) through diffusers'
  WanVACEPipeline - an object or the background drawn anew, a frame's edges drawn (hub/torch_scripts/replace.py).
  No code archive: its libraries from PyPI by their pins (the last for Python 3.10 - diffusers 0.41.0, 58). The
  text encoder is city96's umT5-xxl Q4_K_M GGUF (3.7 GB), not the repository's bf16 (11.4 GB): 58.1 saw the same
  frames; when AFO's Wan2.2 holds it already (`near`), it is linked, not fetched.
- «Глубина кадра» (`depth`, stage 59): Depth Anything V2 Small (Apache-2.0; its Large is not for commercial use) -
  each frame's depth, the picture VACE draws a whole video anew by (hub/torch_scripts/depth.py). The same libraries
  as «Замена и дорисовка» - only transformers of them; its own part: a weight more in that one's list would change its
  fingerprint and fetch its 7.7 GB again.
- «Губы под голос» (`lips`, stage 57): MuseTalk 1.5 (TMElyralab; code MIT, weights trained on HDTF - not for
  commercial use, asked first, 62) - a face's mouth made to say a voice (hub/torch_scripts/lips.py). Its code is no
  package: the archive of its commit unpacked by AFO (`unpack`). Its libraries are of 2024 (numpy before 2 - over
  PyTorch's pin); mmcv without its compiled ops (`MMCV_WITH_OPS=0`, no compiler) and chumpy are built here with numpy
  and pip put in before (`tools`). Its face detector's weights are not on Hugging Face - by their author's address.

Two engines put in or taken off at once (58.2): what they share - uv, its Python, PyTorch in uv's cache - is made by
one at a time (`_SHARED`), and the last one taken off leaves the shared folder to an engine still being put in.
"""

from __future__ import annotations

import contextlib
import hashlib
import json
import os
import shutil
import stat
import subprocess
import sys
import tempfile
import threading
import time
import zipfile
from collections.abc import Callable
from pathlib import Path
from typing import Any

from common.i18n import N_, tr
from common.paths import engines_home

from . import engines, hf, winjob
from .engines import EngineError

SOURCES_ENV = "LOCALCHAT_ENGINE_FILES"  # tests point it at a stand-in: <it>/<host>/<path>
UV = {"url": "https://github.com/astral-sh/uv/releases/download/0.13.9/uv-x86_64-pc-windows-msvc.zip",
      "name": "uv-x86_64-pc-windows-msvc.zip", "size": 15567875,
      "sha256": "edd61f7f5728a56cc62455386661860bdfc7bab65872f39b0c25bdcc1d8e1beb"}  # fmt: skip
PYTHON = "3.10.22"  # python-build-standalone, as uv 0.13.9 has it (56.1)
PYTHON_SIZE = 22020096  # what uv fetches of it (56.1: 21.0 MiB)
TORCH = (
    {"name": "torch", "size": 3461427674,
     "url": "https://download-r2.pytorch.org/whl/cu128/torch-2.8.0%2Bcu128-cp310-cp310-win_amd64.whl",
     "sha256": "43938e9a174c90e5eb9e906532b2f1e21532bbfa5a61b65193b4f54714d34f9e"},
    {"name": "torchvision", "size": 7540139,
     "url": "https://download-r2.pytorch.org/whl/cu128/torchvision-0.23.0%2Bcu128-cp310-cp310-win_amd64.whl",
     "sha256": "8ec6f2281ef5d52471b01b99eb04243d0c2cccb1972ba43217085025fe5a6c3f"},
)  # fmt: skip
# what PyTorch takes, as 56.1 had it - the same on every computer; setuptools and wheel: an engine's code is built here
TORCH_PINS = ("filelock==4.1.0", "fsspec==2026.9.0", "jinja2==3.1.6", "markupsafe==3.0.3", "mpmath==1.3.0",
              "networkx==3.4.2", "numpy==2.2.6", "pillow==12.3.0", "sympy==1.14.0", "typing-extensions==4.16.0",
              "setuptools==84.2.1", "wheel==0.48.0")  # fmt: skip
LIBRARIES_SIZE = 40 << 20  # ≈ what those libraries and an engine's own come to from PyPI (56.1)
TORCH_DISK = int(7.05 * 2**30)  # an environment with PyTorch on the disk (56.1: 7.01 GiB) - once, in the uv cache
BASE_DISK = 100 << 20  # uv and its Python (56.1: 0.03 + 0.06 GiB)
SCRIPTS = Path(__file__).with_name("torch_scripts")  # what an engine runs: AFO's own, in the engine's Python
# (58) what diffusers and transformers take, as 58.1 had it (uv pip freeze), but PyTorch's own (TORCH_PINS) - the
# replace engine's and (59) the depth engine's: the same libraries in the same versions
VACE_PINS = ("annotated-doc==0.0.6", "annotated-types==0.8.0", "anyio==4.16.0", "certifi==2026.10.23",
             "charset-normalizer==3.5.2", "click==8.5.1", "colorama==0.4.6", "exceptiongroup==1.3.2", "h11==0.16.0",
             "hf-xet==1.8.0", "httpcore==1.0.9", "httpx==0.28.1", "huggingface-hub==1.33.0", "idna==3.20",
             "importlib-metadata==9.0.1", "markdown-it-py==4.3.0", "mdurl==0.1.2", "packaging==26.3",
             "psutil==7.2.2", "pydantic==2.14.4", "pydantic-core==2.50.5", "pygments==2.21.0", "pyyaml==6.0.3",
             "regex==2026.9.29", "requests==2.34.2", "rich==15.0.0", "safetensors==0.8.0", "shellingham==1.5.4",
             "tokenizers==0.23.3", "tomli==2.5.0", "tqdm==4.70.1", "typer==0.29.1", "typing-inspection==0.4.5",
             "urllib3==2.8.0", "wcwidth==0.9.4", "zipp==4.1.2")  # fmt: skip
ENGINES: dict[str, dict[str, Any]] = {
    "select": {
        "name": N_("Выделение предмета"),
        "about": N_("SAM 2.1: предмет на всём ролике по одному щелчку — видеокарта NVIDIA от 8 ГБ"),
        "licence": "Apache-2.0", "card_gib": 8.0, "script": "select.py",
        "code": {"package": "sam-2", "size": 55952852, "disk": 250 << 20,
                 "url": "https://github.com/facebookresearch/sam2/archive/2b90b9f5ceec907a1c18123530e92e794ad901a4.zip",
                 "sha256": "fe93082a71a885a427894b1eab76341768781b6b58a298a0717e03862097d137"},
        "pins": ("antlr4-python3-runtime==4.9.3", "colorama==0.4.6", "hydra-core==1.3.8", "iopath==0.1.10",
                 "omegaconf==2.3.1", "packaging==26.3", "portalocker==4.4.2", "pyyaml==6.0.3", "tqdm==4.70.1"),
        "build": {"SAM2_BUILD_CUDA": "0", "SAM2_BUILD_ALLOW_ERRORS": "1"},
        "weights": ({"hf": "facebook/sam2.1-hiera-large", "revision": "665f8e2ad61cf5f53d65644ff27c8ee525124610",
                     "path": "sam2.1_hiera_large.pt", "size": 898083611,
                     "sha256": "2647878d5dfa5098f2f8649825738a9345572bae2d4350a2468587ece47dd318"},),
    },
    "replace": {
        "name": N_("Замена и дорисовка"),
        "about": N_("Wan2.1 VACE: заменить предмет или фон, дорисовать края — видеокарта NVIDIA от 16 ГБ"),
        "licence": "Apache-2.0", "card_gib": 15.5, "script": "replace.py", "code": None,
        "packages": ("diffusers==0.41.0", "transformers==5.20.2", "accelerate==1.15.0", "ftfy==6.3.1",
                     "gguf==0.19.0"),
        "pins": VACE_PINS, "build": {},
        "weights": (
            *({"hf": "Wan-AI/Wan2.1-VACE-1.3B-diffusers", "revision": "ec4d2cb062b548996b179d493fdd05340de702a1",
               "into": "Wan2.1-VACE-1.3B", "path": path, "size": size, "sha256": sha256} for path, size, sha256 in (
                ("model_index.json", 408, "078a0c056f61edae212c2e7e78fcb061e48a9bc886c08fb245bf5e165e371ea2"),
                ("scheduler/scheduler_config.json", 751,
                 "070dc020808eec985634b83015c1596425c264d8fa80afe22aa1e02e9e903e6c"),
                ("tokenizer/special_tokens_map.json", 7079,
                 "456b58fd240a06c743a7c2cf8008bec501240d68ebd1fc4018ea569505fea270"),
                ("tokenizer/spiece.model", 4548313, "e3909a67b780650b35cf529ac782ad2b6b26e6d1f849d3fbb6a872905f452458"),
                ("tokenizer/tokenizer.json", 16837459,
                 "20a46ac256746594ed7e1e3ef733b83fbc5a6f0922aa7480eda961743de080ef"),
                ("tokenizer/tokenizer_config.json", 61786,
                 "89bef11d01c80a229079666dd470d536aba87b8eaa00b51cc481ba625be59269"),
                ("transformer/config.json", 662, "58a07adcbe6ae67624d66918ca2ba65615f931c4a26d8f384ae8ac1e68de0608"),
                ("transformer/diffusion_pytorch_model.safetensors.index.json", 114130,
                 "2ed4b491e7a7f3e8185526d5d9a259944871a122a1eec86808bac9194a965d83"),
                ("transformer/diffusion_pytorch_model-00001-of-00002.safetensors", 4999964504,
                 "192804a4e10b5bb0a13f5c224bc4ec9707b3b8cc0def8eea005dbce7c9d6752a"),
                ("transformer/diffusion_pytorch_model-00002-of-00002.safetensors", 2146103400,
                 "dadaf1d5e9ccf826e2c497aa9fc2756a044f606d0aa919e5405a734a121bcf7e"),
                ("vae/config.json", 724, "7a3afcf422b9a9d63e0b6ca7e0d3b5193220a61f5b203bb8417900bc090705e8"),
                ("vae/diffusion_pytorch_model.safetensors", 507591892,
                 "d6e524b3fffede1787a74e81b30976dce5400c4439ba64222168e607ed19e793"),
            )),
            {"hf": "city96/umt5-xxl-encoder-gguf", "revision": "b535255bee98c2b0a59ea7c0ae2dcd0c6657b3b7",
             "path": "umt5-xxl-encoder-Q4_K_M.gguf", "size": 3655145312, "near": "pictures",
             "sha256": "17cf97a5bbbc60a646d6105b832b6f657ce904a8a1ad970e4b59df0c67584a40"},
        ),
    },
    # (59) the depth of each frame - what VACE draws a whole video anew by («Перерисовать всё»); its own part, not a
    # weight of «Замена и дорисовка»: a new weight there would change its fingerprint and fetch its 7.7 GB again
    "depth": {
        "name": N_("Глубина кадра"),
        "about": N_("Depth Anything V2 Small: глубина каждого кадра — для «Перерисовать всё»"),
        "licence": "Apache-2.0", "card_gib": 2.0, "script": "depth.py", "code": None,
        "packages": ("transformers==5.20.2",), "pins": VACE_PINS, "build": {},
        "weights": tuple({"hf": "depth-anything/Depth-Anything-V2-Small-hf",
                          "revision": "5426e4f0f36572d16453bbda7a8389317b1bef99", "into": "Depth-Anything-V2-Small",
                          "path": path, "size": size, "sha256": sha256} for path, size, sha256 in (
            ("config.json", 950, "c56698d3643dde1f83ea2212759e6b31a22b8f827246a36dd007ee8a22b3ff75"),
            ("model.safetensors", 99173660, "3152477ce0d8d6978d76b995120de97cb5b928701fd0f817769f59e249a16b70"),
            ("preprocessor_config.json", 775, "d41175c0d889477ca8fc67191e540faef14baf6275157b3fdecf78469e6bbf84"),
        )),
    },
    "lips": {
        "name": N_("Губы под голос"),
        "about": N_("MuseTalk 1.5: рот человека в кадре — под голос — видеокарта NVIDIA от 10 ГБ"),
        # (62) the code is MIT, but its weights were trained on HDTF - commercial use only by agreement with HDTF's
        # owner (MuseTalk's NOTICE of 2026-11-19): asked before «Скачать»
        "licence": "MuseTalk (HDTF)", "commercial": False, "card_gib": 10.0, "script": "lips.py",
        # not a package: its archive unpacked into <engine>/code, its folder given to the script (`--code`)
        "code": {"unpack": True, "size": 15984282, "disk": 850 << 20,
                 "url": "https://github.com/TMElyralab/MuseTalk/archive/2536eaf06b85f775d8e04b7fd81bf6699155a58d.zip",
                 "sha256": "38b7d9d86dd02425b6648cf5bdd3bb49e7be32bc2d65205d3ec763db4d8762be"},
        # (56.1) mmcv without its compiled ops (`MMCV_WITH_OPS=0`: no compiler) and chumpy are built here, with the
        # tools they use but do not declare - numpy and pip put in before, beside setuptools and wheel
        "tools": ("numpy", "pip"),
        "sources": ({"package": "mmcv-lite", "size": 469456,
                     "url": "https://files.pythonhosted.org/packages/ab/03/6c4fb70f2c547ab96cbf66a235aa4465631f77d045e57d84d941b3e66311/mmcv-2.0.1.tar.gz",
                     "sha256": "2b9b2c23795b3f43cf2ebcaca46552d9b4ef7e903eacdcf7c08ee15195044071"},),
        "packages": ("diffusers==0.30.2", "accelerate==0.28.0", "transformers==4.39.2", "huggingface-hub==0.30.2",
                     "opencv-python==4.9.0.80", "librosa==0.11.0", "soundfile==0.12.1", "einops==0.8.1",
                     "omegaconf==2.4.0", "mmengine==0.10.7", "mmdet==3.1.0", "chumpy==0.70", "mmpose==1.1.0"),
        # what those take, as the probe of 56 had it; numpy before 2 and setuptools before 70 - over PyTorch's
        "pins": ("accelerate==0.28.0", "addict==2.4.0", "audioread==3.1.0", "certifi==2026.10.23", "cffi==2.1.1",
                 "charset-normalizer==3.5.2", "chumpy==0.70", "cloudpickle==3.1.3", "colorama==0.4.6",
                 "contourpy==1.3.2", "cycler==0.12.1", "cython==3.3.1", "decorator==4.4.2", "diffusers==0.30.2",
                 "einops==0.8.1", "fonttools==4.65.0", "huggingface-hub==0.30.2", "idna==3.20",
                 "importlib-metadata==9.0.1", "joblib==1.6.0", "json-tricks==3.17.3", "kiwisolver==1.5.1",
                 "lazy-loader==0.6", "librosa==0.11.0", "llvmlite==0.50.0", "markdown-it-py==4.3.0",
                 "matplotlib==3.10.9", "mdurl==0.1.2", "mmdet==3.1.0", "mmengine==0.10.7", "mmpose==1.1.0",
                 "msgpack==1.2.4", "munkres==3.0.0", "numba==0.68.1", "numpy==1.23.5", "omegaconf==2.4.0",
                 "opencv-python==4.9.0.80", "packaging==26.3", "pip==26.3", "platformdirs==4.12.4", "pooch==1.9.0",
                 "psutil==7.2.2", "pycocotools==2.0.11", "pycparser==3.11", "pygments==2.21.0", "pyparsing==3.3.3",
                 "python-dateutil==3.0.0", "pyyaml==6.0.3", "regex==2026.9.29", "requests==2.34.2", "rich==15.0.0",
                 "safetensors==0.8.0", "scikit-learn==1.7.2", "scipy==1.15.3", "setuptools==69.5.1",
                 "shapely==2.1.2", "six==1.17.0", "soundfile==0.12.1", "soxr==1.1.0", "termcolor==3.3.0",
                 "terminaltables==3.1.10", "threadpoolctl==3.8.0", "tokenizers==0.15.2", "tomli==2.5.0",
                 "tqdm==4.70.1", "transformers==4.39.2", "tzdata==2026.6", "urllib3==2.8.0", "xtcocotools==1.14.3",
                 "yapf==0.43.0", "zipp==4.1.2"),
        "build": {"MMCV_WITH_OPS": "0"},
        "libraries": 260 << 20,  # opencv, scipy, numba, matplotlib, transformers... - more than an engine's usual
        # <models>/lips as MuseTalk's own `models` folder has them (`into`); S3FD - from its author's site, where
        # MuseTalk's face detector would fetch it at its first run
        "weights": (*({"hf": repo, "revision": revision, "into": into, "path": path, "size": size,
                       "sha256": sha256} for repo, revision, into, path, size, sha256 in (
            ("TMElyralab/MuseTalk", "c9ac4cdcedcae22c186fbbf67261961e246c4760", "", "musetalkV15/musetalk.json", 748,
             "5b6923aee04d71692e0e9846c471e0a4ea07a4f686d39545e472bd4ba17e1b47"),
            ("TMElyralab/MuseTalk", "c9ac4cdcedcae22c186fbbf67261961e246c4760", "", "musetalkV15/unet.pth",
             3400074924, "7ebf6c98c181e20838e4c0054e96e944ac60d5d692cc01db42839fe11b787007"),
            ("stabilityai/sd-vae-ft-mse", "31f26fdeee1355a5c34592e401dd41e45d25a493", "sd-vae", "config.json", 547,
             "92d3dfb746fca211a2c9e019e285f8597412211728dce3c5bcf4eda0f2d62e7e"),
            ("stabilityai/sd-vae-ft-mse", "31f26fdeee1355a5c34592e401dd41e45d25a493", "sd-vae",
             "diffusion_pytorch_model.bin", 334707217,
             "1b4889b6b1d4ce7ae320a02dedaeff1780ad77d415ea0d744b476155c6377ddc"),
            ("openai/whisper-tiny", "169d4a4341b33bc18d8881c4b69c2e104e1cc0af", "whisper", "config.json", 1983,
             "ffdccec4f3211f4c63310f2b7098f309fe70f3952cedc5e4d11e43f5b2379b98"),
            ("openai/whisper-tiny", "169d4a4341b33bc18d8881c4b69c2e104e1cc0af", "whisper", "pytorch_model.bin",
             151095027, "9607f98a2b22d9e229ae43c52ecea79dcede9e0c5cfae67e8da6eda86d8aac1d"),
            ("openai/whisper-tiny", "169d4a4341b33bc18d8881c4b69c2e104e1cc0af", "whisper", "preprocessor_config.json",
             184990, "9b5cd03a36fbb8a627c64d98a5b5b126ead95a77720723944487311f0110b666"),
            ("yzd-v/DWPose", "1a7144101628d69ee7a3768d1ee3a094070dc388", "dwpose", "dw-ll_ucoco_384.pth", 406878486,
             "0d9408b13cd863c4e95a149dd31232f88f2a12aa6cf8964ed74d7d97748c7a07"),
            ("ManyOtherFunctions/face-parse-bisent", "0073b233a5a3c4b1377d4dbf49245017938a72b5", "face-parse-bisent",
             "79999_iter.pth", 53289463, "468e13ca13a9b43cc0881a9f99083a430e9c0a38abd935431d1c28ee94b26567"),
            ("ManyOtherFunctions/face-parse-bisent", "0073b233a5a3c4b1377d4dbf49245017938a72b5", "face-parse-bisent",
             "resnet18-5c106cde.pth", 46827520, "5c106cde386e87d4033832f2996f5493238eda96ccf559d1d62760c4de0613f8"),
        )), {"url": "https://www.adrianbulat.com/downloads/python-fan/s3fd-619a316812.pth",
             "path": "s3fd-619a316812.pth", "size": 89843225,
             "sha256": "619a31681264d3f7f7fc7a16a42cbbe8b23f31a256f75a366e5a1bcd59b33543"}),
    },
}  # fmt: skip
VACE = "Wan2.1-VACE-1.3B"  # the replace engine's model: its folder under <models>/replace
UV_TIMEOUT_S = 3600.0  # PyTorch's 3.5 GB over a slow line
CHECK_TIMEOUT_S = 600.0  # the first import of PyTorch reads its 7 GB
RUN_TIMEOUT_S = 3600.0
JOB_MEMORY = 1 << 40  # the job is for «Стоп», not for a limit: an engine takes what its video needs
OUT_OF_CARD = ("CUDA out of memory", "OutOfMemoryError")  # PyTorch's words when the card is too small
LOOK_S = 0.5  # how often what the disk took is told while uv works
SCRATCH = "tmp"  # (56.6) where uv's and an engine's temporary files go - a folder a run, gone after it
DROPPED = {"PYTHONPATH", "PYTHONHOME", "PYTHONSTARTUP", "VIRTUAL_ENV", "CONDA_PREFIX", "PIP_REQUIRE_VIRTUALENV"}
_SHARED = threading.Lock()  # (58.2) uv, its Python, PyTorch in uv's cache: made by one install at a time
_GUARD = threading.Lock()  # over `_PUTTING`, and the shared folder's removal
_PUTTING: set[str] = set()  # the engines being put in now


class Stopped(EngineError):
    """«Стоп»: the program was killed, with all it started."""


def home() -> Path:
    """Where the engines on PyTorch lie: <engines>/torch - AFO's own for the card's quiet (hub/gpu_prefs.py)."""
    return engines_home() / "torch"


def source(url: str) -> str:
    """Where a pinned file is fetched from: its own address, or the tests' stand-in's (`SOURCES_ENV`)."""
    stand_in = os.environ.get(SOURCES_ENV)
    return f"{stand_in.rstrip('/')}/{url.split('://', 1)[1]}" if stand_in else url


def uv_exe(base: Path) -> Path:
    return base / "uv" / "uv.exe"


def python_here(base: Path) -> bool:
    return any((found / "python.exe").is_file() for found in (base / "python").glob(f"cpython-{PYTHON}-*"))


def engine_dir(base: Path, engine: str) -> Path:
    return base / engine


def python_of(base: Path, engine: str) -> Path:
    return engine_dir(base, engine) / "env" / "Scripts" / "python.exe"


def home_of(base: Path, engine: str) -> Path:
    """The engine's own folder for its caches - and where it runs."""
    return engine_dir(base, engine) / "home"


def weight_file(models: Path, engine: str, spec: dict[str, Any]) -> Path:
    """<models>/<engine>/[<into>/]<its path in the repository> (58.2: VACE has config.json in several folders; SAM's
    path has none - its file stays where 56 put it)."""
    return models / engine / spec.get("into", "") / spec["path"]


def part(engine: str) -> dict[str, Any]:
    """An engine as a part of another list («Улучшить ролик»): its name, what it does, its licence - and (62)
    `commercial` False when its licence forbids commercial use, so its download is asked first."""
    spec = ENGINES[engine]
    return {key: spec[key] for key in ("name", "about", "licence", "commercial") if key in spec} | {"torch": True}


def vace_dir(models: Path) -> Path:
    """The replace engine's model, as diffusers reads it: a folder of the repository's files."""
    return models / "replace" / VACE


def depth_dir(models: Path) -> Path:
    """(59) The depth engine's model, as transformers reads it: a folder of the repository's files."""
    return models / "depth" / ENGINES["depth"]["weights"][0]["into"]


def encoder_file(models: Path) -> Path:
    spec = ENGINES["replace"]["weights"][-1]
    return weight_file(models, "replace", spec)


def _linkable(models: Path | None, spec: dict[str, Any]) -> Path | None:
    """(58.2) The same file another part of AFO keeps (`near`: a folder under <models>) - its name and size; its
    sha256 is checked before it is linked. None - none there."""
    if models is None or not spec.get("near"):
        return None
    name = spec["path"].rsplit("/", 1)[-1]
    with contextlib.suppress(OSError):
        for found in (models / spec["near"]).rglob(name):
            if found.is_file() and found.stat().st_size == spec["size"]:
                return found
    return None


def _sha256(path: Path, cancel: threading.Event) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as file:
        while chunk := file.read(1 << 24):
            if cancel.is_set():
                raise hf.Cancelled()
            digest.update(chunk)
    return digest.hexdigest()


def script_of(engine: str) -> Path:
    """What the engine runs; a stand-in of the tests is given its own."""
    return SCRIPTS / ENGINES[engine]["script"]


def _named(pin: str) -> str:
    return pin.split("==", 1)[0].strip().lower()


def pins_of(engine: str) -> list[str]:
    """PyTorch's pins and the engine's own - (57) its own of the same library over PyTorch's (MuseTalk: numpy before
    2, setuptools before 70); an engine with none of them has [*TORCH_PINS, *its pins], as before."""
    own = ENGINES[engine]["pins"]
    names = {_named(p) for p in own}
    return [p for p in TORCH_PINS if _named(p) not in names] + list(own)


def tools_of(engine: str) -> list[str]:
    """What is put in with PyTorch, before the engine's own: setuptools and wheel - an engine's code is built here,
    without isolation - and (57) what else its builds use but do not declare (`tools`)."""
    wanted = {"setuptools", "wheel", *ENGINES[engine].get("tools", ())}
    return [p for p in pins_of(engine) if _named(p) in wanted]


def code_dir(base: Path, engine: str) -> Path:
    """(57) An engine's code unpacked from its archive (`unpack`) - not a package."""
    return engine_dir(base, engine) / "code"


def check_args(engine: str, models: Path) -> list[str]:
    """What the look after the install is given beside `--check`: (57) an unpacked code's folder and the weights'."""
    if not (ENGINES[engine]["code"] or {}).get("unpack"):
        return []
    return ["--code", str(code_dir(home(), engine)), "--models", str(models / engine)]


def fingerprint(engine: str) -> dict[str, Any]:
    """What an engine was put in with: another one in AFO's code - it is put in anew."""
    spec = ENGINES[engine]
    found = {"python": PYTHON, "torch": [t["sha256"] for t in TORCH], "code": (spec["code"] or {}).get("sha256"),
             "pins": pins_of(engine), "weights": [w["sha256"] for w in spec["weights"]]}  # fmt: skip
    if spec.get("packages"):  # (58) only an engine that has them: SAM put in by 56 stays put in
        found["packages"] = list(spec["packages"])
    if spec.get("sources"):  # (57) the same: only an engine that has them
        found["sources"] = [s["sha256"] for s in spec["sources"]]
    return found


def torch_here(base: Path) -> bool:
    """PyTorch is in the uv cache already: an engine put in with it before."""
    try:
        return json.loads((base / "torch.json").read_text(encoding="utf-8")) == [t["sha256"] for t in TORCH]
    except OSError, ValueError:
        return False


def has(models: Path, engine: str) -> bool:
    """The engine here, put in by this AFO's pins, and its weights whole."""
    base = home()
    try:
        ready = json.loads((engine_dir(base, engine) / "ready.json").read_text(encoding="utf-8"))
    except OSError, ValueError:
        return False
    return (ready.get("fingerprint") == fingerprint(engine) and python_of(base, engine).is_file()
            and all(_whole(models, engine, w) for w in ENGINES[engine]["weights"]))  # fmt: skip


def _whole(models: Path | None, engine: str, spec: dict[str, Any]) -> bool:
    """A weights file here at its size: not fetched again."""
    if models is None:
        return False
    path = weight_file(models, engine, spec)
    return path.is_file() and path.stat().st_size == spec["size"]


def size(engine: str, models: Path | None = None) -> int:
    """What «Скачать» brings, in bytes: what is not here yet of uv, its Python and PyTorch, the libraries, the
    engine's code and weights (56.6: weights already whole in `models` are not fetched - not counted)."""
    base, spec = home(), ENGINES[engine]
    shared = (0 if uv_exe(base).is_file() else UV["size"]) + (0 if python_here(base) else PYTHON_SIZE)
    shared += 0 if torch_here(base) else sum(t["size"] for t in TORCH)
    weights = sum(w["size"] for w in spec["weights"] if not _whole(models, engine, w) and not _linkable(models, w))
    code = 0 if code_here(base, engine) else (spec["code"] or {}).get("size", 0)
    return shared + libraries_of(engine) + code + weights


def code_here(base: Path, engine: str) -> bool:
    """(57) An unpacked code here from its pinned archive - not fetched again."""
    code = ENGINES[engine]["code"] or {}
    if not code.get("unpack"):
        return False
    try:
        return (code_dir(base, engine) / ".afo-sha256").read_text(encoding="utf-8").strip() == code["sha256"]
    except OSError:
        return False


def libraries_of(engine: str) -> int:
    """≈ what an engine's libraries come to from PyPI (57: MuseTalk's - several times the usual)."""
    return ENGINES[engine].get("libraries", LIBRARIES_SIZE)


def disk(engine: str) -> int:
    """What it takes on the disk once in: put in - as measured then (56.6: the window said «установлено · 98 МБ»
    of 8.6 GB); not yet - PyTorch unpacked is twice its download (56.1)."""
    base, spec = home(), ENGINES[engine]
    with contextlib.suppress(OSError, ValueError, TypeError):
        ready = json.loads((engine_dir(base, engine) / "ready.json").read_text(encoding="utf-8"))
        if ready.get("fingerprint") == fingerprint(engine) and ready.get("disk"):
            return int(ready["disk"])
    shared = (0 if python_here(base) else BASE_DISK) + (0 if torch_here(base) else TORCH_DISK)
    return shared + (spec["code"] or {}).get("disk", libraries_of(engine) * 3) + sum(w["size"] for w in spec["weights"])


def measured(base: Path, engine: str) -> int:
    """What an engine put in takes on the disk: the folder of the engines on PyTorch - uv, its Python, its cache, the
    environments; a file uv linked in several places once - and the engine's weights."""
    seen, total = set(), 0
    for path in base.rglob("*"):
        with contextlib.suppress(OSError):
            found = path.lstat()
            if stat.S_ISREG(found.st_mode) and (found.st_dev, found.st_ino) not in seen:
                seen.add((found.st_dev, found.st_ino))
                total += found.st_size
    return total + sum(w["size"] for w in ENGINES[engine]["weights"])


def usable(engine: str, machine: dict[str, Any]) -> str:
    """Why the engine cannot work on this computer, in words; '' - it can. PyTorch here is CUDA's: an NVIDIA card."""
    need = ENGINES[engine]["card_gib"]
    if machine.get("build") != "cuda12" or float(machine.get("memory_gib") or 0) < need:
        return tr("нужна видеокарта NVIDIA от {gib} ГБ", gib=round(need))
    return ""


# --- the programs ---


def command(program: Path) -> list[str]:
    """How uv is started; a stand-in of the tests is started its own way."""
    return [str(program)]


def _env(add: dict[str, str]) -> dict[str, str]:
    """The hub's environment without what would make a program take another Python or uv's other settings."""
    kept = {k: v for k, v in os.environ.items() if k.upper() not in DROPPED and not k.upper().startswith("UV_")}
    return kept | add


def uv_env(base: Path, add: dict[str, str] | None = None) -> dict[str, str]:
    return _env({"UV_CACHE_DIR": str(base / "cache"), "UV_PYTHON_INSTALL_DIR": str(base / "python"),
                 "UV_NO_CONFIG": "1", "UV_PYTHON_PREFERENCE": "only-managed", "UV_PYTHON_DOWNLOADS": "manual",
                 "UV_LINK_MODE": "hardlink", "UV_NO_PROGRESS": "1", **(add or {})})  # fmt: skip


def engine_env(base: Path, engine: str) -> dict[str, str]:
    """An engine's run: its caches in its folder, nothing fetched (its weights lie in AFO's), UTF-8."""
    place = home_of(base, engine)
    return _env({"HF_HOME": str(place / "hf"), "TORCH_HOME": str(place / "torch"), "XDG_CACHE_HOME": str(place),
                 "MPLCONFIGDIR": str(place / "matplotlib"), "HF_HUB_OFFLINE": "1", "TRANSFORMERS_OFFLINE": "1",
                 "PYTHONNOUSERSITE": "1", "PYTHONIOENCODING": "utf-8", "PYTHONUTF8": "1"})  # fmt: skip


def _tail(text: str) -> str:
    return " ".join(text.split())[-300:]


def _started(args: list[str], *, env: dict[str, str], cwd: Path, cancel: threading.Event, timeout: float,
             failed: str, name: str, short: tuple[str, ...] = (), scratch: Path) -> str:  # fmt: skip
    """A program once (blocking), in a Windows job: what it said. `cancel` set, or the time out - the job is killed,
    with everything the program started. It failed - `failed` with its words ({name}, {said}); the card too small
    (`short`) - said so. Its temporary files (TEMP, TMP) - in a folder of its own under `scratch`, in AFO's folder,
    gone after it (56.6: SAM's frames of a run killed by ✕ stayed in the person's TEMP)."""
    scratch.mkdir(parents=True, exist_ok=True)
    own = Path(tempfile.mkdtemp(dir=scratch))
    try:
        return _once(args, env=env | {"TEMP": str(own), "TMP": str(own), "TMPDIR": str(own)}, cwd=cwd, cancel=cancel,
                     timeout=timeout, failed=failed, name=name, short=short)  # fmt: skip
    finally:
        engines.remove_tree(own)


def _once(args: list[str], *, env: dict[str, str], cwd: Path, cancel: threading.Event, timeout: float,
          failed: str, name: str, short: tuple[str, ...]) -> str:  # fmt: skip
    flags = subprocess.CREATE_NO_WINDOW if sys.platform == "win32" else 0
    began = time.monotonic()
    with tempfile.TemporaryFile() as said, winjob.Job(JOB_MEMORY) as job:  # a file, not a pipe: nothing reads one
        try:
            process = subprocess.Popen(args, stdout=said, stderr=subprocess.STDOUT, stdin=subprocess.DEVNULL,
                                       cwd=str(cwd), env=env, creationflags=flags)  # fmt: skip
        except OSError as exc:
            raise EngineError(tr(failed, name=name, said=exc)) from exc
        try:
            job.add(process)
            while process.poll() is None:
                if cancel.is_set() or time.monotonic() - began > timeout:
                    job.kill()
                    process.wait(timeout=30)
                    if cancel.is_set():
                        raise Stopped(tr("остановлено"))
                    raise EngineError(tr("«{name}» не закончило за {minutes} мин", name=name,
                                         minutes=round(timeout / 60)))  # fmt: skip
                time.sleep(0.1)
        finally:
            if process.poll() is None:
                job.kill()
                process.wait(timeout=30)
        said.seek(0)
        text = said.read().decode("utf-8", "replace")
    if process.returncode != 0:
        if any(word in text for word in short):
            raise EngineError(tr("не хватило памяти видеокарты — закройте программы, которые её занимают, или "
                                 "возьмите ролик короче или меньше"))  # fmt: skip
        raise EngineError(tr(failed, name=name, said=_tail(text) or process.returncode))
    return text


def _uv(base: Path, args: list[str], cancel: threading.Event, *, add: dict[str, str] | None = None,
        told: Callable[[float], None] | None = None, takes: int = 0) -> str:  # fmt: skip
    """uv once, in its folder, with its folders; how far it is - what the disk took of the `takes` bytes it will -
    told meanwhile."""
    done = threading.Event()

    def look() -> None:
        before = shutil.disk_usage(base).free
        while not done.wait(LOOK_S):
            with contextlib.suppress(OSError):
                told(min(max(before - shutil.disk_usage(base).free, 0) / takes, 0.99))  # type: ignore[misc]

    watcher = threading.Thread(target=look, daemon=True) if told is not None and takes else None
    if watcher is not None:
        watcher.start()
    try:
        return _started([*command(uv_exe(base)), *args], env=uv_env(base, add), cwd=base, cancel=cancel,
                        timeout=UV_TIMEOUT_S, failed=N_("uv не поставил «{name}»: {said}"), name=args[0],
                        scratch=base / SCRATCH)  # fmt: skip
    finally:
        done.set()
        if watcher is not None:
            watcher.join()


def _fetch_uv(base: Path, cancel: threading.Event, report: Callable[[int], None]) -> None:
    """uv's archive (sha256 checked), of it only uv.exe - put in whole."""
    downloads = base / "downloads"
    downloads.mkdir(parents=True, exist_ok=True)
    archive = downloads / UV["name"]
    hf.download(source(UV["url"]), archive, size=UV["size"], sha256=UV["sha256"], token=None, cancel=cancel,
                report=report)  # fmt: skip
    part = base / "uv.part"
    engines.remove_tree(part)
    part.mkdir(parents=True)
    with zipfile.ZipFile(archive) as zf:
        name = next((n for n in zf.namelist() if n.rsplit("/", 1)[-1] == "uv.exe"), None)
        if name is None:
            raise EngineError(tr("в архиве {archive} нет uv.exe", archive=UV["name"]))
        (part / "uv.exe").write_bytes(zf.read(name))
    engines.remove_tree(base / "uv")
    part.replace(base / "uv")


def install(engine: str, models: Path, cancel: threading.Event, report: Callable[[int, int], None]) -> None:
    """The engine put in (blocking): uv and its Python once, the engine's environment - PyTorch, the libraries by
    their pins, its code -, its weights, then a look that it starts and sees the card. «Стоп» (hf.Cancelled): what
    came stays for the next time; the environment is made anew then - from uv's cache, without fetching again."""
    base, spec = home(), ENGINES[engine]
    total, got = size(engine, models), 0
    with _GUARD:  # (58.2) an engine being taken off now leaves the shared folder to this one
        _PUTTING.add(engine)

    def at(n: float) -> None:
        report(min(got + int(n), total), total)

    try:
        place, py = engine_dir(base, engine), python_of(base, engine)
        with _held(_SHARED, cancel):  # uv, its Python, PyTorch: one install at a time makes them
            base.mkdir(parents=True, exist_ok=True)
            if not uv_exe(base).is_file():
                _fetch_uv(base, cancel, at)
                got += UV["size"]
            if not python_here(base):
                _uv(base, ["python", "install", PYTHON, "--no-bin", "--no-registry"], cancel,
                    told=lambda share: at(share * PYTHON_SIZE), takes=BASE_DISK)  # fmt: skip
                got += PYTHON_SIZE
            (place / "ready.json").unlink(missing_ok=True)
            engines.remove_tree(place / "env")
            place.mkdir(parents=True, exist_ok=True)
            wanted = [f"{t['name']} @ {source(t['url'])}#sha256={t['sha256']}" for t in TORCH]
            (place / "requirements.txt").write_bytes(("\n".join([*wanted, *tools_of(engine)]) + "\n").encode())
            (place / "pins.txt").write_bytes(("\n".join(pins_of(engine)) + "\n").encode())
            _uv(base, ["venv", str(place / "env"), "--python", PYTHON, "--no-project"], cancel)
            shared, libraries = torch_here(base), libraries_of(engine)
            bring = (0 if shared else sum(t["size"] for t in TORCH)) + libraries // 2
            _uv(base, ["pip", "install", "--python", str(py), "-r", str(place / "requirements.txt"), "-c",
                       str(place / "pins.txt")], cancel, told=lambda share: at(share * bring),
                takes=(0 if shared else TORCH_DISK) + (libraries // 2) * 3)  # fmt: skip
            got += bring
            (base / "torch.json").write_bytes(json.dumps([t["sha256"] for t in TORCH]).encode())
            engines.remove_tree(base / "downloads")  # uv's archive: here, under the lock - another may fetch it
        code = spec["code"] or {}
        if code.get("unpack"):  # (57) not a package: AFO fetches and unpacks it
            if _unpack(base, engine, code, cancel, at):
                got += code["size"]
            wanted = []
        else:
            wanted = [f"{code['package']} @ {source(code['url'])}#sha256={code['sha256']}"] if code else []
        wanted += [f"{s['package']} @ {source(s['url'])}#sha256={s['sha256']}" for s in spec.get("sources", ())]
        bring = (0 if code.get("unpack") else code.get("size", 0)) + libraries - libraries // 2
        _uv(base, ["pip", "install", "--python", str(py), "--no-build-isolation", "-c", str(place / "pins.txt"),
                   *wanted, *spec.get("packages", ())], cancel, add=spec["build"],
            told=lambda share: at(share * bring), takes=code.get("disk", libraries * 3))  # fmt: skip
        got += bring
        for weight in spec["weights"]:
            if _whole(models, engine, weight):  # whole - not fetched, not counted in `total`
                continue
            path = weight_file(models, engine, weight)
            path.parent.mkdir(parents=True, exist_ok=True)
            near = _linkable(models, weight)
            if near is not None and _sha256(near, cancel) == weight["sha256"]:  # AFO has it: linked, not counted
                path.unlink(missing_ok=True)
                try:
                    os.link(near, path)
                except OSError:
                    shutil.copyfile(near, path)
                continue
            # (57) a file not on Hugging Face (MuseTalk's face detector) - by its own address
            url = hf.file_url(weight["hf"], weight["path"], weight["revision"]) if "hf" in weight else source(
                weight["url"])  # fmt: skip
            hf.download(url, path, size=weight["size"], sha256=weight["sha256"], token=None, cancel=cancel,
                        report=at)  # fmt: skip
            got += weight["size"]
        home_of(base, engine).mkdir(parents=True, exist_ok=True)
        looked = _started([str(py), "-I", str(script_of(engine)), "--check", *check_args(engine, models)],
                          env=engine_env(base, engine),
                          cwd=home_of(base, engine), cancel=cancel, timeout=CHECK_TIMEOUT_S,
                          failed=N_("«{name}» не запустилось: {said}"), name=tr(spec["name"]),
                          scratch=engine_dir(base, engine) / SCRATCH)  # fmt: skip
        (place / "ready.json").write_bytes(json.dumps({"fingerprint": fingerprint(engine), "check": _tail(looked),
                                                       "disk": measured(base, engine)}).encode())  # fmt: skip
        report(total, total)
    except Stopped:
        raise hf.Cancelled() from None
    finally:
        with _GUARD:
            _PUTTING.discard(engine)


def _unpack(base: Path, engine: str, code: dict[str, Any], cancel: threading.Event,
            report: Callable[[int], None]) -> bool:  # fmt: skip
    """(57) An engine's code that is not a package: its archive by AFO's own download (sha256; «Стоп» goes on from
    where it was), unpacked into <engine>/code without the archive's one top folder. Whether it was fetched now - one
    unpacked from the same archive before is kept."""
    place, target = engine_dir(base, engine), code_dir(base, engine)
    if code_here(base, engine):
        return False
    archive = place / "downloads" / code["url"].rsplit("/", 1)[-1]
    archive.parent.mkdir(parents=True, exist_ok=True)
    hf.download(source(code["url"]), archive, size=code["size"], sha256=code["sha256"], token=None, cancel=cancel,
                report=report)  # fmt: skip
    part = place / "code.part"
    engines.remove_tree(part)
    with zipfile.ZipFile(archive) as zf:
        zf.extractall(part)  # its names: ".." and drives taken off by zipfile itself
    found = list(part.iterdir())
    inner = found[0] if len(found) == 1 and found[0].is_dir() else part
    engines.remove_tree(target)
    inner.replace(target)
    engines.remove_tree(part)
    (target / ".afo-sha256").write_bytes(code["sha256"].encode())
    engines.remove_tree(place / "downloads")
    return True


@contextlib.contextmanager
def _held(lock: threading.Lock, cancel: threading.Event):
    """The lock, waited for while another install holds it; «Стоп» meanwhile - stopped."""
    while not lock.acquire(timeout=0.2):
        if cancel.is_set():
            raise hf.Cancelled()
    try:
        yield
    finally:
        lock.release()


def remove(models: Path, engine: str) -> None:
    """An engine gone from the disk: its environment and weights (its folder under <models>; a file linked from
    another part of AFO - only the link); the last one takes uv, its Python and the cache with it - nothing of the
    engines on PyTorch is left. (58.2) Not while another engine is being put in: that one needs them."""
    base = home()
    engines.remove_tree(engine_dir(base, engine))
    engines.remove_tree(models / engine)
    with _GUARD:
        if not (_PUTTING - {engine}) and not any((engine_dir(base, o) / "env").exists() for o in ENGINES):
            engines.remove_tree(base)


def run(engine: str, args: list[str], cancel: threading.Event, *, timeout: float = RUN_TIMEOUT_S) -> str:
    """The engine once at a piece of work (blocking), with its Python, in its folder: what it said."""
    base = home()
    home_of(base, engine).mkdir(parents=True, exist_ok=True)
    return _started([str(python_of(base, engine)), "-I", str(script_of(engine)), *args],
                    env=engine_env(base, engine), cwd=home_of(base, engine), cancel=cancel, timeout=timeout,
                    failed=N_("«{name}» не справилось: {said}"), name=tr(ENGINES[engine]["name"]),
                    short=OUT_OF_CARD, scratch=engine_dir(base, engine) / SCRATCH)  # fmt: skip


def lips_args(models: Path, frames: Path, voice: Path, out: Path, *, fps: int) -> list[str]:
    """(57) MuseTalk: the mouth of the face in the frames of `frames` (FRAME_NAME) made to say `voice` (a WAV of 16
    kHz) into `out` by the same names - where no face is found, or the voice has ended, as they were."""
    return ["--code", str(code_dir(home(), "lips")), "--models", str(models / "lips"), "--frames", str(frames),
            "--voice", str(voice), "--out", str(out), "--fps", str(fps)]  # fmt: skip


def select_args(models: Path, frames: Path, out: Path, *, at: int, x: float, y: float,
                masks: bool = False) -> list[str]:  # fmt: skip
    """SAM 2.1: the object at (x, y) - shares of the frame's width and height - of frame `at` (from 0) followed
    through the frames of `frames` (FRAME_NAME) into `out` by the same names: it as it is, the rest darkened; (58)
    `masks` - its masks instead (white the object, black the rest)."""
    weights = weight_file(models, "select", ENGINES["select"]["weights"][0])
    return ["--weights", str(weights), "--frames", str(frames), "--out", str(out), "--at", str(at), "--x",
            f"{x:.4f}", "--y", f"{y:.4f}", *(["--masks"] if masks else [])]  # fmt: skip
