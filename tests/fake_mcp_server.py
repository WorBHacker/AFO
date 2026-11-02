"""An outside MCP server for the tests of the hub's MCP client (stage 9.5): stdio, three tools with their hints
(list_notes says its notes stay within the server: it brings in no one else's text, D19); the NOTES_OWNER from its
environment (as a server's API key would come) is in what add_note answers."""

import os

from mcp.server.mcpserver import MCPServer
from mcp.types import ToolAnnotations

server = MCPServer("fake-notes")
NOTES: list[str] = []


@server.tool(annotations=ToolAnnotations(readOnlyHint=True, openWorldHint=False))
def list_notes() -> str:
    """The notes kept so far."""
    return "\n".join(NOTES) or "no notes"


@server.tool()
def add_note(text: str) -> str:
    """Keep a note."""
    NOTES.append(text)
    return f"kept for {os.environ.get('NOTES_OWNER', 'nobody')}: {text}"


@server.tool(annotations=ToolAnnotations(destructiveHint=True))
def wipe_notes() -> str:
    """Delete every note."""
    NOTES.clear()
    return "wiped"


if __name__ == "__main__":
    server.run()
