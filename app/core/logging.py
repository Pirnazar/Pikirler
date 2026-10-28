"""JSON logging that never carries thought content or secrets.

Only explicitly whitelisted ``extra`` fields reach the output, so a careless
``log.info(..., extra={"content": ...})`` cannot leak private text.
"""

from __future__ import annotations

import json
import logging
import re
import sys
from datetime import UTC, datetime

_ALLOWED_EXTRA = {
    "request_id",
    "method",
    "route",
    "status",
    "duration_ms",
    "owner_id",
    "job_id",
    "job_kind",
    "attempt",
    "error_type",
    "provider",
    "model",
    "count",
    "client",
}
_SECRET_PATTERNS = [
    re.compile(r"(?i)(api[_-]?key|token|password|secret|authorization)=([^&\s]+)"),
    re.compile(r"(?i)(bearer\s+)[A-Za-z0-9._\-]+"),
    re.compile(r"key=AIza[0-9A-Za-z_\-]+"),
]


def redact(text: str) -> str:
    for pattern in _SECRET_PATTERNS:
        text = pattern.sub(lambda m: m.group(0)[: len(m.group(1)) + 1] + "[REDACTED]", text)
    return text


class JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        payload: dict[str, object] = {
            "ts": datetime.fromtimestamp(record.created, UTC).isoformat(),
            "level": record.levelname,
            "logger": record.name,
            "event": redact(record.getMessage()),
        }
        for key in _ALLOWED_EXTRA:
            if key in record.__dict__:
                payload[key] = record.__dict__[key]
        if record.exc_info and record.exc_info[0] is not None:
            # Exception type only; tracebacks can contain local variables' text.
            payload["error_type"] = record.exc_info[0].__name__
        return json.dumps(payload, ensure_ascii=False, default=str)


def configure_logging(level: str = "INFO") -> None:
    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(JsonFormatter())
    root = logging.getLogger()
    root.handlers[:] = [handler]
    root.setLevel(level)
    # arq installs its own console handler; route its records through ours only.
    arq_logger = logging.getLogger("arq")
    arq_logger.handlers.clear()
    arq_logger.propagate = True
    # httpx logs full request URLs, and Gemini puts the key in the query string.
    logging.getLogger("httpx").setLevel(logging.WARNING)
    logging.getLogger("botocore").setLevel(logging.WARNING)
