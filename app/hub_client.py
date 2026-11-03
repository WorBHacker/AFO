"""The app's side of the hub (D31): find the running hub or start it, then call its REST API with the app token.

The calls are blocking (httpx); the window runs them off the GUI thread (app/tasks.py). The hub runs as its own
process and keeps running when the window closes (D2).
"""

from __future__ import annotations

import json
import logging
from collections.abc import Iterator
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


def _unlock(ticket: str) -> dict[str, str]:
    """The connections' unlock ticket (D13), when the window has one."""
    return {"X-LocalChat-Unlock": ticket} if ticket else {}


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

    def _call(
        self,
        method: str,
        path: str,
        body: dict | None = None,
        params: dict | None = None,
        headers: dict | None = None,
        timeout: float | None = None,
    ) -> Any:
        """`timeout`: seconds for a request known to take long (a local model loading from disk)."""
        extra = {"timeout": httpx.Timeout(timeout, connect=3.0)} if timeout else {}
        try:
            response = self._http.request(method, path, json=body, params=params,
                                          headers={**self._headers, **(headers or {})}, **extra)  # fmt: skip
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

    # --- the models' settings (stage 8, D39) ---

    def model_settings(self, model_id: str) -> dict:
        """'' - the general row (the system prompt for every model)."""
        return self._call("GET", "/v1/model_settings", params={"model_id": model_id})

    def save_model_settings(self, model_id: str, settings: dict) -> dict:
        return self._call("PUT", "/v1/model_settings", {"model_id": model_id, "settings": settings})

    def revert_model_settings(self, model_id: str, version: int) -> dict:
        return self._call("POST", "/v1/model_settings/revert", {"model_id": model_id, "version": version})

    # --- a local model's launch settings in llama-swap (stage 8.5) ---

    def llama_models(self) -> dict:
        return self._call("GET", "/v1/llama/models")

    def change_llama_model(self, model_id: str, changes: dict) -> dict:
        return self._call("PUT", f"/v1/llama/models/{model_id}", {"changes": changes}, timeout=60)

    def trial_llama_model(self, model_id: str) -> dict:
        """The model loads with its settings first (minutes from a disk): the window waits as long as the hub."""
        return self._call("POST", f"/v1/llama/models/{model_id}/trial", timeout=330)

    # --- «Проверить систему» and the logs for support (10.6) ---

    def support_check(self) -> dict:
        return self._call("GET", "/v1/support/check", timeout=60)

    def support_report(self) -> dict:
        return self._call("POST", "/v1/support/report", timeout=120)

    # --- the first-run wizard (10.2): this computer, the engine from GitHub, the first models ---

    def setup_state(self) -> dict:
        return self._call("GET", "/v1/setup", timeout=30)

    def setup_models(self) -> dict:
        return self._call("GET", "/v1/setup/models", timeout=90)

    def setup_engine(self, build: str = "") -> dict:
        return self._call("POST", "/v1/setup/engine", {"build": build}, timeout=30)

    def setup_update_info(self) -> dict:
        return self._call("GET", "/v1/setup/update", timeout=30)

    def setup_update(self) -> dict:
        return self._call("POST", "/v1/setup/update", timeout=30)

    def setup_cancel(self) -> dict:
        return self._call("POST", "/v1/setup/engine/cancel")

    # --- local models from Hugging Face (10.1, D37): the hub downloads, writes the config, tries the model ---

    def install_system(self) -> dict:
        return self._call("GET", "/v1/install/system")

    def install_catalog(self) -> dict:
        return self._call("GET", "/v1/install/catalog", timeout=90)

    def install_search(self, query: str) -> dict:
        return self._call("GET", "/v1/install/search", params={"q": query}, timeout=60)

    def install_repo(self, repo: str) -> dict:
        return self._call("GET", f"/v1/install/repo/{repo}", timeout=60)

    def install(self, spec: dict) -> dict:
        return self._call("POST", "/v1/install", spec, timeout=60)

    def install_jobs(self) -> dict:
        return self._call("GET", "/v1/install/jobs")

    def install_act(self, job_id: str, action: str) -> dict:
        return self._call("POST", f"/v1/install/jobs/{job_id}/{action}")

    def install_forget(self, job_id: str) -> dict:
        return self._call("DELETE", f"/v1/install/jobs/{job_id}")

    def install_remove(self, model_id: str) -> dict:
        return self._call("DELETE", f"/v1/install/models/{model_id}", timeout=60)

    def restore_llama(self, backup: str) -> dict:
        return self._call("POST", "/v1/llama/restore", {"backup": backup}, timeout=60)

    def export_model_settings(self) -> dict:
        return self._call("GET", "/v1/model_settings/export")

    def import_model_settings(self, data: dict) -> dict:
        return self._call("POST", "/v1/model_settings/import", {"data": data})

    # --- all the settings in one file (D46, 13.10) ---

    def export_settings(self) -> dict:
        return self._call("GET", "/v1/settings/export")

    def preview_settings(self, data: dict, parts: list[str] | None, ticket: str = "") -> dict:
        return self._call("POST", "/v1/settings/import/preview", {"data": data, "parts": parts},
                          headers=_unlock(ticket))  # fmt: skip

    def import_settings(self, data: dict, parts: list[str] | None, ticket: str = "") -> dict:
        return self._call("POST", "/v1/settings/import", {"data": data, "parts": parts}, headers=_unlock(ticket),
                          timeout=60)  # fmt: skip

    # --- the autonomous agent (stage 9) ---

    def start_autonomous(self, body: dict) -> dict:
        return self._call("POST", "/v1/runs/autonomous", body)

    # --- «Песочница» (D17, 9.4b): what a chat changed in its worktree ---

    # --- the proving ground and the models' exam (stage 12, D45) ---

    def proving_tasks(self) -> list[dict]:
        return self._call("GET", "/v1/proving/tasks")["tasks"]

    def proving_try(self, task_id: str, model_id: str) -> dict:
        return self._call("POST", "/v1/proving/try", {"task_id": task_id, "model_id": model_id}, timeout=120)["run"]

    def proving_runs(self) -> list[dict]:
        return self._call("GET", "/v1/proving/runs")["runs"]

    def delete_proving_run(self, run_id: str) -> None:
        self._call("DELETE", f"/v1/proving/runs/{run_id}")

    def proving_exams(self) -> list[dict]:
        return self._call("GET", "/v1/proving/exams")["exams"]

    def start_exam(self, model_ids: list[str], task_ids: list[str], judge: bool) -> dict:
        return self._call("POST", "/v1/proving/exams", {"model_ids": model_ids, "task_ids": task_ids,
                                                        "judge": judge})["exam"]  # fmt: skip

    def stop_exam(self, exam_id: str) -> dict:
        return self._call("POST", f"/v1/proving/exams/{exam_id}/stop")["exam"]

    def delete_exam(self, exam_id: str) -> None:
        self._call("DELETE", f"/v1/proving/exams/{exam_id}", timeout=120)

    # --- a chat's goal (stage 11, D44) ---

    def goal(self, conversation_id: str) -> dict:
        return self._call("GET", f"/v1/conversations/{conversation_id}/goal")

    def set_goal(self, conversation_id: str, contract: dict) -> dict:
        return self._call("PUT", f"/v1/conversations/{conversation_id}/goal", contract)["goal"]

    def draft_goal(self, conversation_id: str, text: str, model_id: str) -> dict:
        # a local model may load from disk first
        return self._call("POST", f"/v1/conversations/{conversation_id}/goal/draft",
                          {"text": text, "model_id": model_id}, timeout=600)  # fmt: skip

    def pause_goal(self, conversation_id: str) -> dict:
        return self._call("POST", f"/v1/conversations/{conversation_id}/goal/pause")["goal"]

    def resume_goal(self, conversation_id: str, limits: dict | None = None) -> dict:
        return self._call("POST", f"/v1/conversations/{conversation_id}/goal/resume",
                          {"limits": limits} if limits else None)["goal"]  # fmt: skip

    def clear_goal(self, conversation_id: str) -> dict:
        return self._call("DELETE", f"/v1/conversations/{conversation_id}/goal")["goal"]

    def steer_goal(self, conversation_id: str, text: str) -> dict:
        return self._call("POST", f"/v1/conversations/{conversation_id}/goal/steer", {"text": text})

    def sandbox(self, conversation_id: str) -> dict:
        return self._call("GET", f"/v1/conversations/{conversation_id}/sandbox")

    def apply_sandbox(self, conversation_id: str, project_id: str) -> dict:
        return self._call("POST", f"/v1/conversations/{conversation_id}/sandbox/{project_id}/apply")

    def discard_sandbox(self, conversation_id: str, project_id: str) -> dict:
        return self._call("POST", f"/v1/conversations/{conversation_id}/sandbox/{project_id}/discard")

    # --- the user's API keys for the agent's requests (9.4b): behind the connections' password ---

    def agent_keys(self) -> dict:
        """{keys: [{name, host, header, prefix, set}]} - never a key."""
        return self._call("GET", "/v1/agent/keys")

    def set_agent_key(self, name: str, body: dict, ticket: str) -> dict:
        return self._call("PUT", f"/v1/agent/keys/{name}", body, headers=_unlock(ticket))

    def delete_agent_key(self, name: str, ticket: str) -> dict:
        return self._call("DELETE", f"/v1/agent/keys/{name}", headers=_unlock(ticket))

    # --- the agent's mailbox (9.4b, Q21): changes behind the connections' password ---

    def mail(self) -> dict:
        """{account: {address, servers...} | None, password: set or not} - never the password."""
        return self._call("GET", "/v1/mail")

    def set_mail(self, body: dict, ticket: str) -> dict:
        return self._call("PUT", "/v1/mail", body, headers=_unlock(ticket))

    def delete_mail(self, ticket: str) -> dict:
        return self._call("DELETE", "/v1/mail", headers=_unlock(ticket))

    def check_mail(self, ticket: str) -> dict:
        return self._call("POST", "/v1/mail/check", headers=_unlock(ticket), timeout=90)

    # --- schedules and events (9.7, Q23) ---

    def schedules(self) -> dict:
        """{schedules: [...], quiet: {enabled, from, to}}."""
        return self._call("GET", "/v1/schedules")

    def add_schedule(self, body: dict) -> dict:
        return self._call("POST", "/v1/schedules", body)

    def set_schedule(self, sid: str, **change) -> dict:
        return self._call("PUT", f"/v1/schedules/{sid}", change)

    def delete_schedule(self, sid: str) -> dict:
        return self._call("DELETE", f"/v1/schedules/{sid}")

    def run_schedule(self, sid: str) -> dict:
        return self._call("POST", f"/v1/schedules/{sid}/run")

    def set_quiet(self, **change) -> dict:
        """enabled, from, to (HH:MM)."""
        return self._call("PUT", "/v1/agent/quiet", change)

    def run_plan(self, run_id: str) -> dict:
        return self._call("GET", f"/v1/runs/{run_id}/plan")

    def retention(self) -> dict:
        return self._call("GET", "/v1/support/retention")

    def set_retention(self, days: int, max_gb: float) -> dict:
        return self._call("PUT", "/v1/support/retention", {"days": days, "max_gb": max_gb})

    def prune_kept(self) -> dict:
        return self._call("POST", "/v1/support/retention/prune")

    def chat_questions(self, conversation_id: str) -> dict:
        return self._call("GET", f"/v1/conversations/{conversation_id}/questions")

    def answer_question(self, run_id: str, question_id: str, answer: str) -> dict:
        return self._call("POST", f"/v1/runs/{run_id}/questions/{question_id}", {"answer": answer})

    def message_run(self, run_id: str, text: str) -> dict:
        """A word to a running task: the answer to its question, or something it reads before its next step."""
        return self._call("POST", f"/v1/runs/{run_id}/message", {"text": text})

    def set_autonomy(self, run_id: str, autonomy: str) -> dict:
        return self._call("PUT", f"/v1/runs/{run_id}/autonomy", {"autonomy": autonomy})

    def pause_run(self, run_id: str) -> dict:
        return self._call("POST", f"/v1/runs/{run_id}/pause")

    def resume_run(self, run_id: str) -> dict:
        return self._call("POST", f"/v1/runs/{run_id}/resume")

    def edit_run_plan(self, run_id: str, steps: list, reason: str) -> dict:
        return self._call("PUT", f"/v1/runs/{run_id}/plan", {"steps": steps, "reason": reason})

    # --- the agent's memory (stage 9.3) ---

    def memory_facts(self, query: str = "") -> list[dict]:
        return self._call("GET", "/v1/memory/facts", params={"q": query})["facts"]

    def add_fact(self, text: str, about: str = "", personal: bool = False) -> dict:
        return self._call("POST", "/v1/memory/facts", {"text": text, "about": about, "personal": personal})

    def change_fact(self, fact_id: int, **changes) -> dict:
        return self._call("PUT", f"/v1/memory/facts/{fact_id}", changes)

    def delete_fact(self, fact_id: int) -> None:
        self._call("DELETE", f"/v1/memory/facts/{fact_id}")

    def skills(self, query: str = "") -> list[dict]:
        return self._call("GET", "/v1/skills", params={"q": query})["skills"]

    def add_skill(self, run_id: str, name: str = "") -> dict:
        return self._call("POST", "/v1/skills", {"run_id": run_id, "name": name})

    def delete_skill(self, skill_id: int) -> None:
        self._call("DELETE", f"/v1/skills/{skill_id}")

    def agent_access(self) -> dict:
        return self._call("GET", "/v1/agent/access")

    def set_agent_access(self, **changes) -> dict:
        return self._call("PUT", "/v1/agent/access", changes)

    def check_model(self, model_id: str) -> dict:
        """A local model may load for minutes first: the hub gives it 5, the window waits a little longer."""
        return self._call("POST", "/v1/models/check", {"model_id": model_id}, timeout=330)

    def status(self) -> dict:
        return self._call("GET", "/v1/status")

    def usage(self) -> dict:
        return self._call("GET", "/v1/usage")

    # --- conversations ---

    def conversations(self) -> dict:
        return self._call("GET", "/v1/conversations")

    def create_conversation(self, title: str = "", home_project: str | None = None) -> str:
        """`home_project`: a session of that project - it lives in «Проекты», not in «Чаты»."""
        body = {"title": title, **({"home_project": home_project} if home_project else {})}
        return self._call("POST", "/v1/conversations", body)["id"]

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

    def send(self, conversation_id: str, text: str, model_id: str, effort: str, *, confirm_over_budget=False,
             files: list[dict] | None = None) -> dict:  # fmt: skip
        body = {"content": text, "model_id": model_id, "effort": effort, "confirm_over_budget": confirm_over_budget}
        if files:  # files and pictures sent with it: {name, data (base64)}
            body["files"] = files
        return self._call("POST", f"/v1/conversations/{conversation_id}/messages", body, timeout=120 if files else None)

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

    def save_file(self, project_id: str, path: str, text: str, encoding: str, base: str) -> dict:
        """The editor's save (D47, 14.1): a 409 HubError carries `reason` and the disk's `file` in its data."""
        return self._call("PUT", f"/v1/projects/{project_id}/file",
                          {"path": path, "text": text, "encoding": encoding, "base": base})  # fmt: skip

    def complete(self, project_id: str, path: str, before: str, after: str, *, load: bool = False,
                 stop=lambda: False) -> Iterator[dict]:  # fmt: skip
        """Code completion at the cursor (D47, 14.6): the hub's lines - {model, how}, {text} pieces, {done}; or one
        {paused, message}. Once `stop()` says so (the next keystroke) the stream is closed: the hub cancels its
        request, and llama.cpp its."""
        body = {"project_id": project_id, "path": path, "before": before, "after": after, "load": load}
        try:
            with self._http.stream("POST", "/v1/editor/complete", json=body, headers=self._headers,
                                   timeout=httpx.Timeout(10.0, connect=3.0)) as response:  # fmt: skip
                if response.status_code >= 400:
                    raise HubError(f"HTTP {response.status_code}")
                for line in response.iter_lines():
                    if stop():
                        return
                    if line:
                        yield json.loads(line)
        except httpx.HTTPError as exc:
            raise HubError(f"хаб не отвечает ({exc})") from exc

    def editor_unsaved(self, files: list[dict[str, str]]) -> dict:
        """The files the editor holds with edits not saved ({project_id, path}), the whole list (D47, 14.2c)."""
        return self._call("PUT", "/v1/editor/dirty", {"files": files})

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

    def terminal_output(self, terminal_id: str, since: int | None = None) -> dict:
        """The last output; `since`: from that offset on, when the hub still has it (`start` then equals it)."""
        params = {"since": since} if since is not None else None
        return self._call("GET", f"/v1/terminals/{terminal_id}/output", params=params)

    def terminal_input(self, terminal_id: str, data: str) -> None:
        self._call("POST", f"/v1/terminals/{terminal_id}/input", {"data": data})

    def resize_terminal(self, terminal_id: str, cols: int, rows: int) -> None:
        self._call("POST", f"/v1/terminals/{terminal_id}/resize", {"cols": cols, "rows": rows})

    def stop_terminal(self, terminal_id: str) -> None:
        self._call("POST", f"/v1/terminals/{terminal_id}/stop")

    def remove_terminal(self, terminal_id: str) -> None:
        self._call("DELETE", f"/v1/terminals/{terminal_id}")

    # --- keys and the hub itself ---

    # --- starting by itself (Q8) ---

    def autostart(self) -> dict:
        return self._call("GET", "/v1/autostart")

    def set_autostart(self, **change: bool | str) -> dict:
        """login (the hub at Windows login), llama_swap (the hub starts it); llama_swap_config, llama_swap_start - the
        files chosen in the window ('' - the default; 10.3)."""
        return self._call("PUT", "/v1/autostart", change)

    # --- connections (D13): keys, tokens and addresses behind the user's password ---

    def connections(self, ticket: str = "") -> dict:
        """{password, unlocked, connections: [...], telegram: {linked}}; never a key's value."""
        return self._call("GET", "/v1/connections", headers=_unlock(ticket))

    def set_connections_password(self, password: str, old: str = "") -> dict:
        return self._call("POST", "/v1/connections/password", {"password": password, "old": old})

    def unlock_connections(self, password: str) -> str:
        return self._call("POST", "/v1/connections/unlock", {"password": password})["ticket"]

    def lock_connections(self, ticket: str) -> None:
        self._call("POST", "/v1/connections/lock", headers=_unlock(ticket))

    def set_connection(self, name: str, value: str, ticket: str) -> dict:
        return self._call("PUT", f"/v1/connections/{name}", {"value": value}, headers=_unlock(ticket))

    def delete_connection(self, name: str, ticket: str) -> dict:
        return self._call("DELETE", f"/v1/connections/{name}", headers=_unlock(ticket))

    def check_connection(self, name: str, ticket: str) -> dict:
        return self._call("POST", f"/v1/connections/{name}/check", headers=_unlock(ticket))

    def telegram_link(self, ticket: str) -> dict:
        return self._call("POST", "/v1/connections/telegram/link", headers=_unlock(ticket))

    def telegram_unlink(self, ticket: str) -> dict:
        return self._call("DELETE", "/v1/connections/telegram/link", headers=_unlock(ticket))

    def provider_models(self, name: str) -> dict:
        """All the models of a provider whose models are chosen by hand (OpenRouter), and which are chosen."""
        return self._call("GET", f"/v1/connections/{name}/models")

    def choose_models(self, name: str, ids: list[str], ticket: str) -> dict:
        return self._call("PUT", f"/v1/connections/{name}/models", {"ids": ids}, headers=_unlock(ticket))

    # --- outside MCP servers (9.5, D42): their tools for new chats and agents; changes behind the password ---

    def mcp_servers(self) -> dict:
        """{servers: [{name, transport, target, enabled, state, detail, tools: [...], env: [names]}], classes}."""
        return self._call("GET", "/v1/mcp/servers")

    def add_mcp_server(self, name: str, target: str, env: str, ticket: str) -> dict:
        """Added and started: a server fetched on its first start (npx, uvx) may take a while."""
        body = {"name": name, "target": target, "env": env}
        return self._call("POST", "/v1/mcp/servers", body, headers=_unlock(ticket), timeout=60)

    def set_mcp_server(self, name: str, ticket: str, **change) -> dict:
        """enabled, env (KEY=value lines)."""
        return self._call("PUT", f"/v1/mcp/servers/{name}", change, headers=_unlock(ticket), timeout=60)

    def restart_mcp_server(self, name: str) -> dict:
        return self._call("POST", f"/v1/mcp/servers/{name}/restart", timeout=60)

    def set_mcp_tool_class(self, name: str, tool: str, cls: str, ticket: str) -> dict:
        return self._call("PUT", f"/v1/mcp/servers/{name}/tools/{tool}", {"class": cls}, headers=_unlock(ticket))

    def delete_mcp_server(self, name: str, ticket: str) -> dict:
        return self._call("DELETE", f"/v1/mcp/servers/{name}", headers=_unlock(ticket))

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

    def card_export(self, project_id: str) -> dict:
        """What writing the card into CLAUDE.md / AGENTS.md / .clinerules would do to each (D23)."""
        return self._call("GET", f"/v1/projects/{project_id}/card/export")

    def export_card(self, project_id: str, targets: list[str]) -> dict:
        return self._call("POST", f"/v1/projects/{project_id}/card/export", {"targets": targets})

    # --- what models wrote, and the project's standing answers (stage 5, D17) ---

    def changes(self, project_id: str) -> list[dict]:
        return self._call("GET", f"/v1/projects/{project_id}/changes")["changes"]

    def change(self, change_id: int) -> dict:
        """A change with its diff."""
        return self._call("GET", f"/v1/changes/{change_id}")

    def revert_change(self, change_id: int, *, force: bool = False) -> dict:
        return self._call("POST", f"/v1/changes/{change_id}/revert", {"force": force})

    def rules(self, project_id: str) -> list[dict]:
        return self._call("GET", f"/v1/projects/{project_id}/rules")["rules"]

    def delete_rule(self, rule_id: int) -> None:
        self._call("DELETE", f"/v1/rules/{rule_id}")

    def advice(
        self, *, conversation_id: str | None = None, project_ids: list[str] | None = None, priority: str = "balanced"
    ) -> dict:
        """Which model for this chat (D11): {recommended, options: [{model_id, score, reasons, blocked, ...}]}."""
        params = {"priority": priority, "project_ids": ",".join(project_ids or [])}
        if conversation_id:
            params["conversation_id"] = conversation_id
        return self._call("GET", "/v1/advice", params=params)

    def agent_estimate(self, agent_id: str) -> dict:
        return self._call("GET", f"/v1/agents/{agent_id}/estimate")

    def stats(self, days: int = 30) -> dict:
        """{days, models: per model speed / errors / money / tasks, spending: the month and its forecast}."""
        return self._call("GET", "/v1/stats", params={"days": days})

    # --- the hub's own agents (stage 5, D16) ---

    def agents(self) -> dict:
        """{agents: profiles, tools: the tool names a profile may choose from}."""
        return self._call("GET", "/v1/agents")

    def save_agent(self, body: dict, agent_id: str | None = None) -> dict:
        if agent_id:
            return self._call("PUT", f"/v1/agents/{agent_id}", body)
        return self._call("POST", "/v1/agents", body)

    def delete_agent(self, agent_id: str) -> None:
        self._call("DELETE", f"/v1/agents/{agent_id}")

    def start_run(self, agent_id: str, task: str, project_ids: list[str] | None = None) -> dict:
        return self._call("POST", f"/v1/agents/{agent_id}/runs", {"task": task, "project_ids": project_ids})

    def runs(self, limit: int = 30) -> list[dict]:
        return self._call("GET", "/v1/runs", params={"limit": limit})["runs"]

    def cancel_run(self, run_id: str) -> dict:
        return self._call("POST", f"/v1/runs/{run_id}/cancel")

    # --- approvals and Claude Code (stage 4, D18) ---

    def approvals(self) -> list[dict]:
        """What agents wait to be allowed, oldest first, each with its preview (command, diff...)."""
        return self._call("GET", "/v1/approvals")["approvals"]

    def answer_approval(self, approval_id: str, *, allow: bool, scope: str = "once", comment: str = "") -> dict:
        body = {"allow": allow, "scope": scope, "comment": comment}
        return self._call("POST", f"/v1/approvals/{approval_id}", body)

    def programs(self) -> dict:
        """{programs, scanning, scanned_at, error, offer}: this computer's programs and the agent's permissions."""
        return self._call("GET", "/v1/programs")

    def scan_programs(self) -> dict:
        return self._call("POST", "/v1/programs/scan")

    def decide_program(self, program_id: str, decision: str | None) -> dict:
        """allow / deny / None (not decided: no)."""
        return self._call("PUT", f"/v1/programs/{program_id}", {"decision": decision})

    def put_off_program(self, program_id: str) -> dict:
        return self._call("PUT", f"/v1/programs/{program_id}", {"later": True})

    def add_program(self, path: str) -> dict:
        return self._call("POST", "/v1/programs", {"path": path})

    def remove_program(self, program_id: str) -> None:
        self._call("DELETE", f"/v1/programs/{program_id}")

    def dismiss_programs_offer(self) -> None:
        self._call("POST", "/v1/programs/offer/dismiss")

    def claude_code(self) -> dict:
        """{installed, command, detail, running}: is the user's Claude Code CLI there."""
        return self._call("GET", "/v1/claude-code")

    def claude_code_terminal(self, project_id: str, cols: int = 120, rows: int = 30) -> dict:
        return self._call("POST", f"/v1/projects/{project_id}/claude-code/terminal", {"cols": cols, "rows": rows})


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
