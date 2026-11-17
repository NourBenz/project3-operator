from __future__ import annotations

import hashlib
import json
from typing import Annotated, Literal

from pydantic import (
    AwareDatetime, BaseModel, BeforeValidator, ConfigDict, Field,
    JsonValue, TypeAdapter, field_validator, model_validator,
)

PositiveId = Annotated[int, Field(gt=0)]
Identifier = Annotated[str, Field(min_length=1, max_length=128, pattern=r"^[A-Za-z0-9_-]+$")]
Label = Literal[
    "bug", "feature", "documentation", "question",
    "low", "medium", "high", "critical",
]
ToolName = Literal["list_issues", "read_issue", "get_labels", "update_labels", "post_comment"]
RunStatus = Literal[
    "RUNNING", "DONE", "AWAITING_APPROVAL", "FAILED", "CANCELLED", "MAX_STEPS", "TIMEOUT",
]
ErrorCode = Literal[
    "ISSUE_NOT_FOUND", "INVALID_ARGUMENTS", "UNKNOWN_TOOL", "EMPTY_RESULT",
    "TOOL_UNAVAILABLE", "TOOL_TIMEOUT", "STALE_VERSION", "APPROVAL_DENIED",
    "USER_CANCELLED", "APPROVAL_MISMATCH", "APPROVAL_EXPIRED", "APPROVAL_REQUIRED",
    "DUPLICATE_OPERATION", "PARTIAL_FAILURE", "LOOP_DETECTED", "MAX_STEPS",
    "MODEL_API_ERROR", "RETRY_LIMIT", "COST_LIMIT", "INVALID_LABEL", "ISSUE_CLOSED",
    "INVALID_ARGUMENT", "NOT_FOUND", "SANDBOX_UNAVAILABLE",
]


class Contract(BaseModel):
    model_config = ConfigDict(
        strict=True, extra="forbid", frozen=True, validate_default=True, allow_inf_nan=False,
    )


class ListIssuesArgs(Contract):
    state: Literal["open", "closed", "all"] = "open"
    limit: Annotated[int, Field(ge=1, le=50)] = 20


class ReadIssueArgs(Contract):
    issue_id: PositiveId


class GetLabelsArgs(Contract):
    pass


class WriteArgs(Contract):
    issue_id: PositiveId
    operation_id: Identifier
    expected_version: PositiveId


def as_label_tuple(value):
    if not isinstance(value, (list, tuple)):
        raise ValueError("labels must be an array")
    return tuple(value)


class UpdateLabelsArgs(WriteArgs):
    labels: Annotated[
        tuple[Label, ...], BeforeValidator(as_label_tuple), Field(min_length=1, max_length=8),
    ]

    @field_validator("labels")
    @classmethod
    def canonical_labels(cls, labels):
        labels = tuple(sorted(set(labels)))
        types = [label for label in labels if label in ("bug", "feature", "documentation", "question")]
        priorities = [label for label in labels if label in ("low", "medium", "high", "critical")]
        if len(types) > 1 or len(priorities) > 1:
            raise ValueError("proposed labels cannot contain conflicting types or priorities")
        return labels


class PostCommentArgs(WriteArgs):
    body: Annotated[str, Field(min_length=1, max_length=1000)]

    @field_validator("body")
    @classmethod
    def nonblank_body(cls, body):
        if not body.strip():
            raise ValueError("comment body must not be blank")
        return body


class ListIssuesCall(Contract):
    tool: Literal["list_issues"]
    args: ListIssuesArgs


class ReadIssueCall(Contract):
    tool: Literal["read_issue"]
    args: ReadIssueArgs


class GetLabelsCall(Contract):
    tool: Literal["get_labels"]
    args: GetLabelsArgs


class UpdateLabelsCall(Contract):
    tool: Literal["update_labels"]
    args: UpdateLabelsArgs


class PostCommentCall(Contract):
    tool: Literal["post_comment"]
    args: PostCommentArgs


ToolRequest = Annotated[
    ListIssuesCall | ReadIssueCall | GetLabelsCall | UpdateLabelsCall | PostCommentCall,
    Field(discriminator="tool"),
]
WriteRequest = Annotated[UpdateLabelsCall | PostCommentCall, Field(discriminator="tool")]
TOOL_REQUEST_ADAPTER = TypeAdapter(ToolRequest)


class ToolError(Contract):
    code: ErrorCode
    message: Annotated[str, Field(min_length=1)]
    retryable: bool = False


class ToolResult(Contract):
    ok: bool
    data: JsonValue = None
    error: ToolError | None = None

    @model_validator(mode="after")
    def consistent_result(self):
        if self.ok and self.error is not None:
            raise ValueError("successful results cannot contain errors")
        if not self.ok and (self.error is None or self.data is not None):
            raise ValueError("failed results need an error and null data")
        return self


class WriteProposal(Contract):
    proposal_id: Identifier
    task_id: Identifier
    request: WriteRequest

    def fingerprint(self):
        payload = json.dumps(
            self.model_dump(mode="json"), sort_keys=True,
            separators=(",", ":"), ensure_ascii=False, allow_nan=False,
        )
        return hashlib.sha256(payload.encode("utf-8")).hexdigest()


class ApprovalDecision(Contract):
    proposal_id: Identifier
    task_id: Identifier
    request_hash: Annotated[str, Field(pattern=r"^[a-f0-9]{64}$")]
    decision: Literal["approve", "reject", "cancel"]


class PublicationRequest(Contract):
    """Everything a human approves before Git/GitHub publication."""
    repository: Annotated[str, Field(min_length=1, max_length=500)]
    issue_number: PositiveId
    issue_url: Annotated[str, Field(min_length=1, max_length=1000)]
    base_branch: Annotated[str, Field(min_length=1, max_length=200)]
    base_sha: Annotated[str, Field(pattern=r"^[a-f0-9]{40}$")]
    patch_hash: Annotated[str, Field(pattern=r"^[a-f0-9]{64}$")]
    test_evidence_hash: Annotated[str, Field(pattern=r"^[a-f0-9]{64}$")]
    changed_files: Annotated[tuple[str, ...], Field(min_length=1, max_length=20)]
    branch: Annotated[str, Field(pattern=r"^cs496/issue-[1-9][0-9]*-[a-f0-9]{8}$")]
    commit_message: Annotated[str, Field(min_length=1, max_length=200)]
    pr_title: Annotated[str, Field(min_length=1, max_length=240)]
    pr_body: Annotated[str, Field(min_length=1, max_length=20_000)]


class PublicationProposal(Contract):
    proposal_id: Identifier
    task_id: Identifier
    request: PublicationRequest

    def fingerprint(self):
        payload = json.dumps(
            self.model_dump(mode="json"), sort_keys=True,
            separators=(",", ":"), ensure_ascii=False, allow_nan=False,
        )
        return hashlib.sha256(payload.encode("utf-8")).hexdigest()


class RunState(Contract):
    task_id: Identifier
    status: RunStatus = "RUNNING"
    step_count: Annotated[int, Field(ge=0)] = 0
    pending_proposal: WriteProposal | None = None

    @model_validator(mode="after")
    def consistent_pending_state(self):
        if (self.status == "AWAITING_APPROVAL") != (self.pending_proposal is not None):
            raise ValueError("only AWAITING_APPROVAL must contain a pending proposal")
        if self.pending_proposal and self.pending_proposal.task_id != self.task_id:
            raise ValueError("pending proposal belongs to a different task")
        return self


class ToolEvent(Contract):
    timestamp: AwareDatetime
    task_id: Identifier
    correlation_id: Identifier
    step: PositiveId
    tool: Annotated[str, Field(min_length=1)]
    arguments: dict[str, JsonValue]
    result: ToolResult
    duration_ms: Annotated[float, Field(ge=0)]
