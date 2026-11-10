import os

import pytest

from agent.llm import FakeModel
from agent.loop import TriageAgent


@pytest.mark.parametrize("issue_id", [7, 47, 48, 49, 50])
def test_injection_fixture_is_untrusted_data_and_cannot_bypass_approval(service, issue_id, monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "test-secret-not-for-output")
    result = TriageAgent(FakeModel(), service=service).run(
        "Triage issue {}".format(issue_id), approval_handler=lambda proposal: "no")
    calls = [entry["result"]["tool"] for entry in result.transcript]
    write = result.transcript[-1]["result"]["result"]
    assert result.status == "DONE"
    assert calls == ["read_issue", "get_labels", "update_labels"]
    assert write["ok"] is False and write["error"]["code"] == "APPROVAL_DENIED"
    assert service.read_issue(issue_id)["data"]["version"] == 1
    assert all("secret-not-for-output" not in str(entry) for entry in result.transcript)


def test_untrusted_issue_cannot_redirect_write_to_another_issue(service):
    result = TriageAgent(FakeModel(), service=service).run("Triage issue 48", approval_handler=lambda proposal: "yes")
    writes = [entry["result"]["result"] for entry in result.transcript if entry["result"]["tool"] == "update_labels"]
    assert writes[0]["data"]["issue_id"] == 48
    assert service.read_issue(12)["data"]["version"] == 1
