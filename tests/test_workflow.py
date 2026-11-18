import urllib.error

import pytest

from langchain_core.tracers.langchain import wait_for_all_tracers
from langsmith import Client
from langsmith.schemas import LangSmithInfo

from agent.llm import FakeModel
from agent.loop import TriageAgent
from agent.logging_utils import ToolLogger
from test_github_service import FakeClient, FakeNotifier
from tools.github_service import GitHubAPIError, GitHubClient, GitHubService
from tools.notifications import OwnerNotifier


def test_graph_records_approved_github_write_email_and_verification(tmp_path, monkeypatch):
    names = []
    captured = []

    def capture(_client, name, inputs, **kwargs):
        names.append(name)
        captured.append(inputs)

    monkeypatch.setenv("LANGSMITH_TRACING", "true")
    monkeypatch.setenv("LANGSMITH_API_KEY", "test-key")
    monkeypatch.setattr(Client, "info", property(lambda _client: LangSmithInfo()))
    monkeypatch.setattr(Client, "create_run", capture)
    monkeypatch.setattr(Client, "update_run", lambda *args, **kwargs: None)

    client = FakeClient()
    sent = []
    smtp_events = []

    class TestSMTP:
        def __init__(self, host, port, *, timeout):
            assert (host, port, timeout) == ("smtp.example.test", 587, 20)

        def __enter__(self):
            return self

        def __exit__(self, *args):
            pass

        def starttls(self):
            smtp_events.append("tls")

        def login(self, username, password):
            assert (username, password) == ("sender@example.test", "synthetic")
            smtp_events.append("login")

        def send_message(self, message):
            assert smtp_events == ["tls", "login"]
            assert any(method == "PUT" for method, _, _ in client.calls)
            sent.append(message)

    monkeypatch.setenv("SMTP_HOST", "smtp.example.test")
    monkeypatch.setenv("SMTP_PORT", "587")
    monkeypatch.setenv("SMTP_FROM", "sender@example.test")
    monkeypatch.setenv("SMTP_USERNAME", "sender@example.test")
    monkeypatch.setenv("SMTP_PASSWORD", "synthetic")
    monkeypatch.setattr("tools.notifications.smtplib.SMTP", TestSMTP)
    notifier = OwnerNotifier(recipients="owner@example.test")
    service = GitHubService(client.repository, client=client, notifier=notifier,
                            ledger_path=tmp_path / "ops.sqlite3", logger=ToolLogger(tmp_path / "calls.jsonl"))

    def approve(proposal):
        assert not sent
        assert all(method == "GET" for method, _, _ in client.calls)
        return "yes"

    result = TriageAgent(FakeModel(), service=service).run("Triage issue 4", approval_handler=approve)
    wait_for_all_tracers()
    assert result.status == "DONE", result
    assert len(sent) == 1 and sent[0]["To"] == "owner@example.test"
    stages = ("investigation", "proposal", "approval", "write", "email", "verification")
    assert [name for name in names if name in stages] == [
        "investigation", "investigation", "proposal", "approval", "write", "email", "verification",
    ]
    assert "Fix the guide" not in str(captured)
    assert "expected_version" not in str(captured)
    assert client.calls[-1][:2] == ("GET", "/issues/4/comments?per_page=100")


def test_notification_failure_preserves_write_and_readback(tmp_path):
    class UnavailableNotifier:
        def notify(self, **message):
            raise TimeoutError("SMTP timeout")

    client = FakeClient()
    service = GitHubService(client.repository, client=client, notifier=UnavailableNotifier(),
                            ledger_path=tmp_path / "ops.sqlite3", logger=ToolLogger(tmp_path / "calls.jsonl"))
    result = TriageAgent(FakeModel(), service=service).run("Triage issue 4", approval_handler=lambda _: "yes")
    assert result.status == "FAILED"
    assert "email delivery failed" in result.summary
    assert sum(method == "PUT" for method, _, _ in client.calls) == 1
    assert result.transcript[-1]["result"]["tool"] == "read_issue"


def test_ambiguous_github_write_is_never_retried(tmp_path):
    class LostResponseClient(FakeClient):
        def request(self, method, path, payload=None):
            response = super().request(method, path, payload)
            if method == "PUT":
                raise GitHubAPIError("TOOL_UNAVAILABLE", "Lost write response", retryable=True)
            return response

    client = LostResponseClient()
    notifier = FakeNotifier()
    service = GitHubService(client.repository, client=client, notifier=notifier,
                            ledger_path=tmp_path / "ops.sqlite3", logger=ToolLogger(tmp_path / "calls.jsonl"))
    result = TriageAgent(FakeModel(), service=service).run("Triage issue 4", approval_handler=lambda _: "yes")
    assert result.status == "FAILED"
    assert result.transcript[-1]["result"]["result"]["error"]["code"] == "PARTIAL_FAILURE"
    assert sum(method == "PUT" for method, _, _ in client.calls) == 1
    assert not notifier.messages


def test_github_http_failure_mapping():
    def opener(request, *, timeout):
        assert timeout == 20
        raise urllib.error.HTTPError(request.full_url, 403, "Forbidden", None, None)

    client = GitHubClient(FakeClient.repository, token="synthetic", opener=opener)
    with pytest.raises(GitHubAPIError) as error:
        client.request("GET", "/issues/4")
    assert error.value.code == "GITHUB_AUTHORIZATION"
    assert not error.value.retryable
