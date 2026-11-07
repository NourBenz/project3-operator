import json
from datetime import datetime, timezone

import pytest
from pydantic import ValidationError

from agent.schemas import (
    ApprovalDecision, GetLabelsArgs, ListIssuesArgs, PostCommentArgs, ReadIssueArgs,
    RunState, TOOL_REQUEST_ADAPTER, ToolEvent, ToolResult, UpdateLabelsArgs,
    WriteProposal,
)
from sandbox.seed import seed_database
from tools.get_labels import get_labels
from tools.read_issue import read_issue


def write_payload(**overrides):
    args = {
        "issue_id": 12, "labels": ["bug", "high"],
        "operation_id": "op-1", "expected_version": 1,
    }
    args.update(overrides)
    return {"tool": "update_labels", "args": args}


def proposal(**overrides):
    values = {"proposal_id": "proposal-1", "task_id": "task-1", "request": write_payload()}
    values.update(overrides)
    return WriteProposal.model_validate(values)


@pytest.mark.parametrize("issue_id", [True, False, 0, -1, "12", 12.0, None])
def test_issue_ids_are_strict(issue_id):
    with pytest.raises(ValidationError):
        ReadIssueArgs(issue_id=issue_id)


@pytest.mark.parametrize("limit", [0, 51, True, "20", 1.5])
def test_listing_is_bounded(limit):
    with pytest.raises(ValidationError):
        ListIssuesArgs(limit=limit)


def test_model_cannot_supply_approval_or_database_arguments():
    for extra in ({"approved": True}, {"approval_token": "fake"}, {"db_path": "other.db"}):
        with pytest.raises(ValidationError):
            UpdateLabelsArgs(**write_payload()["args"], **extra)
    with pytest.raises(ValidationError):
        GetLabelsArgs(approved=True)


@pytest.mark.parametrize("labels", [
    [], ["unknown"], ["enhancement"], ["priority:normal"], ["priority:low"],
    ["bug", "feature"], ["low", "high"], "bug",
])
def test_label_allowlist_and_conflicts(labels):
    with pytest.raises(ValidationError):
        UpdateLabelsArgs(**write_payload(labels=labels)["args"])


@pytest.mark.parametrize("priority", ["low", "medium", "high", "critical"])
def test_plain_priority_values_are_valid(priority):
    value = UpdateLabelsArgs(**write_payload(labels=["bug", priority])["args"])
    assert set(value.labels) == {"bug", priority}


def test_labels_are_canonical_immutable_and_json_arrays():
    value = proposal(request=write_payload(labels=["high", "bug", "bug"]))
    assert value.request.args.labels == ("bug", "high")
    assert value.fingerprint() == proposal().fingerprint()
    assert value.model_dump(mode="json")["request"]["args"]["labels"] == ["bug", "high"]
    with pytest.raises(ValidationError):
        value.request.args.issue_id = 99
    assert WriteProposal.model_validate_json(value.model_dump_json()) == value


@pytest.mark.parametrize("body", ["", "   ", "x" * 1001, 123])
def test_comment_bounds(body):
    with pytest.raises(ValidationError):
        PostCommentArgs(issue_id=1, operation_id="op", expected_version=1, body=body)


def test_all_tool_requests_and_unknown_tool():
    calls = [
        {"tool": "list_issues", "args": {}},
        {"tool": "read_issue", "args": {"issue_id": 1}},
        {"tool": "get_labels", "args": {}},
        write_payload(),
        {"tool": "post_comment", "args": {
            "issue_id": 1, "body": "Please provide reproduction steps.",
            "operation_id": "op", "expected_version": 1,
        }},
    ]
    for call in calls:
        result = TOOL_REQUEST_ADAPTER.validate_python(call)
        assert TOOL_REQUEST_ADAPTER.validate_json(result.model_dump_json()) == result
    for call in [
        {"tool": "run_shell", "args": {}},
        {"tool": "read_issue", "args": {"limit": 1}},
    ]:
        with pytest.raises(ValidationError):
            TOOL_REQUEST_ADAPTER.validate_python(call)


def test_proposal_hash_binds_every_authorized_field():
    baseline = proposal().fingerprint()
    changed = [
        proposal(task_id="task-2"), proposal(proposal_id="proposal-2"),
        proposal(request=write_payload(issue_id=99)),
        proposal(request=write_payload(expected_version=2)),
        proposal(request=write_payload(operation_id="op-2")),
        proposal(request=write_payload(labels=["question"])),
        proposal(request={"tool": "post_comment", "args": {
            "issue_id": 12, "operation_id": "op-1",
            "expected_version": 1, "body": "A different action",
        }}),
    ]
    assert all(value.fingerprint() != baseline for value in changed)
    with pytest.raises(ValidationError):
        proposal(request={"tool": "read_issue", "args": {"issue_id": 12}})


def test_approval_is_explicit_and_serializable_not_a_grant():
    pending = proposal()
    fields = {
        "proposal_id": pending.proposal_id, "task_id": pending.task_id,
        "request_hash": pending.fingerprint(),
    }
    with pytest.raises(ValidationError):
        ApprovalDecision(**fields)
    decision = ApprovalDecision(**fields, decision="reject")
    assert ApprovalDecision.model_validate_json(decision.model_dump_json()) == decision
    with pytest.raises(ValidationError):
        ApprovalDecision(**fields, decision="approve", approved=True)
    state = RunState(task_id="task-1", status="AWAITING_APPROVAL", pending_proposal=pending)
    assert RunState.model_validate_json(state.model_dump_json()) == state
    with pytest.raises(ValidationError):
        RunState(task_id="task-2", status="AWAITING_APPROVAL", pending_proposal=pending)
    with pytest.raises(ValidationError):
        RunState(task_id="task-1", status="AWAITING_APPROVAL")


@pytest.mark.parametrize("value", [
    {"ok": True, "error": {"code": "UNKNOWN_TOOL", "message": "bad"}},
    {"ok": False},
    {"ok": False, "data": {}, "error": {"code": "UNKNOWN_TOOL", "message": "bad"}},
    {"ok": "true", "data": {}},
    {"ok": False, "error": {"code": "MADE_UP", "message": "bad"}},
])
def test_result_consistency(value):
    with pytest.raises(ValidationError):
        ToolResult.model_validate(value)


def test_existing_tools_fit_result_envelope(tmp_path):
    db = tmp_path / "issues.sqlite3"
    seed_database(db)
    for result in [read_issue(1, db), read_issue(999, db), read_issue(True, db),
                   get_labels(db), get_labels(tmp_path / "absent.db")]:
        assert ToolResult.model_validate(result).model_dump(mode="json") == result


def test_event_roundtrip_and_generated_request_schema():
    event = ToolEvent(
        timestamp=datetime.now(timezone.utc), task_id="task-1", correlation_id="call-1",
        step=1, tool="read_issue", arguments={"issue_id": 1},
        result=ToolResult(ok=True, data={"id": 1}), duration_ms=0.5,
    )
    assert ToolEvent.model_validate_json(event.model_dump_json()) == event
    schema = TOOL_REQUEST_ADAPTER.json_schema()
    assert set(schema["discriminator"]["mapping"]) == {
        "list_issues", "read_issue", "get_labels", "update_labels", "post_comment",
    }
    json.dumps(schema)
