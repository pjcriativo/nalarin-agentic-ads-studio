"""Structured (one JSON object per line) logging for the agent worker.

What may appear in a log line: event name, ids (worker, task, organization), counters, durations, status and error KINDS.
What never does: prompts, objectives, tool inputs/outputs, model answers, credentials, DSNs, exception messages or
tracebacks (an exception is logged by TYPE only). Free text that does reach a line is passed through ``redact_secrets``."""
import json
import logging
import sys
from datetime import datetime, timezone

from app.agents.security import redact_secrets

_RESERVED = {"ts", "level", "logger", "event", "msg"}


class JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        payload = {"ts": datetime.fromtimestamp(record.created, timezone.utc).isoformat(timespec="milliseconds"),
                   "level": record.levelname.lower(), "logger": record.name}
        event = getattr(record, "event", None)
        if event:
            payload["event"] = event
        else:
            payload["msg"] = redact_secrets(record.getMessage())[:500]
        for key, value in (getattr(record, "fields", None) or {}).items():
            if key not in _RESERVED:
                payload[key] = value if isinstance(value, (int, float, bool, type(None))) else redact_secrets(str(value))[:200]
        if record.exc_info and record.exc_info[0] is not None:
            payload["exception_type"] = record.exc_info[0].__name__      # never the message or the traceback
        return json.dumps(payload, ensure_ascii=False)


def configure_logging(level: str = "INFO") -> None:
    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(JsonFormatter())
    root = logging.getLogger()
    root.handlers[:] = [handler]
    root.setLevel(level.upper())
    for noisy in ("sqlalchemy.engine", "httpx", "httpcore"):
        logging.getLogger(noisy).setLevel(logging.WARNING)


def log_event(logger: logging.Logger, event: str, level: int = logging.INFO, **fields) -> None:
    logger.log(level, event, extra={"event": event, "fields": fields})
