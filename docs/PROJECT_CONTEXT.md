# GitHub Issue Triage Agent - project context

Tasks: [TASKS.md](TASKS.md). Completed work: [COMPLETED_WORK.md](COMPLETED_WORK.md). Commands: [README.md](../README.md).

## Current project idea

We are building an AI agent that triages issues in the GitHub repository that contains this project. It reads an issue, classifies it, proposes labels or a useful comment, waits for explicit human approval, applies only the approved change and verifies the result.

The professor clarified that the final demonstration must use real GitHub issues through the GitHub API. LangGraph and LangSmith are mandatory. The repository itself becomes the controlled environment for the demonstration.

## What changes from the previous architecture

The previous implementation used:

```text
Python tools -> SQLite mock database
```

The target implementation is:

```text
Python tools -> GitHub API -> real issues in this repository
```

The SQLite backend may remain for isolated automated tests, local development and repeatable failure testing. It must not be used as the final source of issue data or final write target.

The existing five-tool interface should be preserved where possible:

- `list_issues` reads repository issues.
- `read_issue` reads title, body, state, labels, comments and metadata.
- `get_labels` reads the allowed repository labels.
- `update_labels` proposes and applies approved label changes.
- `post_comment` proposes and applies approved comments.

The backend migration must preserve exact human approval, input validation, safe retries, logging and read-back verification. Approved GitHub modifications must trigger a short email notification to the repository owner. A GitHub API token and email credentials must be supplied through private configuration and never committed.

## What is already complete

The local TOOLS-02 implementation is complete and tested. It includes a SQLite schema, 50 fixtures, eight labels, four baseline comments, seed/reset commands, five tools, approval enforcement, version checks, operation records, transactions, errors, redacted logs and automated tests.

The agent workstream added an OpenAI adapter, offline model, tool registry, bounded decide/tool/observe loop, approval interaction, CLI and read-back verification. A real OpenAI workflow was manually demonstrated against SQLite.

These results show that the local workflow works. They do not yet prove that the same workflow works against live GitHub.

## Dataset and real repository plan

The existing fixtures contain 30 public issue snapshots, 15 synthetic edge cases and five malicious test cases. The team may reuse them to create repository issues automatically, but creation must be repeatable and must not create duplicates.

Before creating live issues, decide how fixtures map to GitHub issue numbers, how existing created issues are recognized, whether the repository can contain all 50, and how cleanup or reset will work. Automatic creation is preferred if it is repeatable and duplicate-safe; otherwise prepare the issue text for manual creation. Do not create live issues until the repository link, owner email and creation procedure are available.

## Four workstreams

- **TOOLS-02:** Owns the tool interface and backend behavior. Local SQLite work is complete; the real GitHub adapter is the main pending change for this workstream.
- **AGENT-01:** Owns model integration and orchestration through mandatory LangGraph state transitions. It must use the GitHub-backed tools.
- **EVAL-03:** Owns scenarios, metrics and traces. It must distinguish local deterministic tests from live GitHub evaluation and use mandatory LangSmith tracing.
- **SEC-04:** Owns security, notifications and final documentation. It must review token permissions, live writes, prompt injection, logging, email behavior and LangSmith privacy.

Do not modify another teammate's workstream without coordination.

## Confirmed requirements and open questions

Confirmed:

- Final demonstration uses actual GitHub issues in our repository.
- The agent reads and modifies those issues through the GitHub API.
- Human approval is required before modifications.
- Unauthorized modifications must be blocked and results verified.
- Existing work should be adapted rather than discarded.

Already clarified:

- LangGraph is mandatory.
- LangSmith is mandatory.
- The final source and write target is the real GitHub repository; SQLite is local test support only.
- The project scope is issue reading, labels and comments. No draft PR or PR is required.
- Approved GitHub changes must send an email notification to the repository owner.

Still needed:

- Repository link and owner email address.
- Whether all 50 fixtures must become real issues.
- Whether creation may be automated or must be manual.
- Email provider and private configuration method.

Do not implement the migration, install new dependencies or create GitHub issues until the repository details and ownership are available. Review this context before the next implementation phase.
