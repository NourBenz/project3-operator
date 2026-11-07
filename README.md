# Project 3: Operator

Read [project context](docs/PROJECT_CONTEXT.md), [tasks](docs/TASKS.md), or
[completed work](docs/COMPLETED_WORK.md).

## Before running commands

Open PowerShell in the project folder. On this computer:

~~~powershell
$operatorPython = ".\.runtime\python\python.exe"
~~~

Use the commands below in that terminal. On another computer, install Python 3.11+
and requirements with `py -3 -m pip install -r requirements.txt`, then replace
`& $operatorPython` with `py -3`.

## Create the database

~~~powershell
& $operatorPython -m sandbox.seed
~~~

Creates 50 issues, eight labels and four comments in sandbox/data/issues.sqlite3.
Repeating it preserves existing issue edits.

## Read data

~~~powershell
& $operatorPython -m tools.list_issues --state all --limit 50
& $operatorPython -m tools.list_issues --state open --limit 10
& $operatorPython -m tools.list_issues --state closed --limit 10
& $operatorPython -m tools.read_issue 12
& $operatorPython -m tools.get_labels
~~~

## Add labels or a comment

~~~powershell
& $operatorPython -m tools.review labels 12 --labels bug high
& $operatorPython -m tools.review comment 12 --body "Which error message appears?"
& $operatorPython -m tools.read_issue 12
~~~

At the prompt, type **yes**, **no**, or **cancel**. Only yes permits the exact write.
The CLI verifies approved changes by reading the issue back.
Labels: bug, feature, documentation, question, low, medium, high, critical.
Conflicting labels and closed-issue writes are rejected.

## Use a separate practice database

~~~powershell
& $operatorPython -m sandbox.seed --db sandbox/data/practice.sqlite3
& $operatorPython -m tools.review labels 12 --labels bug high --db sandbox/data/practice.sqlite3
& $operatorPython -m tools.read_issue 12 --db sandbox/data/practice.sqlite3
~~~

All database/read/review commands accept --db PATH.

## Reset data

These commands discard changes in the selected database and restore the baseline.
Audit logs are kept.

~~~powershell
& $operatorPython -m sandbox.reset
& $operatorPython -m sandbox.reset --db sandbox/data/practice.sqlite3
~~~

## View logs

~~~powershell
Get-Content .\sandbox\logs\tool_calls.jsonl -Tail 10
~~~

Follow new records live; press Ctrl+C to stop:

~~~powershell
Get-Content .\sandbox\logs\tool_calls.jsonl -Tail 10 -Wait
~~~

Choose a different log for a manual write:

~~~powershell
& $operatorPython -m tools.review labels 12 --labels bug high --log sandbox/logs/manual.jsonl
~~~

## Run tests

~~~powershell
& $operatorPython -m pytest -q
& $operatorPython -m pytest tests/test_sandbox.py -q
& $operatorPython -m pytest tests/test_schemas.py tests/test_stopping.py -q
& $operatorPython -m pytest tests/test_tools.py tests/test_approval.py tests/test_idempotency.py -q
~~~

Check timeout recovery specifically:

~~~powershell
& $operatorPython -m pytest tests/test_idempotency.py::test_timeout_after_commit_retries_without_duplicate_comment -v
~~~

Latest result: **172 passed**. Tests use temporary databases and simulated human
decisions; they do not call OpenAI or modify the active database.

## Inspect SQLite directly

These read-only queries use the SQLite CLI included with Python 3.12+;
the local Python 3.13 runtime supports them.

~~~powershell
& $operatorPython -m sqlite3 sandbox/data/issues.sqlite3 "SELECT name FROM sqlite_master WHERE type='table' ORDER BY name;"
& $operatorPython -m sqlite3 sandbox/data/issues.sqlite3 "SELECT source_kind, COUNT(*) FROM issues GROUP BY source_kind;"
& $operatorPython -m sqlite3 sandbox/data/issues.sqlite3 "SELECT name, category FROM labels ORDER BY category, name;"
& $operatorPython -m sqlite3 sandbox/data/issues.sqlite3 "SELECT issue_id, body FROM comments ORDER BY id;"
& $operatorPython -m sqlite3 sandbox/data/issues.sqlite3 "SELECT operation_id, action, status FROM operations;"
~~~

## Show command options

~~~powershell
& $operatorPython -m sandbox.seed --help
& $operatorPython -m sandbox.reset --help
& $operatorPython -m tools.list_issues --help
& $operatorPython -m tools.read_issue --help
& $operatorPython -m tools.get_labels --help
& $operatorPython -m tools.review --help
~~~

Operation IDs are generated automatically. Reusing a named ID in a new CLI task
is not the same as retrying an existing request; use the idempotency tests above
to check replay behavior.

The AI controller, trajectory evaluation and Streamlit app are not runnable yet.
No API key is needed for these manual tools.
