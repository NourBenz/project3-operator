from agent.approval import ApprovalManager
from tools.github_service import GitHubService


class FakeClient:
    repository = "https://github.com/test/repo"

    def __init__(self):
        self.issue = {
            "number": 4, "title": "Docs issue", "body": "Fix the guide", "state": "open",
            "updated_at": "2026-11-14T18:00:00Z", "html_url": "https://github.com/test/repo/issues/4",
            "labels": [],
        }
        self.calls = []

    def request(self, method, path, payload=None):
        self.calls.append((method, path, payload))
        if path.startswith("/issues?"):
            return [self.issue]
        if path == "/labels?per_page=100":
            return [{"name": "bug"}, {"name": "documentation"}]
        if path == "/issues/4/comments?per_page=100":
            return []
        if path == "/issues/4":
            return self.issue
        if method == "PUT" and path == "/issues/4/labels":
            self.issue["labels"] = [{"name": value} for value in payload["labels"]]
            self.issue["updated_at"] = "2026-11-14T18:01:00Z"
            return self.issue["labels"]
        if method == "POST" and path == "/issues/4/comments":
            return {"id": 7, "body": payload["body"], "created_at": "2026-11-14T18:02:00Z"}
        raise AssertionError((method, path, payload))


class FakeNotifier:
    def __init__(self):
        self.messages = []

    def notify(self, **message):
        self.messages.append(message)


def service(tmp_path):
    fake = FakeClient()
    notifier = FakeNotifier()
    instance = GitHubService(
        "https://github.com/test/repo", client=fake, approvals=ApprovalManager(),
        notifier=notifier, ledger_path=tmp_path / "operations.sqlite3",
    )
    return instance, fake, notifier


def approve(service, tool, arguments, task_id="task-1"):
    proposal = service.propose(tool, arguments, task_id=task_id)
    outcome = service.approvals.prompt(proposal.proposal_id, input_fn=lambda _: "yes", output_fn=lambda _: None)
    return proposal, outcome


def test_github_reads_map_issue_and_labels(tmp_path):
    service_instance, _, _ = service(tmp_path)
    result = service_instance.read_issue(4)
    assert result["ok"] and result["data"]["id"] == 4
    labels = service_instance.get_labels()
    assert labels["ok"] and "documentation" in labels["data"]["labels"]


def test_github_write_requires_approval_and_notifies(tmp_path):
    service_instance, fake, notifier = service(tmp_path)
    args = {"issue_id": 4, "labels": ["documentation"], "operation_id": "op-1", "expected_version": service_instance.read_issue(4)["data"]["version"]}
    denied = service_instance.call("update_labels", args, task_id="task-1")
    assert denied["ok"] is False and denied["error"]["code"] == "APPROVAL_REQUIRED"
    proposal, outcome = approve(service_instance, "update_labels", args)
    result = service_instance.call("update_labels", args, task_id="task-1", proposal_id=proposal.proposal_id, grant=outcome.grant)
    assert result["ok"] and result["data"]["labels"] == ["documentation"]
    assert notifier.messages and fake.issue["labels"] == [{"name": "documentation"}]


def test_github_exact_retry_returns_saved_result(tmp_path):
    service_instance, fake, _ = service(tmp_path)
    args = {"issue_id": 4, "labels": ["documentation"], "operation_id": "op-2", "expected_version": service_instance.read_issue(4)["data"]["version"]}
    proposal, outcome = approve(service_instance, "update_labels", args)
    first = service_instance.call("update_labels", args, task_id="task-1", proposal_id=proposal.proposal_id, grant=outcome.grant)
    proposal2, outcome2 = approve(service_instance, "update_labels", args, task_id="task-1")
    second = service_instance.call("update_labels", args, task_id="task-1", proposal_id=proposal2.proposal_id, grant=outcome2.grant)
    assert first == second
    assert len([call for call in fake.calls if call[0] == "PUT"]) == 1
