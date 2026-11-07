import argparse
import json
import re
from datetime import date, datetime
from pathlib import Path
from urllib.parse import urlparse

from sandbox.database import DEFAULT_DB, connect, initialize
from sandbox.taxonomy import LABELS, LABEL_DETAILS

SEED_FILE = Path(__file__).with_name("seed_issues.json")
def _seed_comments(db, issue):
    db.executemany(
        "INSERT INTO comments(issue_id,body,created_at) VALUES (?,?,?)",
        [(issue["id"], comment["body"], comment["created_at"]) for comment in issue["comments"]],
    )

def load_seed(seed_file=SEED_FILE):
    issues = json.loads(Path(seed_file).read_text(encoding="utf-8"))
    if not isinstance(issues, list) or not issues:
        raise ValueError("Seed must be a nonempty issue list")
    ids = set()
    required = {"id", "title", "body", "state", "labels", "comments", "version",
                "source_url", "retrieved_at", "source_kind"}
    for issue in issues:
        if not isinstance(issue, dict) or set(issue) != required:
            raise ValueError("Seed issue has missing or unexpected fields")
        if (type(issue["id"]) is not int or issue["id"] <= 0 or issue["id"] in ids
            or not isinstance(issue["title"], str) or not issue["title"].strip()
            or not isinstance(issue["body"], str)
            or len(issue["title"]) > 300 or len(issue["body"]) > 10000
            or issue["state"] not in ("open", "closed")
            or type(issue["version"]) is not int or issue["version"] != 1
            or not isinstance(issue["labels"], list)
            or any(not isinstance(label, str) or label not in LABELS for label in issue["labels"])
            or len(set(issue["labels"])) != len(issue["labels"])
            or not isinstance(issue["comments"], list)
            or issue["source_kind"] not in ("synthetic", "public_snapshot", "synthetic_injection")):
            raise ValueError("Invalid seed issue")
        if issue["source_kind"] == "public_snapshot":
            if not isinstance(issue["source_url"], str):
                raise ValueError("Public snapshot requires a source URL")
            source = urlparse(issue["source_url"])
            if (source.scheme != "https" or source.netloc != "github.com"
                or not re.fullmatch(r"/psf/requests/issues/[0-9]+", source.path)
                or source.query or source.fragment):
                raise ValueError("Public snapshot requires a valid source URL")
            try:
                date.fromisoformat(issue["retrieved_at"])
            except (TypeError, ValueError):
                raise ValueError("Public snapshot requires an ISO retrieval date") from None
        elif issue["source_url"] is not None or issue["retrieved_at"] is not None:
            raise ValueError("Synthetic fixtures cannot claim public provenance")
        for comment in issue["comments"]:
            if (not isinstance(comment, dict) or set(comment) != {"body", "created_at"}
                or not isinstance(comment["body"], str) or not comment["body"].strip()
                or len(comment["body"]) > 1000):
                raise ValueError("Invalid seed comment")
            try:
                timestamp = datetime.fromisoformat(comment["created_at"].replace("Z", "+00:00"))
                if timestamp.tzinfo is None:
                    raise ValueError()
            except (AttributeError, TypeError, ValueError):
                raise ValueError("Seed comment needs a timezone-aware timestamp") from None
        ids.add(issue["id"])
    return issues

def seed_database(db_path=DEFAULT_DB, *, reset=False, seed_file=SEED_FILE):
    issues = load_seed(seed_file)
    inserted = False
    with connect(db_path) as db:
        db.execute("BEGIN IMMEDIATE")
        is_legacy = db.execute("PRAGMA user_version").fetchone()[0] == 0 and db.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name='issues'"
        ).fetchone() is not None
        initialize(db)
        if reset:
            for table in ("operations", "comments", "issue_labels", "issues", "labels"):
                db.execute(f"DELETE FROM {table}")
            db.execute("DELETE FROM sqlite_sequence WHERE name='comments'")
        db.executemany(
            "INSERT INTO labels(name,category,description) VALUES (?,?,?) "
            "ON CONFLICT(name) DO UPDATE SET category=excluded.category, description=excluded.description",
            [(name, *details) for name, details in LABEL_DETAILS.items()],
        )
        existing = {row[0] for row in db.execute("SELECT id FROM issues")}
        for issue in issues:
            if issue["id"] in existing:
                row = db.execute("SELECT * FROM issues WHERE id=?", (issue["id"],)).fetchone()
                current_labels = [value[0] for value in db.execute(
                    "SELECT label FROM issue_labels WHERE issue_id=? ORDER BY label", (issue["id"],),
                )]
                unchanged = all(row[key] == issue[key] for key in ("title", "body", "state", "version"))
                if is_legacy and unchanged and current_labels == sorted(issue["labels"]):
                    if row["source_url"] is None and row["retrieved_at"] is None:
                        db.execute("UPDATE issues SET source_url=?,retrieved_at=?,source_kind=? WHERE id=?",
                                   (issue["source_url"], issue["retrieved_at"], issue["source_kind"], issue["id"]))
                    if issue["comments"] and not db.execute(
                        "SELECT 1 FROM comments WHERE issue_id=?", (issue["id"],),
                    ).fetchone():
                        _seed_comments(db, issue)
                        inserted = True
                continue
            db.execute(
                "INSERT INTO issues(id,title,body,state,version,source_url,retrieved_at,source_kind) "
                "VALUES (?,?,?,?,?,?,?,?)",
                tuple(issue[key] for key in (
                    "id", "title", "body", "state", "version", "source_url", "retrieved_at", "source_kind",
                )),
            )
            db.executemany("INSERT INTO issue_labels VALUES (?,?)",
                           [(issue["id"], label) for label in issue["labels"]])
            _seed_comments(db, issue)
            inserted = True
        if db.execute("PRAGMA foreign_key_check").fetchall():
            raise ValueError("Database contains broken references")
    return inserted

def main():
    parser = argparse.ArgumentParser(description="Initialize or add missing sample issues; preserve existing issues.")
    parser.add_argument("--db", type=Path, default=DEFAULT_DB)
    args = parser.parse_args()
    changed = seed_database(args.db)
    print(f"{'Seeded missing sample issues' if changed else 'Kept existing issues'}: {args.db.resolve()}")

if __name__ == "__main__":
    main()
