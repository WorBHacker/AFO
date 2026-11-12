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
import json
import os
import re
import threading
import time
from collections.abc import Callable, Iterator
from dataclasses import dataclass, field
from pathlib import Path, PurePosixPath
from typing import Any
from urllib.parse import quote, urlsplit

import httpx

from common import __version__
from common.gguf import GGUFError, header_info, read_header
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
SEARCH_FIELDS = ("gated", "downloads", "likes", "tags", "lastModified", "pipeline_tag", "gguf")
INFO_FIELDS = (*SEARCH_FIELDS, "cardData", "sha")
# a search's «для чего» (32.5): Hugging Face's tags - many chat models have no pipeline tag, but all have this one
TASKS = {"chat": "conversational", "code": "code", "vision": "image-text-to-text"}
SORTS = {"downloads": "downloads", "trending": "trendingScore", "likes": "likes", "updated": "lastModified"}
# a variant's quantization in its file name: Q4_K_M, IQ3_XXS, UD-Q4_K_XL, Q8_0, MXFP4, BF16...
QUANT = re.compile(r"(?i)(?<![A-Z0-9])((?:UD-)?(?:I?Q\d(?:_[A-Z0-9]+)*|TQ\d_\d|MXFP4(?:_MOE)?|NVFP4|BF16|F16|FP16|F32))"
                   r"(?![A-Z0-9])")  # fmt: skip
SPLIT = re.compile(r"-(\d{5})-of-(\d{5})$")
# files beside the model that are not a variant of it: vision projectors (offered apart), speculative drafts, imatrix
# data (imatrix.gguf, model.imatrix.gguf) - a model's own name may say «imatrix» too (32.3: …-iMatrix-NVFP4.gguf)
NOT_MODEL = re.compile(r"(?i)^(mmproj|mtp-|dflash-|eagle\d*-|draft|imatrix)|[._-]imatrix\.gguf$")
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
    try:
        response = await http.get(f"{base_url()}{path}", params=params or None, headers=headers(token))
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
        "updated": (info.get("lastModified") or "")[:10],
        "pipeline": info.get("pipeline_tag") or "",
        "params": params_of({**info, "id": repo}),
        "moe": experts(info, repo),
        "unrestricted": bool(UNRESTRICTED.search(repo)) or any(UNRESTRICTED.search(t) for t in info.get("tags") or []),
        "page": f"{base_url()}/{repo}",
    }


async def search(http: httpx.AsyncClient, query: str = "", *, limit: int = 30, token: str | None = None,
                 task: str = "", fewest: int = 0, most: int = 0, sort: str = "downloads",
                 plain: bool = False) -> list[dict]:  # fmt: skip
    """GGUF repositories matching the words (none: all of them), the most downloaded first - or as `sort` says;
    `task` (TASKS), a parameter count from `fewest` to `most` (0: no bound) and `plain` (none whose refusals were
    taken out) narrow it (32.5). Hugging Face bounds the count it read - some it read from a projector - so the hub
    bounds params_of again, asking for more than it shows when some may drop."""
    narrowed = bool(plain or fewest or most)
    params: dict[str, Any] = {"filter": ["gguf", *([TASKS[task]] if task in TASKS else [])],
                              "sort": SORTS.get(sort, "downloads"), "direction": "-1",
                              "limit": str(limit * 2 if narrowed else limit),
                              "expand[]": list(SEARCH_FIELDS)}  # fmt: skip
    if query:
        params["search"] = query
    if fewest or most:
        params["num_parameters"] = f"min:{fewest}" + (f",max:{most}" if most else "")
    found = await _get(http, "/api/models", tr("Поиск"), token, **params)
    out = []
    for info in found:
        if not isinstance(info, dict):
            continue
        shown = summary(info)
        count = shown["params"]
        if (plain and shown["unrestricted"]) or (count and (count < fewest or (most and count > most))):
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


async def repository(http: httpx.AsyncClient, repo: str, token: str | None = None) -> dict[str, Any]:
    """A repository: its licence and gating, its parameter count and trained context (when Hugging Face read the
    GGUF), the variants and the vision projectors."""
    if not re.fullmatch(r"[\w.-]+/[\w.-]+", repo):
        raise HubError(tr("имя репозитория — «автор/модель»"), 422)
    info = await _get(http, f"/api/models/{repo}", repo, token, **{"expand[]": list(INFO_FIELDS)})
    tree = await _get(http, f"/api/models/{repo}/tree/main", repo, token, recursive="true")
    files = [{"path": f["path"], "size": int(f.get("size") or 0),
              "sha256": (f.get("lfs") or {}).get("oid") or (f.get("lfs") or {}).get("sha256")}
             for f in tree if isinstance(f, dict) and f.get("type") == "file"]  # fmt: skip
    found, projectors = variants(files)
    gguf = info.get("gguf") or {}
    params = params_of({**info, "id": info.get("id") or repo})
    for v in found:
        v["bits"] = round(v["size"] * 8 / params, 1) if params else None  # bits per weight: how much was kept
    return {**summary({**info, "id": info.get("id") or repo}), "revision": info.get("sha") or "main",
            "params": params, "architecture": gguf.get("architecture") or "",
            "context_trained": int(gguf.get("context_length") or 0), "variants": found,
            "projectors": projectors}  # fmt: skip


async def files(http: httpx.AsyncClient, repo: str, token: str | None = None) -> dict[str, Any]:
    """A repository's files - {path: {size, sha256}} - with its licence and the commit they are at: a picture model
    takes its files from several repositories (D55, 29.3)."""
    if not re.fullmatch(r"[\w.-]+/[\w.-]+", repo):
        raise HubError(tr("имя репозитория — «автор/модель»"), 422)
    info = await _get(http, f"/api/models/{repo}", repo, token, **{"expand[]": list(INFO_FIELDS)})
    tree = await _get(http, f"/api/models/{repo}/tree/main", repo, token, recursive="true")
    listed = {f["path"]: {"size": int(f.get("size") or 0),
                          "sha256": (f.get("lfs") or {}).get("oid") or (f.get("lfs") or {}).get("sha256")}
              for f in tree if isinstance(f, dict) and f.get("type") == "file"}  # fmt: skip
    return {**summary({**info, "id": info.get("id") or repo}), "revision": info.get("sha") or "main", "files": listed}


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
            raise HubError(
                tr("{what}: нет связи с Hugging Face ({error})", what=path, error=exc.__class__.__name__)
            ) from exc
    return header_info(head)


@dataclass
class Progress:
    """A download's state kept between tries: the bytes on disk and their sha256 so far; for a file that comes in
    pieces - the pieces on disk and how far from the start the sha256 has gone."""

    done: int = 0
    hasher: Any = field(default_factory=hashlib.sha256)
    pieces: set[int] = field(default_factory=set)
    hashed: int = 0


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
    lock, wake, stop = threading.Lock(), threading.Condition(), threading.Event()
    waiting = [i for i in range(count) if i not in progress.pieces]
    failed: list[BaseException] = []
    coming: dict[int, int] = {}  # a piece on its way -> its bytes so far

    def worker() -> None:
        with httpx.Client(follow_redirects=True, timeout=httpx.Timeout(30.0, read=120.0), transport=transport) as http:
            while not stop.is_set():
                with lock:
                    if not waiting:
                        return
                    index = waiting.pop(0)
                try:
                    _piece(http, url, part, index, size, token, cancel, stop, coming, lock)
                except BaseException as exc:  # the main thread raises it
                    with lock:
                        failed.append(exc)
                    stop.set()
                    with wake:
                        wake.notify()
                    return
                with lock:
                    progress.pieces.add(index)
                    coming.pop(index, None)
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
                    on_way = sum(coming.values())
                    progress.done = sum(_span(i, size)[1] - _span(i, size)[0] + 1 for i in ready)
                report(progress.done + on_way)
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


def _piece(http: httpx.Client, url: str, part: Path, index: int, size: int, token: str | None,
           cancel: threading.Event, stop: threading.Event, coming: dict[int, int],
           lock: threading.Lock) -> None:  # fmt: skip
    """One piece at its place in the part, asked again when the connection breaks (SEGMENT_TRIES)."""
    first, last = _span(index, size)
    need = last + 1 - first
    for attempt in range(SEGMENT_TRIES):
        got = 0
        try:
            with http.stream("GET", url, headers={**headers(token), "Range": f"bytes={first}-{last}"}) as response:
                if response.status_code == 200:
                    raise _NoRanges()
                if response.status_code != 206:
                    raise _refused(response, part.name.removesuffix(".part"))
                with open(part, "r+b") as f:
                    f.seek(first)
                    for chunk in response.iter_bytes(CHUNK):
                        if cancel.is_set() or stop.is_set():
                            raise Cancelled()
                        f.write(chunk[: need - got])
                        got += len(chunk)
                        with lock:
                            coming[index] = min(got, need)
                        if got >= need:  # what a server sends past the end asked for is the next piece's place
                            break
            if got >= need:
                return
        except httpx.HTTPError:
            pass
        with lock:
            coming.pop(index, None)
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
                        report(progress.done)
        except httpx.HTTPError as exc:
            raise HubError(tr("{file}: связь оборвалась ({error}) — загрузка продолжится", file=dest.name,
                              error=exc.__class__.__name__)) from exc  # fmt: skip
    if size and progress.done != size:
        raise HubError(tr("{file}: пришло {done} байт из {size} — загрузка продолжится", file=dest.name,
                          done=progress.done, size=size))  # fmt: skip
    if sha256 and progress.hasher.hexdigest() != sha256.lower():
        with contextlib.suppress(OSError):
            part.unlink()
        progress.hasher, progress.done = hashlib.sha256(), 0
        raise HubError(tr("{file}: файл пришёл повреждённым (sha256 не совпал) — скачается заново", file=dest.name),
                       409)  # fmt: skip
    os.replace(part, dest)
