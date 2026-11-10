"""Command-line entry point for local issue triage."""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from agent.llm import build_model
from agent.logging_utils import redact
from agent.loop import TriageAgent
from sandbox.database import DEFAULT_DB
from sandbox.mock_github import MockGitHub


USE_COLOR = sys.stdout.isatty()


def color(text, code):
    if not USE_COLOR:
        return text
    return f"\033[{code}m{text}\033[0m"


def heading(title):
    print()
    print(color("-" * 58, "36"))
    print(color(f"  {title}", "1;36"))
    print(color("-" * 58, "36"))


def show_value(label, value):
    import textwrap

    if value is None or value == "":
        value = "Not provided"

    if isinstance(value, (dict, list)):
        value = json.dumps(value, indent=2, ensure_ascii=False, default=str)

    prefix = f"  {label:<19}: "
    indent = " " * len(prefix)
    lines = str(value).splitlines() or [""]

    print(textwrap.fill(
        lines[0],
        width=76,
        initial_indent=prefix,
        subsequent_indent=indent,
        break_long_words=False,
        break_on_hyphens=False,
    ))

    for line in lines[1:]:
        print(textwrap.fill(
            line,
            width=76,
            initial_indent=indent,
            subsequent_indent=indent,
            break_long_words=False,
            break_on_hyphens=False,
        ))


def print_proposal(proposal, issue, explanation):
    request = proposal.request.model_dump(mode="json")
    tool = request["tool"]
    tool_args = request["args"]

    print()
    print(color("=" * 58, "36"))
    print(color("              HUMAN APPROVAL REQUIRED", "1;33"))
    print(color("=" * 58, "36"))

    heading("ISSUE INFORMATION")
    if issue is None:
        show_value("Issue", "Issue information unavailable")
    else:
        show_value("Issue number", f"#{issue['id']}")
        show_value("Title", issue["title"])
        show_value("Description", issue["body"])

    heading("AI RECOMMENDATION")
    show_value("Explanation", explanation or "No separate explanation returned.")

    if tool == "update_labels":
        show_value("Action", "Update issue labels")
        show_value("Labels", tool_args.get("labels"))
    elif tool == "post_comment":
        show_value("Action", "Post a comment")
        show_value("Comment", tool_args.get("body"))
    else:
        show_value("Action", tool)

    heading("EXACT PROPOSED TOOL CALL")
    show_value("Tool", tool)
    show_value("Arguments", redact(tool_args))

    print()
    print(color("  Review the proposed change before approving it.", "1;33"))


def print_result(result):
    status = result.status
    status_color = "32" if status == "DONE" else "31"

    print()
    print(color("=" * 58, "36"))
    print(color("              ISSUE TRIAGE AGENT", "1;36"))
    print(color("=" * 58, "36"))

    heading("FINAL RESULT")
    print(f"  Status              : {color(status, '1;' + status_color)}")
    show_value("Task ID", result.task_id)
    show_value("Summary", result.summary)

    heading("EXECUTION PLAN")
    plan = result.plan or []

    if isinstance(plan, str):
        try:
            plan = json.loads(plan)
        except (ValueError, TypeError):
            plan = [plan]

    if isinstance(plan, list):
        for index, step in enumerate(plan, 1):
            print(f"  {index}. {step}")
    else:
        show_value("Plan", plan)

    heading("EXECUTION STATUS")
    stop_reason = result.stop_reason
    if stop_reason:
        show_value("Stop reason", stop_reason)
    else:
        show_value("Stop reason", "Task completed without a reported stop reason.")

    usage = result.usage or {}
    if hasattr(usage, "model_dump"):
        usage = usage.model_dump()
    elif not isinstance(usage, dict):
        usage = vars(usage) if hasattr(usage, "__dict__") else {}

    heading("TOKEN USAGE")
    if usage:
        for key, value in usage.items():
            show_value(key.replace("_", " ").title(), value)
    else:
        show_value("Usage", "No token usage reported")

    print()
    print(color("=" * 58, "36"))
    if status == "DONE":
        print(color("             TASK COMPLETED", "1;32"))
    else:
        print(color("             TASK NOT COMPLETED", "1;31"))
    print(color("=" * 58, "36"))
    print()


def main():
    parser = argparse.ArgumentParser(
        description="Safely triage one issue in the local MockGitHub sandbox."
    )
    parser.add_argument("--issue", required=True, type=int, help="Local sandbox issue number")
    parser.add_argument("--offline", action="store_true", help="Use the deterministic no-credit demo model")
    parser.add_argument("--model", help="Optional OpenAI model name")
    parser.add_argument("--db", type=Path, default=DEFAULT_DB, help="Sandbox database path")
    parser.add_argument("--yes", action="store_true", help="Explicitly approve proposed local writes (demo/automation only)")
    args = parser.parse_args()

    model = build_model(offline=args.offline, model=args.model)
    service = MockGitHub(args.db)

    def present_proposal(proposal, issue, explanation):
        print_proposal(proposal, issue, explanation)

    handler = (
        (lambda proposal: "yes")
        if args.yes
        else (lambda proposal: service.approvals.prompt(proposal.proposal_id))
    )

    result = TriageAgent(model, service=service).run(
        f"Triage issue {args.issue}",
        approval_handler=handler,
        proposal_presenter=present_proposal,
    )

    print_result(result)
    return 0 if result.status == "DONE" else 1


if __name__ == "__main__":
    raise SystemExit(main())
