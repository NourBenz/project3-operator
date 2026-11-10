import json

import pytest

from agent.approval import ApprovalManager
from agent.llm import FakeModel, ModelDecision, ModelToolCall
from agent.loop import TriageAgent
from agent.stopping import StopLimits
from sandbox.mock_github import MockGitHub


def tools(result):
    return [entry["result"]["tool"] for entry in result.transcript]


def test_offline_agent_reads_proposes_approves_writes_and_verifies(service):
    result = TriageAgent(FakeModel(), service=service).run("Triage issue 12", approval_handler=lambda proposal: "yes")
    assert result.status == "DONE"
    assert tools(result) == ["read_issue", "get_labels", "update_labels", "read_issue"]
    final_read = result.transcript[-1]["result"]["result"]["data"]
    assert {"bug", "high"}.issubset(final_read["labels"])
    assert result.usage["model"] == "offline-fake-triage-v1"


def test_agent_pauses_without_a_human_decision(service):
    result = TriageAgent(FakeModel(), service=service).run("Triage issue 12")
    assert result.status == "AWAITING_APPROVAL"
    assert result.pending_proposal is not None
    assert tools(result) == ["read_issue", "get_labels", "update_labels"]


def test_agent_presents_retrieved_issue_and_model_rationale_before_approval(service):
    presented = []
    result = TriageAgent(FakeModel(), service=service).run(
        "Triage issue 12", approval_handler=lambda proposal: "no",
        proposal_presenter=lambda proposal, issue, explanation: presented.append((proposal, issue, explanation)),
    )
    assert result.status == "REJECTED"
    proposal, issue, explanation = presented[0]
    assert proposal.request.args.issue_id == 12
    assert issue["id"] == 12 and issue["title"]
    assert "Recommended labels" in explanation


@pytest.mark.parametrize("answer", ["no", "cancel"])
def test_rejected_or_cancelled_agent_write_is_not_executed(service, answer):
    result = TriageAgent(FakeModel(), service=service).run("Triage issue 12", approval_handler=lambda proposal: answer)
    assert result.status == ("REJECTED" if answer == "no" else "CANCELLED")
    assert result.transcript[-1]["result"]["result"]["ok"] is False
    assert service.read_issue(12)["data"]["version"] == 1


def test_agent_handles_approval_expiration_without_a_write(service):
    now = [0.0]
    service.approvals = ApprovalManager(ttl_seconds=1, clock=lambda: now[0])
    def expire(proposal):
        now[0] = 2.0
        return "yes"
    result = TriageAgent(FakeModel(), service=service).run("Triage issue 12", approval_handler=expire)
    assert result.status == "DONE"
    assert result.transcript[-1]["result"]["result"]["error"]["code"] == "APPROVAL_EXPIRED"
    assert service.read_issue(12)["data"]["version"] == 1


class TextOnlyCommentModel:
    """Models the live regression: it describes a comment but never calls it."""
    def decide(self, request, transcript, registry):
        calls = tools_from_transcript(transcript)
        if not calls:
            return ModelDecision((ModelToolCall("read_issue", {"issue_id": 13}),))
        if "get_labels" not in calls:
            return ModelDecision((ModelToolCall("get_labels", {}),))
        return ModelDecision(final="I will post a comment asking for human review.")


class HumanReviewCommentModel:
    def decide(self, request, transcript, registry):
        calls = tools_from_transcript(transcript)
        if not calls:
            return ModelDecision((ModelToolCall("read_issue", {"issue_id": 13}),))
        if "get_labels" not in calls:
            return ModelDecision((ModelToolCall("get_labels", {}),))
        if "post_comment" not in calls:
            return ModelDecision((ModelToolCall("post_comment", {
                "issue_id": 13, "body": "Please review the conflicting existing type labels.",
                "operation_id": "request_label_review_13", "expected_version": 1,
            }),))
        return ModelDecision(final="The human-review comment has been posted.")


class NoChangeModel:
    def decide(self, request, transcript, registry):
        calls = tools_from_transcript(transcript)
        if not calls:
            return ModelDecision((ModelToolCall("read_issue", {"issue_id": 11}),))
        if "get_labels" not in calls:
            return ModelDecision((ModelToolCall("get_labels", {}),))
        return ModelDecision(final="No change is needed because the issue is already classified.")


def tools_from_transcript(transcript):
    return [entry["result"]["tool"] for entry in transcript if entry.get("kind") == "tool_result"]


def test_text_only_claimed_comment_cannot_complete_conflicting_issue(service):
    result = TriageAgent(TextOnlyCommentModel(), service=service).run("Triage issue 13")
    assert result.status == "FAILED"
    assert tools(result) == ["read_issue", "get_labels"]
    assert service.read_issue(13)["data"]["comments"] == []


def test_human_review_comment_waits_for_explicit_approval(service):
    result = TriageAgent(HumanReviewCommentModel(), service=service).run("Triage issue 13")
    assert result.status == "AWAITING_APPROVAL"
    assert result.pending_proposal.request.tool == "post_comment"
    assert service.read_issue(13)["data"]["comments"] == []


@pytest.mark.parametrize("answer,expected_status", [("yes", "DONE"), ("no", "REJECTED"), ("cancel", "CANCELLED")])
def test_human_review_comment_respects_approval_and_verifies_success(service, answer, expected_status):
    result = TriageAgent(HumanReviewCommentModel(), service=service).run(
        "Triage issue 13", approval_handler=lambda proposal: answer,
    )
    assert result.status == expected_status
    issue = service.read_issue(13)["data"]
    if answer == "yes":
        assert tools(result) == ["read_issue", "get_labels", "post_comment", "read_issue"]
        assert issue["comments"][0]["operation_id"] == "request_label_review_13"
    else:
        assert tools(result) == ["read_issue", "get_labels", "post_comment"]
        assert issue["comments"] == []


def test_completed_investigation_allows_a_legitimate_no_change_result(service):
    result = TriageAgent(NoChangeModel(), service=service).run("Triage issue 11")
    assert result.status == "DONE"
    assert tools(result) == ["read_issue", "get_labels"]


class ScopeExpansionModel:
    def decide(self, request, transcript, registry):
        calls = tools_from_transcript(transcript)
        if not calls:
            return ModelDecision((ModelToolCall("read_issue", {"issue_id": 12}),))
        if "get_labels" not in calls:
            return ModelDecision((ModelToolCall("get_labels", {}),))
        if "update_labels" in calls:
            return ModelDecision(final="Stopped because the requested target does not match the proposed write.")
        return ModelDecision((ModelToolCall("update_labels", {
            "issue_id": 13, "labels": ["bug"], "operation_id": "wrong-target", "expected_version": 1,
        }),))


class ReadOnlyWriteModel:
    def decide(self, request, transcript, registry):
        calls = tools_from_transcript(transcript)
        if not calls:
            return ModelDecision((ModelToolCall("read_issue", {"issue_id": 12}),))
        if "update_labels" not in calls:
            return ModelDecision((ModelToolCall("update_labels", {
                "issue_id": 12, "labels": ["bug"], "operation_id": "not-authorized", "expected_version": 1,
            }),))
        return ModelDecision(final="The issue was analyzed without making changes.")


def test_scope_guard_rejects_a_write_to_a_different_issue(service):
    result = TriageAgent(ScopeExpansionModel(), service=service).run("Triage issue 12")
    assert result.status == "FAILED"
    assert result.transcript[-1]["result"]["result"]["error"]["code"] == "INVALID_ARGUMENTS"
    assert service.read_issue(13)["data"]["version"] == 1


def test_read_only_request_never_executes_a_model_requested_write(service):
    result = TriageAgent(ReadOnlyWriteModel(), service=service).run("Analyze issue 12")
    assert result.status == "DONE"
    assert result.transcript[-1]["result"]["result"]["error"]["code"] == "INVALID_ARGUMENTS"
    assert service.read_issue(12)["data"]["version"] == 1


def test_ambiguous_task_stops_for_clarification_without_a_tool_call(service):
    result = TriageAgent(FakeModel(), service=service).run("Issue 12")
    assert result.status == "FAILED"
    assert "Clarification required" in result.summary
    assert result.transcript == []


class RepeatModel:
    def decide(self, request, transcript, registry):
        return ModelDecision((ModelToolCall("read_issue", {"issue_id": 1}),))


def test_agent_stops_repeated_calls_before_a_third_execution(service):
    result = TriageAgent(RepeatModel(), service=service).run("Triage issue 1", approval_handler=lambda proposal: "yes")
    assert result.status == "FAILED"
    assert result.stop_reason == "LOOP_DETECTED"
    assert tools(result) == ["read_issue", "read_issue"]


def test_agent_stops_at_decision_limit_without_write(service):
    result = TriageAgent(RepeatModel(), service=service, limits=StopLimits(max_steps=2, max_consecutive_same_calls=10)).run(
        "Triage issue 1", approval_handler=lambda proposal: "yes")
    assert result.status == "MAX_STEPS"
    assert tools(result) == ["read_issue", "read_issue"]


class InvalidToolModel:
    def __init__(self):
        self.calls = 0

    def decide(self, request, transcript, registry):
        self.calls += 1
        if self.calls == 1:
            return ModelDecision((ModelToolCall("shell", {"command": "whoami"}),))
        return ModelDecision(final="Stopped after invalid request")


def test_invalid_model_tool_is_rejected_without_side_effect(service):
    result = TriageAgent(InvalidToolModel(), service=service).run("Triage issue 12", approval_handler=lambda proposal: "yes")
    assert result.status == "FAILED"
    assert result.transcript[0]["result"]["result"]["error"]["code"] == "INVALID_ARGUMENTS"
    assert service.read_issue(12)["data"]["version"] == 1


class TimeoutModel:
    def __init__(self, now):
        self.now = now

    def decide(self, request, transcript, registry):
        self.now[0] = 2.0
        return ModelDecision((ModelToolCall("read_issue", {"issue_id": 1}),))


def test_agent_timeout_stops_before_tool_execution(service):
    now = [0.0]
    result = TriageAgent(TimeoutModel(now), service=service, limits=StopLimits(max_wall_time_seconds=1), clock=lambda: now[0]).run("Triage issue 1")
    assert result.status == "TIMEOUT"
    assert result.stop_reason == "TOOL_TIMEOUT"
    assert not result.transcript
