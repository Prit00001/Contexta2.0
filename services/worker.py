"""Background ingestion worker: polls the jobs table and runs the pipeline. Runs as
its own process (python -m services.worker), separate from the FastAPI request path - or, with
INGEST_IN_API=true, as a thread inside the API process (single-service hosts such as Render's
free tier, which has no background workers).

One long-lived connection; if the database drops it (e.g. Supabase's pooler closing an idle
connection) the loop reconnects after a short pause.
"""
from __future__ import annotations

import logging
import threading
import time
from typing import Optional

import psycopg2

from app.config import get_settings
from core import conversations, db
from services.ingestion import run_ingestion

log = logging.getLogger("contexta.worker")

PURGE_INTERVAL_S = 3600.0  # stored conversations past HISTORY_RETENTION_DAYS (P2-14, ADR-006)


def run_once(conn, settings) -> bool:
    """Claims and processes one job. Returns False if there was nothing to do."""
    job = db.claim_job(conn)
    if job is None:
        return False
    try:
        run_ingestion(conn, settings, job["document_id"])
        db.mark_job(conn, job["id"], "done")
    except Exception as exc:  # noqa: BLE001 - one bad document must not kill the worker loop
        log.exception("ingestion failed for document %s", job["document_id"])
        db.mark_job(conn, job["id"], "failed", str(exc))
        db.set_document_status(conn, job["document_id"], "failed", error=str(exc))
    return True


def purge_history(conn, settings) -> int:
    """Delete stored conversations idle for longer than the retention period. The API already
    hides them on read; this removes the data."""
    n = conversations.purge_expired(conn, settings.history_retention_days)
    if n:
        log.info("Deleted %d conversation(s) past the %d-day retention", n, settings.history_retention_days)
    return n


RECONNECT_DELAY_S = 5.0


def run_forever(poll_interval: float = 2.0, stop: Optional[threading.Event] = None) -> None:
    """Poll for jobs until ``stop`` is set (never, when run as its own process)."""
    settings = get_settings()
    stop = stop or threading.Event()
    conn = None
    log.info("Worker ready, polling every %.1fs", poll_interval)
    next_purge = 0.0
    while not stop.is_set():
        try:
            if conn is None:
                conn = db.get_conn(settings.database_url)
            if time.monotonic() >= next_purge:
                purge_history(conn, settings)
                next_purge = time.monotonic() + PURGE_INTERVAL_S
            if not run_once(conn, settings):
                stop.wait(poll_interval)
        except (psycopg2.OperationalError, psycopg2.InterfaceError) as exc:
            log.warning("Worker lost its database connection (%s); reconnecting", type(exc).__name__)
            if conn is not None:
                try:
                    conn.close()
                except Exception:  # noqa: BLE001 - it is already broken
                    pass
            conn = None
            stop.wait(RECONNECT_DELAY_S)
    if conn is not None:
        conn.close()


def start_in_thread(poll_interval: float = 2.0) -> tuple[threading.Thread, threading.Event]:
    """Run the worker inside the current (API) process. Set the returned event to stop it."""
    stop = threading.Event()
    t = threading.Thread(target=run_forever, args=(poll_interval, stop), name="ingest-worker", daemon=True)
    t.start()
    return t, stop


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    run_forever()
