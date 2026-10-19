"""Hybrid retrieval over a workspace's chunks: pgvector cosine + Postgres full-text
search, merged and re-ranked in Python - same style as search_service.py's existing
local+arXiv candidate merge, just over real ingested chunks instead."""

from __future__ import annotations

from typing import List, Optional

_COLS = ["id", "document_id", "filename", "chunk_index", "page_number", "section_heading", "content", "score"]

_BASE_SELECT = "SELECT c.id, c.document_id, d.filename, c.chunk_index, c.page_number, c.section_heading, c.content"
_FROM = "FROM chunks c JOIN documents d ON d.id = c.document_id"


def _group_filter(allowed_group_ids: Optional[List[str]]):
    """None means no restriction (admin / API key). Otherwise: visible if the chunk has
    no access group, or its group is one the caller belongs to."""
    if allowed_group_ids is None:
        return "", []
    return " AND (c.access_group_id IS NULL OR c.access_group_id = ANY(%s))", [allowed_group_ids]


def _vector_candidates(conn, workspace_id, qvec, limit, allowed_group_ids):
    extra_sql, extra_params = _group_filter(allowed_group_ids)
    with conn.cursor() as cur:
        cur.execute(
            f"{_BASE_SELECT}, 1 - (c.embedding <=> %s) AS score {_FROM} "
            f"WHERE c.workspace_id = %s{extra_sql} ORDER BY c.embedding <=> %s LIMIT %s",
            (qvec, workspace_id, *extra_params, qvec, limit),
        )
        return cur.fetchall()


def _lexical_candidates(conn, workspace_id, query_text, limit, allowed_group_ids):
    extra_sql, extra_params = _group_filter(allowed_group_ids)
    with conn.cursor() as cur:
        cur.execute(
            f"{_BASE_SELECT}, ts_rank(c.content_tsv, plainto_tsquery('english', %s)) AS score {_FROM} "
            f"WHERE c.workspace_id = %s AND c.content_tsv @@ plainto_tsquery('english', %s){extra_sql} "
            "ORDER BY score DESC LIMIT %s",
            (query_text, workspace_id, query_text, *extra_params, limit),
        )
        return cur.fetchall()


def hybrid_search(
    conn, workspace_id: str, query_text: str, qvec, top_k: int = 8, allowed_group_ids: Optional[List[str]] = None
) -> List[dict]:
    vec_rows = _vector_candidates(conn, workspace_id, qvec, top_k * 3, allowed_group_ids)
    lex_rows = _lexical_candidates(conn, workspace_id, query_text, top_k * 3, allowed_group_ids)

    max_lex = max((r[-1] for r in lex_rows), default=0.0) or 1.0
    merged: dict = {}
    for row in vec_rows:
        d = dict(zip(_COLS, row, strict=True))
        merged[d["id"]] = {**d, "vec_score": d["score"], "lex_score": 0.0}
    for row in lex_rows:
        d = dict(zip(_COLS, row, strict=True))
        norm = d["score"] / max_lex
        if d["id"] in merged:
            merged[d["id"]]["lex_score"] = norm
        else:
            merged[d["id"]] = {**d, "vec_score": 0.0, "lex_score": norm}

    ranked = sorted(merged.values(), key=lambda d: 0.7 * d["vec_score"] + 0.3 * d["lex_score"], reverse=True)
    for d in ranked:
        d["score"] = round(0.7 * d["vec_score"] + 0.3 * d["lex_score"], 4)
    return ranked[:top_k]
