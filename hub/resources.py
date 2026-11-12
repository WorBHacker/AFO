"""What local models need on this computer, looked at on the disk (stage 39): llama-swap's program, its config, the
llama-server the config names and llama-bench beside it.

Not «who answers on llama-swap's port»: another program there answers too - Tomcat in IntelliJ on 8080 says 404, and
that was taken for llama-swap (39.1, the plan's finding 1). A llama-swap that answers as llama-swap (its /running) -
the person's own, run in a way AFO does not know - counts as there.
"""

from __future__ import annotations

from typing import Any

import httpx

from . import autostart, llama_config
from .tuning import BENCH, server_path

ANSWER_S = 2.0


def scan() -> dict[str, Any]:
    """{ready, missing: [{what, path}], start, config, server} - blocking (the disk): call it in a thread. `ready`:
    llama-swap's program, its config and llama-server are there; llama-bench missing is only said - tuning needs it,
    answers do not."""
    start = autostart.llama_swap_script()
    config = llama_config.config_path()
    missing: list[dict[str, str]] = []
    if not start.is_file():
        missing.append({"what": "llama-swap", "path": str(start)})
    server = None
    if not config.is_file():
        missing.append({"what": "config", "path": str(config)})
    else:
        try:
            server = server_path(config.read_text(encoding="utf-8"))
        except OSError, llama_config.ConfigError:
            server = None
        if server is None or not str(server) or not server.is_file():
            missing.append({"what": "llama-server", "path": str(server or "")})
        elif not server.with_name(BENCH).is_file():
            missing.append({"what": "llama-bench", "path": str(server.with_name(BENCH))})
    return {"ready": all(m["what"] == "llama-bench" for m in missing), "missing": missing, "start": str(start),
            "config": str(config), "server": str(server or "")}  # fmt: skip


async def llama_swap_answers(url: str, transport: httpx.AsyncBaseTransport | None = None) -> bool:
    """llama-swap answers at `url`: its /running, as the status monitor reads it - not just any program on its port."""
    try:
        async with httpx.AsyncClient(timeout=ANSWER_S, transport=transport) as http:
            response = await http.get(f"{url.rstrip('/')}/running")
        return response.status_code == 200 and isinstance(response.json().get("running"), list)
    except httpx.HTTPError, ValueError, AttributeError:
        return False
