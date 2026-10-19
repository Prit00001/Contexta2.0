"""Stored conversation threads for workspace Q&A (P2-14, ADR-006). Sync Postgres access, like
core/db.py; routes call it through asyncio.to_thread.

The API connects as the service role (no RLS), so every read and write here is scoped to
``(workspace_id, user_id)`` explicitly - the same rule as the ``own_conversations`` RLS policy:
a thread belongs to the user who asked, and nobody else (admins included) can see it.

Retention: a thread whose last turn is older than ``retention_days`` is treated as gone by every
function here, and ``purge_expired`` deletes it (run by services.worker).
"""

from __future__ import annotations

import json
from functools import partial
from typing import Optional

from psycopg2.extras import Json

TITLE_CHARS = 80
_LIVE = "updated_at >= now() - make_interval(days => %s)"


def _json(value) -> Json:
    return Json(value, dumps=partial(json.dumps, default=str))


def title_from(question: str) -> str:
    q = " ".join(question.split())
    return q if len(q) <= TITLE_CHARS else q[: TITLE_CHARS - 1].rstrip() + "…"


def create_conversation(conn, *, workspace_id: str, user_id: str, title: str) -> str:
    with conn.cursor() as cur:
        cur.execute(
            "INSERT INTO conversations (workspace_id, user_id, title) VALUES (%s, %s, %s) RETURNING id",
            (workspace_id, user_id, title),
        )
        cid = cur.fetchone()[0]
    conn.commit()
    return str(cid)


def get_conversation(conn, cid: str, *, workspace_id: str, user_id: str, retention_days: int) -> Optional[dict]:
    with conn.cursor() as cur:
        cur.execute(
            f"SELECT id, title, created_at, updated_at FROM conversations "
            f"WHERE id = %s AND workspace_id = %s AND user_id = %s AND {_LIVE}",
            (cid, workspace_id, user_id, retention_days),
        )
        row = cur.fetchone()
    if row is None:
        return None
    return {"id": str(row[0]), "title": row[1], "created_at": row[2], "updated_at": row[3]}


def list_conversations(conn, *, workspace_id: str, user_id: str, retention_days: int, limit: int = 50) -> list[dict]:
    with conn.cursor() as cur:
        cur.execute(
            f"SELECT c.id, c.title, c.created_at, c.updated_at, "
            f"(SELECT count(*) FROM conversation_turns t WHERE t.conversation_id = c.id) "
            f"FROM conversations c WHERE workspace_id = %s AND user_id = %s AND {_LIVE} "
            f"ORDER BY updated_at DESC LIMIT %s",
            (workspace_id, user_id, retention_days, limit),
        )
        rows = cur.fetchall()
    return [{"id": str(r[0]), "title": r[1], "created_at": r[2], "updated_at": r[3], "turns": r[4]} for r in rows]


_TURN_COLUMNS = "position, question, standalone_question, answer, outcome, sources, citations, claims, created_at"
_TURN_KEYS = [c.strip() for c in _TURN_COLUMNS.split(",")]


def get_turns(conn, cid: str, last: Optional[int] = None) -> list[dict]:
    """All turns oldest first, or only the ``last`` n. Callers check ownership first."""
    with conn.cursor() as cur:
        if last is None:
            cur.execute(
                f"SELECT {_TURN_COLUMNS} FROM conversation_turns WHERE conversation_id = %s ORDER BY position",
                (cid,),
            )
        else:
            cur.execute(
                f"SELECT * FROM (SELECT {_TURN_COLUMNS} FROM conversation_turns WHERE conversation_id = %s "
                f"ORDER BY position DESC LIMIT %s) t ORDER BY position",
                (cid, last),
            )
        rows = cur.fetchall()
    return [dict(zip(_TURN_KEYS, r, strict=True)) for r in rows]


def append_turn(conn, cid: str, turn: dict) -> int:
    """Store one turn and bump the thread's ``updated_at`` (which restarts its retention clock).
    The row lock serialises concurrent appends to the same thread. Returns the turn's position."""
    with conn.cursor() as cur:
        cur.execute("SELECT 1 FROM conversations WHERE id = %s FOR UPDATE", (cid,))
        cur.execute("SELECT coalesce(max(position), 0) + 1 FROM conversation_turns WHERE conversation_id = %s", (cid,))
        position = cur.fetchone()[0]
        cur.execute(
            "INSERT INTO conversation_turns (conversation_id, position, question, standalone_question, answer, "
            "outcome, sources, citations, claims) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)",
            (
                cid,
                position,
                turn["question"],
                turn.get("standalone_question"),
                turn["answer"],
                turn["outcome"],
                _json(turn.get("sources", [])),
                _json(turn.get("citations", [])),
                _json(turn.get("claims", [])),
            ),
        )
        cur.execute("UPDATE conversations SET updated_at = now() WHERE id = %s", (cid,))
    conn.commit()
    return position


def delete_conversation(conn, cid: str, *, workspace_id: str, user_id: str) -> bool:
    with conn.cursor() as cur:
        cur.execute(
            "DELETE FROM conversations WHERE id = %s AND workspace_id = %s AND user_id = %s RETURNING id",
            (cid, workspace_id, user_id),
        )
        deleted = cur.fetchone() is not None
    conn.commit()
    return deleted


def purge_expired(conn, retention_days: int) -> int:
    """Delete threads idle for longer than the retention period (turns cascade). Returns the count."""
    with conn.cursor() as cur:
        cur.execute("DELETE FROM conversations WHERE updated_at < now() - make_interval(days => %s)", (retention_days,))
        n = cur.rowcount
    conn.commit()
    return n
