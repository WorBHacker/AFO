"""The approval queue in the window (stage 4, D17, D18; stage 7, D38): what agents wait to be allowed, and the
user's answers.

Claude Code's permission requests (and the hub's own agents') come from the hub with a preview: the command, the
diff of the file as it would become, the address. The open chat's requests show as a card at the top of that chat,
oldest first ("1 из N"): that chat stands still until they are answered. The shield in the title bar counts the
requests of every chat; its list says who waits and opens that chat. A request with no chat (an outside MCP client)
is answered in the list itself. The user allows once, for the rest of the chat, always in the project, or denies
with a comment the agent reads.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any

from PySide6.QtCore import Property, QObject, Signal, Slot

from .models import RowModel
from .project_rows import local_time
from .rows import model_name

if TYPE_CHECKING:
    from .controller import Controller

HUB_CLAUDE_CODE = "hub-claude-code"  # the client the hub gives the Claude Code it starts
SCOPE_WORDS = {"set_plan": "все новые планы этой задачи", "start_step": "все шаги этой задачи"}  # an agent's (D40)
PREVIEW_LINES = 400
QUEUE_ROLES = ["key", "id", "title", "tool", "kind", "icon", "who", "where", "note", "previewKind", "lines",
               "destructive", "alwaysAsk", "chatScope", "projectScope", "conversationId", "time"]  # fmt: skip


def short_path(path: str, roots: list[str]) -> str:
    """A path inside a project, from the project's folder; others as they are."""
    norm = path.replace("\\", "/")
    for root in roots:
        base = root.replace("\\", "/").rstrip("/") + "/"
        if norm.lower().startswith(base.lower()):
            return norm[len(base) :]
    return norm


def title(tool: str, data: dict[str, Any], roots: list[str]) -> str:
    """What the agent wants, in a few words: Claude Code's tools and the hub's own (their paths are the project's)."""
    path = short_path(str(data.get("file_path") or data.get("notebook_path") or data.get("path") or ""), roots)
    match tool:
        case "Bash" | "PowerShell" | "run_command":
            return "Выполнить команду"
        case "Edit" | "MultiEdit" | "edit_file":
            return f"Править {path}"
        case "Write" | "write_file":
            return f"Записать {path}"
        case "NotebookEdit":
            return f"Править блокнот {path}"
        case "WebFetch" | "web_fetch":
            return f"Открыть {data.get('url', '')}"
        case "WebSearch" | "web_search":
            return f"Искать в сети «{data.get('query', '')}»"
        case "set_plan":  # an autonomous agent's (D40)
            return "Утвердить план" + (f" (версия {data['version']})" if data.get("version", 1) != 1 else "")
        case "start_step":
            return f"Начать шаг {data.get('step')}: {data.get('title', '')}"
        case "remember":
            return "Запомнить личные данные" + (f" о «{data['about']}»" if data.get("about") else "")
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


def asker(approval: dict[str, Any], speakers: dict[str, str]) -> str:
    """Who asks: Claude Code, an outside client by its name, or the model of the chat (or agent run) it works in."""
    if approval.get("client") == HUB_CLAUDE_CODE:
        return "Claude Code"
    return approval.get("client") or speakers.get(approval.get("conversation_id") or "") or "Агент"


def approval_rows(
    approvals: list[dict[str, Any]],
    *,
    projects: dict[str, dict[str, Any]],
    chats: dict[str, str],
    speakers: dict[str, str] | None = None,
) -> list[dict[str, Any]]:
    """`speakers`: a chat's model by name, for the requests of the hub's own models (they come with no client)."""
    roots = [p.get("path", "") for p in projects.values() if p.get("path")]
    rows = []
    for a in approvals:
        data = a.get("input") if isinstance(a.get("input"), dict) else {}
        preview = a.get("preview") or {}
        who = asker(a, speakers or {})
        project = projects.get(a.get("project_id") or "", {}).get("name", "")
        chat = chats.get(a.get("conversation_id") or "", "")
        always = ("destructive" if a.get("destructive") else "network" if a.get("network")
                  else "personal" if a.get("tool") == "remember" else "")  # fmt: skip
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
            "alwaysAsk": always,  # destructive / network: asked every time, never for chat or project (D17, D19)
            "chatScope": "эта команда в этом чате" if a.get("kind") == "command"
                         else SCOPE_WORDS.get(a.get("tool", ""), f"все {a.get('tool')} в чате"),
            "projectScope": f"всегда в «{project}»" if project and not always else "",
            "conversationId": a.get("conversation_id") or "",
            "time": local_time(a.get("created_at", ""))[-5:],
        })  # fmt: skip
    return rows


def age(created_at: str, now: datetime) -> str:
    """How long a request has waited: "только что", "5 мин", "2 ч 10 мин", "3 дн"."""
    try:
        then = datetime.fromisoformat(created_at.replace("Z", "+00:00"))
    except ValueError:
        return ""
    minutes = max(0, int((now - then).total_seconds() // 60))
    if minutes < 1:
        return "только что"
    if minutes < 60:
        return f"{minutes} мин"
    if minutes < 24 * 60:
        hours, rest = divmod(minutes, 60)
        return f"{hours} ч {rest} мин" if rest else f"{hours} ч"
    return f"{minutes // (24 * 60)} дн"


def waiting_rows(
    approvals: list[dict[str, Any]],
    *,
    projects: dict[str, dict[str, Any]],
    chats: dict[str, str],
    current: str,
    now: datetime,
    speakers: dict[str, str] | None = None,
) -> list[dict[str, Any]]:
    """The shield's list: one row per chat that waits (its oldest request, how many more, how long), oldest first.
    Requests with no chat are not here: the list shows them as cards of their own."""
    groups: dict[str, list[dict[str, Any]]] = {}
    for a in approvals:
        if a.get("conversation_id"):
            groups.setdefault(a["conversation_id"], []).append(a)
    out = []
    for chat_id, asked in groups.items():
        first = asked[0]
        (row,) = approval_rows([first], projects=projects, chats={}, speakers=speakers)
        out.append({
            "conversationId": chat_id,
            "chat": chats.get(chat_id) or "Чат без названия",
            "what": f"{row['who']}: {row['title']}",
            "more": f"ещё {len(asked) - 1}" if len(asked) > 1 else "",
            "count": len(asked),
            "age": age(first.get("created_at", ""), now),
            "icon": row["icon"],
            "destructive": any(a.get("destructive") for a in asked),
            "here": chat_id == current,
        })  # fmt: skip
    return out


class Approvals(QObject):
    changed = Signal()
    hereChanged = Signal()  # the open chat's card: another request, or none
    requested = Signal(str, str)  # every new request: a notice outside the window when it is not in front (D21)

    def __init__(self, app: Controller):
        super().__init__(app)
        self.app = app
        self._pending: list[dict[str, Any]] = []
        self._card: dict[str, Any] = {}
        self._card_count = 0
        self._latest = ""  # the chat of the newest request: a click on its notice opens that chat
        # the requests with no chat, as cards in the shield's list, each its own row: an answer or a new request
        # changes its own card only, so a comment being typed in another one stays
        self.loose = RowModel(self, roles=QUEUE_ROLES)
        self.changed.connect(self._refresh)
        app.currentChanged.connect(self._refresh)
        app.chatsChanged.connect(self._refresh)  # a chat's name in the card
        app.modeChanged.connect(self._refresh)  # the card is the open chat's - or the open agent task's

    def _speakers(self) -> dict[str, str]:
        """Each chat's model by name (its latest answer's)."""
        return {c["id"]: model_name(self.app.models, c["last_model_id"])
                for c in self.app.conversations if c.get("last_model_id")}  # fmt: skip

    def _open(self) -> str:
        """What the card belongs to: the open chat, or in the Agent mode the open task (stage 9.2)."""
        if self.app.mode == "agent":
            return self.app.autonomy_view.selected() if hasattr(self.app, "autonomy_view") else ""
        return self.app.current or ""

    def _rows(self, approvals: list[dict[str, Any]]) -> list[dict[str, Any]]:
        projects = {p["id"]: p for p in self.app.projects.items}
        chats = {c["id"]: c.get("title") or "" for c in self.app.conversations}
        return approval_rows(approvals, projects=projects, chats=chats, speakers=self._speakers())

    def _refresh(self) -> None:
        """The card of the open chat (its oldest request) and the cards with no chat; a signal only on a change."""
        current = self._open()
        here = [a for a in self._pending if current and a.get("conversation_id") == current]
        card = self._rows(here[:1])[0] if here else {}
        if card != self._card or len(here) != self._card_count:
            self._card, self._card_count = card, len(here)
            self.hereChanged.emit()
        loose = [a for a in self._pending if not a.get("conversation_id")]
        self.loose.sync([{**row, "key": row["id"]} for row in self._rows(loose)])

    # ================= for QML =================

    @Property(QObject, constant=True)
    def looseModel(self) -> RowModel:
        return self.loose

    @Property(list, notify=changed)
    def items(self) -> list:
        return self._rows(self._pending)

    @Property(int, notify=changed)
    def count(self) -> int:
        return len(self._pending)

    @Property(int, notify=changed)
    def looseCount(self) -> int:
        return sum(1 for a in self._pending if not a.get("conversation_id"))

    @Property(list, notify=changed)
    def waiting(self) -> list:
        projects = {p["id"]: p for p in self.app.projects.items}
        chats = {c["id"]: c.get("title") or "" for c in self.app.conversations}
        return waiting_rows(self._pending, projects=projects, chats=chats, current=self.app.current or "",
                            now=datetime.now(UTC), speakers=self._speakers())  # fmt: skip

    @Property("QVariantMap", notify=hereChanged)
    def card(self) -> dict:
        return self._card

    @Property(int, notify=hereChanged)
    def cardCount(self) -> int:
        return self._card_count

    # ================= the hub =================

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

    # ================= where the requests are =================

    @Slot(str)
    def openChat(self, conversation_id: str) -> None:
        """The chat that waits, with its card: from the projects mode too; an autonomous agent's task opens in the
        Agent mode."""
        view = getattr(self.app, "autonomy_view", None)
        if view is not None and view.is_task(conversation_id):
            self.app.setMode("agent")
            view.select(conversation_id)
            return
        self.app.setMode("chats")
        self.app.openChat(conversation_id)

    def show_latest(self) -> None:
        """A notice was clicked: the chat of the newest request (its card waits there), or the shield's list when
        that request has no chat."""
        ids = [a.get("conversation_id") or "" for a in self._pending]
        if not ids:
            return
        chat = self._latest if self._latest in ids else ids[-1]
        if chat:
            self.openChat(chat)
        else:
            self.app.uiRequested.emit("approvals")

    def on_event(self, kind: str, payload: dict[str, Any]) -> None:
        approval = payload.get("approval") or {}
        if not approval.get("id"):
            return
        self._pending = [a for a in self._pending if a["id"] != approval["id"]]
        if kind == "approval.requested" and approval.get("status") == "pending":
            self._pending.append(approval)
            self._latest = approval.get("conversation_id") or ""
            (row,) = self._rows([approval])
            who = row["who"]
            shown = bool(self._latest) and self._latest == self._open() and self.app.mode in ("chats", "agent")
            if not shown:  # the open chat's card says it itself
                self.app.flash(f"{who} ждёт подтверждения — значок щита в заголовке", ms=8000)
            self.requested.emit(f"{who} ждёт подтверждения", " · ".join(s for s in (row["title"], row["where"]) if s))
        self.changed.emit()
