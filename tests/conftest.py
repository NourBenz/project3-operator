import json

import pytest

from agent.approval import ApprovalManager
from agent.logging_utils import ToolLogger
from sandbox.mock_github import MockGitHub
from sandbox.seed import seed_database


@pytest.fixture
def service(tmp_path):
    db = tmp_path / "issues.sqlite3"
    seed_database(db)
    return MockGitHub(db, approvals=ApprovalManager(),
                      logger=ToolLogger(tmp_path / "calls.jsonl"))


@pytest.fixture
def authorize():
    def simulate_human(service, tool="update_labels", *, response="yes", task_id="task-1", **changes):
        arguments = {"issue_id": 12, "operation_id": "op-1", "expected_version": 1}
        arguments["labels" if tool == "update_labels" else "body"] = (
            ["bug", "high"] if tool == "update_labels" else "Please share the error message."
        )
        arguments.update(changes)
        proposal = service.propose(tool, arguments, task_id=task_id)
        outcome = service.approvals.prompt(proposal.proposal_id,
                                          input_fn=lambda _: response, output_fn=lambda _: None)
        return arguments, {"task_id": task_id, "proposal_id": proposal.proposal_id, "grant": outcome.grant}
    return simulate_human
