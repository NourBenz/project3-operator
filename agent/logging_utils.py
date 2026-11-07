import json
import math
import os
import re
import sqlite3
import threading
import time
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path

from pydantic import ValidationError

from agent.approval import ApprovalError
from tools.common import failure, validation_message

DEFAULT_LOG = Path(__file__).resolve().parents[1] / "sandbox" / "logs" / "tool_calls.jsonl"
_LOG_LOCK = threading.RLock()
_SECRET_KEY = re.compile(r"(?i)(api[_-]?key|authorization|password|secret|token|grant)")
_ASSIGNMENT = re.compile(r"(?i)(OPENAI_API_KEY\s*[=:]\s*)[^\s,;]+")
_API_KEY = re.compile(r"\bsk-[A-Za-z0-9_-]{8,}\b")
_BEARER = re.compile(r"(?i)\bBearer\s+[^\s,;]+")
_KNOWN_ERROR_CODES = {"APPROVAL_DENIED", "APPROVAL_REQUIRED", "APPROVAL_MISMATCH",
                      "APPROVAL_EXPIRED", "USER_CANCELLED"}


@dataclass(frozen=True)
class CallContext:
    task_id: str = "standalone"
    step: int = 1
    correlation_id: str = field(default_factory=lambda: uuid.uuid4().hex)


def redact(value):
    if isinstance(value, dict):
        return {str(key): "[REDACTED]" if _SECRET_KEY.search(str(key)) else redact(item)
                for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [redact(item) for item in value]
    if isinstance(value, str):
        for secret in (os.environ.get("OPENAI_API_KEY"),):
            if secret:
                value = value.replace(secret, "[REDACTED]")
        value = _API_KEY.sub("[REDACTED]", value)
        value = _ASSIGNMENT.sub(r"\1[REDACTED]", value)
        return _BEARER.sub("Bearer [REDACTED]", value)
    if isinstance(value, float) and not math.isfinite(value):
        return "[non-finite]"
    if value is None or type(value) in (bool, int, float):
        return value
    return f"[unsupported {type(value).__name__}]"


class ToolLogger:
    def __init__(self, path=DEFAULT_LOG):
        self.path = Path(path)

    def emit(self, event):
        line = json.dumps(redact(event), ensure_ascii=False, allow_nan=False)
        with _LOG_LOCK:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            with self.path.open("a", encoding="utf-8") as stream:
                stream.write(line + "\n")
                stream.flush()


def _normalize(error):
    if isinstance(error, ApprovalError):
        return failure(error.code, str(error))
    if isinstance(error, ValidationError):
        return failure("INVALID_ARGUMENTS", validation_message(error))
    if isinstance(error, TimeoutError):
        return failure("TOOL_TIMEOUT", "Tool timed out; retry a write only with the same operation ID", True)
    if isinstance(error, sqlite3.IntegrityError):
        return failure("INVALID_ARGUMENTS", "The requested change violates a sandbox constraint")
    if isinstance(error, sqlite3.Error):
        busy = isinstance(error, sqlite3.OperationalError) and any(
            word in str(error).lower() for word in ("locked", "busy")
        )
        return failure("TOOL_UNAVAILABLE", "Sandbox unavailable; check initialization and retry only when safe", busy)
    if isinstance(error, KeyboardInterrupt):
        return failure("USER_CANCELLED", "Tool call was cancelled")
    return failure("TOOL_UNAVAILABLE", "Tool could not complete; no internal traceback is exposed", False)


def logged_call(tool, arguments, callback, *, context=None, logger=None):
    context = context or CallContext()
    logger = logger or ToolLogger()
    started = time.perf_counter()
    base = {
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "task_id": context.task_id, "step": context.step,
        "correlation_id": context.correlation_id, "tool": tool,
        "arguments": arguments,
        "operation_id": arguments.get("operation_id") if isinstance(arguments, dict) else None,
    }
    try:
        logger.emit({**base, "event": "tool_start"})
    except (OSError, ValueError, TypeError):
        return failure("TOOL_UNAVAILABLE", "Cannot record tool attempt; operation was not started", True)
    try:
        result = callback()
    except (Exception, KeyboardInterrupt) as error:
        result = _normalize(error)
    duration = (time.perf_counter() - started) * 1000
    code = result["error"]["code"] if result["error"] else None
    approval = "not_applicable"
    if tool in ("update_labels", "post_comment"):
        approval = code if code in _KNOWN_ERROR_CODES else "authorized" if result["ok"] else "not_completed"
    try:
        logger.emit({**base, "event": "tool_result", "duration_ms": duration,
                     "approval_outcome": approval, "result": result})
    except (OSError, ValueError, TypeError):
        return failure(
            "PARTIAL_FAILURE" if tool in ("update_labels", "post_comment") else "TOOL_UNAVAILABLE",
            "Result logging failed; a write may have committed. Retry only the same operation ID and verify state",
            True,
        )
    return result
