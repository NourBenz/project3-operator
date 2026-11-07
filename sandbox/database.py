import sqlite3
from contextlib import contextmanager
from pathlib import Path

from sandbox.taxonomy import LABEL_DETAILS, LEGACY_LABELS

DEFAULT_DB = Path(__file__).resolve().parent / "data" / "issues.sqlite3"
SCHEMA_VERSION = 2

SCHEMA = (
    "CREATE TABLE issues ("
    "id INTEGER PRIMARY KEY CHECK(id>0), title TEXT NOT NULL, body TEXT NOT NULL, "
    "state TEXT NOT NULL CHECK(state IN ('open','closed')), "
    "version INTEGER NOT NULL DEFAULT 1 CHECK(version>0), source_url TEXT, retrieved_at TEXT, "
    "source_kind TEXT NOT NULL DEFAULT 'synthetic' "
    "CHECK(source_kind IN ('synthetic','public_snapshot','synthetic_injection')))",
    "CREATE TABLE labels (name TEXT PRIMARY KEY NOT NULL, category TEXT NOT NULL "
    "CHECK(category IN ('type','priority','other')), description TEXT NOT NULL)",
    "CREATE TABLE issue_labels (issue_id INTEGER NOT NULL REFERENCES issues(id) ON DELETE CASCADE, "
    "label TEXT NOT NULL REFERENCES labels(name), PRIMARY KEY(issue_id,label))",
    "CREATE TABLE comments (id INTEGER PRIMARY KEY AUTOINCREMENT, "
    "issue_id INTEGER NOT NULL REFERENCES issues(id) ON DELETE CASCADE, "
    "body TEXT NOT NULL CHECK(length(trim(body)) BETWEEN 1 AND 1000), "
    "operation_id TEXT UNIQUE, created_at TEXT NOT NULL "
    "DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ','now')))",
    "CREATE TABLE operations (operation_id TEXT PRIMARY KEY NOT NULL, "
    "action TEXT NOT NULL CHECK(action IN ('update_labels','post_comment')), "
    "request_hash TEXT NOT NULL, outcome TEXT NOT NULL CHECK(json_valid(outcome)), "
    "status TEXT NOT NULL DEFAULT 'succeeded' CHECK(status IN ('succeeded','failed')), "
    "created_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ','now')))",
    "CREATE INDEX comments_issue_id ON comments(issue_id)",
    "CREATE INDEX issues_state_id ON issues(state,id)",
)

@contextmanager
def connect(db_path=DEFAULT_DB, *, readonly=False):
    path = Path(db_path).resolve()
    if readonly:
        db = sqlite3.connect(path.as_uri() + "?mode=ro", uri=True)
        db.execute("PRAGMA query_only=ON")
    else:
        path.parent.mkdir(parents=True, exist_ok=True)
        db = sqlite3.connect(path)
    db.row_factory = sqlite3.Row
    db.execute("PRAGMA foreign_keys=ON")
    try:
        with db:
            yield db
    finally:
        db.close()

def _restore_legacy(db, tables):
    for row in tables.get("issues", []):
        db.execute(
            "INSERT INTO issues(id,title,body,state,version) VALUES (?,?,?,?,?)",
            tuple(row[key] for key in ("id", "title", "body", "state", "version")),
        )
    for row in tables.get("labels", []):
        name = LEGACY_LABELS.get(row["name"], row["name"])
        category, description = LABEL_DETAILS.get(
            name, ("other", "Preserved legacy label; not an allowed triage label."),
        )
        db.execute("INSERT OR IGNORE INTO labels VALUES (?,?,?)", (name, category, description))
    for row in tables.get("issue_labels", []):
        db.execute("INSERT OR IGNORE INTO issue_labels VALUES (?,?)",
                   (row["issue_id"], LEGACY_LABELS.get(row["label"], row["label"])))
    for row in tables.get("comments", []):
        db.execute("INSERT INTO comments(id,issue_id,body) VALUES (?,?,?)",
                   (row["id"], row["issue_id"], row["body"]))
    for row in tables.get("operations", []):
        db.execute("INSERT INTO operations(operation_id,action,request_hash,outcome) VALUES (?,?,?,?)",
                   tuple(row[key] for key in ("operation_id", "action", "request_hash", "outcome")))

def _normalize_legacy_labels(db):
    db.execute("SAVEPOINT normalize_labels")
    try:
        for old, new in LEGACY_LABELS.items():
            if db.execute("SELECT 1 FROM labels WHERE name=?", (old,)).fetchone() is None:
                continue
            category, description = LABEL_DETAILS[new]
            db.execute("INSERT INTO labels(name,category,description) VALUES (?,?,?) "
                       "ON CONFLICT(name) DO UPDATE SET category=excluded.category,description=excluded.description",
                       (new, category, description))
            db.execute("INSERT OR IGNORE INTO issue_labels(issue_id,label) "
                       "SELECT issue_id,? FROM issue_labels WHERE label=?", (new, old))
            db.execute("DELETE FROM issue_labels WHERE label=?", (old,))
            db.execute("DELETE FROM labels WHERE name=?", (old,))
        if db.execute("PRAGMA foreign_key_check").fetchall():
            raise ValueError("Foreign key check failed")
    except Exception:
        db.execute("ROLLBACK TO normalize_labels")
        db.execute("RELEASE normalize_labels")
        raise
    else:
        db.execute("RELEASE normalize_labels")

def initialize(db):
    version = db.execute("PRAGMA user_version").fetchone()[0]
    if version == SCHEMA_VERSION:
        _normalize_legacy_labels(db)
        return
    if version != 0:
        raise ValueError(f"Unsupported database schema version: {version}")
    db.execute("SAVEPOINT initialize_schema")
    try:
        names = {row[0] for row in db.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        legacy = {}
        if "issues" in names:
            required = {"issues", "labels", "issue_labels", "comments", "operations"}
            if not required.issubset(names):
                raise ValueError("Incomplete legacy database; no changes made")
            columns = {
                "issues": {"id", "title", "body", "state", "version"},
                "labels": {"name"}, "issue_labels": {"issue_id", "label"},
                "comments": {"id", "issue_id", "body"},
                "operations": {"operation_id", "action", "request_hash", "outcome"},
            }
            for table, expected in columns.items():
                actual = {row[1] for row in db.execute(f"PRAGMA table_info({table})")}
                if actual != expected:
                    raise ValueError("Unrecognized legacy columns; no changes made")
            legacy = {name: [dict(row) for row in db.execute(f"SELECT * FROM {name}")]
                      for name in required}
            for name in ("comments", "issue_labels", "operations", "issues", "labels"):
                db.execute(f"DROP TABLE {name}")
        elif names - {"sqlite_sequence"}:
            raise ValueError("Unrecognized database; no changes made")
        for sql in SCHEMA:
            db.execute(sql)
        _restore_legacy(db, legacy)
        if db.execute("PRAGMA foreign_key_check").fetchall():
            raise ValueError("Foreign key check failed")
        db.execute(f"PRAGMA user_version={SCHEMA_VERSION}")
    except Exception:
        db.execute("ROLLBACK TO initialize_schema")
        db.execute("RELEASE initialize_schema")
        raise
    else:
        db.execute("RELEASE initialize_schema")
