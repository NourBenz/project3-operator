import importlib
import json
import sqlite3
import subprocess
import sys

import pytest

from agent.logging_utils import CallContext, ToolLogger
from sandbox.database import connect
from tools.post_comment import post_comment
from tools.update_labels import update_labels


def test_approved_label_write_persists_and_is_verified_by_readback(service, authorize):
    args, auth = authorize(service)
    result = update_labels(**args, **auth, service=service)
    assert result["ok"] and result["data"]["version"] == 2
    assert result["data"]["labels"] == ["bug", "high"]
    issue = service.read_issue(12)["data"]
    assert issue["labels"] == ["bug", "high"] and issue["version"] == 2
    assert len(service.list_issues("all", 50)["data"]["issues"]) == 50


def test_approved_comment_persists_timestamp_and_version(service, authorize):
    args, auth = authorize(service, "post_comment", body="Which version and error message reproduce this?")
    result = post_comment(**args, **auth, service=service)
    assert result["ok"] and result["data"]["version"] == 2
    comment = service.read_issue(12)["data"]["comments"][0]
    assert comment == result["data"]["comment"]
    assert comment["body"] == args["body"] and comment["operation_id"] == "op-1"
    assert comment["created_at"].endswith("Z")


@pytest.mark.parametrize("issue_id,version,labels,code", [
    (999, 1, ["bug"], "ISSUE_NOT_FOUND"),
    (6, 1, ["documentation"], "ISSUE_CLOSED"),
    (12, 2, ["bug"], "STALE_VERSION"),
    (13, 1, ["bug"], "INVALID_LABEL"),
    (14, 1, ["high"], "INVALID_LABEL"),
    (1, 1, ["feature"], "INVALID_LABEL"),
])
def test_missing_closed_stale_and_conflicting_issues_do_not_write(
        service, authorize, issue_id, version, labels, code):
    args, auth = authorize(service, issue_id=issue_id, expected_version=version, labels=labels)
    assert service.update_labels(**args, **auth)["error"]["code"] == code
    with connect(service.db_path) as db:
        assert db.execute("SELECT COUNT(*) FROM operations").fetchone()[0] == 0


@pytest.mark.parametrize("changes", [
    {"issue_id": True}, {"issue_id": 0}, {"expected_version": "1"},
    {"labels": ["unknown"]}, {"labels": ["bug", "feature"]},
    {"labels": ["low", "high"]}, {"labels": []}, {"operation_id": ""},
])
def test_write_argument_validation_is_logged_and_has_no_effect(service, changes):
    args = {"issue_id": 12, "labels": ["bug"], "operation_id": "op", "expected_version": 1}
    result = service.call("update_labels", args | changes)
    assert result["error"]["code"] == "INVALID_ARGUMENTS"
    events = [json.loads(line) for line in service.logger.path.read_text().splitlines()]
    assert events[-1]["result"]["error"]["code"] == "INVALID_ARGUMENTS"


@pytest.mark.parametrize("body", ["", "   ", "x" * 1001, 123])
def test_comment_validation_is_logged(service, body):
    result = service.call("post_comment", {
        "issue_id": 12, "body": body, "operation_id": "op", "expected_version": 1,
    })
    assert result["error"]["code"] == "INVALID_ARGUMENTS"


def test_comment_on_closed_issue_is_denied(service, authorize):
    args, auth = authorize(service, "post_comment", issue_id=6)
    assert service.post_comment(**args, **auth)["error"]["code"] == "ISSUE_CLOSED"


def test_unrelated_existing_labels_are_preserved(service, authorize):
    with connect(service.db_path) as db:
        db.execute("INSERT INTO labels VALUES ('team:backend','other','Existing project label')")
        db.execute("INSERT INTO issue_labels VALUES (12,'team:backend')")
    args, auth = authorize(service)
    assert service.update_labels(**args, **auth)["data"]["labels"] == ["bug", "high", "team:backend"]


def test_already_present_labels_do_not_increment_version(service, authorize):
    args, auth = authorize(service, issue_id=11, labels=["documentation", "low"])
    result = service.update_labels(**args, **auth)
    assert result["ok"] and not result["data"]["changed"] and result["data"]["version"] == 1


def test_comment_payload_is_data_and_cannot_execute_sql(service, authorize):
    body = "'); DROP TABLE issues; --"
    args, auth = authorize(service, "post_comment", body=body)
    assert service.post_comment(**args, **auth)["ok"]
    assert len(service.list_issues("all", 50)["data"]["issues"]) == 50
    assert service.read_issue(12)["data"]["comments"][0]["body"] == body


def test_unknown_tool_and_scope_injection_are_rejected(service):
    assert service.call("run_shell", {"command": "delete database"})["error"]["code"] == "UNKNOWN_TOOL"
    assert service.call("read_issue", {"issue_id": 12, "db_path": "other.sqlite3"})["error"]["code"] == "INVALID_ARGUMENTS"
    assert service.call("get_labels", {"approved": True})["error"]["code"] == "INVALID_ARGUMENTS"


def test_every_read_failure_and_write_attempt_has_correlated_result_logs(service, authorize):
    context = CallContext(task_id="task-1", step=4, correlation_id="known-call")
    service.read_issue(12, context=context)
    service.read_issue("invalid", context=context)
    service.read_issue(999, context=context)
    service.get_labels(context=context)
    service.list_issues(context=context)
    args, auth = authorize(service)
    service.update_labels(**args, **auth, context=context)
    service.post_comment(12, "not approved", "op-2", 2, task_id="task-1", context=context)
    raw = [json.loads(line) for line in service.logger.path.read_text().splitlines()]
    assert [event["decision"] for event in raw if event["event"] == "approval_decision"] == ["approve"]
    events = [event for event in raw if event["event"].startswith("tool_")]
    assert len(events) == 14
    for event in events:
        assert event["task_id"] == "task-1" and event["correlation_id"] == "known-call"
        assert event["step"] == 4 and event["timestamp"] and "arguments" in event
    for event in events[1::2]:
        assert event["duration_ms"] >= 0 and "result" in event
    assert events[-1]["result"]["error"]["code"] == "APPROVAL_REQUIRED"


def test_logs_redact_secret_fields_text_and_trusted_grant(service, authorize, monkeypatch):
    secret = "sk-proj-supersecret123456789"
    monkeypatch.setenv("OPENAI_API_KEY", secret)
    service.call("get_labels", {"api_key": secret, "authorization": "Bearer secret-value"})
    args, auth = authorize(service, "post_comment", body="OPENAI_API_KEY=" + secret)
    assert service.post_comment(**args, **auth)["ok"]
    text = service.logger.path.read_text()
    assert secret not in text and "secret-value" not in text and auth["grant"]._token not in text
    assert "[REDACTED]" in text


def test_logging_failure_before_start_prevents_any_write(service, authorize):
    args, auth = authorize(service)
    class BrokenLogger:
        def emit(self, event):
            raise OSError("disk unavailable")
    service.logger = BrokenLogger()
    assert service.update_labels(**args, **auth)["error"]["code"] == "TOOL_UNAVAILABLE"
    with connect(service.db_path) as db:
        assert db.execute("SELECT version FROM issues WHERE id=12").fetchone()[0] == 1
        assert db.execute("SELECT COUNT(*) FROM operations").fetchone()[0] == 0


def test_normalized_tool_failure_does_not_expose_traceback(service, monkeypatch):
    module = importlib.import_module("tools.read_issue")
    def unavailable(*args, **kwargs):
        raise sqlite3.OperationalError("database is locked; internal confidential details")
    monkeypatch.setattr(module, "connect", unavailable)
    result = service.read_issue(12)
    assert result["error"]["code"] == "TOOL_UNAVAILABLE" and result["error"]["retryable"]
    assert "confidential" not in json.dumps(result)


def test_partial_failure_preserves_earlier_successful_write(service, authorize, monkeypatch):
    labels, auth = authorize(service)
    assert service.update_labels(**labels, **auth)["ok"]
    comment, comment_auth = authorize(service, "post_comment", operation_id="comment-op", expected_version=2)
    def fail(db, tool, args):
        raise sqlite3.OperationalError("database is locked")
    monkeypatch.setattr(service, "_before_commit", fail)
    assert not service.post_comment(**comment, **comment_auth)["ok"]
    issue = service.read_issue(12)["data"]
    assert issue["labels"] == ["bug", "high"] and issue["version"] == 2 and issue["comments"] == []
    with connect(service.db_path) as db:
        assert db.execute("SELECT COUNT(*) FROM operations").fetchone()[0] == 1


@pytest.mark.parametrize("response,expected_version", [("yes", 2), ("no", 1), ("cancel", 1)])
def test_human_review_cli_respects_explicit_decision(service, response, expected_version):
    result = subprocess.run([
        sys.executable, "-m", "tools.review", "labels", "12", "--labels", "bug", "high",
        "--db", str(service.db_path), "--log", str(service.logger.path),
    ], input=response + "\n", text=True, capture_output=True, timeout=15)
    assert result.returncode == 0, result.stderr
    assert service.read_issue(12)["data"]["version"] == expected_version
    if response == "yes":
        assert "Verified local change." in result.stdout
    else:
        assert "No write executed." in result.stdout


def test_human_review_comment_cli_verifies_persisted_comment(service):
    result = subprocess.run([
        sys.executable, "-m", "tools.review", "comment", "12", "--body", "Which error message appears?",
        "--db", str(service.db_path), "--log", str(service.logger.path),
    ], input="yes\n", text=True, capture_output=True, timeout=15)
    assert result.returncode == 0 and "Verified local change." in result.stdout
    assert service.read_issue(12)["data"]["comments"][0]["body"] == "Which error message appears?"
