"""Structured logging, request correlation and safe error responses (FR-10, NFR-08, SR-41..SR-43).

* Every log record carries the current ``request_id`` (a context variable set by
  :class:`RequestContextMiddleware`), plus any structured fields passed via ``extra=`` -
  ``event``, ``stage``, ``status``, ``duration_ms``, ``category``, ``provider``...
* ``LOG_FORMAT=json`` emits one JSON object per line for log aggregation; ``text`` (default)
  is human-readable for local development.
* User content is not logged: the question is recorded as length + 8-char SHA-256 prefix.
* A redaction filter masks anything shaped like an API key or bearer token, as a backstop -
  the code never passes secrets to the logger in the first place.
"""

from __future__ import annotations

import contextvars
import hashlib
import json
import logging
import re
import time
import uuid

from starlette.responses import JSONResponse
from starlette.types import ASGIApp, Message, Receive, Scope, Send

request_id_var: contextvars.ContextVar[str] = contextvars.ContextVar("request_id", default="-")

STRUCTURED_FIELDS = (
    "event", "request_id", "method", "path", "status_code", "stage", "status", "duration_ms", "category",
    "provider", "cooldown_s", "outcome", "n_sources", "llm_used", "cached", "question_len", "question_sha8",
    "top_k", "use_arxiv", "error_type",
)  # fmt: skip
_SAFE_REQUEST_ID = re.compile(r"^[A-Za-z0-9._-]{1,64}$")
_SECRET_PATTERNS = re.compile(
    r"(sk-[A-Za-z0-9_\-]{8,}|gsk_[A-Za-z0-9]{8,}|AIza[0-9A-Za-z_\-]{20,}|cxk_[A-Za-z0-9_\-]{8,}"
    r"|(?i:bearer)\s+[A-Za-z0-9._\-]{8,}|postgres(?:ql)?://[^\s@]+@)"
)

log = logging.getLogger("contexta.http")


def redact(text: str) -> str:
    return _SECRET_PATTERNS.sub("[REDACTED]", text)


def question_fingerprint(question: str) -> dict:
    """What we log instead of the question itself."""
    return {"question_len": len(question), "question_sha8": hashlib.sha256(question.encode()).hexdigest()[:8]}


class _ContextFilter(logging.Filter):
    def filter(self, record: logging.LogRecord) -> bool:
        if not hasattr(record, "request_id"):
            record.request_id = request_id_var.get()
        record.msg = redact(str(record.msg))
        if record.args:
            record.args = tuple(redact(a) if isinstance(a, str) else a for a in record.args)
        return True


class JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        out = {
            "ts": self.formatTime(record, "%Y-%m-%dT%H:%M:%S"),
            "level": record.levelname,
            "logger": record.name,
            "msg": record.getMessage(),
        }
        for k in STRUCTURED_FIELDS:
            v = getattr(record, k, None)
            if v is not None:
                out[k] = v
        if record.exc_info:
            out["exc_type"] = record.exc_info[0].__name__ if record.exc_info[0] else None
            out["exc"] = redact(self.formatException(record.exc_info))
        return json.dumps(out, default=str)


class TextFormatter(logging.Formatter):
    def __init__(self):
        super().__init__("%(asctime)s %(levelname)s %(name)s [%(request_id)s] %(message)s")

    def format(self, record: logging.LogRecord) -> str:
        base = super().format(record)
        extras = {k: getattr(record, k) for k in ("event", "stage", "status", "duration_ms", "category")
                  if getattr(record, k, None) is not None}  # fmt: skip
        return f"{base} {extras}" if extras else base


def configure_logging(level: str = "INFO", fmt: str = "text") -> None:
    """Install one handler on the root logger (idempotent - safe across app reloads and tests)."""
    root = logging.getLogger()
    for h in list(root.handlers):
        if getattr(h, "_contexta", False):
            root.removeHandler(h)
    handler = logging.StreamHandler()
    handler._contexta = True  # type: ignore[attr-defined]
    handler.setFormatter(JsonFormatter() if fmt == "json" else TextFormatter())
    handler.addFilter(_ContextFilter())
    root.addHandler(handler)
    root.setLevel(level)
    for noisy in ("httpx", "httpcore", "httpx2", "openai"):  # these would log full request URLs at INFO
        logging.getLogger(noisy).setLevel(logging.WARNING)


class RequestContextMiddleware:
    """Assigns/propagates X-Request-ID, times the request, logs one ``http.request`` event."""

    def __init__(self, app: ASGIApp):
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        incoming = dict(scope.get("headers") or []).get(b"x-request-id", b"").decode("latin-1")
        rid = incoming if _SAFE_REQUEST_ID.match(incoming) else uuid.uuid4().hex[:16]
        scope.setdefault("state", {})["request_id"] = rid
        token = request_id_var.set(rid)
        status = {"code": 500, "started": False}
        t0 = time.perf_counter()

        async def send_wrapper(message: Message) -> None:
            if message["type"] == "http.response.start":
                status["code"], status["started"] = message["status"], True
                message.setdefault("headers", [])
                message["headers"] = [*message["headers"], (b"x-request-id", rid.encode())]
            await send(message)

        try:
            await self.app(scope, receive, send_wrapper)
        except Exception as exc:  # SR-41/SR-42: generic body + request ID; details only in redacted logs
            logging.getLogger("contexta.error").error(
                "unhandled exception", exc_info=exc,
                extra={"event": "http.error", "category": "internal", "error_type": type(exc).__name__},
            )  # fmt: skip
            if status["started"]:
                raise
            response = JSONResponse({"detail": "Internal server error", "request_id": rid}, status_code=500)
            await response(scope, receive, send_wrapper)
        finally:
            code = status["code"]
            category = (
                "rate_limited" if code == 429 else "validation" if code == 422 else "internal" if code >= 500 else None
            )
            log.info(
                "%s %s %s",
                scope.get("method"),
                scope.get("path"),
                code,
                extra={"event": "http.request", "method": scope.get("method"), "path": scope.get("path"),
                       "status_code": code, "duration_ms": round((time.perf_counter() - t0) * 1000),
                       "category": category},
            )  # fmt: skip
            request_id_var.reset(token)
