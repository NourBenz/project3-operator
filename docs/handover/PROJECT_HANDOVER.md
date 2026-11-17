# CS496 GitHub Issue-Solving Agent — Project Handover

## 1. Purpose and target

This repository began as a safe GitHub issue triage agent. The current code can read an issue, classify it, propose labels or a comment, require exact human approval, execute an approved write, and read the issue back. The new Level 2 objective is broader:

1. Read a real issue from the team's private GitHub repository.
2. inspect the repository source in an isolated workspace;
3. diagnose the likely cause and propose a solution;
4. edit files and run relevant tests locally;
5. show the patch and test evidence to a human;
6. after explicit approval, create a non-default branch, commit, push, open a draft pull request, and verify it.

The agent must never push directly to the default branch. Important repository mutations must be authorized by a human using an approval bound to the exact proposed action.

This document describes the current working tree, not only committed `main`. The newest backend-interface work is present but uncommitted.

## 2. Three names that must not be confused

### 2.1 Private GitHub repository named `MockGitHub`

This is a real, private repository owned by the team. It contains real code and issues and is intended to become the main integration-test target. It was not accessed during this audit. Nothing in this handover changes its visibility or contents.

### 2.2 Python class `sandbox.mock_github.MockGitHub`

This is a local SQLite-backed simulation. It does not contact GitHub. It supports deterministic development, failure injection, concurrency testing, and offline evaluation. Preserve it as the regression backend.

### 2.3 `tools.github_service.GitHubService`

This is the adapter for actual GitHub repositories. It uses the GitHub REST API for issues, labels, and comments. It is the backend intended eventually to target the private repository named `MockGitHub`.

Throughout this handover, **private repository** means the real repository, **local MockGitHub** means the Python/SQLite simulation, and **GitHubService** means the real API adapter.

## 3. Verified repository state

Audit date: 2026-11-17, working directory `/Users/ksilamahdi/Desktop/project3-operator`.

- Branch: `main`.
- `main` equals `origin/main` at commit `7306ff1` (`Mark tools task complete`).
- Modified, unstaged: `agent/loop.py`.
- Untracked: `agent/backend.py`.
- Untracked: `tests/test_backend_contract.py`.
- Nothing is staged or committed from the latest Task 1 work.
- Full isolated test suite: **210 passed in 1.82s** during this audit.
- Focused backend/agent suite previously run in the same working tree: **30 passed**.
- `git diff --check` previously passed.
- No real GitHub request was made and `.env` was not read.

The latest Task 1 introduced a runtime-checkable `IssueService` protocol and annotated `TriageAgent` against it. Review and commit these three changes before beginning the next implementation task.

## 4. High-level architecture today

The implemented system is still an issue-triage agent, not a code-solving agent.

```text
User
  -> agent/main.py CLI
  -> model (FakeModel or OpenAIModel)
  -> TriageAgent controller loop
  -> ToolRegistry validation and scope checks
  -> injected IssueService
       -> local MockGitHub + SQLite, or theoretically GitHubService
  -> normalized tool result
  -> controller-owned read-back verification
  -> final AgentResult
```

In practice, `agent/main.py` always constructs the local MockGitHub. `TriageAgent` can accept a GitHubService through dependency injection, but the AI CLI has no backend-selection option yet. `tools/review.py` is the only CLI that currently selects local or GitHub mode.

## 5. Agent files and responsibilities

### `agent/main.py`

The local AI CLI. It parses `--issue`, `--offline`, `--model`, `--db`, and `--yes`; builds a model; always constructs `MockGitHub(args.db)`; presents exact proposals; obtains terminal approval; runs `TriageAgent`; and prints the result. It does not construct GitHubService.

### `agent/loop.py`

The controller-owned decide/tool/observe loop. It:

- generates a task ID;
- parses task scope;
- starts a `StopController`;
- supplies tool schemas and the current transcript to the model;
- permits only one tool call per decision;
- validates tool arguments;
- enforces issue scope and read-before-write requirements;
- creates proposals for write tools;
- obtains a human response through an injected handler;
- dispatches through the injected service;
- performs a separate `read_issue` after successful writes;
- refuses unsupported final answers;
- returns `DONE`, `REJECTED`, `CANCELLED`, `AWAITING_APPROVAL`, or safe failure/limit statuses.

The current uncommitted edit changes its service annotation from an untyped object to `IssueService | None`. Its default remains the local MockGitHub.

### `agent/backend.py` — untracked Task 1 file

Defines the runtime-checkable `IssueService` protocol. Required attributes and methods are:

- `approvals: ApprovalManager`
- `scope: str`
- `propose()`
- `call()`
- `list_issues()`
- `read_issue()`
- `get_labels()`
- `update_labels()`
- `post_comment()`

The protocol deliberately promises a common surface and normalized mapping result, not identical storage, concurrency, verification, or recovery behavior.

### `agent/llm.py`

Defines model-neutral decision objects and two adapters:

- `FakeModel`: deterministic keyword classifier used by tests and offline evaluation. It follows a fixed read -> taxonomy -> label update -> verification sequence. It does not reason about source code.
- `OpenAIModel`: loads configuration, calls OpenAI Chat Completions with system/user/transcript messages and function tools, disables parallel calls, parses JSON arguments, and hides provider exception details.

The default configured model name is `gpt-4o-mini`. The adapter has mocked tests but no current live test in the automated suite.

### `agent/prompts.py`

Contains the trusted triage policy and label rubric. It treats issue content and tool output as untrusted data, prohibits undeclared tools and secret access, requires read-before-write and human approval, and asks for one tool call at a time. The wording is still local-triage-specific and has no code-solving policy.

### `agent/registry.py`

Maps five names to strict Pydantic argument schemas and generates OpenAI function definitions. Trusted values such as grants, task context, and backend paths are never model parameters. Its descriptions still say “local.” `list_issues` is registered but the controller scope guard rejects it.

### `agent/approval.py`

Owns proposals and grants. A registered proposal is bound to:

- proposal ID;
- task ID;
- exact tool and arguments through a SHA-256 fingerprint;
- backend scope (database path or repository URL);
- expiration time, five minutes by default.

Only literal `yes` produces a private random `ApprovalGrant`; `cancel` cancels; all other text rejects. The service verifies type, token hash, fingerprint, scope, status, and expiration. Grants are consumed after writes. Audit failure fails closed. Approval state is in memory and does not survive a process restart.

### `agent/schemas.py`

Contains strict, frozen, extra-forbidden Pydantic models for tool inputs, discriminated tool requests, normalized results, proposals, approval decisions, run state, and tool events. Allowed labels are hard-coded to four types and four priorities. One inconsistency: `TriageAgent` returns status `REJECTED`, but `RunStatus` does not include `REJECTED`.

### `agent/stopping.py`

Implements controller-owned bounds:

- 10 decisions by default;
- 2 retries per tool;
- maximum 2 consecutive identical calls;
- 120-second wall-time boundary by default;
- optional cost limit;
- terminal cancellation.

Checks occur at controller boundaries; this is not a mechanism for forcibly killing an in-progress external call. The loop's `_retry()` performs one immediate retry; it does not independently loop through the full retry allowance.

### `agent/logging_utils.py`

Provides call context/correlation IDs, JSONL logging, timing, error normalization, and redaction. It redacts secret-looking keys, OpenAI keys, bearer tokens, and grants. It currently checks the actual value of `OPENAI_API_KEY` when redacting but not every possible configured secret value. GitHub and SMTP secrets remain protected by key-name redaction when they are structured fields, but general string-value redaction is narrower.

### `agent/task.py`

Extracts one positive issue number and determines whether the request is read-only or permits triage writes. Ambiguous requests produce clarification without a tool call. This is intentionally narrow and will need a richer task model for issue solving.

## 6. Backends and tools

### 6.1 Local SQLite backend

`sandbox/mock_github.py` implements the local MockGitHub service. It validates proposals, dispatches five tools, verifies approval, obtains a database write lock, checks the operation ledger, applies a mutation, stores the result atomically, consumes approval, and exposes hooks for failure tests.

`sandbox/database.py` defines schema version 2 with issues, labels, issue-label links, comments, and operations. It supports read-only connections, constraints, safe legacy migration, foreign keys, and rollback. `sandbox/seed.py` validates and seeds 50 fixtures. `sandbox/reset.py` is destructive and was not run during this audit. `sandbox/taxonomy.py` defines the eight labels and legacy aliases.

Local write protections include closed-issue checks, expected-version checks, type/priority conflict checks against existing labels, label allowlisting, atomic version increments, operation-ID replay, transactions, and concurrency handling.

### 6.2 GitHubService

`tools/github_service.py` contains:

- `GitHubClient`, using `urllib.request` with bearer authentication and a 20-second request timeout;
- HTTP/network error mapping;
- GitHubService with the same controller-facing method names;
- a local SQLite operation ledger;
- version numbers derived from a hash of GitHub `updated_at`;
- real issue/label/comment reads;
- approved label and comment writes;
- owner email notification after changed writes.

GitHubService is usable directly and through `tools/review.py --github`. It is not selected by `agent/main.py`.

### 6.3 Tool modules

- `tools/read_issue.py`: direct read-only SQLite query; returns issue, labels, comments.
- `tools/list_issues.py`: direct read-only SQLite listing with state and limit validation.
- `tools/get_labels.py`: direct read-only SQLite taxonomy query.
- `tools/update_labels.py`: wrapper that accepts an injected service but defaults to a new local MockGitHub.
- `tools/post_comment.py`: same pattern for comments.
- `tools/common.py`: normalized failure envelope and safe Pydantic error rendering.
- `tools/review.py`: non-AI human-review CLI; selects local or GitHub backend, proposes, prompts, writes, reads back, and checks the effect.
- `tools/notifications.py`: SMTP/TLS owner notification using environment configuration.
- `tools/seed_github.py`: dry-run by default; with `--apply`, creates missing labels/issues/comments and closes fixtures. It uses hidden fixture markers to avoid duplicates. This is an administrative utility, not an agent tool.

## 7. Backend comparison

| Feature | Local SQLite MockGitHub | GitHubService |
|---|---|---|
| Issue reading | Read-only SQLite query | GitHub issue and comments API |
| Issue listing | Stable local query, state/limit | One GitHub page; filters pull requests |
| Label reading | Only type/priority taxonomy | All repository labels, unknown labels categorized `other` |
| Label updates | Add-only, local allowlist, checks existing conflicts | Add-only merge; schema checks proposal but not conflict with existing GitHub labels |
| Comments | Stores local ID, operation ID, timestamp | Posts GitHub comment; write result has GitHub ID, read-back maps operation ID to `None` |
| Approval | ApprovalManager bound to database-path scope | Same manager bound to repository-URL scope |
| Idempotency | Operation ledger in same transaction as mutation | Local ledger separate from remote mutation; uncertain window remains |
| Concurrency | Database lock, optimistic version, tested workers | GitHub version check plus local ledger; limited concurrency coverage |
| Verification | Agent and review CLI can verify labels/comments | Labels can usually verify; comment verification currently fails by operation ID |
| Error handling | Common logger normalizes validation/SQLite/timeout errors | Explicit GitHub error mapping plus logger; narrower test coverage |
| Notifications | None | SMTP notification after changed successful write |
| Atomicity | Mutation and ledger are atomic locally | Remote mutation and local ledger cannot be atomic |
| Current AI CLI use | Always used | Not selectable in `agent/main.py` |

## 8. Complete execution flow

1. `agent/main.py` builds FakeModel or OpenAIModel and a local MockGitHub.
2. `TriageAgent.run()` parses the request and starts stop controls.
3. The model receives the system policy, user request, controller transcript, and tool schemas.
4. The controller accepts at most one requested tool.
5. ToolRegistry validates strict arguments; the controller checks issue scope.
6. Reads are sent to `service.call()`.
7. Before writes, the controller requires a successful issue read and taxonomy read for the same issue.
8. The service registers an exact `WriteProposal`.
9. The proposal presenter shows issue context, rationale, and exact arguments.
10. A human response is converted to approve/reject/cancel. The model cannot create a grant.
11. The service authorizes the exact proposal and executes the mutation.
12. The controller independently calls `read_issue` using a new bounded step.
13. `_writes_verified()` checks label inclusion or local comment operation ID.
14. A model final answer is accepted only if supported by the transcript.

## 9. Current feature status

| Feature | Status | Evidence and limitation |
|---|---|---|
| OpenAI integration | PARTIALLY IMPLEMENTED | Adapter and mocked parsing test exist; no current live automated evaluation |
| Fake/offline model | COMPLETE for triage | Deterministic keyword classifier; not source-code reasoning |
| Manual tool loop | COMPLETE for five triage tools | Bounded controller with validation and transcript |
| Issue reading | COMPLETE locally; PARTIALLY IMPLEMENTED live | Both services read issues; live pagination/coverage is limited |
| Issue classification | COMPLETE only for basic triage | Keyword fake or LLM prompt; no root-cause analysis |
| Label updates | COMPLETE locally; PARTIAL live | GitHub existing-label conflict parity is missing |
| Comment creation | COMPLETE locally; PARTIAL live | GitHub posting exists; read-back verification is incompatible |
| Human approval | COMPLETE for label/comment proposals | In-memory exact grant system |
| Approval security | COMPLETE for current scope | Strong tests; new code/branch/PR actions need new proposal schemas |
| Read-back verification | COMPLETE locally; PARTIAL live | GitHub comments fail current identity check |
| Stopping controls | COMPLETE for current loop | Boundary checks; no subprocess/network hard-kill orchestration |
| Prompt-injection protection | PARTIALLY IMPLEMENTED | Strong tool/scope controls and local fixtures; no source-file injection policy/evaluation |
| Local SQLite backend | COMPLETE | Extensive unit, migration, concurrency, rollback tests |
| GitHub API backend | PARTIALLY IMPLEMENTED | Issue/label/comment adapter exists; not end-to-end through AI CLI |
| Private repository connectivity | NEEDS VERIFICATION | Historical docs claim manual use; not accessed during audit |
| Shared backend interface | COMPLETE in working tree, UNCOMMITTED | Protocol and conformance tests pass |
| Backend selection in AI CLI | NOT IMPLEMENTED | `agent/main.py` always constructs local MockGitHub |
| Repository cloning | NOT IMPLEMENTED | No clone/worktree service or schema |
| Source-code investigation | NOT IMPLEMENTED | No repository tools in registry |
| File modification | NOT IMPLEMENTED | No isolated editor/patch tool |
| Agent-driven test execution | NOT IMPLEMENTED | Tests exist for project, but agent has no test runner tool |
| Branch creation | NOT IMPLEMENTED | No git workflow component |
| Commit | NOT IMPLEMENTED | No commit tool or approval contract |
| Push | NOT IMPLEMENTED | No protected-branch enforcement or push tool |
| Draft PR creation | NOT IMPLEMENTED | No pull-request API methods or schemas |
| PR verification | NOT IMPLEMENTED | No read-back of head/base/draft/commit/files/checks |
| Offline evaluation framework | PARTIALLY IMPLEMENTED | Runner/metrics exist; current expected statuses are stale |
| Automated tests | COMPLETE for current scope | 210 passing; live GitHub and Level 2 behaviors absent |
| Email notifications | PARTIALLY IMPLEMENTED | Code connected to GitHub writes; SMTP/private-repository delivery not verified here |

## 10. Testing and evaluation

### Automated tests

The suite uses temporary databases and fake clients. During this audit, `./.venv/bin/python -m pytest -q` produced **210 passed in 1.82s**.

- `test_schemas.py`: strict types, allowlists, proposal hashes, result consistency.
- `test_stopping.py`: step/retry/repetition/deadline/cost/cancel limits.
- `test_sandbox.py`: all fixtures, queries, migrations, constraints, rollback, read-only behavior.
- `test_tools.py`: local reads/writes, conflicts, logs, redaction, partial failures, manual review.
- `test_approval.py`: missing/forged/mismatched/expired approvals, scope, cancellation, audit failure.
- `test_idempotency.py`: exact retries, ambiguous failures, restart, concurrency, stale versions.
- `test_agent.py`: loop trajectories, pending/reject/cancel, scope, no-op, invalid calls, stopping.
- `test_injection.py`: five malicious fixtures and cross-issue redirection.
- `test_openai_adapter.py`: missing key and mocked tool-call parsing.
- `test_github_service.py`: fake-client reads, approved label update/notification, exact retry.
- `test_backend_contract.py` (untracked): both backends satisfy IssueService and normalized read/approval-required contracts.

### Offline evaluation

`eval/runner.py` creates a temporary database per scenario and uses FakeModel. `eval/metrics.py` calculates exact trajectory accuracy, positional tool-call precision, task success, recovery, injection resistance, average steps, tokens/cost estimate, and latency percentiles.

`eval/results/latest.json` is historical and reports 30/30 success, 0.9 step accuracy, and 1.0 injection resistance. It is stale. A fresh audit run, without writing the result file, reported:

- 30 scenarios;
- task success rate 0.7667 (23/30);
- step accuracy 0.9;
- tool-call precision 1.0;
- recovery 1.0;
- injection score 0.0;
- seven failures: reject, cancel, and five injection cases.

The seven failures are evaluator expectation problems: the controller now correctly returns `REJECTED` or `CANCELLED`, while scenarios default to expected status `DONE`. The injection actions remained constrained to allowed tools and were rejected. Fix scenario expected statuses and define safety success separately from task completion before trusting the metrics.

## 11. Known issues and risk register

| Issue | Location | Severity | Why it matters | Recommended fix | Gate |
|---|---|---:|---|---|---|
| GitHub comments lose operation ID on read | `GitHubService._detail`, loop/review verification | High | Successful comment can be reported unverified | Verify stable GitHub comment ID and body; share verifier | Before real comment writes |
| GitHub existing-label conflicts unchecked | `GitHubService._write` | High | Can create both `bug` and `feature`, or conflicting priorities | Apply taxonomy conflict policy to existing + proposed labels | Before real label writes |
| GitHub exposes extra labels | `GitHubService.get_labels` vs schema | Medium | Model sees labels it cannot submit; policy ambiguity | Return explicit allowed taxonomy separately from repository metadata | Before live agent integration |
| Remote write/ledger non-atomic | `GitHubService.call` | Critical for later git writes | Crash can leave remote mutation without ledger proof | Use remote idempotency marker/reconciliation and explicit uncertain outcome | Before autonomous repository writes |
| Email can fail after mutation | `GitHubService.call` | High | `PARTIAL_FAILURE` may trigger unsafe mutation retry | Model mutation and notification as separate states; never repeat mutation to resend email | Before real writes |
| GitHub concurrency/recovery thin | GitHubService tests | High | Duplicate or stale remote effects under races/timeouts | Add failure-point, concurrency, restart, and reconciliation tests | Before autonomous writes |
| `list_issues` blocked | `TriageAgent._call_is_in_scope` | Medium | Registered tool is unusable by the agent | Add explicit listing scope or remove it from model tools | Before broad issue selection |
| Standalone reads are SQLite-only | three read modules | Medium | Backend choice can be bypassed outside the agent | Route public wrappers through injected IssueService or label them local-only | Before backend-selection release |
| AI CLI has no backend selection | `agent/main.py` | High | Cannot run end-to-end against GitHubService | Add explicit backend factory/flags, fail closed, retain local default/test mode | Next task |
| No code investigation/modification | registry/schemas absent | Critical to Level 2 | Target capability does not exist | Build isolated repository/workspace services incrementally | Before Level 2 demo |
| GitHub `propose` treats unknown tool as comment schema | `GitHubService.propose` | Medium | Direct misuse gives misleading validation | Explicitly reject non-write/unknown tool names | Before expanding tools |
| GitHub saved-result branch does not consume new proposal | `GitHubService.call` | Medium | Leaves approval record approved after replay return | Consume or explicitly close replay proposal after authorization | Before production retry use |
| `REJECTED` missing from RunStatus | schemas vs loop | Medium | State model and runtime result disagree | Add status or standardize terminal semantics with tests | Before workflow expansion |
| Logger string redaction is OpenAI-centric | `logging_utils.redact` | High for Level 2 | GitHub/SMTP secrets embedded in free text may escape | Generalize known-secret registration and forbid secret-bearing tool output | Before source/git tools |
| Tool descriptions and prompt say local | prompt/registry | Medium | Wrong model context in GitHub mode | Make policy backend-neutral and inject trusted target metadata | With backend selection |
| GitHub pagination incomplete | issue/label/comment reads | Medium | Can omit data and misdiagnose/verify incorrectly | Implement bounded pagination and test it | Before relying on complete live context |
| Version uses `updated_at` hash | GitHubService | Medium | Any unrelated issue update invalidates proposal; timestamp resolution assumptions | Document optimistic concurrency contract and reconcile after 409/stale state | Before live writes |
| Cost stop not wired to model usage | loop/stopping | Medium | `max_cost` exists but run never records model cost | Convert token usage to trusted cost and call `record_cost` | Before costly autonomous loop |
| Approval state is memory-only | ApprovalManager | Medium | Awaiting approval cannot safely resume after restart | Add durable, signed/stateful workflow only when async approval is required | Before long-running Level 2 flow |
| Evaluation safety metric is stale/weak | eval runner/scenarios | High academically | Current 0.0 injection score misrepresents safe rejections | Separate outcome correctness, side effects, policy compliance, and trajectory | Before final evaluation |
| Documentation contradicts new objective | old TASKS/PROJECT_CONTEXT | Medium | Next developer may implement wrong “no PR” scope | Treat this handover as current direction; reconcile old docs after team approval | Before final report |

## 12. Configuration and dependencies

`requirements.txt` pins OpenAI, Pydantic, python-dotenv, pytest, and Streamlit. There is no `pyproject.toml`. Streamlit is installed but no Streamlit application exists. No static type checker is configured. The old docs say LangGraph/LangSmith were mandatory, but neither is installed or implemented; the current instruction is to add a framework only for a concrete reason.

`.env.example` names OpenAI, GitHub, owner-email, and SMTP variables. `.env` is ignored. The example contains team email addresses but blank secrets. The actual `.env` was not read.

`.gitignore` excludes virtual environments, `.env*` except the example, pytest cache, all sandbox data except its README, logs, runtime files, and docs/archive.

## 13. Academic explanation

The loop is manual so the team can show exactly where trust decisions occur. The LLM proposes; the controller validates and authorizes. This separation matters because an LLM is probabilistic and consumes untrusted issue/repository text. Human approval protects consequential writes. The SQLite sandbox enables deterministic and destructive failure tests without risking the private repository. Idempotency prevents retries from duplicating effects. Read-back verification proves the actual state rather than trusting an API response or model claim. Prompt injection is mitigated through trusted/untrusted message separation, a small allowlisted tool surface, strict schemas, scope checks, and controller-owned approval.

The Level 2 agent is more advanced than a chatbot because it must gather evidence from code, change a controlled workspace, execute tests, produce a reviewable patch, and coordinate several irreversible external actions while maintaining provenance and human control.

## 14. Current stopping point and next action

Task 1—formalize the backend interface—is implemented and tested but uncommitted. The exact next task is:

> Review and commit `agent/backend.py`, the `agent/loop.py` type annotation, and `tests/test_backend_contract.py`; then implement explicit backend selection in `agent/main.py` using an isolated factory and fake-client CLI tests, without contacting the private repository.

Do not begin repository cloning or code-solving tools until backend selection, GitHub verification parity, and remote-write uncertainty are addressed.
