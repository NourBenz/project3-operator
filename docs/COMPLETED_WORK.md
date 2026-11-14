# Completed work and affected files

The local SQLite backend remains available for tests. The live GitHub tools have now been added and tested with real repository reads and one approved label change.

- **DONE - Shared contracts:** Strict tool inputs, results, write proposals, approval decisions and run state.
  Files: agent/schemas.py, tests/test_schemas.py.

- **DONE - Stopping controls:** Decision, retry, repeated-call, time, cost and cancellation limits.
  Files: agent/stopping.py, tests/test_stopping.py.

- **DONE - Local database:** SQLite schema, constraints, migrations, relationships and atomic persistence.
  Files: sandbox/database.py, sandbox/taxonomy.py, tests/test_sandbox.py.

- **DONE - Local dataset:** 50 issues, eight labels, four baseline comments and source metadata.
  Files: sandbox/seed_issues.json, sandbox/dataset_manifest.json, sandbox/taxonomy.py.

- **DONE - Seed and reset:** Creates the local database, preserves edits during seeding and restores the baseline on reset.
  Files: sandbox/seed.py, sandbox/reset.py, tests/test_sandbox.py.

- **DONE - Read tools:** Lists issues, reads issue details and returns the allowed label taxonomy.
  Files: tools/list_issues.py, tools/read_issue.py, tools/get_labels.py.

- **DONE - Approved writes:** Adds labels or comments only after exact human approval, with state and version checks.
  Files: tools/update_labels.py, tools/post_comment.py, agent/approval.py, sandbox/mock_github.py.

- **DONE - Safe persistence:** Operation IDs, transactions, duplicate protection, rollback and concurrent-write handling.
  Files: sandbox/mock_github.py, sandbox/database.py, tests/test_idempotency.py.

- **DONE - Errors and logs:** Normalized error responses and redacted records for tool and approval calls.
  Files: tools/common.py, agent/logging_utils.py, tests/test_tools.py.

- **DONE - Verification:** Local database, tool, approval, retry, rollback, concurrency and CLI tests; 108 selected tools tests passed and 199 full-project tests passed.
  Files: tests/test_sandbox.py, tests/test_tools.py, tests/test_approval.py, tests/test_idempotency.py.

- **DONE - Agent integration baseline:** OpenAI adapter, offline model, registry, bounded loop, approval flow and CLI were added by the agent workstream.
  Files: agent/llm.py, agent/loop.py, agent/main.py, agent/registry.py, agent/prompts.py.

- **DONE - GitHub backend:** Added real issue/label/comment reads and approved writes while preserving validation, approval, operation IDs and read-back verification.
  Files: tools/github_service.py, tools/review.py.

- **DONE - GitHub dataset seeder:** Seeded all 50 fixtures into repository issues 2-51, with marker-based duplicate detection and missing-label creation.
  Files: tools/seed_github.py, sandbox/seed_issues.json.

- **PARTIAL - Owner notifications:** Added email notifications to the configured recipients after approved GitHub changes; full SMTP delivery confirmation remains.
  Files: tools/notifications.py, .env.example.

- **PARTIAL - GitHub adapter tests:** Added focused fake-client tests for reads, approval, notifications and exact retries; broader API failure tests and execution in the team runtime remain.
  Files: tests/test_github_service.py.
