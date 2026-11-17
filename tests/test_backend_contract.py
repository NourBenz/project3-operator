"""Shared controller-facing contract tests for local and GitHub backends."""
from __future__ import annotations

from collections.abc import Callable

import pytest

from agent.approval import ApprovalManager
from agent.backend import IssueService
from agent.schemas import ToolResult, WriteProposal
from sandbox.mock_github import MockGitHub
from sandbox.seed import seed_database
from tools.github_service import GitHubService


class FakeGitHubClient:
    repository = "https://github.com/test/backend-contract"

    def __init__(self):
        self.calls = []
        self.issue = {
            "number": 4,
            "title": "Documentation problem",
            "body": "The setup guide is unclear.",
            "state": "open",
            "updated_at": "2026-11-14T18:00:00Z",
            "html_url": "https://github.com/test/backend-contract/issues/4",
            "labels": [],
        }

    def request(self, method, path, payload=None):
        self.calls.append((method, path, payload))
        if path.startswith("/issues?"):
            return [self.issue]
        if path == "/issues/4":
            return self.issue
        if path == "/issues/4/comments?per_page=100":
            return []
        if path == "/labels?per_page=100":
            return [
                {"name": "bug", "description": "A defect"},
                {"name": "documentation", "description": "Documentation"},
            ]
        raise AssertionError((method, path, payload))


class FakeNotifier:
    def notify(self, **message):
        raise AssertionError(f"Contract tests must not notify: {message}")


@pytest.fixture(params=("sqlite", "github"))
def backend(request, tmp_path):
    if request.param == "sqlite":
        database = tmp_path / "issues.sqlite3"
        seed_database(database)
        return MockGitHub(database), 12

    client = FakeGitHubClient()
    service = GitHubService(
        client.repository,
        client=client,
        approvals=ApprovalManager(),
        notifier=FakeNotifier(),
        ledger_path=tmp_path / "github-operations.sqlite3",
    )
    return service, 4


def assert_result(value):
    """Require the normalized envelope without equating backend data models."""
    return ToolResult.model_validate(value)


def test_backends_satisfy_runtime_protocol(backend):
    service, _ = backend
    assert isinstance(service, IssueService)
    assert isinstance(service.approvals, ApprovalManager)
    assert isinstance(service.scope, str) and service.scope
    for name in (
        "propose", "call", "list_issues", "read_issue", "get_labels",
        "update_labels", "post_comment",
    ):
        assert isinstance(getattr(service, name), Callable)


def test_backends_return_normalized_read_results(backend):
    service, issue_id = backend

    listed = assert_result(service.list_issues(state="open", limit=5))
    issue = assert_result(service.read_issue(issue_id))
    labels = assert_result(service.get_labels())
    dispatched = assert_result(service.call("read_issue", {"issue_id": issue_id}))

    assert listed.ok and isinstance(listed.data["issues"], list)
    assert issue.ok and issue.data["id"] == issue_id
    assert labels.ok and isinstance(labels.data["taxonomy"], list)
    assert dispatched.ok and dispatched.data["id"] == issue_id


@pytest.mark.parametrize("tool", ("update_labels", "post_comment"))
def test_backends_share_proposal_and_approval_required_contract(backend, tool):
    service, issue_id = backend
    version = service.read_issue(issue_id)["data"]["version"]
    arguments = {
        "issue_id": issue_id,
        "operation_id": f"contract-{tool}-{issue_id}",
        "expected_version": version,
    }
    if tool == "update_labels":
        arguments["labels"] = ["documentation"]
        direct_call = service.update_labels
    else:
        arguments["body"] = "Please provide more details."
        direct_call = service.post_comment

    proposal = service.propose(tool, arguments, task_id="contract-task")
    denied_dispatch = assert_result(
        service.call(tool, arguments, task_id="contract-task")
    )
    denied_direct = assert_result(direct_call(**arguments, task_id="contract-task"))

    assert isinstance(proposal, WriteProposal)
    assert proposal.request.tool == tool
    assert proposal.request.args.issue_id == issue_id
    for denied in (denied_dispatch, denied_direct):
        assert not denied.ok
        assert denied.error.code == "APPROVAL_REQUIRED"
