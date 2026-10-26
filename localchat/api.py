"""llama-swap client: model list, running state, load/unload, and a streaming chat worker."""
from __future__ import annotations

import json
import subprocess
import time
from dataclasses import dataclass, field
from pathlib import Path

import httpx
from PySide6.QtCore import QThread, Signal

LOADING_PREFIX = "llama-swap loading model:"


@dataclass
class ModelInfo:
    id: str
    name: str
    description: str = ""
    meta: dict = field(default_factory=dict)


class SwapClient:
    """Small synchronous calls; each one is quick on localhost and has a short timeout."""

    def __init__(self, base_url: str):
        self.base_url = base_url.rstrip("/")

    def healthy(self) -> bool:
        try:
            return httpx.get(f"{self.base_url}/health", timeout=1.0).status_code == 200
        except httpx.HTTPError:
            return False

    def models(self) -> list[ModelInfo]:
        r = httpx.get(f"{self.base_url}/v1/models", timeout=5.0)
        r.raise_for_status()
        return [ModelInfo(m["id"], m.get("name") or m["id"], m.get("description", ""), m.get("meta", {}) or {})
                for m in r.json().get("data", [])]

    def running(self) -> dict[str, str]:
        """model id -> state (ready, starting, stopping, ...)."""
        r = httpx.get(f"{self.base_url}/running", timeout=2.0)
        r.raise_for_status()
        return {m["model"]: m.get("state", "") for m in r.json().get("running", [])}

    def load(self, model_id: str) -> None:
        """Asking the upstream's health page makes llama-swap start the model."""
        httpx.get(f"{self.base_url}/upstream/{model_id}/health", timeout=None)

    def unload(self) -> None:
        httpx.post(f"{self.base_url}/api/models/unload", timeout=60.0)

    @staticmethod
    def start_llama_swap(exe: str, config: str, base_url: str) -> bool:
        exe_path, cfg_path = Path(exe), Path(config)
        if not exe_path.is_file() or not cfg_path.is_file():
            return False
        listen = base_url.split("://", 1)[-1].rstrip("/")
        subprocess.Popen([str(exe_path), "-config", str(cfg_path), "-listen", listen, "-watch-config"],
                         cwd=str(exe_path.parent), stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                         creationflags=subprocess.CREATE_NO_WINDOW | subprocess.DETACHED_PROCESS)
        return True


class StatusPoller(QThread):
    """Polls /running every couple of seconds so the UI can show what is loaded."""

    status = Signal(bool, dict)          # llama-swap reachable, {model: state}

    def __init__(self, client: SwapClient):
        super().__init__()
        self.client = client
        self._stop = False

    def run(self) -> None:
        while not self._stop:
            try:
                self.status.emit(True, self.client.running())
            except httpx.HTTPError:
                self.status.emit(False, {})
            for _ in range(15):
                if self._stop:
                    return
                self.msleep(100)

    def stop(self) -> None:
        self._stop = True


class LoadWorker(QThread):
    finished_ok = Signal(str)
    failed = Signal(str)

    def __init__(self, client: SwapClient, model_id: str | None):
        super().__init__()
        self.client, self.model_id = client, model_id

    def run(self) -> None:
        try:
            if self.model_id:
                self.client.load(self.model_id)
            else:
                self.client.unload()
            self.finished_ok.emit(self.model_id or "")
        except httpx.HTTPError as e:
            self.failed.emit(str(e))


class ChatStream(QThread):
    """Streams one completion. Loading lines from llama-swap go to `loading`, not to the reasoning."""

    loading = Signal(str)
    reasoning = Signal(str)
    content = Signal(str)
    done = Signal(dict)
    failed = Signal(str)

    def __init__(self, base_url: str, model: str, messages: list[dict], effort: str):
        super().__init__()
        self.url = f"{base_url.rstrip('/')}/v1/chat/completions"
        self.body = {"model": model, "messages": messages, "stream": True,
                     "stream_options": {"include_usage": True}, "reasoning_effort": effort}
        self._cancel = False

    def cancel(self) -> None:
        self._cancel = True

    def run(self) -> None:
        t0 = time.time()
        first = None
        in_loading = False
        chunks = 0
        usage, timings = {}, {}
        try:
            timeout = httpx.Timeout(connect=10.0, read=None, write=30.0, pool=10.0)
            with httpx.Client(timeout=timeout) as client, client.stream("POST", self.url, json=self.body) as r:
                if r.status_code != 200:
                    r.read()
                    self.failed.emit(f"HTTP {r.status_code}: {r.text[:500]}")
                    return
                for line in r.iter_lines():
                    if self._cancel:
                        break
                    if not line.startswith("data:"):
                        continue
                    data = line[5:].strip()
                    if data == "[DONE]":
                        break
                    obj = json.loads(data)
                    usage = obj.get("usage") or usage
                    timings = obj.get("timings") or timings
                    if not obj.get("choices"):
                        continue
                    delta = obj["choices"][0].get("delta") or {}
                    think = delta.get("reasoning_content") or delta.get("reasoning") or ""
                    text = delta.get("content") or ""
                    if think and not in_loading and first is None and think.lstrip().startswith(LOADING_PREFIX):
                        in_loading = True
                    if in_loading:
                        self.loading.emit(think or text)
                        if "Done!" in (think + text):
                            in_loading = False
                            t0 = time.time()       # time to first token is measured from the end of the load
                        continue
                    if think or text:
                        first = first or time.time()
                        chunks += 1
                    if think:
                        self.reasoning.emit(think)
                    if text:
                        self.content.emit(text)
        except (httpx.HTTPError, ValueError) as e:
            self.failed.emit(str(e))
            return
        end = time.time()
        tokens = usage.get("completion_tokens") or chunks
        speed = timings.get("predicted_per_second") or (tokens / (end - first) if first and end > first else 0)
        self.done.emit({"tokens": tokens, "tok_s": round(speed, 1),
                        "ttft_s": round(first - t0, 1) if first else None,
                        "total_s": round(end - t0, 1), "cancelled": self._cancel})
