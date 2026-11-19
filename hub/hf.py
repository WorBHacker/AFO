"""Hugging Face, where local models come from (stage 10.1, D37): search, a repository's GGUF files grouped into
variants (Q4_K_M, Q8_0...), the first bytes of a file for its header, and the download itself.

Only the public API: GET /api/models (search), /api/models/<repo> (licence, gating, parameter count), its tree (files,
sizes and the sha256 Hugging Face publishes), and /<repo>/resolve/main/<file> with Range - a download cut short goes
on from where it stopped, and the file counts only when its sha256 matches. A token (the user's, from «Подключения»)
goes with each request to huggingface.co only; the CDN the files redirect to gets signed links (httpx drops the
header on another host). A gated model's licence is accepted by the person on the site, never here.
"""

from __future__ import annotations

import asyncio
import contextlib
import hashlib
import json
import math
import os
import re
import threading
import time
from collections.abc import Callable, Iterator
from dataclasses import dataclass, field
from pathlib import Path, PurePosixPath
from typing import Any, BinaryIO
from urllib.parse import quote, urlsplit

import httpx

from common import __version__
from common.gguf import GGUFError, header_info, read_header, tokenizer_info
from common.gguf import largest_tensor as largest_in_gguf
from common.i18n import tr

BASE_ENV = "LOCALCHAT_HF_URL"  # tests point it at a stand-in
DEFAULT_BASE = "https://huggingface.co"
TOKEN = "huggingface_token"  # the user's token in the secret store («Подключения», connections.HUGGINGFACE_TOKEN)
CHUNK = 1 << 20
HEADER_LIMIT = 64 << 20  # a GGUF header longer than this is not read over the network
PARTS = 8  # connections at once for a big file (live on 2026-11-05: 4-16 fill a 500 Mbit/s line, one does not)
SEGMENT = 64 << 20  # a piece of a big file: what one connection asks for; a break loses at most this
PARALLEL_MIN = 2 * SEGMENT  # smaller files come over one connection
SEGMENT_TRIES = 3  # a piece whose connection breaks is asked again this many times in all
RETRY_S = 1.0
# the fix after 0.1.16: a piece left to one slow connection is shared - judged after STEAL_AFTER_S, the part taken over
# at least STEAL_MIN (less is not worth a new connection); a free connection looks for one every STEAL_LOOK_S
STEAL_AFTER_S = 2.0
STEAL_MIN = 4 << 20
STEAL_LOOK_S = 0.1
PIECE_READ_S = 30.0  # a piece's connection silent this long is broken: asked again from where it stopped
SEARCH_FIELDS = ("gated", "downloads", "likes", "tags", "lastModified", "pipeline_tag", "gguf")
INFO_FIELDS = (*SEARCH_FIELDS, "cardData", "sha")
# a search's «для чего» (32.5): Hugging Face's tags - many chat models have no pipeline tag, but all have this one; a
# video (48.1) - by words or by a picture: either tag
TASKS = {"chat": "conversational", "code": "code", "vision": "image-text-to-text", "image": "text-to-image",
         "video": ("text-to-video", "image-to-video")}  # fmt: skip
PICTURE_TASKS = ("image", "video")  # models that draw and film (41.2): sd-server's, not llama.cpp's
PICTURE_PIPELINES = ("text-to-image", "image-to-image", "text-to-video", "image-to-video")
BASES = ("quantized", "finetune")  # whose files a repository's are (47.2): not a LoRA's (`adapter`) or a merge's
SORTS = {"downloads": "downloads", "trending": "trendingScore", "likes": "likes", "updated": "lastModified",
         "new": "createdAt"}  # fmt: skip
# the search's other filters (41.2): Hugging Face's - a language tag, who put the files there; the hub's own - how the
# model is built, its licence (Hugging Face takes one licence at a time)
LANGUAGES = ("ru", "en")
AUTHORS = ("ggml-org", "unsloth", "bartowski", "lmstudio-community", "mradermacher")
BUILDS = ("moe", "dense")
LICENCES = ("free", "work")  # free - with no conditions; work - and the companies' own, on their terms
FREE_LICENCES = frozenset({"apache-2.0", "mit", "isc", "unlicense", "cc0-1.0", "mpl-2.0", "zlib", "0bsd", "bsl-1.0",
                           "postgresql", "artistic-2.0", "afl-3.0", "ecl-2.0", "wtfpl"})  # fmt: skip
NOT_FOR_WORK = re.compile(r"(?:^|[-_.])nc(?:[-_.]|$)|research|non-?commercial")
OWN_TERMS = re.compile(r"llama|gemma|qwen|tongyi|deepseek|openrail|gpl|nvidia-open-model|falcon")
# a variant's quantization in its file name: Q4_K_M, IQ3_XXS, UD-Q4_K_XL, Q8_0, MXFP4, BF16...
QUANT = re.compile(r"(?i)(?<![A-Z0-9])((?:UD-)?(?:I?Q\d(?:_[A-Z0-9]+)*|TQ\d_\d|MXFP4(?:_MOE)?|NVFP4|BF16|F16|FP16|F32))"
                   r"(?![A-Z0-9])")  # fmt: skip
SPLIT = re.compile(r"-(\d{5})-of-(\d{5})$")
SHA256 = re.compile(r"[0-9a-f]{64}")
# files beside the model that are not a variant of it: vision projectors (offered apart), speculative drafts, imatrix
# data (imatrix.gguf, model.imatrix.gguf) - a model's own name may say «imatrix» too (32.3: …-iMatrix-NVFP4.gguf)
NOT_MODEL = re.compile(r"(?i)^(mmproj|mtp-|dflash-|eagle\d*-|draft|imatrix)|[._-]imatrix\.gguf$")
# a half of a video model in two (48.2, Wan2.2 A14B): QuantStack's «HighNoise/…-HighNoise-», bullerwins' «_high_noise_»
NOISE = re.compile(r"(?i)(?<![a-z])(high|low)[_-]?noise(?![a-z])")
UNRESTRICTED = re.compile(r"(?i)abliterat|uncensor|heretic|unfilter|unrestrict|refusal")
# a model's size in its repository's name: 27B, 0.8B, 135M - not the active 3B of «30B-A3B», not «E4B» or «8x7B»
NAMED_SIZE = re.compile(r"(?i)(?<![A-Z0-9.])(\d+(?:\.\d+)?)([BM])(?![A-Z0-9])")
SIXTEEN_BITS = {"BF16", "F16", "FP16", "F32"}
# a mixture of experts by its name (41.1): «30B-A3B» - all its weights, then those at work for a token
EXPERTS_NAME = re.compile(r"(?i)(?<![A-Z0-9.])\d+(?:\.\d+)?B-A\d+(?:\.\d+)?B(?![A-Z0-9])")


class HubError(Exception):
    """Hugging Face refused or could not be reached; the message is for the window."""

    def __init__(self, message: str, status: int = 502):
        super().__init__(message)
        self.status = status


class Cancelled(Exception):
    """The download was paused or cancelled; the part on disk stays for later."""


def base_url() -> str:
    return safe_base(os.environ.get(BASE_ENV), DEFAULT_BASE)


def safe_base(override: str | None, default: str) -> str:
    """An address set in the environment (tests' stand-ins) - https, or http to this computer only: what a program is
    downloaded from never goes over the open network unencrypted (D46). Anything else: the default."""
    if override:
        parts = urlsplit(override)
        if parts.scheme == "https" or (parts.scheme == "http" and parts.hostname in ("127.0.0.1", "localhost", "::1")):
            return override.rstrip("/")
    return default.rstrip("/")


def headers(token: str | None = None) -> dict[str, str]:
    out = {"User-Agent": f"AFO/{__version__}"}
    if token:
        out["Authorization"] = f"Bearer {token}"
    return out


def _refused(response: httpx.Response, what: str) -> HubError:
    if response.status_code in (401, 403):
        return HubError(tr("{what}: Hugging Face не пускает — модель закрыта. Примите её лицензию на странице "
                           "модели на huggingface.co и добавьте свой токен в «Подключениях»", what=what),
                        403)  # fmt: skip
    if response.status_code == 404:
        return HubError(tr("{what}: на Hugging Face такого нет", what=what), 404)
    if response.status_code == 429:
        return HubError(tr("{what}: Hugging Face просит подождать (слишком много запросов) — попробуйте позже",
                           what=what), 429)  # fmt: skip
    return HubError(tr("{what}: Hugging Face ответил {status}", what=what, status=response.status_code))


async def _get(http: httpx.AsyncClient, path: str, what: str, token: str | None, **params: Any) -> Any:
    """One API answer. A renamed repository is answered with a 307 to its new name (47.9: Comfy-Org/flux2-klein-4B,
    2026-11) - followed; httpx keeps the token only while the host is Hugging Face's own."""
    try:
        response = await http.get(f"{base_url()}{path}", params=params or None, headers=headers(token),
                                  follow_redirects=True)  # fmt: skip
    except httpx.HTTPError as exc:
        raise HubError(
            tr("{what}: нет связи с Hugging Face ({error})", what=what, error=exc.__class__.__name__)
        ) from exc
    if response.status_code != 200:
        raise _refused(response, what)
    return response.json()


async def whoami(token: str | None) -> str:
    """Whose token it is (the «Проверить» of «Подключения»)."""
    if not token:
        raise HubError(tr("токен не задан"), 422)
    async with httpx.AsyncClient(timeout=20.0) as http:
        found = await _get(http, "/api/whoami-v2", tr("Токен"), token)
    return str(found.get("name") or found.get("fullname") or "?")


def _license(info: dict[str, Any]) -> str:
    card = info.get("cardData") or {}
    found = card.get("license_name") or card.get("license")
    if isinstance(found, list):
        found = ", ".join(str(x) for x in found)
    if not found:
        found = next((t.split(":", 1)[1] for t in info.get("tags") or [] if t.startswith("license:")), "")
    return str(found or "")


def license_kind(name: str) -> str:
    """What a licence lets one do (41.2): «free» - work with the model with no conditions (Apache, MIT, BSD,
    CC-BY); «terms» - a company's own (Llama, Gemma, Qwen): for work too, on its terms; «closed» - research or
    non-commercial: not for work; '' - the hub cannot tell, the model's page says."""
    name = name.strip().lower()
    if not name or name in ("other", "unknown"):
        return ""
    if NOT_FOR_WORK.search(name):
        return "closed"
    if name in FREE_LICENCES or name.startswith(("bsd", "cc-by")):
        return "free"
    return "terms" if OWN_TERMS.search(name) else ""


def named_params(repo: str) -> int:
    """The parameter count a repository's name says - the largest («7B-Instruct-1M» is 7B) - or 0."""
    found = [float(n) * (1e9 if unit.upper() == "B" else 1e6) for n, unit in NAMED_SIZE.findall(repo.split("/")[-1])]
    return round(max(found)) if found else 0


def params_of(info: dict[str, Any]) -> int:
    """A model's parameter count: Hugging Face's, read from one of the repository's GGUF files - unless the name says
    more than twice as many: then it read the vision projector or a draft (32.4: «…-27B-…-MTP-GGUF», 0.46 billion)."""
    read = int((info.get("gguf") or {}).get("total") or 0)
    named = named_params(info.get("id") or info.get("modelId") or "")
    return named if named > 2 * read else read


def bases(info: dict[str, Any]) -> list[str]:
    """The models a repository's files are made of (47.2): Hugging Face's `base_model:quantized:<id>` and
    `base_model:finetune:<id>` tags - a LoRA (`adapter`) or a merge is not the model itself."""
    out: list[str] = []
    for tag in info.get("tags") or []:
        kind, _, name = str(tag).removeprefix("base_model:").partition(":")
        if str(tag).startswith("base_model:") and kind in BASES and name and name not in out:
            out.append(name)
    return out


def experts(info: dict[str, Any], repo: str) -> bool:
    """A mixture of experts (41.1): its architecture says so (qwen3moe), its tags, or its name («30B-A3B») - not every
    repository tags it."""
    architecture = str((info.get("gguf") or {}).get("architecture") or "").lower()
    tags = [str(t).lower() for t in info.get("tags") or []]
    return "moe" in architecture or "moe" in tags or bool(EXPERTS_NAME.search(repo.split("/")[-1]))


def summary(info: dict[str, Any]) -> dict[str, Any]:
    """A repository as a search lists it."""
    repo = info.get("id") or info.get("modelId") or ""
    return {
        "repo": repo,
        "downloads": info.get("downloads") or 0,
        "likes": info.get("likes") or 0,
        "gated": info.get("gated") or False,  # False, "auto" or "manual": the licence is accepted on the site
        "license": _license(info),
        "license_kind": license_kind(_license(info)),
        "updated": (info.get("lastModified") or "")[:10],
        "pipeline": info.get("pipeline_tag") or "",
        "params": params_of({**info, "id": repo}),
        "moe": experts(info, repo),
        "bases": bases(info),
        "unrestricted": bool(UNRESTRICTED.search(repo)) or any(UNRESTRICTED.search(t) for t in info.get("tags") or []),
        "page": f"{base_url()}/{repo}",
    }


async def search(http: httpx.AsyncClient, query: str = "", *, limit: int = 30, token: str | None = None,
                 task: str = "", fewest: int = 0, most: int = 0, sort: str = "downloads", plain: bool = False,
                 language: str = "", author: str = "", build: str = "", licence: str = "") -> list[dict]:  # fmt: skip
    """GGUF repositories matching the words (none: all of them), the most downloaded first - or as `sort` says;
    `task` (TASKS), a parameter count from `fewest` to `most` (0: no bound) and `plain` (none whose refusals were
    taken out) narrow it (32.5), and (41.2) the `language` tag, the `author`, the `build` (BUILDS) and the
    `licence` (LICENCES). Hugging Face bounds the count it read - some it read from a projector - so the hub bounds
    params_of again; the build and the licence are the hub's own: it asks for more than it shows when some may drop.
    A task of several tags (48.1: a video by words or by a picture) - Hugging Face's filters all have to match: a
    search a tag, the models found once each, in the order asked."""
    narrowed = bool(plain or fewest or most or build or licence)
    wanted = TASKS.get(task, ())
    kinds = [wanted] if isinstance(wanted, str) else list(wanted)
    order = SORTS.get(sort, "downloads")
    params: dict[str, Any] = {"sort": order, "direction": "-1", "limit": str(limit * 2 if narrowed else limit),
                              "expand[]": [*SEARCH_FIELDS, *([order] if len(kinds) > 1 and order not in SEARCH_FIELDS
                                                             else [])]}  # fmt: skip
    if query:
        params["search"] = query
    if author in AUTHORS:
        params["author"] = author
    if fewest or most:
        params["num_parameters"] = f"min:{fewest}" + (f",max:{most}" if most else "")
    spoken = [language] if language in LANGUAGES else []
    answers = await asyncio.gather(*(_get(http, "/api/models", tr("Поиск"), token,
                                          **params, filter=["gguf", *([kind] if kind else []), *spoken])
                                     for kind in kinds or [""]))  # fmt: skip
    found: dict[str, dict[str, Any]] = {}
    for answer in answers:
        for info in answer:
            if isinstance(info, dict):
                found.setdefault(str(info.get("id") or info.get("modelId") or ""), info)
    infos = list(found.values())
    if len(answers) > 1:  # two answers, one order: by Hugging Face's own field, as it sorts
        empty = "" if order in ("lastModified", "createdAt") else 0
        infos.sort(key=lambda info: info.get(order) or empty, reverse=True)
    out = []
    for info in infos:
        shown = summary(info)
        count = shown["params"]
        if (plain and shown["unrestricted"]) or (count and (count < fewest or (most and count > most))):
            continue
        if build and shown["moe"] != (build == "moe"):
            continue
        kind = shown["license_kind"]
        if licence and not (kind == "free" or (licence == "work" and kind == "terms")):
            continue
        out.append(shown)
    return out[:limit]


def quant_of(path: str) -> str:
    """The quantization a file name says (upper case), or ''."""
    stem = SPLIT.sub("", PurePosixPath(path).name.removesuffix(".gguf"))
    found = QUANT.findall(stem)
    return found[-1].upper() if found else ""


def variants(files: list[dict[str, Any]]) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """A repository's GGUF files as the model's variants (each its files - one, or the parts of a split one) and its
    vision projectors. The same quantization as one file and as parts: the one file."""
    groups: dict[tuple[str, str, int], list[dict[str, Any]]] = {}
    projectors = []
    for f in files:
        path = f["path"]
        name = PurePosixPath(path).name
        if not name.lower().endswith(".gguf"):
            continue
        if name.lower().startswith("mmproj"):
            projectors.append({"file": path, "size": f["size"], "sha256": f.get("sha256"), "quant": quant_of(path)})
            continue
        if NOT_MODEL.search(name):
            continue
        stem = name.removesuffix(".gguf")
        split = SPLIT.search(stem)
        parts = int(split.group(2)) if split else 1
        key = (str(PurePosixPath(path).parent), SPLIT.sub("", stem), parts)  # one file and its split copy: apart
        groups.setdefault(key, []).append({**f, "part": int(split.group(1)) if split else 0, "parts": parts})
    by_quant: dict[str, dict[str, Any]] = {}
    for (_folder, stem, _count), parts in groups.items():
        parts.sort(key=lambda f: f["part"])
        if parts[0]["parts"] != len(parts):  # a split file with parts missing: not offered
            continue
        quant = quant_of(stem + ".gguf") or stem
        variant = {"quant": quant, "files": [{"path": f["path"], "size": f["size"], "sha256": f.get("sha256")}
                                             for f in parts], "size": sum(f["size"] for f in parts)}  # fmt: skip
        if quant not in by_quant or len(variant["files"]) < len(by_quant[quant]["files"]):
            by_quant[quant] = variant
    out = sorted(by_quant.values(), key=lambda v: v["size"])
    return out, sorted(projectors, key=lambda p: p["size"])


def model_of(path: str) -> str:
    """Which model of a repository a file is (48.2): its path without the quantization and the half's word - lower case;
    a model's variants and a pair's halves share it, unsloth's dev and distilled LTX-2.3 do not."""
    stem = SPLIT.sub("", path.removesuffix(".gguf"))
    found = list(QUANT.finditer(stem))
    if found:
        stem = stem[: found[-1].start()].rstrip("-_.") + stem[found[-1].end() :]
    return NOISE.sub("noise", stem).strip("-_.").lower()


def picture_variants(files: list[dict[str, Any]], *, has: list[str] | tuple[str, ...] = (),
                     lacks: list[str] | tuple[str, ...] = ()) -> list[dict[str, Any]]:  # fmt: skip
    """A repository's variants of the model that draws or films (48.2): the GGUF files of one model (model_of) and one
    quantization - one file (`diffusion-model`), or a pair's halves (HighNoise and LowNoise: the low one the model, the
    high one `high-noise-diffusion-model`; a half without its pair is not offered). `has` and `lacks` - words the path
    has and has not (lower case): a family's model among the repository's others. A split file is not offered:
    sd-server takes one file a part. Several models left - a variant's `quant` names its model too."""
    groups: dict[tuple[str, str], dict[str, dict[str, Any]]] = {}
    for f in files:
        path, low = f["path"], f["path"].lower()
        name = PurePosixPath(path).name
        if not low.endswith(".gguf") or NOT_MODEL.search(name) or SPLIT.search(name.removesuffix(".gguf")):
            continue
        if any(word not in low for word in has) or any(word in low for word in lacks):
            continue
        quant = quant_of(path)
        if quant:
            half = NOISE.search(name) or NOISE.search(path)
            groups.setdefault((model_of(path), quant), {})[half.group(1).lower() if half else ""] = f
    models = {model for model, _ in groups}
    out = []
    for (model, quant), halves in groups.items():
        if "" in halves:
            files_ = [(halves[""], "diffusion-model")]
        elif "high" in halves and "low" in halves:
            files_ = [(halves["low"], "diffusion-model"), (halves["high"], "high-noise-diffusion-model")]
        else:
            continue
        label = quant if len(models) == 1 else f"{PurePosixPath(model).name} {quant}"
        out.append({"quant": label, "model": model, "size": sum(f["size"] for f, _ in files_),
                    "files": [{"path": f["path"], "size": f["size"], "sha256": f.get("sha256"), "role": role}
                              for f, role in files_]})  # fmt: skip
    return sorted(out, key=lambda v: v["size"])


def sha256_of(value: Any) -> str | None:
    """A sha256 as Hugging Face gave it - or None: none given, or hidden (the fix after 0.1.17: a gated repository's
    files listed to one it does not let in carry 64 asterisks in its place)."""
    return value.lower() if isinstance(value, str) and SHA256.fullmatch(value.lower()) else None


async def _tree(http: httpx.AsyncClient, repo: str, token: str | None,
                revision: str = "main") -> tuple[list[dict[str, Any]], bool]:  # fmt: skip
    """A repository's files at a commit - path, size, sha256 (sha256_of) - and whether Hugging Face hid their sums:
    asked without a token it lets in."""
    tree = await _get(http, f"/api/models/{repo}/tree/{revision}", repo, token, recursive="true")
    files, hidden = [], False
    for f in tree:
        if not (isinstance(f, dict) and f.get("type") == "file"):
            continue
        lfs = f.get("lfs") or {}
        given = lfs.get("oid") or lfs.get("sha256")
        files.append({"path": f["path"], "size": int(f.get("size") or 0), "sha256": sha256_of(given)})
        hidden = hidden or (bool(given) and files[-1]["sha256"] is None)
    return files, hidden


async def repository(http: httpx.AsyncClient, repo: str, token: str | None = None) -> dict[str, Any]:
    """A repository: its licence and gating, its parameter count and trained context (when Hugging Face read the
    GGUF), the variants and the vision projectors; `hidden` - Hugging Face hid the files' sha256 (_tree)."""
    if not re.fullmatch(r"[\w.-]+/[\w.-]+", repo):
        raise HubError(tr("имя репозитория — «автор/модель»"), 422)
    info = await _get(http, f"/api/models/{repo}", repo, token, **{"expand[]": list(INFO_FIELDS)})
    files, hidden = await _tree(http, repo, token)
    found, projectors = variants(files)
    gguf = info.get("gguf") or {}
    params = params_of({**info, "id": info.get("id") or repo})
    for v in found:
        v["bits"] = round(v["size"] * 8 / params, 1) if params else None  # bits per weight: how much was kept
    return {**summary({**info, "id": info.get("id") or repo}), "revision": info.get("sha") or "main",
            "params": params, "architecture": gguf.get("architecture") or "",
            "context_trained": int(gguf.get("context_length") or 0), "variants": found,
            "projectors": projectors, "hidden": hidden}  # fmt: skip


async def files(http: httpx.AsyncClient, repo: str, token: str | None = None) -> dict[str, Any]:
    """A repository's files - {path: {size, sha256}} - with its licence and the commit they are at: a picture model
    takes its files from several repositories (D55, 29.3); `hidden` - Hugging Face hid their sha256 (_tree)."""
    if not re.fullmatch(r"[\w.-]+/[\w.-]+", repo):
        raise HubError(tr("имя репозитория — «автор/модель»"), 422)
    info = await _get(http, f"/api/models/{repo}", repo, token, **{"expand[]": list(INFO_FIELDS)})
    found, hidden = await _tree(http, repo, token)
    listed = {f["path"]: {"size": f["size"], "sha256": f["sha256"]} for f in found}
    return {**summary({**info, "id": info.get("id") or repo}), "revision": info.get("sha") or "main", "files": listed,
            "hidden": hidden}  # fmt: skip


async def sums(http: httpx.AsyncClient, repo: str, revision: str, token: str | None) -> dict[str, str | None]:
    """The sha256 of a repository's files at a commit (the fix after 0.1.17): a file of an install whose sum is not
    known - Hugging Face hid it from a look without the token - has it asked before it comes."""
    return {f["path"]: f["sha256"] for f in (await _tree(http, repo, token, revision))[0]}


def file_url(repo: str, path: str, revision: str = "main") -> str:
    return f"{base_url()}/{repo}/resolve/{revision}/{quote(path)}"


async def readable(http: httpx.AsyncClient, repo: str, path: str, token: str | None, revision: str = "main") -> bool:
    """Whether Hugging Face gives this file to this token - or to no token (the fix after 0.1.16): a gated
    repository's only once its terms are accepted on the site by the account the token is of. Its first byte is asked,
    the redirect to the CDN not followed; no answer (no internet, a timeout) - True: the download itself will say."""
    try:
        async with http.stream("GET", file_url(repo, path, revision), headers={**headers(token), "Range": "bytes=0-0"},
                               follow_redirects=False) as response:  # fmt: skip
            return response.status_code not in (401, 403)
    except httpx.HTTPError:
        return True


class _Stream:
    """A response body as a file read forward only (the GGUF reader skips arrays by seeking ahead)."""

    def __init__(self, chunks: Iterator[bytes], limit: int):
        self._chunks = chunks
        self._buffer = b""
        self._read = 0
        self._limit = limit

    def read(self, size: int) -> bytes:
        while len(self._buffer) < size:
            if self._read > self._limit:
                raise GGUFError("the header is longer than AFO reads over the network")
            chunk = next(self._chunks, b"")
            if not chunk:
                break
            self._read += len(chunk)
            self._buffer += chunk
        data, self._buffer = self._buffer[:size], self._buffer[size:]
        return data

    def seek(self, offset: int, whence: int = 0) -> None:
        if whence != 1 or offset < 0:
            raise GGUFError("only forward over the network")
        while offset > 0:
            data = self.read(min(offset, CHUNK))
            if not data:
                raise GGUFError("the file ends inside its header")
            offset -= len(data)


def _first_bytes(repo: str, path: str, token: str | None, revision: str, read: Callable[[BinaryIO], Any]) -> Any:
    """What `read` takes from the first bytes of a file on Hugging Face, read as they come (blocking)."""
    with httpx.Client(follow_redirects=True, timeout=httpx.Timeout(30.0)) as http:
        request_headers = {**headers(token), "Range": f"bytes=0-{HEADER_LIMIT - 1}"}
        try:
            with http.stream("GET", file_url(repo, path, revision), headers=request_headers) as response:
                if response.status_code not in (200, 206):
                    raise _refused(response, path)
                return read(_Stream(response.iter_bytes(256 << 10), HEADER_LIMIT))
        except httpx.HTTPError as exc:
            raise HubError(
                tr("{what}: нет связи с Hugging Face ({error})", what=path, error=exc.__class__.__name__)
            ) from exc


def header(repo: str, path: str, token: str | None = None, revision: str = "main") -> dict[str, Any]:
    """The model's layers, attention heads and trained context from the first bytes of its (first) file - before
    any download, for the memory estimate (blocking: in a thread)."""
    return header_info(_first_bytes(repo, path, token, revision, lambda f: read_header(f, until_tokenizer=True)))


def _safetensors_largest(f: BinaryIO) -> int:
    """The bytes of a safetensors file's largest tensor, from its header: 8 bytes of its length, then JSON - each
    tensor's `data_offsets`."""
    first = f.read(8)
    if len(first) != 8:
        raise GGUFError("the file ends inside its header")
    length = int.from_bytes(first, "little")
    if length > HEADER_LIMIT:
        raise GGUFError("the header is longer than AFO reads over the network")
    text = f.read(length)
    if len(text) != length:
        raise GGUFError("the file ends inside its header")
    table = json.loads(text)
    return max((int(t["data_offsets"][1]) - int(t["data_offsets"][0]) for name, t in table.items()
                if name != "__metadata__"), default=0)  # fmt: skip


def largest_tensor(repo: str, path: str, token: str | None = None, revision: str = "main") -> int:
    """A picture model's encoder's largest tensor, bytes (the fix after 0.1.15), from the first bytes of its file - a
    GGUF or a safetensors one - before any download: sd.cpp stages it on the card beside the model (blocking: in a
    thread)."""
    read = largest_in_gguf if path.lower().endswith(".gguf") else _safetensors_largest
    return _first_bytes(repo, path, token, revision, read)


def tokenizer(repo: str, path: str, token: str | None = None, revision: str = "main") -> dict[str, Any]:
    """A model's vocabulary from the first bytes of its file (42.3): a draft model is paired with a model before it
    is downloaded - its whole header, a few megabytes (blocking: in a thread)."""
    return _first_bytes(repo, path, token, revision, tokenizer_info)


@dataclass
class Progress:
    """A download's state kept between tries: the bytes on disk and their sha256 so far; for a file that comes in
    pieces - the pieces on disk, how far from the start the sha256 has gone, and each piece begun: its bytes on disk
    (0.1.15 step 5: a broken piece goes on from there); `top` - the most the window was shown."""

    done: int = 0
    hasher: Any = field(default_factory=hashlib.sha256)
    pieces: set[int] = field(default_factory=set)
    hashed: int = 0
    partial: dict[int, int] = field(default_factory=dict)
    top: int = 0


def _shown(progress: Progress, report: Callable[[int], None], done: int) -> None:
    """The number for the window never goes down (0.1.15 step 5): the bytes on disk are not counted again; only a
    file thrown away (`_forget_pieces`, a wrong sha256) starts it anew."""
    progress.top = max(progress.top, done)
    report(progress.top)


def _hash_part(part: Path, progress: Progress, report: Callable[[int], None], cancel: threading.Event) -> None:
    progress.hasher, progress.done = hashlib.sha256(), 0
    with open(part, "rb") as f:
        while chunk := f.read(8 * CHUNK):
            if cancel.is_set():
                raise Cancelled()
            progress.hasher.update(chunk)
            progress.done += len(chunk)
            _shown(progress, report, progress.done)


def download(url: str, dest: Path, *, size: int, sha256: str | None, token: str | None,
             report: Callable[[int], None], cancel: threading.Event, progress: Progress | None = None,
             transport: httpx.BaseTransport | None = None) -> None:  # fmt: skip
    """The file into `dest` (blocking: in a thread). It comes in as `<dest>.part` and takes its name only once whole
    and its sha256 matches; a wrong one is thrown away. A big file comes in pieces over several connections
    (_pieces); a small one, or one from a server that ignores Range, over one."""
    progress = progress if progress is not None else Progress()
    if size >= PARALLEL_MIN and PARTS > 1:
        try:
            _pieces(url, dest, size=size, sha256=sha256, token=token, report=report, cancel=cancel,
                    progress=progress, transport=transport)  # fmt: skip
            return
        except _NoRanges:
            _forget_pieces(dest, progress)
    _stream(url, dest, size=size, sha256=sha256, token=token, report=report, cancel=cancel, progress=progress,
            transport=transport)  # fmt: skip


class _NoRanges(Exception):
    """The server sends the whole file whatever part is asked for: pieces cannot come apart."""


def _forget_pieces(dest: Path, progress: Progress) -> None:
    for left in (dest.with_name(dest.name + ".part"), dest.with_name(dest.name + ".parts")):
        with contextlib.suppress(OSError):
            left.unlink()
    progress.hasher, progress.done, progress.pieces, progress.hashed = hashlib.sha256(), 0, set(), 0
    progress.partial, progress.top = {}, 0


def _pieces(url: str, dest: Path, *, size: int, sha256: str | None, token: str | None,
            report: Callable[[int], None], cancel: threading.Event, progress: Progress,
            transport: httpx.BaseTransport | None) -> None:  # fmt: skip
    """A big file in SEGMENT pieces, PARTS of them at once (live on 2026-11-05: one connection to Hugging Face's CDN
    gave 0.6-19 MB/s here, four to sixteen - 60-70, the line's limit). Each piece is written at its place in
    `<dest>.part` (as long as the file from the start); `<dest>.parts` lists the pieces done, so a break - or a new
    hub - asks only for the rest. A broken piece is asked again (SEGMENT_TRIES); the sha256 follows the pieces done
    from the start, reading back what was just written. Only this thread calls `report`."""
    part, marks = dest.with_name(dest.name + ".part"), dest.with_name(dest.name + ".parts")
    count = -(-size // SEGMENT)
    if not progress.pieces:
        progress.pieces = _pieces_on_disk(part, marks, size, count)
        progress.done = sum(_span(i, size)[1] - _span(i, size)[0] + 1 for i in progress.pieces)
        progress.hasher, progress.hashed = hashlib.sha256(), 0
    if not part.exists() or part.stat().st_size != size:
        # the list first: a part as long as the file and no list would read next time as one that came whole over one
        # connection - its zeros as pieces done (32.1)
        marks.write_text(json.dumps(sorted(progress.pieces)), encoding="utf-8")
        with open(part, "ab") as f:
            f.truncate(size)
        progress.partial.clear()  # a part made anew: no piece's bytes are on it
    lock, wake, stop = threading.Lock(), threading.Condition(), threading.Event()
    waiting = [i for i in range(count) if i not in progress.pieces]
    failed: list[BaseException] = []
    coming = progress.partial  # a piece begun -> its bytes on disk from its start; kept between tries (0.1.15 step 5)
    flying: dict[int, list[_Span]] = {}  # a piece on its way -> the connections' parts of it (the fix after 0.1.16)

    def next_span() -> _Span | None:  # under the lock: a piece waiting, or a part of a slow one
        if waiting:
            index = waiting.pop(0)
            first, last = _span(index, size)
            flying[index] = [_Span(index, first + coming.get(index, 0), last + 1)]
            return flying[index][0]
        return _share(flying, time.monotonic())

    def worker() -> None:
        with httpx.Client(follow_redirects=True, timeout=httpx.Timeout(30.0, read=PIECE_READ_S),
                          transport=transport) as http:  # fmt: skip
            while not stop.is_set():
                with lock:
                    span = next_span()
                    over = span is None and not flying
                if over:
                    return
                if span is None:  # the pieces left are on their way and none is slow: look again soon
                    stop.wait(STEAL_LOOK_S)
                    continue
                try:
                    _piece(http, url, part, span, token, cancel, stop, lock)
                except BaseException as exc:  # the main thread raises it
                    with lock:
                        failed.append(exc)
                    stop.set()
                    with wake:
                        wake.notify()
                    return
                with lock:  # a piece is done when all its parts are
                    parts = flying.get(span.index) or []
                    if parts and all(p.left == 0 for p in parts):
                        del flying[span.index]
                        progress.pieces.add(span.index)
                        coming.pop(span.index, None)
                        marks.write_text(json.dumps(sorted(progress.pieces)), encoding="utf-8")
                with wake:
                    wake.notify()

    threads = [threading.Thread(target=worker, daemon=True) for _ in range(min(PARTS, len(waiting)))]
    for t in threads:
        t.start()
    try:
        with open(part, "rb", buffering=0) as f:  # no read-ahead: bytes past the pieces done are not there yet
            while True:
                with lock:
                    ready = progress.pieces.copy()
                    on_way = sum(_on_disk(i, parts, size) for i, parts in flying.items()) + sum(
                        n for i, n in coming.items() if i not in flying)  # fmt: skip
                    progress.done = sum(_span(i, size)[1] - _span(i, size)[0] + 1 for i in ready)
                _shown(progress, report, progress.done + on_way)
                upto = 0  # the bytes from the start that are on disk
                while upto < count and upto in ready:
                    upto += 1
                end = min(upto * SEGMENT, size)
                f.seek(progress.hashed)
                while progress.hashed < end:
                    chunk = f.read(min(8 * CHUNK, end - progress.hashed))
                    progress.hasher.update(chunk)
                    progress.hashed += len(chunk)
                if failed or progress.hashed >= size:
                    break
                if cancel.is_set():
                    stop.set()
                    break
                with wake:
                    wake.wait(0.2)
    finally:
        stop.set()
        for t in threads:
            t.join()
        for index, parts in flying.items():  # a next try goes on from each piece's bytes on disk from its start
            coming[index] = _contiguous(index, parts, size)
    if failed:
        raise failed[0]
    if cancel.is_set() and progress.hashed < size:
        raise Cancelled()
    if sha256 and progress.hasher.hexdigest() != sha256.lower():
        _forget_pieces(dest, progress)
        raise HubError(tr("{file}: файл пришёл повреждённым (sha256 не совпал) — скачается заново", file=dest.name),
                       409)  # fmt: skip
    os.replace(part, dest)
    with contextlib.suppress(OSError):
        marks.unlink()


def _span(index: int, size: int) -> tuple[int, int]:
    """The first and the last byte of a piece."""
    return index * SEGMENT, min((index + 1) * SEGMENT, size) - 1


def _pieces_on_disk(part: Path, marks: Path, size: int, count: int) -> set[int]:
    """The pieces an earlier try left: by its list - or, for a part that came over one connection, the whole pieces
    from its start."""
    if not part.exists():
        return set()
    if marks.exists():
        try:
            listed = json.loads(marks.read_text(encoding="utf-8"))
            if part.stat().st_size == size and isinstance(listed, list):
                return {i for i in listed if isinstance(i, int) and 0 <= i < count}
        except OSError, ValueError:
            pass
        return set()
    return set(range(min(part.stat().st_size, size) // SEGMENT))


@dataclass
class _Span:
    """What one connection asks for of a piece: from `start` to `end` (one past its last byte). `end` comes down when a
    free connection takes the rest over (the fix after 0.1.16); `got` - its bytes written, `moved` - when the last
    came."""

    index: int
    start: int
    end: int
    got: int = 0
    began: float = field(default_factory=time.monotonic)
    moved: float = field(default_factory=time.monotonic)

    @property
    def left(self) -> int:
        return max(self.end - self.start - self.got, 0)


def _share(flying: dict[int, list[_Span]], now: float) -> _Span | None:
    """A free connection's part of the piece whose connection would need longest - over STEAL_AFTER_S, and the part
    worth a new connection (STEAL_MIN): the second half of what it has left, or all but a chunk when it stands still
    (the fix after 0.1.16: sd.cpp's archive waited minutes for one piece over GitHub's 0,6 MB/s while seven connections
    had nothing to do). The slow one keeps a chunk at least past what it wrote: the chunk it may be writing now never
    reaches the part taken. Under the lock."""
    best: tuple[_Span, int] | None = None
    longest = STEAL_AFTER_S
    for parts in flying.values():
        for span in parts:
            if now - span.began < STEAL_AFTER_S:
                continue  # too young to judge
            still = now - span.moved >= STEAL_AFTER_S
            keep = CHUNK if still else max(CHUNK, span.left // 2)
            if span.left - keep < STEAL_MIN:
                continue
            rate = span.got / (now - span.began)
            needs = span.left / rate if rate > 0 and not still else math.inf
            if needs > longest:
                best, longest = (span, keep), needs
    if best is None:
        return None
    span, keep = best
    taken = _Span(span.index, span.start + span.got + keep, span.end)
    span.end = taken.start
    flying[span.index].append(taken)
    return taken


def _on_disk(index: int, parts: list[_Span], size: int) -> int:
    """A piece's bytes on disk: what an earlier try left before its first part, and each part's own."""
    return min(p.start for p in parts) - _span(index, size)[0] + sum(min(p.got, p.end - p.start) for p in parts)


def _contiguous(index: int, parts: list[_Span], size: int) -> int:
    """A piece's bytes on disk from its start without a gap - what a next try goes on from (0.1.15 step 5)."""
    upto = min(p.start for p in parts)
    for p in sorted(parts, key=lambda p: p.start):
        if p.start != upto:
            break
        upto += min(p.got, p.end - p.start)
        if p.got < p.end - p.start:
            break
    return upto - _span(index, size)[0]


def _piece(http: httpx.Client, url: str, part: Path, span: _Span, token: str | None, cancel: threading.Event,
           stop: threading.Event, lock: threading.Lock) -> None:  # fmt: skip
    """One connection's part of a piece at its place in the part, asked again when the connection breaks
    (SEGMENT_TRIES) - from where it broke (0.1.15 step 5); it stops where its end has come down to (the fix after
    0.1.16: a free connection took the rest over)."""
    for attempt in range(SEGMENT_TRIES):
        with lock:
            begin, end = span.start + span.got, span.end
        if begin >= end:  # all of it written by a try before, or taken over
            return
        try:
            ranged = {**headers(token), "Range": f"bytes={begin}-{end - 1}"}
            with http.stream("GET", url, headers=ranged) as response:
                if response.status_code == 200:
                    raise _NoRanges()
                if response.status_code != 206:
                    raise _refused(response, part.name.removesuffix(".part"))
                with open(part, "r+b") as f:
                    f.seek(begin)
                    for chunk in response.iter_bytes(CHUNK):
                        if cancel.is_set() or stop.is_set():
                            raise Cancelled()
                        with lock:  # still its own: the end may have come down meanwhile
                            room = span.left
                        f.write(chunk[:room])
                        with lock:
                            span.got += min(len(chunk), room)
                            span.moved = time.monotonic()
                        if len(chunk) >= room:  # past its end: the next piece's place, or the part taken over
                            break
            if span.left == 0:
                return
        except httpx.HTTPError:
            pass
        if attempt + 1 < SEGMENT_TRIES:
            time.sleep(RETRY_S)
    raise HubError(tr("{file}: связь оборвалась — загрузка продолжится", file=part.name.removesuffix(".part")))


def _stream(url: str, dest: Path, *, size: int, sha256: str | None, token: str | None,
            report: Callable[[int], None], cancel: threading.Event, progress: Progress,
            transport: httpx.BaseTransport | None) -> None:  # fmt: skip
    """The file over one connection. A part left by an earlier try is read again for its sha256 (unless `progress`
    already holds it) and the rest is asked for with Range."""
    part = dest.with_name(dest.name + ".part")
    on_disk = part.stat().st_size if part.exists() else 0
    if size and on_disk > size:
        part.unlink()
        on_disk = 0
    if on_disk != progress.done:
        if on_disk:
            _hash_part(part, progress, report, cancel)
        else:
            progress.hasher, progress.done = hashlib.sha256(), 0
    if not size or progress.done < size:
        request_headers = headers(token)
        if progress.done:
            request_headers["Range"] = f"bytes={progress.done}-"
        timeout = httpx.Timeout(30.0, read=120.0)
        try:
            with httpx.Client(follow_redirects=True, timeout=timeout, transport=transport) as http, \
                    http.stream("GET", url, headers=request_headers) as response:  # fmt: skip
                if response.status_code == 200 and progress.done:  # the range was not taken: from the start
                    progress.hasher, progress.done = hashlib.sha256(), 0
                elif response.status_code not in (200, 206):
                    raise _refused(response, dest.name)
                with open(part, "ab" if progress.done else "wb") as f:
                    for chunk in response.iter_bytes(CHUNK):
                        if cancel.is_set():
                            raise Cancelled()
                        f.write(chunk)
                        progress.hasher.update(chunk)
                        progress.done += len(chunk)
                        _shown(progress, report, progress.done)
        except httpx.HTTPError as exc:
            raise HubError(tr("{file}: связь оборвалась ({error}) — загрузка продолжится", file=dest.name,
                              error=exc.__class__.__name__)) from exc  # fmt: skip
    if size and progress.done != size:
        raise HubError(tr("{file}: пришло {done} байт из {size} — загрузка продолжится", file=dest.name,
                          done=progress.done, size=size))  # fmt: skip
    if sha256 and progress.hasher.hexdigest() != sha256.lower():
        with contextlib.suppress(OSError):
            part.unlink()
        progress.hasher, progress.done, progress.top = hashlib.sha256(), 0, 0  # thrown away: the number anew too
        raise HubError(tr("{file}: файл пришёл повреждённым (sha256 не совпал) — скачается заново", file=dest.name),
                       409)  # fmt: skip
    os.replace(part, dest)
