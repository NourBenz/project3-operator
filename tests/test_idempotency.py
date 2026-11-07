import json
import sqlite3
from concurrent.futures import ThreadPoolExecutor

import pytest

from agent.approval import ApprovalManager
from agent.logging_utils import ToolLogger
from sandbox.database import connect
from sandbox.mock_github import MockGitHub
from sandbox.seed import seed_database


@pytest.mark.parametrize("tool", ["update_labels", "post_comment"])
def test_exact_retry_returns_saved_result_without_a_second_effect(service, authorize, tool):
    args, auth = authorize(service, tool)
    method = getattr(service, tool)
    first = method(**args, **auth)
    assert first["ok"]
    for _ in range(3):
        assert method(**args, **auth) == first
    issue = service.read_issue(12)["data"]
    assert issue["version"] == 2
    assert len(issue["comments"]) == (1 if tool == "post_comment" else 0)
    with connect(service.db_path) as db:
        assert db.execute("SELECT COUNT(*) FROM operations").fetchone()[0] == 1


def test_operation_id_reused_for_different_approved_request_fails(service, authorize):
    args, auth = authorize(service)
    assert service.update_labels(**args, **auth)["ok"]
    changed, changed_auth = authorize(service, "post_comment", expected_version=2)
    assert service.post_comment(**changed, **changed_auth)["error"]["code"] == "DUPLICATE_OPERATION"
    assert service.read_issue(12)["data"]["comments"] == []


def test_timeout_after_commit_retries_without_duplicate_comment(service, authorize, monkeypatch):
    args, auth = authorize(service, "post_comment")
    def timeout(tool, args):
        raise TimeoutError("simulated lost response after commit")
    monkeypatch.setattr(service, "_after_commit", timeout)
    result = service.post_comment(**args, **auth)
    assert result["error"]["code"] == "TOOL_TIMEOUT"
    with connect(service.db_path) as db:
        assert db.execute("SELECT COUNT(*) FROM operations").fetchone()[0] == 1
    recovered = service.post_comment(**args, **auth)
    assert recovered["ok"] and recovered["data"]["version"] == 2
    assert len(service.read_issue(12)["data"]["comments"]) == 1


def test_failure_before_commit_rolls_back_effect_and_ledger_and_allows_retry(service, authorize, monkeypatch):
    args, auth = authorize(service, "post_comment")
    def timeout(db, tool, args):
        raise TimeoutError("before commit")
    monkeypatch.setattr(service, "_before_commit", timeout)
    assert service.post_comment(**args, **auth)["error"]["code"] == "TOOL_TIMEOUT"
    assert service.read_issue(12)["data"]["version"] == 1
    assert service.read_issue(12)["data"]["comments"] == []
    with connect(service.db_path) as db:
        assert db.execute("SELECT COUNT(*) FROM operations").fetchone()[0] == 0
    monkeypatch.setattr(service, "_before_commit", lambda *args: None)
    assert service.post_comment(**args, **auth)["ok"]


def test_expired_grant_can_retrieve_exact_committed_outcome_but_cannot_create_new_write(service, authorize):
    now = [0.0]
    service.approvals = ApprovalManager(ttl_seconds=10, clock=lambda: now[0])
    args, auth = authorize(service)
    first = service.update_labels(**args, **auth)
    now[0] = 11.0
    assert service.update_labels(**args, **auth) == first
    seed_database(service.db_path, reset=True)
    assert service.update_labels(**args, **auth)["error"]["code"] == "APPROVAL_EXPIRED"
    assert service.read_issue(12)["data"]["version"] == 1


def test_committed_result_requires_trusted_authorization_even_for_replay(service, authorize):
    args, auth = authorize(service)
    assert service.update_labels(**args, **auth)["ok"]
    assert service.update_labels(**args, **(auth | {"grant": None}))["error"]["code"] == "APPROVAL_REQUIRED"


def test_restart_requires_new_human_approval_then_can_retrieve_saved_outcome(service, authorize):
    args, auth = authorize(service, "post_comment")
    first = service.post_comment(**args, **auth)
    restarted = MockGitHub(service.db_path, logger=service.logger)
    assert restarted.post_comment(**args, **auth)["error"]["code"] == "APPROVAL_REQUIRED"
    fresh_args, fresh_auth = authorize(restarted, "post_comment")
    assert restarted.post_comment(**fresh_args, **fresh_auth) == first
    assert len(restarted.read_issue(12)["data"]["comments"]) == 1


def test_two_workers_replaying_same_operation_commit_only_one_comment(service, authorize):
    other = MockGitHub(service.db_path, logger=service.logger)
    args1, auth1 = authorize(service, "post_comment")
    args2, auth2 = authorize(other, "post_comment")
    with ThreadPoolExecutor(max_workers=2) as pool:
        futures = [pool.submit(backend.post_comment, **args, **auth)
                   for backend, args, auth in [(service, args1, auth1), (other, args2, auth2)]]
        results = [future.result(timeout=10) for future in futures]
    assert results[0] == results[1] and results[0]["ok"]
    assert len(service.read_issue(12)["data"]["comments"]) == 1


def test_final_log_failure_reports_partial_failure_and_replay_is_safe(service, authorize):
    args, auth = authorize(service, "post_comment")
    actual_logger = service.logger
    class FinalFailure:
        def emit(self, event):
            if event["event"] == "tool_result":
                raise OSError("disk full")
            actual_logger.emit(event)
    service.logger = FinalFailure()
    assert service.post_comment(**args, **auth)["error"]["code"] == "PARTIAL_FAILURE"
    service.logger = actual_logger
    assert service.post_comment(**args, **auth)["ok"]
    assert len(service.read_issue(12)["data"]["comments"]) == 1


def test_two_different_writes_with_same_expected_version_cannot_both_commit(service, authorize):
    args1, auth1 = authorize(service, "post_comment")
    args2, auth2 = authorize(service, "post_comment", operation_id="other-op")
    assert service.post_comment(**args1, **auth1)["ok"]
    assert service.post_comment(**args2, **auth2)["error"]["code"] == "STALE_VERSION"
    assert len(service.read_issue(12)["data"]["comments"]) == 1


def test_non_timeout_failure_after_commit_is_reported_as_partial(service, authorize, monkeypatch):
    args, auth = authorize(service, "post_comment")
    def fail(tool, args):
        raise RuntimeError("simulated response failure")
    monkeypatch.setattr(service, "_after_commit", fail)
    result = service.post_comment(**args, **auth)
    assert result["error"]["code"] == "PARTIAL_FAILURE"
    assert service.post_comment(**args, **auth)["ok"]
    assert len(service.read_issue(12)["data"]["comments"]) == 1
