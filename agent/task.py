"""Controller-owned interpretation of the user's requested scope."""
from __future__ import annotations

import re
from dataclasses import dataclass


@dataclass(frozen=True)
class TaskScope:
    issue_id: int | None
    read_only: bool
    permits_writes: bool
    clarification: str | None = None


def understand_task(user_request) -> TaskScope:
    text = str(user_request).strip()
    issue = re.search(r"\bissue\s*#?\s*(\d+)\b", text, re.I)
    if issue is None:
        return TaskScope(None, False, False, "Clarification required: specify one target issue number.")

    normalized = text.lower()
    write_requested = bool(re.search(r"\b(triage|label|comment|update|classify)\b", normalized))
    read_requested = bool(re.search(r"\b(read|show|summari[sz]e|analy[sz]e|inspect|explain)\b", normalized))
    if not write_requested and not read_requested:
        return TaskScope(int(issue.group(1)), False, False,
                         "Clarification required: specify whether to inspect or triage the issue.")
    return TaskScope(int(issue.group(1)), read_requested and not write_requested, write_requested)
