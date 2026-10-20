"""The ingestion worker loop: reconnects when the database drops its connection, stops on request."""

from __future__ import annotations

import threading

import psycopg2

from services import worker


class FakeConn:
    closed = False

    def close(self):
        self.closed = True


def test_reconnects_after_a_dropped_connection_and_stops(monkeypatch):
    conns: list[FakeConn] = []
    stop = threading.Event()
    jobs = iter([psycopg2.OperationalError("server closed the connection"), True, False])

    def get_conn(_dsn):
        conns.append(FakeConn())
        return conns[-1]

    def run_once(conn, _settings):
        step = next(jobs)
        if isinstance(step, Exception):
            raise step
        if step is False:
            stop.set()
        return step

    monkeypatch.setattr(worker.db, "get_conn", get_conn)
    monkeypatch.setattr(worker, "run_once", run_once)
    monkeypatch.setattr(worker, "purge_history", lambda conn, settings: 0)
    monkeypatch.setattr(worker, "RECONNECT_DELAY_S", 0)
    worker.run_forever(poll_interval=0, stop=stop)
    assert len(conns) == 2  # the broken connection was replaced
    assert conns[0].closed and conns[1].closed


def test_start_in_thread_stops(monkeypatch):
    monkeypatch.setattr(worker.db, "get_conn", lambda dsn: FakeConn())
    monkeypatch.setattr(worker, "run_once", lambda conn, settings: False)
    monkeypatch.setattr(worker, "purge_history", lambda conn, settings: 0)
    thread, stop = worker.start_in_thread(poll_interval=0.01)
    stop.set()
    thread.join(timeout=2)
    assert not thread.is_alive()
