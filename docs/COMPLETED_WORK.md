# Completed work and affected files

The work below is complete for the local SQLite backend. The final system now needs a GitHub API backend; the local work remains useful for tests but is not the final data source.

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

- **OPEN - Real GitHub migration:** Adapt the tool backend to read and write the repository's actual issues while preserving approval, validation, version and retry safety. LangGraph and LangSmith are mandatory for the final architecture; post-change emails are also required.
  Files to be agreed by the TOOLS-02 owner and team after the repository link and owner email are provided.
