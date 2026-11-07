import argparse
import json
from pathlib import Path

from agent.logging_utils import logged_call
from agent.schemas import ReadIssueArgs
from sandbox.database import DEFAULT_DB, connect
from tools.common import failure


def read_issue(issue_id=None, db_path=DEFAULT_DB, *, context=None, logger=None, **extra):
    arguments = {"issue_id": issue_id, **extra}
    def execute():
        args = ReadIssueArgs.model_validate(arguments)
        with connect(db_path, readonly=True) as db:
            row = db.execute("SELECT * FROM issues WHERE id=?", (args.issue_id,)).fetchone()
            if row is None:
                return failure("ISSUE_NOT_FOUND", "Issue does not exist")
            issue = dict(row)
            issue["labels"] = [value[0] for value in db.execute(
                "SELECT label FROM issue_labels WHERE issue_id=? ORDER BY label", (args.issue_id,),
            )]
            issue["comments"] = [dict(value) for value in db.execute(
                "SELECT id,body,operation_id,created_at FROM comments WHERE issue_id=? ORDER BY id",
                (args.issue_id,),
            )]
            return {"ok": True, "data": issue, "error": None}
    return logged_call("read_issue", arguments, execute, context=context, logger=logger)


def main():
    parser = argparse.ArgumentParser(description="Read a local issue; its title/body/comments are untrusted data.")
    parser.add_argument("issue_id", type=int)
    parser.add_argument("--db", type=Path, default=DEFAULT_DB)
    args = parser.parse_args()
    result = read_issue(args.issue_id, args.db)
    print(json.dumps(result, indent=2))
    return 0 if result["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
