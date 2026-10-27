"""The app's side of the hub (D31): find the running hub or start it, then call its REST API with the app token.

The calls are blocking (httpx); the window runs them off the GUI thread (app/tasks.py). The hub runs as its own
process and keeps running when the window closes (D2).
"""

from __future__ import annotations

import logging
from typing import Any

import httpx

from common.paths import DataPaths, data_paths
from hub.locate import START_TIMEOUT_S, HubUnavailable, hub_address, probe, running_hub

__all__ = ["HubClient", "HubError", "connect", "hub_address", "probe"]

log = logging.getLogger("app.hub")


class HubError(Exception):
    """A hub call failed; the message is fit to show to the user. `code` and `data` come with errors the app
    acts on (e.g. code "budget_exceeded" with the budgets and whether the user may still send)."""

    def __init__(self, message: str, *, code: str | None = None, data: dict | None = None):
        super().__init__(message)
        self.code = code
        self.data = data or {}


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

    def _call(self, method: str, path: str, body: dict | None = None, params: dict | None = None) -> Any:
        try:
            response = self._http.request(method, path, json=body, params=params, headers=self._headers)
        except httpx.HTTPError as exc:
            raise HubError(f"хаб не отвечает ({exc})") from exc
        if response.status_code >= 400:
            try:
                detail = response.json().get("detail")
            except ValueError:
                detail = response.text[:300]
            if isinstance(detail, dict):
                raise HubError(str(detail.get("message") or detail), code=detail.get("code"), data=detail)
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

    def set_chat_projects(self, conversation_id: str, project_ids: list[str]) -> list[str]:
        return self._call("PUT", f"/v1/conversations/{conversation_id}/projects", {"project_ids": project_ids})[
            "projects"
        ]

    def set_context(self, conversation_id: str, **settings) -> dict:
        """map, live (bool), pinned ([{project_id, path}]) - from the chat's next message on."""
        return self._call("PUT", f"/v1/conversations/{conversation_id}/context", settings)

    def delete_conversation(self, conversation_id: str) -> None:
        self._call("DELETE", f"/v1/conversations/{conversation_id}")

    def send(self, conversation_id: str, text: str, model_id: str, effort: str, *, confirm_over_budget=False) -> dict:
        body = {"content": text, "model_id": model_id, "effort": effort, "confirm_over_budget": confirm_over_budget}
        return self._call("POST", f"/v1/conversations/{conversation_id}/messages", body)

    def cancel(self, conversation_id: str) -> bool:
        return self._call("POST", f"/v1/conversations/{conversation_id}/cancel")["cancelled"]

    # --- budgets (D12) ---

    def budgets(self) -> list[dict]:
        return self._call("GET", "/v1/budgets")["budgets"]

    def set_budget(self, **budget) -> dict:
        """scope ("total" / "provider"), provider, period, soft_usd, hard_usd, hard_action ("ask" / "block")."""
        return self._call("PUT", "/v1/budgets", budget)

    def delete_budget(self, budget_id: int) -> None:
        self._call("DELETE", f"/v1/budgets/{budget_id}")

    # --- projects (stage 2) ---

    def projects(self) -> list[dict]:
        return self._call("GET", "/v1/projects")["projects"]

    def add_project(self, path: str, name: str = "") -> dict:
        return self._call("POST", "/v1/projects", {"path": path, "name": name})

    def update_project(self, project_id: str, **changes) -> dict:
        """name, description, cloud_ok."""
        return self._call("PATCH", f"/v1/projects/{project_id}", changes)

    def remove_project(self, project_id: str) -> None:
        self._call("DELETE", f"/v1/projects/{project_id}")

    def tree(self, project_id: str, path: str = "") -> list[dict]:
        return self._call("GET", f"/v1/projects/{project_id}/tree", params={"path": path})["entries"]

    def file(self, project_id: str, path: str) -> dict:
        return self._call("GET", f"/v1/projects/{project_id}/file", params={"path": path})

    def git(self, project_id: str, log: int = 20) -> dict:
        return self._call("GET", f"/v1/projects/{project_id}/git", params={"log": log})

    def links(self) -> list[dict]:
        return self._call("GET", "/v1/links")["links"]

    def scan_links(self) -> dict:
        return self._call("POST", "/v1/links/scan")

    def set_link(self, link_id: int, **changes) -> dict:
        """status (confirmed / rejected), kind, description."""
        return self._call("PUT", f"/v1/links/{link_id}", changes)

    def delete_link(self, link_id: int) -> None:
        self._call("DELETE", f"/v1/links/{link_id}")

    def notes(self, project_id: str) -> list[dict]:
        return self._call("GET", f"/v1/projects/{project_id}/notes")["notes"]

    def add_note(self, project_id: str, text: str) -> dict:
        return self._call("POST", f"/v1/projects/{project_id}/notes", {"text": text})

    def update_note(self, note_id: int, text: str) -> dict:
        return self._call("PUT", f"/v1/notes/{note_id}", {"text": text})

    def delete_note(self, note_id: int) -> None:
        self._call("DELETE", f"/v1/notes/{note_id}")

    def card(self, project_id: str) -> dict:
        return self._call("GET", f"/v1/projects/{project_id}/card")

    def draft_card(self, project_id: str, model_id: str, *, update: bool = False) -> dict:
        """A model writes a draft: anew, or (`update`) the confirmed card brought up to date."""
        body = {"model_id": model_id, "update": update}
        return self._call("POST", f"/v1/projects/{project_id}/card/draft", body)

    def confirm_card(self, project_id: str, text: str) -> dict:
        return self._call("PUT", f"/v1/projects/{project_id}/card", {"text": text})

    def restore_card(self, project_id: str, version: int) -> dict:
        return self._call("POST", f"/v1/projects/{project_id}/card/restore", {"version": version})

    def drop_card_draft(self, project_id: str) -> None:
        self._call("DELETE", f"/v1/projects/{project_id}/card/draft")

    def search(self, project_id: str, text: str, *, regex: bool = False, case: str = "smart", limit: int = 500) -> dict:
        params = {"q": text, "regex": "true" if regex else "false", "case": case, "limit": limit}
        return self._call("GET", f"/v1/projects/{project_id}/search", params=params)

    def find_files(self, project_id: str, part: str, limit: int = 20) -> list[str]:
        """The project's files whose path matches a few letters, best first."""
        return self._call("GET", f"/v1/projects/{project_id}/files", params={"q": part, "limit": limit})["files"]

    def symbols(self, project_id: str, *, q: str = "", path: str = "", kind: str = "", limit: int = 200) -> dict:
        """Definitions named like `q`, or the outline of the file `path` (the hub's symbol index, D26)."""
        params = {"q": q, "path": path, "kind": kind, "limit": limit}
        return self._call("GET", f"/v1/projects/{project_id}/symbols", params=params)

    def diff(self, project_id: str, path: str = "", *, staged: bool = False) -> dict:
        params = {"path": path, "staged": "true" if staged else "false"}
        return self._call("GET", f"/v1/projects/{project_id}/diff", params=params)

    def processes(self) -> dict[str, list[dict]]:
        """project id -> its processes."""
        return self._call("GET", "/v1/processes")["projects"]

    def stop_process(self, pid: int) -> dict:
        return self._call("POST", f"/v1/processes/{pid}/stop")

    # --- terminals ---

    def terminals(self, project_id: str | None = None) -> list[dict]:
        params = {"project_id": project_id} if project_id else None
        return self._call("GET", "/v1/terminals", params=params)["terminals"]

    def start_terminal(self, project_id: str | None, *, cols: int, rows: int, path: str = "", command=None) -> dict:
        body = {"project_id": project_id, "path": path, "command": command, "cols": cols, "rows": rows}
        return self._call("POST", "/v1/terminals", body)

    def terminal_output(self, terminal_id: str) -> dict:
        return self._call("GET", f"/v1/terminals/{terminal_id}/output")

    def terminal_input(self, terminal_id: str, data: str) -> None:
        self._call("POST", f"/v1/terminals/{terminal_id}/input", {"data": data})

    def resize_terminal(self, terminal_id: str, cols: int, rows: int) -> None:
        self._call("POST", f"/v1/terminals/{terminal_id}/resize", {"cols": cols, "rows": rows})

    def stop_terminal(self, terminal_id: str) -> None:
        self._call("POST", f"/v1/terminals/{terminal_id}/stop")

    def remove_terminal(self, terminal_id: str) -> None:
        self._call("DELETE", f"/v1/terminals/{terminal_id}")

    # --- keys and the hub itself ---

    def secrets(self) -> list[dict]:
        return self._call("GET", "/v1/secrets")["secrets"]

    def set_secret(self, name: str, value: str) -> dict:
        return self._call("PUT", f"/v1/secrets/{name}", {"value": value})

    def delete_secret(self, name: str) -> dict:
        return self._call("DELETE", f"/v1/secrets/{name}")

    def feed(self, *, limit: int = 100, before: int | None = None, project_id: str = "", types: str = "") -> dict:
        """The event log, newest first: {events, more}."""
        params: dict = {"limit": limit, "types": types}
        if before is not None:
            params["before"] = before
        if project_id:
            params["project_id"] = project_id
        return self._call("GET", "/v1/feed", params=params)

    def shutdown(self) -> None:
        self._call("POST", "/v1/shutdown")


def connect(paths: DataPaths | None = None, *, start: bool = True, timeout: float = START_TIMEOUT_S) -> HubClient:
    """The client of the running hub; starts the hub first when none answers and `start` is set."""
    paths = paths or data_paths()
    try:
        base = running_hub(paths, start=start, timeout=timeout)
    except HubUnavailable as exc:
        raise HubError(str(exc)) from exc
    try:
        token = (paths.tokens / "app.token").read_text(encoding="utf-8").strip()
    except OSError as exc:
        raise HubError(f"нет токена приложения ({exc})") from exc
    return HubClient(base, token)
