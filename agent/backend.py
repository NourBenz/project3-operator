"""Controller-facing contract for issue backends.

The protocol describes the capabilities used by :class:`TriageAgent`.  It does
not imply that local SQLite and remote GitHub backends have identical
persistence, consistency, or recovery behavior.
"""
from __future__ import annotations

from typing import Any, Protocol, runtime_checkable

from agent.approval import ApprovalGrant, ApprovalManager
from agent.logging_utils import CallContext
from agent.schemas import WriteProposal


ToolArguments = dict[str, Any]
ToolResultPayload = dict[str, Any]


@runtime_checkable
class IssueService(Protocol):
    """Minimum issue-service surface required by the triage controller."""

    approvals: ApprovalManager
    scope: str

    def propose(
        self, tool: str, arguments: ToolArguments, *, task_id: str,
    ) -> WriteProposal: ...

    def call(
        self,
        tool: str,
        arguments: ToolArguments,
        *,
        task_id: str = "standalone",
        proposal_id: str | None = None,
        grant: ApprovalGrant | None = None,
        context: CallContext | None = None,
    ) -> ToolResultPayload: ...

    def list_issues(
        self, state: str = "open", limit: int = 20, *, context: CallContext | None = None,
    ) -> ToolResultPayload: ...

    def read_issue(
        self, issue_id: int, *, context: CallContext | None = None,
    ) -> ToolResultPayload: ...

    def get_labels(
        self, *, context: CallContext | None = None,
    ) -> ToolResultPayload: ...

    def update_labels(
        self,
        issue_id: int,
        labels: list[str] | tuple[str, ...],
        operation_id: str,
        expected_version: int,
        *,
        task_id: str = "standalone",
        proposal_id: str | None = None,
        grant: ApprovalGrant | None = None,
        context: CallContext | None = None,
        **extra: Any,
    ) -> ToolResultPayload: ...

    def post_comment(
        self,
        issue_id: int,
        body: str,
        operation_id: str,
        expected_version: int,
        *,
        task_id: str = "standalone",
        proposal_id: str | None = None,
        grant: ApprovalGrant | None = None,
        context: CallContext | None = None,
        **extra: Any,
    ) -> ToolResultPayload: ...
