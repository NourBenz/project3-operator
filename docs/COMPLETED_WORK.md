

- **DONE - AGENT-01.1 - Shared contracts:** Strict tool inputs, results, write proposals and approval formats.
  Files: agent/schemas.py, tests/test_schemas.py.

- **DONE - AGENT-01.2 - Stopping controls:** Decision, retry, repetition, time and cost limits; cancellation.
  Files: agent/stopping.py, tests/test_stopping.py.

 
----------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------


- **DONE - TOOLS-02.1 - Database schema:** SQLite tables, constraints, migration and atomic persistence.
  Files: sandbox/database.py, sandbox/taxonomy.py, sandbox/mock_github.py, tests/test_sandbox.py.

- **DONE - TOOLS-02.2 - Dataset:** 50 issues, eight labels, four comments and source metadata.
  Files: sandbox/seed_issues.json, sandbox/dataset_manifest.json, sandbox/taxonomy.py, tests/test_sandbox.py.

- **DONE - TOOLS-02.3 - Initialize/reset:** Create the database, preserve edits and restore the baseline.
  Files: sandbox/seed.py, sandbox/reset.py, tests/test_sandbox.py.

- **DONE - TOOLS-02.4 - Read tools:** List issues, read details/comments and get allowed labels.
  Files: tools/list_issues.py, tools/read_issue.py, tools/get_labels.py, tests/test_sandbox.py.

- **DONE - TOOLS-02.5 - Human approval:** Exact approval, rejection, expiry and cancellation.
  Files: agent/approval.py, sandbox/mock_github.py, tools/review.py, tests/test_approval.py.

- **DONE - TOOLS-02.6 - Label updates:** Approved updates with conflict, closed-issue and version checks.
  Files: tools/update_labels.py, sandbox/mock_github.py, tests/test_tools.py.

- **DONE - TOOLS-02.7 - Comments:** Approved comments with length checks, timestamps and versions.
  Files: tools/post_comment.py, sandbox/mock_github.py, tests/test_tools.py.

- **DONE - TOOLS-02.8 - Safe retries:** Saved outcomes prevent duplicates and support failure recovery.
  Files: sandbox/mock_github.py, agent/approval.py, tests/test_idempotency.py.

- **DONE - TOOLS-02.9 - Errors and logs:** Clear errors and redacted tool/approval records.
  Files: agent/logging_utils.py, tools/common.py, tools/*.py, agent/approval.py, tests/test_tools.py.

- **DONE - TOOLS-02.10 - Verification:** Validation, approval, writes, retries, rollback, concurrency and CLI tests.
  Files: tests/test_tools.py, tests/test_approval.py, tests/test_idempotency.py, tests/conftest.py.
 
----------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------

mehdi next tajem taamel : AGENT-01  kamalha ; connecti OpenAI to the finished tools, add the bounded AI loop, human-approval pause/resume, and CLI.
Then test the full workflow: read → propose → approve → write → verify → stop. 

and check read me w task w project context


----------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------

172 tests hedhom

test_schemas.py — input and data contracts

- Issue IDs must be positive integers.
- Listing limits must be within bounds.
- Model arguments cannot supply approval or database paths.
- Unknown or conflicting labels are rejected.
- All four plain priority values are accepted.
- Labels are sorted, deduplicated and serialized correctly.
- Empty, oversized or incorrectly typed comments are rejected.
- All five tool formats validate; unknown tools fail.
- Changing an approved action changes its fingerprint.
- Approval decisions require an explicit response and matching task.
- Success/error results cannot contradict themselves.
- Existing tools return the expected result format.
- Events and tool schemas serialize correctly.

test_stopping.py — execution limits

- Repeated invalid decisions stop at 10.
- Changing tool arguments cannot bypass the overall limit.
- A third consecutive identical call is blocked.
- Reordering argument keys cannot bypass repetition detection.
- Retries are limited and consume decisions.
- Calling another tool does not reset retry counts.
- Each tool call requires its own reserved decision.
- Reaching the deadline blocks further calls.
- Disabling the deadline does not disable the decision limit.
- Reaching the cost limit stops further work.
- Invalid cost values are rejected.
- Cancellation prevents further work.
- Invalid limit configurations are rejected.

test_sandbox.py — database and reads

- Seed data can be created and read.
- Seeding preserves edits; reset restores the original records.
- Invalid IDs and missing issues return errors.
- Reads never create a missing database.
- Separate databases remain isolated; malicious text remains data.
- All 50 fixtures, sources, comments and labels load correctly.
- Listing order, filters, limits and empty results work.
- Invalid listing arguments fail.
- Missing fixture IDs can be added without overwriting edits.
- Older databases upgrade while preserving records.
- Failed upgrades roll back.
- Invalid fixtures fail before creating a database.
- Database constraints reject invalid records.
- Duplicate operation/comment keys are blocked; timestamps work.
- Read-only connections cannot write.
- Unrelated databases are not overwritten.
- Unknown legacy columns are preserved by refusing migration.
- Old priority names migrate without duplicate links.
- Failed priority migration rolls back.

test_approval.py — human authorization

- No approval means no write.
- Rejection, invalid responses and cancellation cause no changes.
- A pending proposal is not approval.
- Changed arguments require fresh approval.
- Approval cannot authorize another task or action.
- Forged grants and approval flags fail.
- Approval cannot cross database boundaries.
- Expired approval fails.
- Task cancellation blocks pending and future writes.
- Closing the input prompt cancels.
- Only yes approves; an existing decision cannot be replaced.
- Instructions inside an issue cannot create approval.
- Human decisions are audited without exposing grants.
- Failed audit logging cannot issue approval.

test_tools.py — actual tool behavior

- Approved labels persist and read back correctly.
- Approved comments persist with timestamps and updated versions.
- Missing, closed, stale or conflicting issues reject writes.
- Invalid write arguments are logged without changes.
- Invalid comment bodies are logged and rejected.
- Closed issues reject comments.
- Unrelated existing labels are preserved.
- Adding already-present labels does not increase the version.
- SQL-looking comment text cannot execute SQL.
- Unknown tools and injected database paths fail.
- Calls and results have matching audit identifiers.
- Logs redact keys, tokens and authorization values.
- Failed logging before execution prevents writes.
- Internal tool failures return errors without tracebacks.
- A later failure preserves an earlier successful write.
- The real CLI respects yes, no and cancel.
- The comment CLI verifies the saved comment.

test_idempotency.py — retries and partial failures

- Repeating an exact write returns the saved result without another effect.
- Reusing an operation ID for a different request fails.
- Timeout after commit does not create duplicate comments.
- Failure before commit rolls back and permits retry.
- Expired grants can retrieve committed results but cannot start new writes.
- Retrieving saved results still requires authorization.
- Restarting requires fresh human approval.
- Concurrent identical operations create only one comment.
- Final-log failure reports partial completion; replay stays safe.
- Two writes using the same old version cannot both succeed.
- Other failures after commit are reported as partial completion.
----------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------
