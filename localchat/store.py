"""Chats on disk: one JSON file per chat in %APPDATA%\\LocalChat\\chats."""
from __future__ import annotations

import json
import time
import uuid
from dataclasses import asdict, dataclass, field

from .config import CHATS_DIR


@dataclass
class Message:
    role: str                     # "user" or "assistant"
    content: str = ""
    reasoning: str = ""
    model: str = ""
    stats: dict = field(default_factory=dict)


@dataclass
class Chat:
    id: str = field(default_factory=lambda: uuid.uuid4().hex)
    title: str = "Новый чат"
    created: float = field(default_factory=time.time)
    updated: float = field(default_factory=time.time)
    messages: list[Message] = field(default_factory=list)

    def api_messages(self, system_prompt: str) -> list[dict]:
        """History in OpenAI form; reasoning is not sent back to the model."""
        out = [{"role": "system", "content": system_prompt}] if system_prompt.strip() else []
        out += [{"role": m.role, "content": m.content} for m in self.messages if m.content or m.role == "user"]
        return out

    def save(self) -> None:
        CHATS_DIR.mkdir(parents=True, exist_ok=True)
        self.updated = time.time()
        data = asdict(self)
        (CHATS_DIR / f"{self.id}.json").write_text(json.dumps(data, ensure_ascii=False, indent=1), encoding="utf-8")

    def delete(self) -> None:
        (CHATS_DIR / f"{self.id}.json").unlink(missing_ok=True)

    @classmethod
    def from_dict(cls, d: dict) -> Chat:
        msgs = [Message(**m) for m in d.get("messages", [])]
        return cls(id=d["id"], title=d.get("title", "Чат"), created=d.get("created", 0),
                   updated=d.get("updated", 0), messages=msgs)


def load_chats() -> list[Chat]:
    chats = []
    for p in CHATS_DIR.glob("*.json"):
        try:
            chats.append(Chat.from_dict(json.loads(p.read_text(encoding="utf-8"))))
        except (OSError, ValueError, KeyError, TypeError):
            continue
    return sorted(chats, key=lambda c: c.updated, reverse=True)
