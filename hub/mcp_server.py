"""The hub's MCP server (stage 4, D32): the ecosystem for MCP clients - Cline, Claude Code - at /mcp.

Streamable HTTP, stateless, answers as JSON. Every request carries a client's token (`Authorization: Bearer`), and
McpGate checks it before the MCP layer sees the request (D31); a tool then finds its client by that token. The
tools only read (D17), like a chat's (hub/tools.py), through the same secret filter. A client counts as cloud until
it is marked local (D32): then it works only with the projects that allow the cloud and sees the others by name
(D10, D27). The map and the cards are also resources: `hub://map`, `hub://project/{id}/card`.

One tool is not for models: approval_prompt, Claude Code's --permission-prompt-tool (D18). It puts Claude Code's
permission request into the app's approval queue and answers when the user has (hub/approvals.py).
"""

from __future__ import annotations

import asyncio
import json
from typing import Annotated, Any

from mcp.server.mcpserver import Context, MCPServer
from mcp.server.mcpserver.exceptions import ToolError
from mcp.types import ToolAnnotations
from pydantic import Field
from starlette.datastructures import Headers
from starlette.responses import JSONResponse
from starlette.types import ASGIApp, Receive, Scope, Send

from common import __version__

from . import cards, context, links, notes, projects, store
from .approvals import trusted_skips
from .auth import Client, bearer_token, find_client
from .claude_code import CONVERSATION_HEADER, HUB_CLIENT
from .projects import Project, ProjectError
from .secret_filter import clean
from .tools import ToolContext, run_tool
from .tools import ToolError as WrongInput

INSTRUCTIONS = (
    "AFO hub: the projects on the user's computer, their code, git, terminals, processes and how they link. "
    "Start with get_ecosystem_map, then get_project_card for the project you work on; read code with the other "
    "tools. Everything here only reads. Secrets appear as [REDACTED]."
)
READ_ONLY = ToolAnnotations(readOnlyHint=True, openWorldHint=False)
EDITS = frozenset({"Edit", "Write", "MultiEdit", "NotebookEdit"})  # Claude Code's tools that change files
CHANGES = EDITS | {"Bash", "PowerShell"}  # and what a read-only chat refuses it (PowerShell: its Windows shell)
ASKS_THE_USER = ToolAnnotations(readOnlyHint=False, destructiveHint=False, openWorldHint=False)
# every tool answers with one text block, no structured copy (`structured_output=False`): Claude Code takes the
# structured one when it is there, and its --permission-prompt-tool then refuses the answer - «Permission prompt
# tool returned an invalid result. Expected a single text block…» (found with the real CLI 2.1.308, 2026-10-28)
EVENTS_MAX = 100
IN_PROCESS = Client(id="", name="in-process", cloud=True)  # a call without HTTP headers: treated as cloud

ProjectName = Annotated[str, Field(description="The project's name, as get_ecosystem_map lists it.")]
FilePath = Annotated[str, Field(description="A path relative to the project's root.")]


class McpGate:
    """Lets a request through to the MCP app only with a valid client token; refuses the rest with 401."""

    def __init__(self, app: Any, inner: ASGIApp):
        self.app = app  # the hub's FastAPI app: its state has the database
        self.inner = inner

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] == "http":
            token = bearer_token(Headers(scope=scope).get("authorization"))
            db = self.app.state.db
            client = await db.acall(lambda conn: find_client(conn, token)) if token else None
            if client is None:
                body = {"error": "unauthorized", "detail": "a client token is needed: Authorization: Bearer <token>"}
                await JSONResponse(body, status_code=401)(scope, receive, send)
                return
        await self.inner(scope, receive, send)


def build(app: Any) -> tuple[MCPServer, ASGIApp]:
    """The MCP server over the hub's state, and its ASGI app (gated) for the /mcp route."""
    server = MCPServer("AFO hub", instructions=INSTRUCTIONS, version=__version__)

    async def caller(ctx: Context) -> Client:
        headers = ctx.headers
        if headers is None:
            return IN_PROCESS
        token = bearer_token(headers.get("authorization"))
        client = await app.state.db.acall(lambda conn: find_client(conn, token)) if token else None
        if client is None:  # the gate lets none through; this guards a server used without it
            raise ToolError("no valid client token")
        return client

    async def everything() -> list[Project]:
        return await app.state.db.acall(projects.list_projects)

    async def tool_context(ctx: Context) -> tuple[Client, ToolContext]:
        client = await caller(ctx)
        usable = [p for p in await everything() if p.cloud_ok or not client.cloud]
        state = app.state
        return client, ToolContext(usable, terminals=state.terminals, processes=state.processes, symbols=state.symbols)

    def pick(tools: ToolContext, name: str) -> Project:
        try:
            return tools.project(name)
        except WrongInput as exc:
            raise ToolError(str(exc)) from exc

    async def run(name: str, args: dict[str, Any], ctx: Context) -> str:
        _client, tools = await tool_context(ctx)
        output, error = await run_tool(name, {k: v for k, v in args.items() if v is not None}, tools)
        if error:
            raise ToolError(output)
        return output

    # --- the map (D22 L0, D25) ---

    @server.tool(annotations=READ_ONLY, structured_output=False)
    async def get_ecosystem_map(ctx: Context) -> str:
        """All projects on this computer: name, one-line description, stack - and the confirmed links between
        them. Projects you may not read are listed by name only."""
        client = await caller(ctx)
        found, all_links = await asyncio.gather(everything(), app.state.db.acall(links.list_links))
        layer = context.map_layer_for_cloud if client.cloud else context.map_layer
        return clean(await asyncio.to_thread(layer, found, all_links))

    @server.tool(annotations=READ_ONLY, structured_output=False)
    async def get_project_card(project: ProjectName, ctx: Context) -> str:
        """What to know about a project before reading it: its card confirmed by the user (or, without one, its
        stack, structure and README), and the user's notes for models."""
        _client, tools = await tool_context(ctx)
        p = pick(tools, project)
        card, written = await app.state.db.acall(
            lambda conn: (cards.confirmed_text(conn, p.id), notes.list_notes(conn, p.id))
        )
        missing = await asyncio.to_thread(cards.missing_paths, p, card) if card else []
        text = await asyncio.to_thread(context.project_layer, p, card, missing)
        if written:
            text += "\n\n" + notes.notes_layer(p.name, written)
        return clean(text)

    @server.tool(annotations=READ_ONLY, structured_output=False)
    async def list_relations(ctx: Context) -> str:
        """The confirmed links between projects: which uses which, what they share, how (D25)."""
        client = await caller(ctx)
        found, all_links = await asyncio.gather(everything(), app.state.db.acall(links.list_links))
        lines = context.link_lines(found, all_links, cloud=client.cloud)
        return clean("\n".join(lines)) if lines else "no confirmed links between projects"

    # --- code (D26) ---

    @server.tool(annotations=READ_ONLY, structured_output=False)
    async def list_files(
        project: ProjectName,
        ctx: Context,
        path: Annotated[str, Field(description="A folder inside the project; empty for its root.")] = "",
        depth: Annotated[int, Field(description="How many levels, 1 to 3.")] = 1,
    ) -> str:
        """A folder of a project: subfolders and files with sizes, as git shows them; secret files marked."""
        return await run("list_files", {"project": project, "path": path, "depth": depth}, ctx)

    @server.tool(annotations=READ_ONLY, structured_output=False)
    async def read_file(
        project: ProjectName,
        path: FilePath,
        ctx: Context,
        start_line: Annotated[int, Field(description="The first line to read.")] = 1,
        line_count: Annotated[int, Field(description="How many lines, at most 400.")] = 300,
    ) -> str:
        """A text file of a project with line numbers, a part at a time; secret files cannot be read."""
        args = {"project": project, "path": path, "start_line": start_line, "line_count": line_count}
        return await run("read_file", args, ctx)

    @server.tool(annotations=READ_ONLY, structured_output=False)
    async def search_code(
        project: ProjectName,
        query: Annotated[str, Field(description="What to look for.")],
        ctx: Context,
        regex: Annotated[bool, Field(description="The query is a regular expression (a|b|c: any of them).")] = False,
        glob: Annotated[str, Field(description="Only files matching this, e.g. *.py.")] = "",
    ) -> str:
        """Lines of a project's files that match, with file and line number (ripgrep; up to 50). For an idea
        rather than a name, several words the code would use at once, as a regex: permission|authoriz|forbidden."""
        return await run("search_code", {"project": project, "query": query, "regex": regex, "glob": glob}, ctx)

    @server.tool(annotations=READ_ONLY, structured_output=False)
    async def find_symbol(
        project: ProjectName,
        name: Annotated[str, Field(description="A name or part of it; Class.method; or words of what it does.")],
        ctx: Context,
        kind: Annotated[str, Field(description="Only this kind: function, method, class, interface, type...")] = "",
    ) -> str:
        """Where a function, class, method, interface or type is defined (the hub's tree-sitter index) - by name,
        or by several English words of what it does: its name as words, comments and docstring, best first."""
        return await run("find_symbol", {"project": project, "name": name, "kind": kind or None}, ctx)

    @server.tool(annotations=READ_ONLY, structured_output=False)
    async def file_outline(project: ProjectName, path: FilePath, ctx: Context) -> str:
        """A code file's classes, functions and methods with their lines, nested as in the file."""
        return await run("file_outline", {"project": project, "path": path}, ctx)

    # --- git ---

    @server.tool(annotations=READ_ONLY, structured_output=False)
    async def git_status(project: ProjectName, ctx: Context) -> str:
        """A project's branch, commits ahead / behind, changed files, recent commits."""
        return await run("git_status", {"project": project}, ctx)

    @server.tool(annotations=READ_ONLY, structured_output=False)
    async def git_diff(
        project: ProjectName,
        ctx: Context,
        path: Annotated[str, Field(description="One file; all changes without it.")] = "",
    ) -> str:
        """A project's uncommitted changes as a unified diff (up to 400 lines)."""
        return await run("git_diff", {"project": project, "path": path}, ctx)

    @server.tool(annotations=READ_ONLY, structured_output=False)
    async def git_log(
        project: ProjectName,
        ctx: Context,
        count: Annotated[int, Field(description="How many commits, up to 100.")] = 20,
    ) -> str:
        """A project's recent commits: short sha, date, author, subject."""
        return await run("git_log", {"project": project, "count": max(1, min(count, 100))}, ctx)  # the chat's own

    # --- what is happening (D4) ---

    @server.tool(annotations=READ_ONLY, structured_output=False)
    async def list_processes(project: ProjectName, ctx: Context) -> str:
        """Processes running in a project's folder: command, listening ports, CPU, memory."""
        return await run("list_processes", {"project": project}, ctx)

    @server.tool(annotations=READ_ONLY, structured_output=False)
    async def list_terminals(project: ProjectName, ctx: Context) -> str:
        """A project's terminals in the hub: running ones and recent ones, with their state."""
        return await run("terminal_output", {"project": project}, ctx)

    @server.tool(annotations=READ_ONLY, structured_output=False)
    async def read_terminal(
        project: ProjectName,
        terminal: Annotated[str, Field(description="The terminal's id or title, from list_terminals.")],
        ctx: Context,
    ) -> str:
        """The last 100 lines a terminal of the project printed."""
        return await run("terminal_output", {"project": project, "terminal": terminal}, ctx)

    @server.tool(annotations=READ_ONLY, structured_output=False)
    async def get_events(
        ctx: Context,
        project: Annotated[str, Field(description="Only this project's events (optional).")] = "",
        limit: Annotated[int, Field(description="How many, newest first, up to 100.")] = 30,
    ) -> str:
        """What happened lately: commits and branch switches, ports opened and closed, terminals started and
        finished, processes that stopped."""
        _client, tools = await tool_context(ctx)
        ids = {p.id: p.name for p in tools.projects}
        if project:
            p = pick(tools, project)
            ids = {p.id: p.name}
        if not ids:
            return "no projects you may read"
        count = max(1, min(limit, EVENTS_MAX))

        def read(conn):
            marks = ", ".join("?" * len(ids))
            return conn.execute(
                f"SELECT ts, type, project_id, payload FROM events WHERE project_id IN ({marks}) AND type IN "
                f"({', '.join('?' * len(context.NEWS_TYPES))}) ORDER BY id DESC LIMIT ?",
                [*ids, *sorted(context.NEWS_TYPES), count],
            ).fetchall()

        rows = await app.state.db.acall(read)
        lines = [
            f"{r['ts'][:16].replace('T', ' ')} UTC {ids[r['project_id']]}: "
            + context.news_line({"type": r["type"], "payload": json.loads(r["payload"])})
            for r in rows
        ]
        return clean("\n".join(lines)) if lines else "nothing happened lately"

    # --- Claude Code's permission requests (D18) ---

    @server.tool(annotations=ASKS_THE_USER, structured_output=False)
    async def approval_prompt(
        tool_name: Annotated[str, Field(description="The tool Claude Code wants to run.")],
        input: Annotated[dict[str, Any], Field(description="Its input.")],  # the name Claude Code sends
        ctx: Context,
        tool_use_id: Annotated[str | None, Field(description="The call's id.")] = None,
    ) -> str:
        """Not for models: Claude Code's --permission-prompt-tool. Asks the user in the AFO app whether the
        tool may run, waits for the answer, and returns it as Claude Code expects."""
        client = await caller(ctx)
        conversation = ctx.headers.get(CONVERSATION_HEADER) if ctx.headers is not None else None
        turn = app.state.claude_code.turns.get(conversation) if client.name == HUB_CLIENT and conversation else None
        if turn is not None and tool_name == "AskUserQuestion":  # its own questions: in the chat, answers to pick
            return await answers_to(turn.conversation_id, input)
        mode = "ask"
        if turn is not None:
            mode = (await app.state.db.acall(lambda conn: store.context_settings(conn, turn.conversation_id)))["mode"]
        if mode == "read_only" and tool_name in CHANGES:  # the chat's rights (D17) hold for Claude Code too
            return json.dumps({"behavior": "deny", "message": "This chat is read-only: say the change in words, the "
                                                              "user makes it."})  # fmt: skip
        reads = tool_name in ("WebFetch", "WebSearch")
        tainted = False  # it has read the web: its writes and commands are asked every time, trusted or not (D19)
        if turn is not None:
            tainted = not reads and await app.state.chat.read_the_web(turn.conversation_id)
            if reads:
                app.state.chat.outside.add(turn.conversation_id)
        if reads and turn is not None and not await app.state.chat.web_trusted(turn.conversation_id):
            mode = "ask"  # a local-only project in the chat: its code could leave in the address or query (D46)
        root = turn.root if turn else None
        unsaved = app.state.approvals.unsaved.holds(tool_name, input, root)
        # a trusted chat: all but destruction, the hub's data, and a file the window's editor holds unsaved (D47)
        if mode == "trusted" and not tainted and not unsaved and trusted_skips(tool_name, input, root):
            return json.dumps({"behavior": "allow", "updatedInput": input}, ensure_ascii=False)
        decision = await app.state.approvals.ask(
            tool_name,
            input,
            conversation_id=turn.conversation_id if turn else None,
            project_id=turn.project.id if turn and turn.project else None,
            client_id=client.id or None,
            root=root,
            cover=not tainted,
        )
        if decision.allow and decision.hunks is not None and turn is not None and turn.project is not None:
            # a part (17.3): its own tool would put all of it - the hub writes the part, and Claude Code reads so
            said = await app.state.chat.write_part(turn.conversation_id, turn.project, tool_name, input, decision)
            return json.dumps({"behavior": "deny", "message": said}, ensure_ascii=False)
        if decision.allow:
            return json.dumps({"behavior": "allow", "updatedInput": input}, ensure_ascii=False)
        return json.dumps({"behavior": "deny", "message": decision.comment}, ensure_ascii=False)

    async def answers_to(conversation_id: str, data: dict[str, Any]) -> str:
        """Claude Code's AskUserQuestion: each question asked in the chat with its options; the answers go back as
        its input's `answers` ({question: answer})."""
        answers: dict[str, str] = {}
        for item in data.get("questions") or []:
            question = " ".join(str(item.get("question") or "").split())[:2000] if isinstance(item, dict) else ""
            if not question:
                continue
            answer = await app.state.autonomy.ask_question(conversation_id, question, item.get("options"),
                                                           asyncio.Event())  # fmt: skip
            if answer is None:
                return json.dumps({"behavior": "deny", "message": "The user did not answer (the chat was stopped)."})
            answers[question] = answer
        return json.dumps({"behavior": "allow", "updatedInput": {**data, "answers": answers}}, ensure_ascii=False)

    # --- resources ---

    @server.resource("hub://map", name="ecosystem-map", mime_type="text/plain")
    async def map_resource() -> str:
        """The ecosystem map, as a cloud client may see it (a resource has no caller to ask about)."""
        found, all_links = await asyncio.gather(everything(), app.state.db.acall(links.list_links))
        return clean(await asyncio.to_thread(context.map_layer_for_cloud, found, all_links))

    @server.resource("hub://project/{project_id}/card", name="project-card", mime_type="text/markdown")
    async def card_resource(project_id: str, ctx: Context) -> str:
        """A project's confirmed card."""
        client = await caller(ctx)
        try:
            p = await app.state.db.acall(lambda conn: projects.get_project(conn, project_id))
        except ProjectError as exc:
            raise ToolError(str(exc)) from exc
        if client.cloud and not p.cloud_ok:
            raise ToolError(f"«{p.name}» is local-only: a cloud client may not read it (D10)")
        card = await app.state.db.acall(lambda conn: cards.confirmed_text(conn, p.id))
        return clean(card) if card else f"«{p.name}» has no confirmed card yet"

    gated = McpGate(app, server.streamable_http_app(stateless_http=True, json_response=True))
    return server, gated
