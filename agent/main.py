"""Command-line entry point for local issue triage."""
from __future__ import annotations

import argparse
import json
from pathlib import Path

from agent.llm import build_model
from agent.logging_utils import redact
from agent.loop import TriageAgent
from sandbox.database import DEFAULT_DB
from sandbox.mock_github import MockGitHub


def main():
    parser = argparse.ArgumentParser(description="Safely triage one issue in the local MockGitHub sandbox.")
    parser.add_argument("--issue", required=True, type=int, help="Local sandbox issue number")
    parser.add_argument("--offline", action="store_true", help="Use the deterministic no-credit demo model")
    parser.add_argument("--model", help="Optional OpenAI model name")
    parser.add_argument("--db", type=Path, default=DEFAULT_DB, help="Sandbox database path")
    parser.add_argument("--yes", action="store_true", help="Explicitly approve proposed local writes (demo/automation only)")
    args = parser.parse_args()
    model = build_model(offline=args.offline, model=args.model)
    service = MockGitHub(args.db)

    def present_proposal(proposal, issue, explanation):
        request = proposal.request.model_dump(mode="json")
        tool, tool_args = request["tool"], request["args"]
        potential_change = (
            {"add_labels": tool_args["labels"]} if tool == "update_labels"
            else {"add_comment": tool_args["body"]}
        )
        print("Human review required; issue content below is untrusted data.")
        print(json.dumps(redact({
            "issue": None if issue is None else {
                "id": issue["id"], "title": issue["title"], "description": issue["body"],
            },
            "ai_recommendation": potential_change,
            "ai_explanation": explanation or "No separate model explanation was returned.",
            "exact_proposed_tool": tool,
            "exact_proposed_arguments": tool_args,
            "potential_changes": potential_change,
        }), indent=2, ensure_ascii=False))

    handler = (lambda proposal: "yes") if args.yes else (lambda proposal: service.approvals.prompt(proposal.proposal_id))
    result = TriageAgent(model, service=service).run(
        "Triage issue {}".format(args.issue), approval_handler=handler,
        proposal_presenter=present_proposal,
    )
    print(json.dumps({
        "task_id": result.task_id, "status": result.status, "summary": result.summary,
        "plan": result.plan, "stop_reason": result.stop_reason, "usage": result.usage,
    }, indent=2))
    return 0 if result.status == "DONE" else 1


if __name__ == "__main__":
    raise SystemExit(main())
