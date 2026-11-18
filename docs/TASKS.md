# Tasks

The final demonstration must use real GitHub issues in our repository. LangGraph and LangSmith are mandatory. SQLite may remain for isolated local tests, but it cannot be the final source of issue data.

Completed tasks and affected files: [COMPLETED_WORK.md](COMPLETED_WORK.md).
Project direction and migration notes: [PROJECT_CONTEXT.md](PROJECT_CONTEXT.md).

## TOOLS-02 - Database and tools: DONE

- **DONE:** Built the SQLite schema, 50-fixture dataset, seed/reset commands and five tools.
- **DONE:** Added validation, human approval, version checks, idempotent writes, transactions, errors, redacted logs and tests.
- **DONE:** Added GitHub API client/service and GitHub mode to the review CLI.
- **DONE:** Populate the repository with the agreed issue dataset, automatically if safe and repeatable; otherwise prepare the manual issue text.
- **DONE:** Added a repeatable, marker-based seeder that skips existing fixtures and creates missing labels/issues/comments when `--apply` is used.
- **DONE:** Read real repository issues and completed an approved real label update with read-back verification.
- **DONE:** Owner notification code is connected and the approved live write completed without notification failure.
- **DONE:** Focused GitHub adapter tests pass for reads, approval, notifications and exact retries; the tools subset passes 111 tests.

The existing SQLite implementation may be preserved for repeatable local tests. The final live demonstration must use the GitHub backend. Fixtures 1-50 currently map to GitHub issues 2-51; issue 1 was already present.

## AGENT-01 - AI controller: PARTIAL

- **DONE:** OpenAI adapter, offline model, tool registry, bounded loop, approval interaction, read-back and CLI were added.
- **DONE:** Connect the controller to the real GitHub-backed tools through LangGraph.
- **DONE:** Represent investigation, proposal, human approval, write, email and verification as explicit LangGraph state transitions.
- **DONE:** Live GitHub reads, a real 404, retry recovery against GitHub after an injected timeout, and rejection of a proposal built from live issue data.
- **DONE:** Automated tests verify bounded stopping, approved-write and email ordering, read-back, and sanitized LangSmith stage traces.
- **OPEN:** Confirm a live approved write through the new graph, SMTP delivery to the intended recipients, and trace ingestion in the LangSmith project.

## EVAL-03 - Evaluation: PARTIAL

- **DONE:** 30 offline scenarios, five injection fixtures, an isolated runner, basic metrics and saved fake-model results exist.
- **OPEN:** Correct the evaluator's expected-status and safety scoring.
- **OPEN:** Decide which tests use SQLite and which tests use real GitHub issues.
- **OPEN:** Evaluate real GitHub trajectories and record the required LangSmith traces.

## SEC-04 - Security, notifications and submission: PARTIAL

- **DONE:** Local tool-boundary protections, approval checks, secret redaction and related tests exist.
- **OPEN:** Review GitHub token permissions, repository access, live-data isolation and issue-creation safety.
- **OPEN:** Review the email content, recipient and failure behavior for post-change notifications.
- **OPEN:** Configure LangSmith tracing and protect trace data and credentials.
- **OPEN:** Complete threat model, error taxonomy, report, postmortem and demo documentation.

## Decisions already clarified

- LangGraph is mandatory.
- LangSmith is mandatory.
- The final source of issues and final writes is the real GitHub repository.
- SQLite is optional local test support, not the final data source.
- The scope is issue reading, labels and comments; no draft pull request or PR.
- After an approved GitHub change, email the repository owner with a short notification.

## Still needed

- Configure and verify SMTP delivery for the owner notification addresses.
- Coordinate the GitHub service integration with AGENT-01; this is agent integration work, not a TOOLS-02 implementation gap.

Do not modify another teammate's workstream without coordination. Update this file and [COMPLETED_WORK.md](COMPLETED_WORK.md) after each agreed change.
