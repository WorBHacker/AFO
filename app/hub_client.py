"""The app's side of the hub (D31): find the running hub or start it, then call its REST API with the app token.

The calls are blocking (httpx); the window runs them off the GUI thread (app/tasks.py). The hub runs as its own
process and keeps running when the window closes (D2).
"""

from __future__ import annotations

import json
import logging
import os
import subprocess
import sys
import time
from typing import Any

import httpx

from common.paths import PROJECT_ROOT, DataPaths, data_paths
from hub.settings import DEFAULT_PORT, PORT_ENV

log = logging.getLogger("app.hub")

SERVICE = "localchat-hub"
START_TIMEOUT_S = 30.0


class HubError(Exception):
    """A hub call failed; the message is fit to show to the user."""


class HubClient:
    def __init__(self, base_url: str, token: str, http: httpx.Client | None = None):
        self.base_url = base_url.rstrip("/")
        self._headers = {"Authorization": f"Bearer {token}"}
        self.token = token
        # tests pass Starlette's TestClient here; it is an httpx.Client too
        self._http = http or httpx.Client(base_url=self.base_url, timeout=httpx.Timeout(30.0, connect=3.0))

    @property
    def events_url(self) -> str:
        return "ws" + self.base_url.removeprefix("http") + "/v1/events"

    def close(self) -> None:
        self._http.close()

    def _call(self, method: str, path: str, body: dict | None = None) -> Any:
        try:
            response = self._http.request(method, path, json=body, headers=self._headers)
        except httpx.HTTPError as exc:
            raise HubError(f"хаб не отвечает ({exc})") from exc
        if response.status_code >= 400:
            try:
                detail = response.json().get("detail")
            except ValueError:
                detail = response.text[:300]
            raise HubError(str(detail or f"HTTP {response.status_code}"))
        return response.json() if response.content else None

    # --- models and the machine ---

    def models(self) -> list[dict]:
        return self._call("GET", "/v1/models")["models"]

    def sync_models(self) -> dict:
        return self._call("POST", "/v1/models/sync")

    def unload_local(self) -> dict:
        return self._call("POST", "/v1/models/unload")

    def status(self) -> dict:
        return self._call("GET", "/v1/status")

    def usage(self) -> dict:
        return self._call("GET", "/v1/usage")

    # --- conversations ---

    def conversations(self) -> dict:
        return self._call("GET", "/v1/conversations")

    def create_conversation(self, title: str = "") -> str:
        return self._call("POST", "/v1/conversations", {"title": title})["id"]

    def conversation(self, conversation_id: str) -> dict:
        return self._call("GET", f"/v1/conversations/{conversation_id}")

    def rename(self, conversation_id: str, title: str) -> dict:
        return self._call("PATCH", f"/v1/conversations/{conversation_id}", {"title": title})

    def delete_conversation(self, conversation_id: str) -> None:
        self._call("DELETE", f"/v1/conversations/{conversation_id}")

    def send(self, conversation_id: str, text: str, model_id: str, effort: str) -> dict:
        body = {"content": text, "model_id": model_id, "effort": effort}
        return self._call("POST", f"/v1/conversations/{conversation_id}/messages", body)

    def cancel(self, conversation_id: str) -> bool:
        return self._call("POST", f"/v1/conversations/{conversation_id}/cancel")["cancelled"]

    # --- keys and the hub itself ---

    def secrets(self) -> list[dict]:
        return self._call("GET", "/v1/secrets")["secrets"]

    def set_secret(self, name: str, value: str) -> dict:
        return self._call("PUT", f"/v1/secrets/{name}", {"value": value})

    def delete_secret(self, name: str) -> dict:
        return self._call("DELETE", f"/v1/secrets/{name}")

    def shutdown(self) -> None:
        self._call("POST", "/v1/shutdown")


def hub_address(paths: DataPaths) -> str:
    """Where the hub listens: hub.json of a running hub, else the configured port."""
    try:
        info = json.loads(paths.hub_info.read_text(encoding="utf-8"))
        return f"http://{info['host']}:{info['port']}"
    except OSError, ValueError, KeyError:
        return f"http://127.0.0.1:{int(os.environ.get(PORT_ENV, DEFAULT_PORT))}"


def probe(base_url: str) -> str:
    """'ok' - a LocalChat hub answers; 'absent' - nothing listens; 'foreign' - some other program does."""
    try:
        response = httpx.get(f"{base_url}/v1/health", timeout=1.0)
    except httpx.HTTPError:
        return "absent"
    try:
        return "ok" if response.json().get("service") == SERVICE else "foreign"
    except ValueError:
        return "foreign"


def start_hub() -> None:
    """Starts `python -m hub` as its own process with no console window; it outlives the app (D2)."""
    flags = 0
    if os.name == "nt":
        # a hidden console rather than none: the venv's python.exe starts the real interpreter as a child,
        # which would open a console window of its own if the parent had none
        flags = subprocess.CREATE_NO_WINDOW | subprocess.CREATE_NEW_PROCESS_GROUP
    subprocess.Popen(
        [sys.executable, "-m", "hub"],
        cwd=PROJECT_ROOT,
        stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        creationflags=flags,
        close_fds=True,
    )


def connect(paths: DataPaths | None = None, *, start: bool = True, timeout: float = START_TIMEOUT_S) -> HubClient:
    """The client of the running hub; starts the hub first when none answers and `start` is set."""
    paths = paths or data_paths()
    base = hub_address(paths)
    state = probe(base)
    if state == "foreign":
        raise HubError(f"на {base} отвечает не хаб LocalChat — порт занят другой программой")
    if state == "absent":
        if not start:
            raise HubError("хаб не запущен")
        log.info("no hub at %s, starting one", base)
        start_hub()
        deadline = time.monotonic() + timeout
        while True:
            base = hub_address(paths)
            if probe(base) == "ok":
                break
            if time.monotonic() > deadline:
                raise HubError(f"хаб не запустился за {timeout:.0f} с, подробности в {paths.logs / 'hub.log'}")
            time.sleep(0.2)
        log.info("hub started at %s", base)
    try:
        token = (paths.tokens / "app.token").read_text(encoding="utf-8").strip()
    except OSError as exc:
        raise HubError(f"нет токена приложения ({exc})") from exc
    return HubClient(base, token)
