"""The model edits the selection in the window's editor (D47, step 14.7): Ctrl+I, «что сделать».

The role «Правка в редакторе» (`edit`) gets the selected code between <SELECTION> marks, with some code around it,
the file's path and the person's words; it answers with the replacement alone. The window shows it as a diff in the
text, and the person takes it (one undo step) or not; the file is saved by them, so there is no approval card.

Asked by the person, a local model may be loaded for it (unlike the grey text, hub/completion.py). Where the code goes
is as everywhere (D10, D12): a cloud model only for a project that allows the cloud, through the secret filter; a
secret file is not edited. The request is in the usage table.
"""

from __future__ import annotations

import asyncio
import re
import time
from typing import Any

from . import store
from .completion import chosen
from .db import Database
from .gateway.base import cost_usd
from .gateway.registry import Gateway
from .projects import Project
from .secret_filter import clean, is_secret_file

ROLE = "edit"
AROUND = 2000  # characters of code before and after the selection that go with it
TIMEOUT_S = 120.0
PROMPT = (
    "You edit code as the user asks. Reply with only the code that replaces the part between <SELECTION> and "
    "</SELECTION> - no explanation, no Markdown fence, nothing from before or after it. Keep its indentation."
)
REFUSALS = {
    "no_model": "Правка: модель не выбрана — роль «Правка в редакторе» в настройках моделей",
    "off": "Правка: модель недоступна",
    "local_only": "Правка: облачная модель, а проект — только локально (D10)",
    "secret": "Правка: файлы с секретами модели не правят",
}
FENCE = re.compile(r"^\s*```[\w+-]*\n(?P<code>.*?)\n?```\s*$", re.DOTALL)


class Refused(Exception):
    def __init__(self, reason: str):
        super().__init__(REFUSALS[reason])
        self.reason = reason


def unfenced(text: str) -> str:
    """The code without a Markdown fence a model put round it anyway."""
    found = FENCE.match(text)
    return found.group("code") if found else text


async def rewrite(db: Database, gateway: Gateway, project: Project, path: str, selected: str, before: str,
                  after: str, instruction: str) -> dict[str, Any]:  # fmt: skip
    """{replacement, model}; Refused when it may not be done."""
    if is_secret_file(path):
        raise Refused("secret")
    model_id = await db.acall(lambda conn: chosen(conn, ROLE))
    if not model_id:
        raise Refused("no_model")
    model = await db.acall(lambda conn: store.get_model(conn, model_id))
    if model is None or not model["enabled"]:
        raise Refused("off")
    if model["locality"] == "cloud":
        if not project.cloud_ok:
            raise Refused("local_only")
        selected, before, after, instruction = clean(selected), clean(before), clean(after), clean(instruction)
    provider, ref = gateway.resolve(model_id)
    messages = [{"role": "system", "content": PROMPT},
                {"role": "user", "content": f"File: {path}\nWhat to do: {instruction}\n\n{before[-AROUND:]}<SELECTION>"
                                            f"{selected}</SELECTION>{after[:AROUND]}"}]  # fmt: skip
    pieces: list[str] = []
    usage: dict[str, int] = {}
    began = time.monotonic()
    cancel = asyncio.Event()

    async def run() -> None:
        async for event in provider.stream_chat(ref, messages, "low", cancel):
            if event.kind == "content":
                pieces.append(event.text)
            elif event.kind == "end" and event.usage:
                usage.update(event.usage)

    try:
        await asyncio.wait_for(run(), TIMEOUT_S)
    finally:
        cancel.set()
        meta = {"kind": "edit", "stats": {"total_s": round(time.monotonic() - began, 3)}}
        await db.acall(lambda conn: store.record_usage(
            conn, client_id=None, conversation_id=None, message_id=None, model_id=model_id, usage=usage,
            cost_usd=cost_usd(usage, model["prices"]), meta=meta))  # fmt: skip
    return {"replacement": unfenced("".join(pieces)), "model": model_id}
