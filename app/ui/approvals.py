"""The approval queue in the window (stage 4, D17, D18): what agents wait to be allowed, and the user's answers.

Claude Code's permission requests (and later the hub's own agents') come from the hub with a preview: the command,
the diff of the file as it would become, the address. The user allows once, allows for the rest of the chat, or
denies with a comment the agent reads. The title bar shows how many wait; a request from the open chat opens the
queue at once, since that chat stands still until it is answered.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from PySide6.QtCore import Property, QObject, Signal, Slot

from .project_rows import local_time

if TYPE_CHECKING:
    from .controller import Controller

HUB_CLAUDE_CODE = "hub-claude-code"  # the client the hub gives the Claude Code it starts
PREVIEW_LINES = 400


def short_path(path: str, roots: list[str]) -> str:
    """A path inside a project, from the project's folder; others as they are."""
    norm = path.replace("\\", "/")
    for root in roots:
        base = root.replace("\\", "/").rstrip("/") + "/"
        if norm.lower().startswith(base.lower()):
            return norm[len(base) :]
    return norm


def title(tool: str, data: dict[str, Any], roots: list[str]) -> str:
    """What the agent wants, in a few words."""
    path = short_path(str(data.get("file_path") or data.get("notebook_path") or ""), roots)
    match tool:
        case "Bash" | "PowerShell":
            return "Выполнить команду"
        case "Edit" | "MultiEdit":
            return f"Править {path}"
        case "Write":
            return f"Записать {path}"
        case "NotebookEdit":
            return f"Править блокнот {path}"
        case "WebFetch":
            return f"Открыть {data.get('url', '')}"
        case "WebSearch":
            return f"Искать в сети «{data.get('query', '')}»"
    return f"Вызвать {tool.removeprefix('mcp__').replace('__', ' · ')}"


def preview_lines(preview: dict[str, Any]) -> list[dict[str, str]]:
    """The preview as lines for the window: a diff's lines marked added / removed / place."""
    text = str(preview.get("text") or "")
    lines = text.splitlines()[:PREVIEW_LINES]
    if preview.get("kind") != "diff":
        return [{"text": line, "mark": ""} for line in lines]
    out = []
    for line in lines:
        if line.startswith(("+++", "---")):
            mark = "file"
        elif line.startswith("@@"):
            mark = "hunk"
        else:
            mark = {"+": "add", "-": "del"}.get(line[:1], "")
        out.append({"text": line, "mark": mark})
    return out


def approval_rows(
    approvals: list[dict[str, Any]], *, projects: dict[str, dict[str, Any]], chats: dict[str, str]
) -> list[dict[str, Any]]:
    roots = [p.get("path", "") for p in projects.values() if p.get("path")]
    rows = []
    for a in approvals:
        data = a.get("input") if isinstance(a.get("input"), dict) else {}
        preview = a.get("preview") or {}
        who = "Claude Code" if a.get("client") == HUB_CLAUDE_CODE else a.get("client") or "агент"
        project = projects.get(a.get("project_id") or "", {}).get("name", "")
        chat = chats.get(a.get("conversation_id") or "", "")
        rows.append({
            "id": a["id"],
            "title": title(a.get("tool", ""), data, roots),
            "tool": a.get("tool", ""),
            "kind": a.get("kind", "other"),
            "icon": {"command": "terminal", "write": "edit", "network": "cloud"}.get(a.get("kind", ""), "shield"),
            "who": who,
            "where": " · ".join(s for s in (project, f"«{chat}»" if chat else "") if s),
            "note": short_path(str(preview.get("note") or ""), roots) if preview.get("kind") == "diff"
                    else str(preview.get("note") or ""),
            "previewKind": preview.get("kind", "text"),
            "lines": preview_lines(preview),
            "destructive": bool(a.get("destructive")),
            "chatScope": "эта команда в этом чате" if a.get("kind") == "command" else f"все {a.get('tool')} в чате",
            "conversationId": a.get("conversation_id") or "",
            "time": local_time(a.get("created_at", ""))[-5:],
        })  # fmt: skip
    return rows


class Approvals(QObject):
    changed = Signal()
    arrived = Signal()  # a request from the chat open now: the window shows the queue

    def __init__(self, app: Controller):
        super().__init__(app)
        self.app = app
        self._pending: list[dict[str, Any]] = []

    @Property(list, notify=changed)
    def items(self) -> list:
        projects = {p["id"]: p for p in self.app.projects.items}
        chats = {c["id"]: c.get("title") or "" for c in self.app.conversations}
        return approval_rows(self._pending, projects=projects, chats=chats)

    @Property(int, notify=changed)
    def count(self) -> int:
        return len(self._pending)

    @Slot()
    def load(self) -> None:
        def show(found: list[dict[str, Any]]) -> None:
            self._pending = found
            self.changed.emit()

        self.app._call(lambda: self.app.client.approvals(), show, lambda _e: None)

    @Slot(str, bool, str, str)
    def answer(self, approval_id: str, allow: bool, scope: str, comment: str) -> None:
        """The answer goes to the hub, and the request leaves the queue at once; it comes back if the hub refuses."""
        self._pending = [a for a in self._pending if a["id"] != approval_id]
        self.changed.emit()

        def failed(error: Exception) -> None:
            self.app.show_error(f"Ответ не принят: {error}")
            self.load()

        self.app._call(
            lambda: self.app.client.answer_approval(approval_id, allow=allow, scope=scope or "once", comment=comment),
            None,
            failed,
        )

    def on_event(self, kind: str, payload: dict[str, Any]) -> None:
        approval = payload.get("approval") or {}
        if not approval.get("id"):
            return
        self._pending = [a for a in self._pending if a["id"] != approval["id"]]
        if kind == "approval.requested" and approval.get("status") == "pending":
            self._pending.append(approval)
            self.app.flash(f"{'Claude Code' if approval.get('client') == HUB_CLAUDE_CODE else 'Агент'} ждёт "
                           "подтверждения — значок щита в заголовке", ms=8000)  # fmt: skip
            if approval.get("conversation_id") and approval["conversation_id"] == self.app.current:
                self.arrived.emit()
        self.changed.emit()
