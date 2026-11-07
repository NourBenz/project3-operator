# GitHub Issue Triage Agent - project context

**Tasks: [TASKS.md](TASKS.md). Completed tasks and affected files: [COMPLETED_WORK.md](COMPLETED_WORK.md). Commands: [README.md](../README.md).**

## Our chosen project

For CS496 Project 3: The Operator, we are building an AI agent that organizes
GitHub issues. It reads a bug report, feature request or question, proposes labels
or a comment, waits for explicit human approval, applies the local change,
checks the result and stops.

Example: triage issue 12 -> read issue -> check labels -> propose bug/high ->
human approves -> save -> read back -> stop.

Stack: Python 3.11+, one configurable OpenAI model, SQLite, Pydantic,
python-dotenv, pytest and Streamlit after the CLI. GitHub is a local mock;
no live GitHub writes or cloud deployment. The supplied
OPERATOR_A_TO_Z_CODEX_MASTER_PLAN.md is the design reference; explicit user
decisions override it. We track four tasks, without additional phase terminology.

## What the PDF requires for this idea

The [professor brief](reference/project3-operator.pdf) requires:

- A model in a plan/tool/observe loop; at least three tools including a real write.
- Strict inputs, useful errors and logging of every call's input/output/time.
- Resettable sandbox, human approval before writes and idempotent writes.
- A stopping rule defined first and tested, plus retries/partial failure handling.
- Error taxonomy with behavior and a test per class.
- At least 30 expected-trajectory scenarios, including failures and five injection cases.
- Metrics: step accuracy, tool/argument precision, wasted steps, task success,
  recovery, injection/stop compliance, steps/tokens/cost, p50/p95 time and 100x cost.
- Clean-clone reproduction, README setup/run/results/contributions, errors.md,
  threat_model.md, genuine postmortem.md and report.pdf (maximum four pages).
  The report covers tools/write effects, loop/stop proof, sandbox/approval,
  evaluation, recovery, injection defenses and cost.

Instructor approval of our labels/comments scope is still pending; the PDF's
GitHub example also mentions a draft PR. Approval is due by end Week 11.
Freeze before the first Week 13 session; demo then, including an injection attempt.
Confirm the calendar date and teammate names. No credentials in Git.

## What works now

- **TOOLS-02 complete:** all five tools, strict inputs, human-reviewed writes,
  version checks, conflict/closed-issue policy, persistent retry-safe outcomes,
  errors, redacted call logs, seed/reset and automated tests.
- **Database:** 50 issues: 30 original public Requests reports, 15 synthetic edge
  cases and five injection fixtures. Eight labels, four synthetic baseline
  comments; operation ledger starts empty. Public URLs/dates/content hashes are
  in sandbox/dataset_manifest.json. Redistribution permission is not established
  merely by attribution; report claims are not verified ground truth.
- **Shared agent contracts:** typed calls/results, exact proposals, explicit
  decisions, serializable pending run state and event formats.
- **Stopping controller:** 10 decisions, two retries per tool, two consecutive
  identical calls, 120 seconds and optional reported-cost ceiling; tests pass.
  The future AI loop must integrate these guards and external-call timeouts.
- **Human tool CLI:** python -m tools.review labels/comment displays the exact
  proposal, accepts yes/no/cancel, executes only an approved change and reads back.
- **Verified:** 172 automated tests passed on Python 3.13.1, including real CLI
  subprocess tests, concurrency, rollback, timeout after commit, expiry/mismatch,
  logging failures and original database/contract/stopping coverage.
  All write tests used isolated databases; the active dataset remains baseline.

## Where each teammate works

- AGENT-01: agent/ - OpenAI adapter, registry, bounded loop, CLI and trajectory
  integration. Shared contracts, stopping, approval and tool logging are available;
  the actual AI controller/model integration is still missing.
- TOOLS-02: sandbox/ and tools/ - complete; use the service API below.
- EVAL-03: eval/ - scenario files are empty; runner, metrics and measured results
  still need implementation.
- SEC-04: security/error tests and docs/deliverables/ - final threat model,
  full taxonomy, report, postmortem and demo still need completion. Existing
  tool-boundary security tests do not replace AI injection/trajectory evaluation.

## Tool/service contract for integration

- Keep one sandbox.mock_github.MockGitHub instance per selected sandbox/task session.
  It owns the database path, ApprovalManager and ToolLogger.
- service.call(tool, arguments, task_id=...) is the allowlisted dispatcher.
  The model gets only tool arguments. It cannot supply database paths, approval
  flags/tokens, shell commands or trusted controller context.
- Results are {ok, data, error}; errors have code, message and retryable.
  Canonical read errors are INVALID_ARGUMENTS, ISSUE_NOT_FOUND, TOOL_UNAVAILABLE.
- For a write, service.propose(tool, arguments, task_id=...) registers an exact
  pending proposal. Suspend the AI task, show it to the human and call
  service.approvals.prompt(proposal_id), or decide(proposal_id, human_response)
  only after a real UI action. Pass the opaque returned grant and matching
  proposal/task IDs to service.call. An ApprovalDecision object is not permission.
- Grants bind to canonical task/proposal/action/arguments/database, expire after
  300 seconds and authorize one mutation. Approval is enforced inside the service.
  Retain the manager across UI reruns; after process restart, obtain fresh approval.
  service.approvals.cancel_task(task_id) blocks that task's later writes.
- update_labels adds allowed labels only. Values: bug, feature, documentation,
  question (category type); low, medium, high, critical (category priority).
  Preserve unrelated labels. Existing/proposed classification conflicts require
  review; no silent replacement. Closed issues reject both write actions.
- post_comment requires a nonblank body of at most 1000 characters.
  Both writes require expected_version and operation_id. A changed proposal/version
  requires fresh approval; a true no-op label addition does not increase the version.
- SQLite commits the effect and saved outcome together. Retry exactly the same
  request/task/operation ID after timeout; committed replay happens before version
  checks and returns the original saved result. Read again for current state.
  Different request/same operation ID fails. A valid original grant can retrieve
  its committed outcome after expiry, but cannot create new changes.
- Logging is in sandbox/logs/tool_calls.jsonl (ignored by Git), with start/result
  records, UTC timestamp, duration, inputs/results/errors, task/step/correlation IDs,
  operation ID and approval outcome. Human decisions are audited; secrets redacted.
  No start log means no write. Final logging failure reports possible partial
  completion; use the same operation ID for recovery.

Before each model attempt, call StopController.begin_decision(); invalid decisions
and retries consume budget. before_tool_call() permits one attempt per decision.
Use checkpoint()/remaining_seconds() around external work. The controller does
not itself interrupt a blocked API/tool call.

## Start working

Run the [README](../README.md) commands, then choose a ticket in [TASKS.md](TASKS.md).
Next work is AGENT-01 against the completed tool service; evaluation/security work
can proceed alongside it. OpenAI model ID, named owners and calendar deadline
remain to be confirmed. No live model results have been measured.

After work, update TASKS.md status, COMPLETED_WORK.md completed tasks/affected files and current context.
No changelog, Markdown tables or triple-quoted Python docstrings.
The [data README](../sandbox/data/README.md) lists the 50 titles without per-issue details.
