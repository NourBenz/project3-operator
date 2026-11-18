"""Opt-in, read-only network checks against a configured GitHub repository."""
import os
import urllib.request

import pytest
from dotenv import load_dotenv

from agent.llm import FakeModel, ModelDecision, ModelToolCall
from agent.logging_utils import ToolLogger
from agent.loop import TriageAgent
from tools.github_service import GitHubClient, GitHubService


@pytest.fixture
def live_target():
    issue = os.environ.get("OPERATOR_LIVE_GITHUB_ISSUE")
    if not issue:
        pytest.skip("Set OPERATOR_LIVE_GITHUB_ISSUE to run real GitHub reads.")
    load_dotenv(os.environ.get("OPERATOR_LIVE_ENV_FILE", ".env"))
    return os.environ["GITHUB_REPOSITORY"], int(issue)


def test_live_github_issue_and_missing_issue(live_target, tmp_path):
    repository, issue = live_target
    service = GitHubService(repository, ledger_path=tmp_path / "ops.sqlite3",
                            logger=ToolLogger(tmp_path / "calls.jsonl"))
    result = TriageAgent(FakeModel(), service=service).run(f"Analyze issue {issue}")
    assert result.status == "DONE"
    assert result.transcript[0]["result"]["result"]["data"]["id"] == issue
    missing = service.read_issue(2147483647)
    assert missing["error"]["code"] == "ISSUE_NOT_FOUND"
    assert missing["error"]["retryable"] is False


def test_live_github_read_retries_after_injected_transport_timeout(live_target, tmp_path):
    repository, issue = live_target
    attempts = []

    def opener(request, *, timeout):
        assert request.get_method() == "GET"
        attempts.append(timeout)
        if len(attempts) == 1:
            raise TimeoutError("Simulated timeout before the request was sent")
        return urllib.request.urlopen(request, timeout=timeout)

    client = GitHubClient(repository, opener=opener)
    service = GitHubService(repository, client=client, ledger_path=tmp_path / "ops.sqlite3",
                            logger=ToolLogger(tmp_path / "calls.jsonl"))
    result = TriageAgent(FakeModel(), service=service).run(f"Analyze issue {issue}")
    assert result.status == "DONE"
    first, second = [step["result"]["result"] for step in result.transcript]
    assert first["error"]["retryable"]
    assert second["ok"]
    assert attempts == [20, 20, 20]

def test_live_github_proposal_rejection_never_leaves_read_only_transport(live_target, tmp_path):
    repository, issue = live_target
    methods = []

    def opener(request, *, timeout):
        methods.append(request.get_method())
        assert methods[-1] == "GET"
        return urllib.request.urlopen(request, timeout=timeout)

    class ProposalModel(FakeModel):
        def decide(self, request, transcript, tools):
            if len(transcript) < 2:
                return super().decide(request, transcript, tools)
            retrieved = transcript[0]["result"]["result"]["data"]
            return ModelDecision((ModelToolCall("post_comment", {
                "issue_id": issue, "body": "Read-only approval test.", "operation_id": "live-readonly-test",
                "expected_version": retrieved["version"],
            }),))

    client = GitHubClient(repository, opener=opener)
    service = GitHubService(repository, client=client, ledger_path=tmp_path / "ops.sqlite3",
                            logger=ToolLogger(tmp_path / "calls.jsonl"))
    seen = []

    def decline(proposal):
        seen.append(proposal.request.args.issue_id)
        return "no"

    result = TriageAgent(ProposalModel(), service=service).run(f"Triage issue {issue}", approval_handler=decline)
    assert result.status == "REJECTED"
    assert seen == [issue] and methods == ["GET"] * 3
