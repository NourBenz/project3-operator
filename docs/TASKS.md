# Tasks

**Current:** tools are done; 172 tests pass. Next: the AI controller.
Completed tasks and affected files: [COMPLETED_WORK.md](COMPLETED_WORK.md).
Manual commands: [README.md](../README.md).

## AGENT-01 - AI controller: PARTIAL

Connect OpenAI to the existing tools and complete the triage workflow.

- **DONE - 01.1:** Shared Pydantic tool, result, proposal and approval contracts.
- **DONE - 01.2:** Tested stopping, retry, repetition, timeout and cost controls.
- **OPEN - 01.3:** Configurable OpenAI adapter and fake model for offline tests.
- **OPEN - 01.4:** Triage instructions and classification rubric.
- **OPEN - 01.5:** Connect the agent registry to the existing service dispatcher.
- **OPEN - 01.6:** Implement the decide -> tool -> observe loop.
- **OPEN - 01.7:** Pause/resume the agent for human approval using the existing approval manager.
- **OPEN - 01.8:** Bounded recovery using the same operation ID for write retries.
- **OPEN - 01.9:** Agent CLI, decision traces and actual token usage.
- **OPEN - 01.10:** End-to-end agent tests.

**Finished when:** a real model reads an issue, proposes a change, waits for approval,
writes, verifies and stops. Denial and failures must be handled safely.

## TOOLS-02 - Database and tools: DONE

- **DONE - 02.1:** SQLite schema, constraints, migration and atomic persistence.
- **DONE - 02.2:** 50 issues, eight labels and four baseline comments.
- **DONE - 02.3:** Initialize/reset and preserve existing edits.
- **DONE - 02.4:** list_issues, read_issue and get_labels.
- **DONE - 02.5:** Exact human approval, rejection, expiry and cancellation.
- **DONE - 02.6:** Approved label updates with conflict/closed-issue/version checks.
- **DONE - 02.7:** Approved comments with length and version checks.
- **DONE - 02.8:** Persisted outcomes, safe retries and duplicate-operation protection.
- **DONE - 02.9:** Normalized errors and redacted call/approval logs.
- **DONE - 02.10:** Tool, approval, retry, rollback, concurrency and CLI tests.

The AI developer should use the existing service rather than rebuild these tools.

## EVAL-03 - Evaluation: OPEN

Measure the steps taken and resulting database changes, not just the final answer.

- **OPEN - 03.1:** Define scenario format and expected outcomes.
- **OPEN - 03.2:** Write 30+ scenarios covering normal flows and failures.
- **OPEN - 03.3:** Include at least five distinct injection cases.
- **OPEN - 03.4:** Build the isolated runner with simulated approvals and injected failures.
- **OPEN - 03.5:** Score step/tool accuracy, wasted steps, success, recovery and safety.
- **OPEN - 03.6:** Measure tokens/cost, p50/p95 time and projected cost at 100x.
- **OPEN - 03.7:** Test the evaluator and scenario isolation.
- **OPEN - 03.8:** Execute and save real traces/results.

**Finished when:** 30+ reproducible scenarios run with documented metrics.
Keep fake-model tests and live-model results separate.

## SEC-04 - Security, report and demo: PARTIAL

- **OPEN - 04.1:** Complete the threat model.
- **OPEN - 04.2:** Implement AI injection tests with EVAL-03.
- **OPEN - 04.3:** Complete error taxonomy: behavior and a test per class.
- **OPEN - 04.4:** Review approval, sandbox isolation, logs and secret handling.
- **PARTIAL - 04.5:** Setup/manual commands exist; add agent/evaluation commands, results and contributions.
- **OPEN - 04.6:** Write the report and export report.pdf, maximum four pages.
- **OPEN - 04.7:** Write the postmortem from real problems and lessons.
- **OPEN - 04.8:** Verify a clean clone and rehearse the demo.

Drafts are in docs/deliverables. Final submission needs root-level errors.md,
threat_model.md, postmortem.md and report.pdf.
Confirm instructor approval, team owners and the official deadline.

After finishing a task, update its status here and its affected files in
[COMPLETED_WORK.md](COMPLETED_WORK.md).
