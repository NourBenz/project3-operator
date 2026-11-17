# Implementation Roadmap — Level 2 GitHub Issue-Solving Agent

## Roadmap principles

- Preserve the local SQLite MockGitHub class and its tests.
- Treat the private repository named `MockGitHub` as a real protected target, never as a disposable sandbox.
- Default to no external mutation. Require explicit target selection and approval.
- Separate local reversible work from remote consequential work.
- Add one capability at a time with fake-client and temporary-workspace tests.
- Never use the LLM's statement as evidence that a tool, test, push, or PR succeeded.
- Do not push to the default branch under any condition.
- A framework is optional unless it solves a concrete need such as durable pause/resume or explicit state transitions.

## Phase 1 — Backend contract and compatibility

### Task 1.1: Review and commit the shared IssueService contract

- **Objective:** Finish the currently uncommitted protocol change.
- **Files:** `agent/backend.py`, `agent/loop.py`, `tests/test_backend_contract.py`.
- **Dependencies:** None.
- **Acceptance:** Both backends satisfy the runtime protocol; TriageAgent is typed against it; no behavior changes.
- **Tests:** Current contract tests plus full suite; expected result at handover is 210 passing.
- **Risks:** A protocol proves surface compatibility, not semantic equivalence.

### Task 1.2: Define backend conformance semantics

- **Objective:** Document and test the common result envelope and required issue/label/comment fields.
- **Files:** likely `agent/backend.py`, `agent/schemas.py`, `tests/test_backend_contract.py`.
- **Dependencies:** Task 1.1.
- **Acceptance:** Parameterized tests cover success and common errors for reads, proposals, rejection, cancellation, stale versions, and closed issues.
- **Tests:** Local temporary DB and fake GitHub client only.
- **Risks:** Over-generalizing can hide legitimate backend differences. Keep remote consistency behavior explicit.

### Task 1.3: Repair GitHub triage parity

- **Objective:** Fix real comment verification, existing-label conflict checks, unknown proposal tools, and replay proposal consumption.
- **Files:** `tools/github_service.py`, `agent/loop.py` or a new shared verifier, `tools/review.py`, GitHub/contract tests.
- **Dependencies:** Tasks 1.1–1.2.
- **Acceptance:** GitHub comments verify by stable GitHub ID and approved body; label conflicts match local policy; unknown writes fail explicitly; replay closes approval state.
- **Tests:** Duplicate-body comments, missing read-back ID, conflicting existing labels, replay, notification failure.
- **Risks:** Verification retries must never repost the mutation.

## Phase 2 — Backend selection and private GitHub connectivity

### Task 2.1: Introduce an explicit backend factory

- **Objective:** Select local or GitHub service once at the composition root.
- **Files:** `agent/main.py`, likely new `agent/service_factory.py`, CLI tests.
- **Dependencies:** Phase 1.
- **Acceptance:** Local mode keeps existing behavior; GitHub mode requires an explicit repository URL/configuration; invalid combinations fail closed; no secret values printed.
- **Tests:** Monkeypatched constructors/fake client; no network.
- **Risks:** Accidental live mode. Require an explicit flag, display target owner/repository, and do not infer live mode merely from a token.

### Task 2.2: Make policy and tool descriptions backend-neutral

- **Objective:** Stop telling the model that all data is local while preserving trusted target context.
- **Files:** `agent/prompts.py`, `agent/registry.py`, prompt/adapter tests.
- **Dependencies:** Task 2.1.
- **Acceptance:** Policy describes an injected trusted backend and untrusted issue content; target identity is controller-provided, not issue-provided.
- **Tests:** Snapshot/schema tests and injection cases.
- **Risks:** Repository text must never be promoted to trusted policy.

### Task 2.3: Read-only private-repository smoke test

- **Objective:** Verify authentication and issue reads against the team’s private repository without writing.
- **Files:** operational runbook; possibly opt-in integration test marker.
- **Dependencies:** Tasks 2.1–2.2 and user authorization/credentials.
- **Acceptance:** Explicit opt-in reads a known issue and labels; logs contain no token; no mutation endpoint called.
- **Tests:** Default suite skips live test; fake tests remain mandatory.
- **Risks:** Privacy and credential leakage. Never make the repository public and never record private issue bodies in public artifacts.

### Task 2.4: Controlled live triage-write verification

- **Objective:** After parity fixes, verify one approved label/comment operation on a designated test issue.
- **Files:** runbook and possibly opt-in integration test.
- **Dependencies:** Phase 1 parity, read-only smoke test, explicit human authorization.
- **Acceptance:** Exact proposal shown, human approves, change verified, notification behavior recorded, no default-branch code change.
- **Tests:** Fake coverage first; live action manual and narrowly scoped.
- **Risks:** Real mutation and email side effects.

## Phase 3 — Repository access and isolated workspaces

### Task 3.1: Define repository/workspace contracts

- **Objective:** Separate issue API service from local source-workspace operations.
- **Files:** new protocols/schemas such as `agent/workspace.py`, tests.
- **Dependencies:** Phase 2 read-only connectivity.
- **Acceptance:** Contract covers target repository identity, base revision, workspace path handle, cleanup policy, and read-only status without exposing arbitrary host paths to the LLM.
- **Tests:** Temporary local repositories only.
- **Risks:** Do not overload IssueService with filesystem/git responsibilities.

### Task 3.2: Create isolated checkout/worktree service

- **Objective:** Materialize a repository at an exact base commit in a task-specific temporary directory.
- **Files:** new workspace implementation and tests.
- **Dependencies:** Task 3.1.
- **Acceptance:** Unique workspace per task; validated repository origin; detached or protected base; no writes outside workspace; cleanup/recovery defined.
- **Tests:** Temporary bare/local Git repositories, path traversal, symlink escape, concurrent tasks.
- **Risks:** Credential exposure, unsafe paths, workspace reuse, malicious repository hooks. Disable hooks and avoid executing repository configuration blindly.

### Task 3.3: Default-branch protection policy

- **Objective:** Centralize branch rules before any git mutation exists.
- **Files:** git policy module and tests.
- **Dependencies:** Task 3.2.
- **Acceptance:** Default branch is discovered and immutable; generated branches use a controlled prefix; force push and arbitrary refspecs are impossible.
- **Tests:** main/master/custom default branches, detached heads, malicious branch names.
- **Risks:** Policy duplicated across commands can drift; keep one trusted implementation.

## Phase 4 — Source-code investigation tools

### Task 4.1: Add bounded repository inventory

- **Objective:** Let the agent list relevant files without arbitrary filesystem access.
- **Files:** new schemas, registry entries, workspace tools, prompt, tests.
- **Dependencies:** Phase 3.
- **Acceptance:** Lists only workspace-relative files, respects ignore/size/count limits, excludes `.git` and secrets.
- **Tests:** traversal, symlinks, large trees, hidden secrets, binary files.
- **Risks:** Context explosion and secret ingestion.

### Task 4.2: Add bounded text search

- **Objective:** Search source code for issue-relevant symbols and messages.
- **Files:** search tool/schema/tests.
- **Dependencies:** Task 4.1.
- **Acceptance:** Literal/controlled regex search, bounded matches and bytes, relative paths only, structured results.
- **Tests:** regex denial-of-service limits, binary files, malicious filenames, injection text.
- **Risks:** Search results are untrusted data, not instructions.

### Task 4.3: Add bounded file reading

- **Objective:** Read selected source ranges with provenance.
- **Files:** read-source tool/schema/tests.
- **Dependencies:** Task 4.1.
- **Acceptance:** Workspace confinement, size/range limits, line numbers, encoding handling, secret-file denylist.
- **Tests:** path traversal, symlink escape, huge files, invalid encodings.
- **Risks:** Source files can contain prompt injection and credentials.

## Phase 5 — Issue analysis and solution planning

### Task 5.1: Extend task and state models

- **Objective:** Represent issue, repository, base commit, investigation evidence, diagnosis, plan, and uncertainty.
- **Files:** `agent/schemas.py`, `agent/task.py`, loop or new orchestration module, tests.
- **Dependencies:** Phase 4.
- **Acceptance:** Every claim cites retrieved issue/source evidence; task remains bound to one repository and issue.
- **Tests:** missing issue, conflicting evidence, injection attempts to switch repository/issue.
- **Risks:** Free-form state can smuggle authority; keep trusted identifiers controller-owned.

### Task 5.2: Add investigation stopping and budgets

- **Objective:** Bound file reads, search calls, tokens, elapsed time, and total bytes.
- **Files:** `agent/stopping.py`, controller, tests.
- **Dependencies:** Task 5.1.
- **Acceptance:** Limits stop before another tool executes; partial evidence is reported honestly.
- **Tests:** changing-call loops, repeated searches, large context, timeout boundaries.
- **Risks:** Current cost controller is not connected to model token cost.

### Task 5.3: Produce a structured solution plan

- **Objective:** Require diagnosis, affected files, intended edits, tests, risks, and non-goals before modification.
- **Files:** schemas, prompts, controller, tests.
- **Dependencies:** Tasks 5.1–5.2.
- **Acceptance:** No edit is possible without a tool-backed plan; plan cannot authorize later remote actions.
- **Tests:** unsupported diagnosis, invented files, scope expansion.
- **Risks:** A plausible plan is not proof of correctness.

## Phase 6 — Controlled file modification

### Task 6.1: Define patch/edit schemas

- **Objective:** Permit narrow edits only inside the workspace.
- **Files:** schemas, registry, workspace editor, tests.
- **Dependencies:** Phase 5.
- **Acceptance:** Relative paths, expected-content/hash checks, bounded patch size, no `.git`, credentials, or generated secret files.
- **Tests:** traversal, stale file, binary edit, oversized patch, symlink attack.
- **Risks:** Arbitrary shell-based editing would bypass the tool boundary; use structured patches.

### Task 6.2: Add local edit proposal and reversible application

- **Objective:** Apply edits to the isolated workspace, not GitHub.
- **Files:** controller and workspace service.
- **Dependencies:** Task 6.1.
- **Acceptance:** Original base remains untouched; diff is reproducible; operation ID prevents duplicate patch application.
- **Tests:** exact replay, partial patch failure, rollback, concurrent workspace isolation.
- **Risks:** Decide whether local edits need an early approval or only the later remote-publication approval. At minimum, remote publication requires approval.

### Task 6.3: Generate structured diff evidence

- **Objective:** Capture changed files, unified diff, size, and base/head tree hashes.
- **Files:** diff tool/schema/tests.
- **Dependencies:** Task 6.2.
- **Acceptance:** Human review artifact is derived from git/workspace state, not model prose.
- **Tests:** untracked files, deletion, rename, line-ending changes, large diff limits.
- **Risks:** Diff may contain secrets; scan/redact or block before presentation/logging.

## Phase 7 — Test execution and result analysis

### Task 7.1: Define an allowlisted command runner

- **Objective:** Run repository tests without offering arbitrary shell access.
- **Files:** execution policy/service/schemas/tests.
- **Dependencies:** isolated workspace.
- **Acceptance:** Command templates are controller-selected; cwd fixed; environment minimized; time/output/process limits; network disabled by default.
- **Tests:** command injection, shell metacharacters, fork bombs, timeout, huge output, child cleanup.
- **Risks:** This is a high-risk execution boundary. Container/OS sandboxing is preferable to string filtering.

### Task 7.2: Discover and select relevant tests

- **Objective:** Choose tests from trusted project configuration plus issue evidence.
- **Files:** project adapters/planner/tests.
- **Dependencies:** Task 7.1.
- **Acceptance:** Baseline test and post-edit test plans are recorded; commands are reviewable.
- **Tests:** Python and unsupported-project fallback; malicious scripts/configuration.
- **Risks:** Repository test code itself is untrusted executable code.

### Task 7.3: Structure test evidence

- **Objective:** Record command, exit code, duration, truncated stdout/stderr, and environment identity.
- **Files:** schemas/controller/tests.
- **Dependencies:** Tasks 7.1–7.2.
- **Acceptance:** Agent cannot claim pass without exit-code evidence; failures remain visible.
- **Tests:** flaky/timeout/no-tests/partial suite.
- **Risks:** Output may leak secrets or contain prompt injection.

## Phase 8 — Human approval for code changes

### Task 8.1: Introduce a code-change proposal schema

- **Objective:** Bind approval to exact base commit, patch/tree hash, files, test evidence, target branch, repository, and planned remote actions.
- **Files:** `agent/schemas.py`, `agent/approval.py` usage, controller/UI tests.
- **Dependencies:** Phases 6–7.
- **Acceptance:** Any patch, base, target, or command-result change invalidates approval.
- **Tests:** changed diff, changed base, forged test evidence, cross-repository grant, expiration.
- **Risks:** Existing `WriteProposal` only supports labels/comments and must not be stretched ambiguously.

### Task 8.2: Build a human review presentation

- **Objective:** Show issue, diagnosis, plan, exact diff, tests, risks, and remote actions.
- **Files:** CLI or UI presenter; tests.
- **Dependencies:** Task 8.1.
- **Acceptance:** Explicit choices approve/reject/cancel; no blanket “yes to future changes.”
- **Tests:** EOF/cancel, large diff, redaction, mismatched displayed vs hashed payload.
- **Risks:** Display integrity is part of authorization.

## Phase 9 — Branch, commit, and push workflow

### Task 9.1: Create controlled task branch locally

- **Objective:** Create a unique non-default branch from the approved base.
- **Files:** git service/schemas/tests.
- **Dependencies:** Phase 8 approval.
- **Acceptance:** Branch name validated; base SHA matches approval; default branch untouched.
- **Tests:** existing branch, invalid name, stale base, default-branch attempt.
- **Risks:** TOCTOU between approval and branch creation.

### Task 9.2: Create deterministic commit

- **Objective:** Stage only approved paths and commit the approved tree.
- **Files:** git service/tests.
- **Dependencies:** Task 9.1.
- **Acceptance:** Committed tree hash equals approved tree hash; no unexpected/untracked secrets; author policy defined.
- **Tests:** extra file, changed file after approval, hook bypass, signing policy.
- **Risks:** Disable repository hooks and reverify tree immediately before commit.

### Task 9.3: Push approved branch

- **Objective:** Push only the new branch with least-privilege credentials.
- **Files:** git remote service/tests.
- **Dependencies:** Task 9.2 and still-valid approval.
- **Acceptance:** Explicit refspec `new-branch:new-branch`; no force; no default branch; remote SHA read back.
- **Tests:** fake/local remote, rejected push, auth failure, remote branch collision, default-branch protection.
- **Risks:** First irreversible code publication. Require controller-owned policy and audit.

## Phase 10 — Draft pull request creation and verification

### Task 10.1: Extend GitHub API for branches/PRs

- **Objective:** Add minimal draft-PR methods separate from issue-label service if appropriate.
- **Files:** GitHub client/service or new pull-request service, schemas, fake tests.
- **Dependencies:** Phase 9.
- **Acceptance:** Create draft PR with exact head/base, title/body; no merge capability.
- **Tests:** fake API 201/422/403/5xx, duplicate PR, wrong base/head.
- **Risks:** Avoid broad token permissions and accidental non-draft PRs.

### Task 10.2: Create approved draft PR

- **Objective:** Open a draft only after branch push verification.
- **Files:** controller/workflow/presenter.
- **Dependencies:** Task 10.1.
- **Acceptance:** PR is draft, base is default branch, head is approved branch, issue link included, body summarizes real tests.
- **Tests:** changed remote head, stale base policy, duplicate request/retry.
- **Risks:** PR creation needs idempotency using remote lookup, not only a local ledger.

### Task 10.3: Controller-owned PR verification

- **Objective:** Read the PR back and verify draft status, repository, head/base SHAs, changed files, and commit.
- **Files:** GitHub service/controller/tests.
- **Dependencies:** Task 10.2.
- **Acceptance:** Final success is impossible without read-back evidence.
- **Tests:** wrong head/base, non-draft response, missing file, API delay, retry.
- **Risks:** Verification must not merge or update the PR.

## Phase 11 — Security, evaluation, and final demonstration

### Task 11.1: Expand threat model

- **Objective:** Cover source prompt injection, malicious tests, secrets, symlinks, git hooks, branch protection, credential scope, and supply-chain execution.
- **Files:** `docs/deliverables/threat_model.md`, security tests.
- **Dependencies:** Architecture stabilized.
- **Acceptance:** Assets, trust boundaries, mitigations, residual risks, and tests are explicit.
- **Risks:** Documentation without enforced tests is insufficient.

### Task 11.2: Repair and expand evaluation

- **Objective:** Separate task completion from safe rejection and add Level 2 trajectories.
- **Files:** `eval/runner.py`, metrics, scenarios, saved results.
- **Dependencies:** Each feature phase.
- **Acceptance:** Correct expected statuses; side-effect assertions; diagnosis/edit/test/approval/push/PR metrics; deterministic offline suite.
- **Tests:** evaluator self-tests so a safe rejection scores as safety success, not task failure.
- **Risks:** Current metrics can report misleading 0% injection resistance.

### Task 11.3: Add tracing only if required and safe

- **Objective:** If the course still mandates LangSmith/LangGraph, add them for explicit durable states and observability without leaking private code.
- **Files:** dependencies, orchestration/tracing configuration, privacy tests.
- **Dependencies:** Instructor confirmation and privacy design.
- **Acceptance:** Traces redact secrets/private content, can be disabled, and do not replace controller enforcement.
- **Risks:** Third-party trace exfiltration and unnecessary framework complexity.

### Task 11.4: Final controlled demonstration

- **Objective:** Demonstrate one issue end-to-end in the private repository.
- **Dependencies:** All gates above.
- **Acceptance:** Read issue, investigate code, edit isolated workspace, run tests, show approval, push non-default branch, create and verify draft PR; no default-branch push; complete audit trail.
- **Tests:** Rehearsal against local/fake remote first; designated issue and rollback/cleanup plan.
- **Risks:** Live credentials, private code, and irreversible remote artifacts.

## Exact next task

First review and commit the uncommitted Phase 1 Task 1.1 files. Then perform **Task 2.1: explicit backend factory and CLI selection**, using only fake GitHub clients in automated tests. Do not contact the private repository as part of that implementation task.
