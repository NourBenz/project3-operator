import argparse
import json
import uuid
from pathlib import Path

from agent.approval import ApprovalError
from agent.logging_utils import CallContext, ToolLogger
from sandbox.database import DEFAULT_DB
from sandbox.mock_github import MockGitHub
from tools.github_service import GitHubService


def main():
    parser = argparse.ArgumentParser(description="Human-reviewed local tool workflow; no AI or automatic approval.")
    parser.add_argument("action", choices=("labels", "comment"))
    parser.add_argument("issue_id", type=int)
    parser.add_argument("--labels", nargs="+")
    parser.add_argument("--body")
    parser.add_argument("--operation-id", default=None)
    parser.add_argument("--db", type=Path, default=DEFAULT_DB)
    parser.add_argument("--log", type=Path, default=None)
    parser.add_argument("--github", action="store_true", help="Use the configured live GitHub repository")
    parser.add_argument("--repository", default=None, help="GitHub repository URL; defaults to GITHUB_REPOSITORY")
    args = parser.parse_args()
    logger = ToolLogger(args.log) if args.log else None
    if args.github:
        import os
        repository = args.repository or os.environ.get("GITHUB_REPOSITORY")
        if not repository:
            parser.error("--repository or GITHUB_REPOSITORY is required with --github")
        service = GitHubService(repository, logger=logger)
    else:
        service = MockGitHub(args.db, logger=logger)
    task_id = "human-" + uuid.uuid4().hex
    context = CallContext(task_id=task_id)
    before = service.read_issue(args.issue_id, context=context)
    if not before["ok"]:
        print(json.dumps(before, indent=2))
        return 1
    tool = "update_labels" if args.action == "labels" else "post_comment"
    if tool == "update_labels" and not args.labels:
        parser.error("--labels is required for labels")
    if tool == "post_comment" and args.body is None:
        parser.error("--body is required for comment")
    payload = {
        "issue_id": args.issue_id, "expected_version": before["data"]["version"],
        "operation_id": args.operation_id or uuid.uuid4().hex,
    }
    payload["labels" if tool == "update_labels" else "body"] = args.labels if tool == "update_labels" else args.body
    try:
        proposal = service.propose(tool, payload, task_id=task_id)
        outcome = service.approvals.prompt(proposal.proposal_id)
    except (ApprovalError, ValueError) as error:
        print(f"Proposal not executed: {error}")
        return 1
    if outcome.grant is None:
        print(f"Human decision: {outcome.decision.decision}. No write executed.")
        return 0
    method = service.update_labels if tool == "update_labels" else service.post_comment
    result = method(**payload, task_id=task_id, proposal_id=proposal.proposal_id,
                    grant=outcome.grant, context=CallContext(task_id=task_id, step=2))
    print(json.dumps(result, indent=2))
    if not result["ok"]:
        return 1
    verified = service.read_issue(args.issue_id, context=CallContext(task_id=task_id, step=3))
    print("Read-back:")
    print(json.dumps(verified, indent=2))
    if not verified["ok"] or verified["data"]["version"] != result["data"]["version"]:
        return 1
    if tool == "update_labels" and not set(payload["labels"]).issubset(verified["data"]["labels"]):
        return 1
    if tool == "post_comment" and not any(
        comment["operation_id"] == payload["operation_id"] for comment in verified["data"]["comments"]
    ):
        return 1
    print("Verified GitHub change." if args.github else "Verified local change.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
