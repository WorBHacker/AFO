"""Hugging Face, where local models come from (stage 10.1, D37): search, a repository's GGUF files grouped into
variants (Q4_K_M, Q8_0...), the first bytes of a file for its header, and the download itself.

Only the public API: GET /api/models (search), /api/models/<repo> (licence, gating, parameter count), its tree (files,
sizes and the sha256 Hugging Face publishes), and /<repo>/resolve/main/<file> with Range - a download cut short goes
on from where it stopped, and the file counts only when its sha256 matches. A token (the user's, from «Подключения»)
goes with each request to huggingface.co only; the CDN the files redirect to gets signed links (httpx drops the
header on another host). A gated model's licence is accepted by the person on the site, never here.
"""

from __future__ import annotations

import contextlib
import hashlib
import os
import re
import threading
from collections.abc import Callable, Iterator
from dataclasses import dataclass, field
from pathlib import Path, PurePosixPath
from typing import Any
from urllib.parse import quote, urlsplit

import httpx

from common import __version__
from common.gguf import GGUFError, header_info, read_header

BASE_ENV = "LOCALCHAT_HF_URL"  # tests point it at a stand-in
DEFAULT_BASE = "https://huggingface.co"
TOKEN = "huggingface_token"  # the user's token in the secret store («Подключения», connections.HUGGINGFACE_TOKEN)
CHUNK = 1 << 20
HEADER_LIMIT = 64 << 20  # a GGUF header longer than this is not read over the network
SEARCH_FIELDS = ("gated", "downloads", "likes", "tags", "lastModified", "pipeline_tag")
INFO_FIELDS = (*SEARCH_FIELDS, "cardData", "gguf", "sha")
# a variant's quantization in its file name: Q4_K_M, IQ3_XXS, UD-Q4_K_XL, Q8_0, MXFP4, BF16...
QUANT = re.compile(r"(?i)(?<![A-Z0-9])((?:UD-)?(?:I?Q\d(?:_[A-Z0-9]+)*|TQ\d_\d|MXFP4(?:_MOE)?|NVFP4|BF16|F16|FP16|F32))"
                   r"(?![A-Z0-9])")  # fmt: skip
SPLIT = re.compile(r"-(\d{5})-of-(\d{5})$")
# files beside the model that are not a variant of it: vision projectors (offered apart), speculative drafts
NOT_MODEL = re.compile(r"(?i)^(mmproj|mtp-|dflash-|eagle\d*-|draft|imatrix)|imatrix")
UNRESTRICTED = re.compile(r"(?i)abliterat|uncensor|heretic|unfilter|unrestrict|refusal")
SIXTEEN_BITS = {"BF16", "F16", "FP16", "F32"}


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
        return HubError(f"{what}: Hugging Face не пускает — модель закрыта. Примите её лицензию на странице модели "
                        "на huggingface.co и добавьте свой токен в «Подключениях»", 403)  # fmt: skip
    if response.status_code == 404:
        return HubError(f"{what}: на Hugging Face такого нет", 404)
    if response.status_code == 429:
        return HubError(f"{what}: Hugging Face просит подождать (слишком много запросов) — попробуйте позже", 429)
    return HubError(f"{what}: Hugging Face ответил {response.status_code}")


async def _get(http: httpx.AsyncClient, path: str, what: str, token: str | None, **params: Any) -> Any:
    try:
        response = await http.get(f"{base_url()}{path}", params=params or None, headers=headers(token))
    except httpx.HTTPError as exc:
        raise HubError(f"{what}: нет связи с Hugging Face ({exc.__class__.__name__})") from exc
    if response.status_code != 200:
        raise _refused(response, what)
    return response.json()


async def whoami(token: str | None) -> str:
    """Whose token it is (the «Проверить» of «Подключения»)."""
    if not token:
        raise HubError("токен не задан", 422)
    async with httpx.AsyncClient(timeout=20.0) as http:
        found = await _get(http, "/api/whoami-v2", "Токен", token)
    return str(found.get("name") or found.get("fullname") or "?")


def _license(info: dict[str, Any]) -> str:
    card = info.get("cardData") or {}
    found = card.get("license_name") or card.get("license")
    if isinstance(found, list):
        found = ", ".join(str(x) for x in found)
    if not found:
        found = next((t.split(":", 1)[1] for t in info.get("tags") or [] if t.startswith("license:")), "")
    return str(found or "")


def summary(info: dict[str, Any]) -> dict[str, Any]:
    """A repository as a search lists it."""
    repo = info.get("id") or info.get("modelId") or ""
    return {
        "repo": repo,
        "downloads": info.get("downloads") or 0,
        "likes": info.get("likes") or 0,
        "gated": info.get("gated") or False,  # False, "auto" or "manual": the licence is accepted on the site
        "license": _license(info),
        "updated": (info.get("lastModified") or "")[:10],
        "pipeline": info.get("pipeline_tag") or "",
        "unrestricted": bool(UNRESTRICTED.search(repo)) or any(UNRESTRICTED.search(t) for t in info.get("tags") or []),
        "page": f"{base_url()}/{repo}",
    }


async def search(http: httpx.AsyncClient, query: str, *, limit: int = 30, token: str | None = None) -> list[dict]:
    """GGUF repositories matching the words, the most downloaded first."""
    found = await _get(http, "/api/models", "Поиск", token, search=query, filter="gguf", sort="downloads",
                       direction="-1", limit=str(limit), **{"expand[]": list(SEARCH_FIELDS)})  # fmt: skip
    return [summary(info) for info in found if isinstance(info, dict)]


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


async def repository(http: httpx.AsyncClient, repo: str, token: str | None = None) -> dict[str, Any]:
    """A repository: its licence and gating, its parameter count and trained context (when Hugging Face read the
    GGUF), the variants and the vision projectors."""
    if not re.fullmatch(r"[\w.-]+/[\w.-]+", repo):
        raise HubError("имя репозитория — «автор/модель»", 422)
    info = await _get(http, f"/api/models/{repo}", repo, token, **{"expand[]": list(INFO_FIELDS)})
    tree = await _get(http, f"/api/models/{repo}/tree/main", repo, token, recursive="true")
    files = [{"path": f["path"], "size": int(f.get("size") or 0),
              "sha256": (f.get("lfs") or {}).get("oid") or (f.get("lfs") or {}).get("sha256")}
             for f in tree if isinstance(f, dict) and f.get("type") == "file"]  # fmt: skip
    found, projectors = variants(files)
    gguf = info.get("gguf") or {}
    params = int(gguf.get("total") or 0)
    for v in found:
        v["bits"] = round(v["size"] * 8 / params, 1) if params else None  # bits per weight: how much was kept
    return {**summary({**info, "id": info.get("id") or repo}), "revision": info.get("sha") or "main",
            "params": params, "architecture": gguf.get("architecture") or "",
            "context_trained": int(gguf.get("context_length") or 0), "variants": found,
            "projectors": projectors}  # fmt: skip


def file_url(repo: str, path: str, revision: str = "main") -> str:
    return f"{base_url()}/{repo}/resolve/{revision}/{quote(path)}"


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


def header(repo: str, path: str, token: str | None = None, revision: str = "main") -> dict[str, Any]:
    """The model's layers, attention heads and trained context from the first bytes of its (first) file - before
    any download, for the memory estimate (blocking: in a thread)."""
    with httpx.Client(follow_redirects=True, timeout=httpx.Timeout(30.0)) as http:
        request_headers = {**headers(token), "Range": f"bytes=0-{HEADER_LIMIT - 1}"}
        try:
            with http.stream("GET", file_url(repo, path, revision), headers=request_headers) as response:
                if response.status_code not in (200, 206):
                    raise _refused(response, path)
                head = read_header(_Stream(response.iter_bytes(256 << 10), HEADER_LIMIT), until_tokenizer=True)
        except httpx.HTTPError as exc:
            raise HubError(f"{path}: нет связи с Hugging Face ({exc.__class__.__name__})") from exc
    return header_info(head)


@dataclass
class Progress:
    """A download's state kept between tries: the bytes on disk and their sha256 so far."""

    done: int = 0
    hasher: Any = field(default_factory=hashlib.sha256)


def _hash_part(part: Path, progress: Progress, report: Callable[[int], None], cancel: threading.Event) -> None:
    progress.hasher, progress.done = hashlib.sha256(), 0
    with open(part, "rb") as f:
        while chunk := f.read(8 * CHUNK):
            if cancel.is_set():
                raise Cancelled()
            progress.hasher.update(chunk)
            progress.done += len(chunk)
            report(progress.done)


def download(url: str, dest: Path, *, size: int, sha256: str | None, token: str | None,
             report: Callable[[int], None], cancel: threading.Event, progress: Progress | None = None,
             transport: httpx.BaseTransport | None = None) -> None:  # fmt: skip
    """The file into `dest` (blocking: in a thread). It comes in as `<dest>.part`: a part left by an earlier try is
    read again for its sha256 (unless `progress` already holds it) and the rest is asked for with Range. The file
    takes its name only once whole and its sha256 matches; a wrong one is thrown away."""
    part = dest.with_name(dest.name + ".part")
    progress = progress if progress is not None else Progress()
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
                        report(progress.done)
        except httpx.HTTPError as exc:
            raise HubError(f"{dest.name}: связь оборвалась ({exc.__class__.__name__}) — загрузка продолжится") from exc
    if size and progress.done != size:
        raise HubError(f"{dest.name}: пришло {progress.done} байт из {size} — загрузка продолжится")
    if sha256 and progress.hasher.hexdigest() != sha256.lower():
        with contextlib.suppress(OSError):
            part.unlink()
        progress.hasher, progress.done = hashlib.sha256(), 0
        raise HubError(f"{dest.name}: файл пришёл повреждённым (sha256 не совпал) — скачается заново", 409)
    os.replace(part, dest)
