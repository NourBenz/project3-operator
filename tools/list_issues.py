import argparse
import json
from pathlib import Path

from agent.logging_utils import logged_call
from agent.schemas import ListIssuesArgs
from sandbox.database import DEFAULT_DB, connect


def list_issues(state="open", limit=20, db_path=DEFAULT_DB, *, context=None, logger=None, **extra):
    arguments = {"state": state, "limit": limit, **extra}
    def execute():
        args = ListIssuesArgs.model_validate(arguments)
        with connect(db_path, readonly=True) as db:
            if args.state == "all":
                rows = db.execute("SELECT id,title,state,version FROM issues ORDER BY id LIMIT ?",
                                  (args.limit,)).fetchall()
            else:
                rows = db.execute(
                    "SELECT id,title,state,version FROM issues WHERE state=? ORDER BY id LIMIT ?",
                    (args.state, args.limit),
                ).fetchall()
            issues = []
            for row in rows:
                issue = dict(row)
                issue["labels"] = [value[0] for value in db.execute(
                    "SELECT label FROM issue_labels WHERE issue_id=? ORDER BY label", (issue["id"],),
                )]
                issues.append(issue)
            return {"ok": True, "data": {"issues": issues}, "error": None}
    return logged_call("list_issues", arguments, execute, context=context, logger=logger)


def main():
    parser = argparse.ArgumentParser(description="List local issues in stable ID order.")
    parser.add_argument("--state", choices=("open", "closed", "all"), default="open")
    parser.add_argument("--limit", type=int, default=20)
    parser.add_argument("--db", type=Path, default=DEFAULT_DB)
    args = parser.parse_args()
    result = list_issues(args.state, args.limit, args.db)
    print(json.dumps(result, indent=2))
    return 0 if result["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
