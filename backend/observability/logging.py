"""Structured logging with request-id correlation.

Every log line carries rid=<request id> (from the middleware's contextvar), so
one grep triages a whole request across routes, services, and the agent stack.
LOG_FORMAT=json switches to one-JSON-object-per-line for log shippers;
LOG_LEVEL sets verbosity (INFO default).
"""

import contextvars
import json
import logging
import sys

from backend.config import settings

request_id_var: contextvars.ContextVar[str] = contextvars.ContextVar("request_id", default="-")

def current_request_id() -> str:
    return request_id_var.get()

class _RequestIdFilter(logging.Filter):
    def filter(self, record: logging.LogRecord) -> bool:
        record.rid = request_id_var.get()
        return True

class _TextFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        base = (f"{self.formatTime(record, '%Y-%m-%dT%H:%M:%S')} "
                f"{record.levelname:<7} {record.name} rid={record.rid} {record.getMessage()}")
        if record.exc_info:
            base += "\n" + self.formatException(record.exc_info)
        return base

class _JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        out = {
            "ts": self.formatTime(record, "%Y-%m-%dT%H:%M:%S"),
            "level": record.levelname,
            "logger": record.name,
            "rid": record.rid,
            "msg": record.getMessage(),
        }
        if record.exc_info:
            out["exc"] = self.formatException(record.exc_info)
        return json.dumps(out)

def setup_logging() -> None:
    root = logging.getLogger()
    root.setLevel(settings.log_level.upper())
    handler = logging.StreamHandler(sys.stdout)
    handler.addFilter(_RequestIdFilter())
    handler.setFormatter(
        _JsonFormatter() if settings.log_format == "json" else _TextFormatter())
    root.handlers = [handler]
    # Our access-log middleware replaces uvicorn's (which lacks request ids).
    logging.getLogger("uvicorn.access").disabled = True
