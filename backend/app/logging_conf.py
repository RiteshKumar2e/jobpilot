"""Structured JSON logging with secret redaction.

Every log line is one JSON object -> easy to grep/ship. A redaction filter
scrubs anything that looks like a key/token/password/cookie so logs never leak
credentials or personal data (security requirement #7).
"""
from __future__ import annotations

import json
import logging
import re
import sys
from datetime import datetime, timezone
from pathlib import Path

# Patterns that must never reach disk in cleartext.
_REDACT_PATTERNS = [
    re.compile(r"(?i)\b(api[_-]?key|token|password|secret|authorization|cookie|bearer)\b\s*[:=]\s*(\S+)"),
    re.compile(r"(gsk_[A-Za-z0-9]{8,})"),          # Groq API key
    re.compile(r"(AIza[A-Za-z0-9\-_]{20,})"),      # Google / Gemini API key
    re.compile(r"(sk-[A-Za-z0-9\-_]{8,})"),        # other provider-style keys
    re.compile(r"(ya29\.[A-Za-z0-9\-_]+)"),        # Google OAuth token
]


def _redact(text: str) -> str:
    if not text:
        return text
    out = text
    out = _REDACT_PATTERNS[0].sub(lambda m: f"{m.group(1)}=***REDACTED***", out)
    for pat in _REDACT_PATTERNS[1:]:
        out = pat.sub("***REDACTED***", out)
    return out


class JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        payload = {
            "ts": datetime.now(timezone.utc).isoformat(),
            "level": record.levelname,
            "logger": record.name,
            "msg": _redact(record.getMessage()),
        }
        # Attach structured extras (job_id, agent, status, etc.)
        for key, val in getattr(record, "__dict__", {}).items():
            if key in ("args", "msg", "levelname", "levelno", "pathname", "filename",
                       "module", "exc_info", "exc_text", "stack_info", "lineno",
                       "funcName", "created", "msecs", "relativeCreated", "thread",
                       "threadName", "processName", "process", "name", "taskName"):
                continue
            try:
                json.dumps(val)
                payload[key] = val
            except (TypeError, ValueError):
                payload[key] = _redact(str(val))
        if record.exc_info:
            payload["exc"] = _redact(self.formatException(record.exc_info))
        return json.dumps(payload, ensure_ascii=False)


_configured = False


def configure_logging(log_dir: Path, level: int = logging.INFO) -> None:
    global _configured
    if _configured:
        return
    log_dir = Path(log_dir)
    log_dir.mkdir(parents=True, exist_ok=True)

    root = logging.getLogger("jobpilot")
    root.setLevel(level)
    root.handlers.clear()

    fmt = JsonFormatter()

    stream = logging.StreamHandler(sys.stdout)
    stream.setFormatter(fmt)
    root.addHandler(stream)

    file_handler = logging.FileHandler(log_dir / "jobpilot.jsonl", encoding="utf-8")
    file_handler.setFormatter(fmt)
    root.addHandler(file_handler)

    root.propagate = False
    _configured = True


def get_logger(name: str) -> logging.LoggerAdapter:
    return logging.LoggerAdapter(logging.getLogger(f"jobpilot.{name}"), {})
