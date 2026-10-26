"""Database helpers for the model catalog, conversations, messages and usage (schema in db/migrations)."""

from __future__ import annotations

import json
import sqlite3
import uuid
from dataclasses import dataclass
from typing import Any

from .db import utc_now
from .gateway import Model

# --- models catalog ---


def upsert_model(conn: sqlite3.Connection, model: Model) -> None:
    conn.execute(
        """
        INSERT INTO models (id, provider, model_ref, name, locality, capabilities, prices, enabled, updated_at)
        VALUES (:id, :provider, :model_ref, :name, :locality, :caps, :prices, 1, :now)
        ON CONFLICT (id) DO UPDATE SET
            provider = excluded.provider, model_ref = excluded.model_ref, name = excluded.name,
            locality = excluded.locality, capabilities = excluded.capabilities,
            prices = excluded.prices, enabled = 1, updated_at = excluded.updated_at
        """,
        {
            "id": model.id,
            "provider": model.provider,
            "model_ref": model.model_ref,
            "name": model.name,
            "locality": model.locality,
            "caps": json.dumps(model.capabilities, ensure_ascii=False),
            "prices": json.dumps(model.prices),
            "now": utc_now(),
        },
    )


def disable_models_except(conn: sqlite3.Connection, provider: str, keep_ids: list[str]) -> int:
    """The provider's models it no longer lists stay in the table (old messages and usage name them) but are
    no longer offered."""
    marks = ",".join("?" * len(keep_ids))
    where = f"provider = ? AND enabled = 1 AND id NOT IN ({marks})" if keep_ids else "provider = ? AND enabled = 1"
    return conn.execute(
        f"UPDATE models SET enabled = 0, updated_at = ? WHERE {where}", (utc_now(), provider, *keep_ids)
    ).rowcount


def list_models(conn: sqlite3.Connection, *, enabled_only: bool = True) -> list[dict[str, Any]]:
    where = "WHERE enabled = 1" if enabled_only else ""
    rows = conn.execute(f"SELECT * FROM models {where} ORDER BY locality, id").fetchall()
    return [_model_row(r) for r in rows]


def get_model(conn: sqlite3.Connection, model_id: str) -> dict[str, Any] | None:
    row = conn.execute("SELECT * FROM models WHERE id = ?", (model_id,)).fetchone()
    return _model_row(row) if row else None


def _model_row(row: sqlite3.Row) -> dict[str, Any]:
    return {
        "id": row["id"],
        "provider": row["provider"],
        "model_ref": row["model_ref"],
        "name": row["name"],
        "locality": row["locality"],
        "capabilities": json.loads(row["capabilities"]),
        "prices": json.loads(row["prices"]),
        "enabled": bool(row["enabled"]),
    }


# --- conversations and messages ---


@dataclass(frozen=True)
class MessageRow:
    id: int
    seq: int
    role: str
    model_id: str | None
    content: list[dict[str, Any]]
    created_at: str
    provider_raw: dict[str, Any] | None = None  # for the gateway only; clients get `content`

    def to_json(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "seq": self.seq,
            "role": self.role,
            "model_id": self.model_id,
            "content": self.content,
            "created_at": self.created_at,
        }


def create_conversation(conn: sqlite3.Connection, *, kind: str = "chat", title: str = "") -> str:
    cid = uuid.uuid4().hex
    conn.execute("INSERT INTO conversations (id, kind, title) VALUES (?, ?, ?)", (cid, kind, title))
    return cid


def conversation_exists(conn: sqlite3.Connection, conversation_id: str) -> bool:
    return conn.execute("SELECT 1 FROM conversations WHERE id = ?", (conversation_id,)).fetchone() is not None


def get_title(conn: sqlite3.Connection, conversation_id: str) -> str | None:
    row = conn.execute("SELECT title FROM conversations WHERE id = ?", (conversation_id,)).fetchone()
    return row["title"] if row else None


def list_conversations(conn: sqlite3.Connection) -> list[dict[str, Any]]:
    rows = conn.execute(
        "SELECT id, kind, title, created_at, updated_at FROM conversations WHERE archived = 0 ORDER BY updated_at DESC"
    ).fetchall()
    return [dict(r) for r in rows]


def set_title(conn: sqlite3.Connection, conversation_id: str, title: str) -> bool:
    return bool(
        conn.execute(
            "UPDATE conversations SET title = ?, updated_at = ? WHERE id = ?", (title, utc_now(), conversation_id)
        ).rowcount
    )


def delete_conversation(conn: sqlite3.Connection, conversation_id: str) -> bool:
    return bool(conn.execute("DELETE FROM conversations WHERE id = ?", (conversation_id,)).rowcount)


def add_message(
    conn: sqlite3.Connection,
    conversation_id: str,
    role: str,
    content: list[dict[str, Any]],
    *,
    model_id: str | None = None,
    provider_raw: dict | None = None,
) -> MessageRow:
    now = utc_now()
    seq = conn.execute(
        "SELECT COALESCE(MAX(seq), 0) + 1 FROM messages WHERE conversation_id = ?", (conversation_id,)
    ).fetchone()[0]
    raw = json.dumps(provider_raw, ensure_ascii=False) if provider_raw is not None else None
    row_id = conn.execute(
        """
        INSERT INTO messages (conversation_id, seq, role, model_id, content, provider_raw, created_at)
        VALUES (?, ?, ?, ?, ?, ?, ?)
        """,
        (conversation_id, seq, role, model_id, json.dumps(content, ensure_ascii=False), raw, now),
    ).lastrowid
    conn.execute("UPDATE conversations SET updated_at = ? WHERE id = ?", (now, conversation_id))
    return MessageRow(row_id, seq, role, model_id, content, now, provider_raw)


def get_messages(conn: sqlite3.Connection, conversation_id: str) -> list[MessageRow]:
    rows = conn.execute(
        "SELECT id, seq, role, model_id, content, provider_raw, created_at FROM messages "
        "WHERE conversation_id = ? ORDER BY seq",
        (conversation_id,),
    ).fetchall()
    return [
        MessageRow(
            r["id"],
            r["seq"],
            r["role"],
            r["model_id"],
            json.loads(r["content"]),
            r["created_at"],
            json.loads(r["provider_raw"]) if r["provider_raw"] is not None else None,
        )
        for r in rows
    ]


def record_usage(
    conn: sqlite3.Connection,
    *,
    client_id: str | None,
    conversation_id: str | None,
    message_id: int | None,
    model_id: str,
    usage: dict[str, int],
    cost_usd: float,
    estimated: bool = False,
    meta: dict[str, Any] | None = None,
) -> None:
    conn.execute(
        """
        INSERT INTO usage (client_id, conversation_id, message_id, model_id, input_tokens, cached_input_tokens,
                           cache_write_tokens, output_tokens, cost_usd, estimated, meta)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            client_id,
            conversation_id,
            message_id,
            model_id,
            usage.get("input_tokens", 0),
            usage.get("cached_input_tokens", 0),
            usage.get("cache_write_tokens", 0),
            usage.get("output_tokens", 0),
            cost_usd,
            int(estimated),
            json.dumps(meta or {}, ensure_ascii=False),
        ),
    )


def usage_by_message(conn: sqlite3.Connection, conversation_id: str) -> dict[int, dict[str, Any]]:
    """message id -> what producing it cost: tokens, USD, and the run's stats (speed, stop reason)."""
    rows = conn.execute(
        "SELECT message_id, input_tokens, cached_input_tokens, output_tokens, cost_usd, estimated, meta "
        "FROM usage WHERE conversation_id = ? AND message_id IS NOT NULL",
        (conversation_id,),
    ).fetchall()
    return {
        r["message_id"]: {
            "input_tokens": r["input_tokens"],
            "cached_input_tokens": r["cached_input_tokens"],
            "output_tokens": r["output_tokens"],
            "cost_usd": r["cost_usd"],
            "estimated": bool(r["estimated"]),
            **json.loads(r["meta"]),
        }
        for r in rows
    }


def usage_totals(conn: sqlite3.Connection, *, today_since: str | None = None) -> dict[str, Any]:
    """Totals over all time, by model, and since `today_since` (UTC ISO time of the local midnight)."""
    row = conn.execute(
        "SELECT COALESCE(SUM(cost_usd), 0) AS cost, COALESCE(SUM(output_tokens), 0) AS out, COUNT(*) AS n FROM usage"
    ).fetchone()
    today = conn.execute(
        "SELECT COALESCE(SUM(cost_usd), 0) AS cost, COUNT(*) AS n FROM usage WHERE ts >= ?", (today_since or "",)
    ).fetchone()
    by_model = conn.execute(
        "SELECT model_id, ROUND(SUM(cost_usd), 6) AS cost, SUM(output_tokens) AS out, COUNT(*) AS n "
        "FROM usage GROUP BY model_id ORDER BY cost DESC"
    ).fetchall()
    return {
        "total_cost_usd": round(row["cost"], 6),
        "total_output_tokens": row["out"],
        "requests": row["n"],
        "today_cost_usd": round(today["cost"], 6),
        "today_requests": today["n"],
        "by_model": [dict(r) for r in by_model],
    }
