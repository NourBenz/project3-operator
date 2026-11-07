import json

import pytest

from agent.approval import ApprovalError, ApprovalGrant, ApprovalManager
from sandbox.database import connect
from sandbox.mock_github import MockGitHub
from sandbox.seed import seed_database


def count_effects(service):
    with connect(service.db_path, readonly=True) as db:
        return (db.execute("SELECT COUNT(*) FROM operations").fetchone()[0],
                db.execute("SELECT version FROM issues WHERE id=12").fetchone()[0])


def test_missing_approval_never_writes_even_when_calling_service_directly(service):
    result = service.update_labels(12, ["bug"], "op-1", 1)
    assert result["error"]["code"] == "APPROVAL_REQUIRED"
    assert count_effects(service) == (0, 1)


@pytest.mark.parametrize("answer,code", [
    ("no", "APPROVAL_DENIED"), ("", "APPROVAL_DENIED"),
    ("approved:true", "APPROVAL_DENIED"), ("y", "APPROVAL_DENIED"),
    ("cancel", "USER_CANCELLED"),
])
def test_rejection_invalid_response_and_cancellation_do_not_mutate(service, authorize, answer, code):
    arguments, authorization = authorize(service, response=answer)
    assert service.update_labels(**arguments, **authorization)["error"]["code"] == code
    assert count_effects(service) == (0, 1)


def test_pending_proposal_is_not_approval(service):
    payload = {"issue_id": 12, "labels": ["bug"], "operation_id": "op", "expected_version": 1}
    proposal = service.propose("update_labels", payload, task_id="task-1")
    result = service.update_labels(**payload, task_id="task-1", proposal_id=proposal.proposal_id)
    assert result["error"]["code"] == "APPROVAL_REQUIRED"
    assert count_effects(service) == (0, 1)


@pytest.mark.parametrize("change", [
    {"issue_id": 9}, {"labels": ["question"]},
    {"operation_id": "op-2"}, {"expected_version": 2},
])
def test_changed_arguments_require_fresh_exact_approval(service, authorize, change):
    arguments, authorization = authorize(service)
    assert service.update_labels(**(arguments | change), **authorization)["error"]["code"] == "APPROVAL_MISMATCH"
    assert count_effects(service) == (0, 1)


def test_wrong_task_and_action_are_rejected(service, authorize):
    args, auth = authorize(service)
    assert service.update_labels(**args, **(auth | {"task_id": "other"}))["error"]["code"] == "APPROVAL_MISMATCH"
    comment = {"issue_id": 12, "body": "Not the approved action", "operation_id": "op-1", "expected_version": 1}
    assert service.post_comment(**comment, **auth)["error"]["code"] == "APPROVAL_MISMATCH"


def test_forged_grant_and_forged_approval_flag_are_rejected(service, authorize):
    args, auth = authorize(service)
    for grant in (ApprovalGrant("fake"), {"approved": True}, auth["grant"]._token):
        result = service.update_labels(**args, **(auth | {"grant": grant}))
        assert result["error"]["code"] in ("APPROVAL_REQUIRED", "APPROVAL_MISMATCH")
    assert service.call("update_labels", args | {"approved": True})["error"]["code"] == "INVALID_ARGUMENTS"
    assert count_effects(service) == (0, 1)


def test_approval_does_not_cross_sandbox_boundaries(service, authorize, tmp_path):
    args, auth = authorize(service)
    other_path = tmp_path / "other.sqlite3"
    seed_database(other_path)
    other = MockGitHub(other_path, approvals=service.approvals, logger=service.logger)
    assert other.update_labels(**args, **auth)["error"]["code"] == "APPROVAL_MISMATCH"
    assert count_effects(other) == (0, 1)


def test_expiry_and_delayed_human_response_fail_closed(service, authorize):
    now = [0.0]
    service.approvals = ApprovalManager(ttl_seconds=10, clock=lambda: now[0])
    args, auth = authorize(service)
    now[0] = 10.0
    assert service.update_labels(**args, **auth)["error"]["code"] == "APPROVAL_EXPIRED"
    assert count_effects(service) == (0, 1)
    proposal = service.propose("update_labels", args, task_id="task-2")
    now[0] = 21.0
    with pytest.raises(ApprovalError, match="expired"):
        service.approvals.decide(proposal.proposal_id, "yes")


def test_cancel_task_prevents_pending_and_future_writes(service, authorize):
    args, auth = authorize(service)
    service.approvals.cancel_task("task-1")
    assert service.update_labels(**args, **auth)["error"]["code"] == "USER_CANCELLED"
    with pytest.raises(ApprovalError):
        service.propose("update_labels", args, task_id="task-1")
    assert count_effects(service) == (0, 1)


def test_eof_at_human_prompt_is_cancellation(service):
    args = {"issue_id": 12, "labels": ["bug"], "operation_id": "op", "expected_version": 1}
    proposal = service.propose("update_labels", args, task_id="task-1")
    def eof(_):
        raise EOFError()
    outcome = service.approvals.prompt(proposal.proposal_id, input_fn=eof, output_fn=lambda _: None)
    assert outcome.decision.decision == "cancel"
    assert outcome.grant is None


def test_only_yes_approves_and_decision_cannot_be_replaced(service, authorize):
    args, auth = authorize(service, response=" YES ")
    assert service.update_labels(**args, **auth)["ok"]
    with pytest.raises(ApprovalError):
        service.approvals.decide(auth["proposal_id"], "yes")
    assert auth["grant"]._token not in repr(auth["grant"])


def test_issue_instruction_text_cannot_mint_approval(service):
    malicious = service.read_issue(49)["data"]["body"]
    result = service.call("update_labels", {
        "issue_id": 12, "labels": ["critical"], "operation_id": "attack", "expected_version": 1,
        "approved": True, "approval_token": malicious,
    })
    assert result["error"]["code"] == "INVALID_ARGUMENTS"
    assert count_effects(service) == (0, 1)


@pytest.mark.parametrize("response", ["yes", "no", "cancel"])
def test_explicit_human_decisions_are_audited_without_token(service, authorize, response):
    args, auth = authorize(service, response=response)
    records = [json.loads(line) for line in service.logger.path.read_text().splitlines()]
    assert records[-1]["event"] == "approval_decision"
    assert records[-1]["decision"] == {"yes": "approve", "no": "reject", "cancel": "cancel"}[response]
    assert records[-1]["task_id"] == "task-1"
    assert "grant" not in records[-1]


def test_unrecorded_human_decision_cannot_issue_a_grant(service):
    class BrokenLogger:
        def emit(self, event):
            raise OSError("disk full")
    service.logger = BrokenLogger()
    proposal = service.propose("update_labels", {
        "issue_id": 12, "labels": ["bug"], "operation_id": "op", "expected_version": 1,
    }, task_id="task-1")
    with pytest.raises(ApprovalError, match="no approval was issued"):
        service.approvals.decide(proposal.proposal_id, "yes")
    assert count_effects(service) == (0, 1)
