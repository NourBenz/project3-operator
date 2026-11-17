import pytest

from agent.llm import FakeModel
from agent.loop import TriageAgent
from agent.main import (
    BackendConfigurationError,
    build_backend,
    check_github_connectivity,
    main,
)
from agent.approval import ApprovalManager
from sandbox.mock_github import MockGitHub
from tools.github_service import GitHubAPIError, GitHubService


class FakeGitHubClient:
    repository = "https://github.com/test/private-repository"

    def __init__(self):
        self.calls = []
        self.issue = {
            "number": 4,
            "title": "Documentation issue",
            "body": "The setup guide is unclear.",
            "state": "open",
            "updated_at": "2026-11-17T10:00:00Z",
            "html_url": "https://github.com/test/private-repository/issues/4",
            "labels": [],
        }

    def request(self, method, path, payload=None):
        self.calls.append((method, path, payload))
        if path == "/issues/4":
            return self.issue
        if path == "/issues/4/comments?per_page=100":
            return []
        if path == "/labels?per_page=100":
            return [
                {"name": "documentation", "description": "Documentation"},
                {"name": "medium", "description": "Ordinary priority"},
            ]
        raise AssertionError((method, path, payload))


class FakeNotifier:
    def notify(self, **message):
        raise AssertionError(f"No notification should be sent: {message}")


def github_service(tmp_path):
    client = FakeGitHubClient()
    service = GitHubService(
        client.repository,
        client=client,
        approvals=ApprovalManager(),
        notifier=FakeNotifier(),
        ledger_path=tmp_path / "operations.sqlite3",
    )
    return service, client


def test_build_backend_defaults_to_local(tmp_path):
    service = build_backend("local", db=tmp_path / "issues.sqlite3")
    assert isinstance(service, MockGitHub)
    assert service.db_path == (tmp_path / "issues.sqlite3").resolve()


def test_github_backend_requires_repository(monkeypatch):
    monkeypatch.setattr("agent.main.load_dotenv", lambda: None)
    monkeypatch.delenv("GITHUB_REPOSITORY", raising=False)
    with pytest.raises(BackendConfigurationError, match="GITHUB_REPOSITORY"):
        build_backend("github")


def test_github_backend_uses_configured_repository_without_exposing_token(monkeypatch):
    captured = []
    monkeypatch.setattr("agent.main.load_dotenv", lambda: None)
    monkeypatch.setenv("GITHUB_REPOSITORY", "https://github.com/test/private-repository")

    service = build_backend(
        "github", github_service_factory=lambda repository: captured.append(repository) or object(),
    )

    assert service is not None
    assert captured == ["https://github.com/test/private-repository"]


def test_github_configuration_error_is_safe(monkeypatch, capsys):
    def fail(_repository):
        raise GitHubAPIError("CONFIGURATION_ERROR", "GITHUB_TOKEN is not configured")

    monkeypatch.setattr("agent.main.load_dotenv", lambda: None)
    monkeypatch.setenv("GITHUB_REPOSITORY", "https://github.com/test/private-repository")
    monkeypatch.setattr("agent.main.GitHubService", fail)

    with pytest.raises(SystemExit) as stopped:
        main(["--backend", "github", "--issue", "4", "--check-connectivity"])

    assert stopped.value.code == 2
    output = capsys.readouterr().err
    assert "GITHUB_TOKEN is not configured" in output
    assert "Bearer" not in output and "ghp_" not in output


def test_connectivity_check_is_read_only(tmp_path):
    service, client = github_service(tmp_path)
    result = check_github_connectivity(service, 4)

    assert result["ok"] and result["data"]["id"] == 4
    assert client.calls == [
        ("GET", "/issues/4", None),
        ("GET", "/issues/4/comments?per_page=100", None),
    ]


def test_connectivity_cli_uses_fake_github_and_does_not_build_model(
    tmp_path, monkeypatch, capsys,
):
    service, client = github_service(tmp_path)
    monkeypatch.setattr("agent.main.load_dotenv", lambda: None)
    monkeypatch.setattr("agent.main.GitHubService", lambda _repository: service)
    monkeypatch.setattr(
        "agent.main.build_model",
        lambda **_kwargs: pytest.fail("connectivity check must not construct a model"),
    )

    status = main([
        "--backend", "github",
        "--repository", service.scope,
        "--issue", "4",
        "--check-connectivity",
    ])

    assert status == 0
    output = capsys.readouterr().out
    assert "authenticated read succeeded" in output
    assert "Remote write" in output and "Not attempted" in output
    assert [method for method, _, _ in client.calls] == ["GET", "GET"]


def test_fake_github_service_can_be_injected_into_agent_without_write(tmp_path):
    service, client = github_service(tmp_path)
    result = TriageAgent(FakeModel(), service=service).run(
        "Triage issue 4", approval_handler=lambda _proposal: "no",
    )

    assert result.status == "REJECTED"
    assert [entry[0] for entry in client.calls] == ["GET", "GET", "GET"]
    assert not any(method in {"POST", "PUT", "PATCH", "DELETE"} for method, _, _ in client.calls)


def test_github_mode_disables_noninteractive_approval():
    with pytest.raises(SystemExit) as stopped:
        main(["--backend", "github", "--issue", "4", "--yes"])
    assert stopped.value.code == 2
