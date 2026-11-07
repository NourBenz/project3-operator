import json
import sqlite3
import tempfile
import unittest
from collections import Counter
from pathlib import Path

import pytest

from agent.schemas import Label
from typing import get_args
from sandbox.database import SCHEMA_VERSION, connect, initialize
from sandbox.seed import LABELS, load_seed, seed_database
from tools.get_labels import get_labels
from tools.list_issues import list_issues
from tools.read_issue import read_issue


def assert_fixture(issue, actual):
    for key, expected in issue.items():
        if key == "comments":
            assert [{"body": value["body"], "created_at": value["created_at"]}
                    for value in actual[key]] == expected
            assert all(value["operation_id"] is None for value in actual[key])
        elif key == "labels":
            assert actual[key] == sorted(expected)
        else:
            assert actual[key] == expected


def snapshot(db):
    with connect(db, readonly=True) as connection:
        return {table: [tuple(row) for row in connection.execute(f"SELECT * FROM {table} ORDER BY 1")]
                for table in ("issues", "labels", "issue_labels", "comments", "operations")}


class SandboxTests(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.db = Path(directory.name) / "issues.sqlite3"

    def test_seed_and_reads(self):
        self.assertTrue(seed_database(self.db))
        assert_fixture(load_seed()[0], read_issue(1, self.db)["data"])
        self.assertEqual(get_labels(self.db)["data"]["labels"], sorted(LABELS))
        self.assertEqual(read_issue(4, self.db)["data"]["labels"], [])
        self.assertEqual(read_issue(6, self.db)["data"]["state"], "closed")

    def test_seed_preserves_changes_reset_restores_all_state(self):
        seed_database(self.db)
        original = snapshot(self.db)
        with connect(self.db) as db:
            db.execute("UPDATE issues SET title='changed',version=2 WHERE id=1")
            db.execute("DELETE FROM issue_labels WHERE issue_id=1")
            db.execute("INSERT INTO comments(issue_id,body) VALUES (1,'test')")
            db.execute("INSERT INTO operations(operation_id,action,request_hash,outcome) "
                       "VALUES ('op','update_labels','hash','{}')")
        self.assertFalse(seed_database(self.db))
        self.assertEqual(read_issue(1, self.db)["data"]["version"], 2)
        for _ in range(2):
            seed_database(self.db, reset=True)
            self.assertEqual(snapshot(self.db), original)

    def test_bad_arguments_and_missing_issue(self):
        seed_database(self.db)
        for value in (True, 0, -1, "1", None, 1.5, "1 OR 1=1"):
            self.assertEqual(read_issue(value, self.db)["error"]["code"], "INVALID_ARGUMENTS")
        self.assertEqual(read_issue(999, self.db)["error"]["code"], "ISSUE_NOT_FOUND")

    def test_missing_database_is_not_created(self):
        for result in (read_issue(1, self.db), get_labels(self.db), list_issues(db_path=self.db)):
            self.assertEqual(result["error"]["code"], "TOOL_UNAVAILABLE")
        self.assertFalse(self.db.exists())

    def test_isolation_and_untrusted_body(self):
        seed_database(self.db)
        other = self.db.with_name("other.sqlite3")
        seed_database(other)
        with connect(self.db) as db:
            db.execute("UPDATE issues SET version=2 WHERE id=1")
        self.assertEqual(read_issue(1, other)["data"]["version"], 1)
        self.assertIn("IGNORE ALL", read_issue(7, self.db)["data"]["body"])
        self.assertEqual(read_issue(7, self.db)["data"]["labels"], [])


def test_all_50_fixtures_roundtrip_with_sources_comments_and_taxonomy(tmp_path):
    db = tmp_path / "full.sqlite3"
    seed_database(db)
    issues = load_seed()
    assert len(issues) == 50
    assert [issue["id"] for issue in issues] == list(range(1, 51))
    assert Counter(issue["source_kind"] for issue in issues) == {
        "synthetic": 15, "public_snapshot": 30, "synthetic_injection": 5,
    }
    assert set(LABELS) == set(get_args(Label))
    for issue in issues:
        assert_fixture(issue, read_issue(issue["id"], db)["data"])
    public = [issue for issue in issues if issue["source_kind"] == "public_snapshot"]
    assert len({issue["source_url"] for issue in public}) == 30
    assert all(issue["retrieved_at"] == "2026-11-06" for issue in public)
    assert all(comment["body"].startswith("Synthetic")
               for issue in issues for comment in issue["comments"])
    issue12 = read_issue(12, db)["data"]
    assert (issue12["state"], issue12["version"], issue12["labels"]) == ("open", 1, [])
    taxonomy = get_labels(db)["data"]["taxonomy"]
    assert {row["category"] for row in taxonomy} == {"type", "priority"}
    assert all(row["description"] for row in taxonomy)
    with connect(db, readonly=True) as connection:
        assert connection.execute("PRAGMA user_version").fetchone()[0] == SCHEMA_VERSION
        assert connection.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
        assert connection.execute("PRAGMA foreign_key_check").fetchall() == []
        assert connection.execute("SELECT COUNT(*) FROM operations").fetchone()[0] == 0


def test_listing_order_filters_limits_and_empty_results(tmp_path):
    db = tmp_path / "full.sqlite3"
    seed_database(db)
    all_issues = list_issues("all", 50, db)["data"]["issues"]
    assert [issue["id"] for issue in all_issues] == list(range(1, 51))
    for state in ("open", "closed"):
        issues = list_issues(state, 50, db)["data"]["issues"]
        assert issues and all(issue["state"] == state for issue in issues)
    assert len(list_issues(limit=3, db_path=db)["data"]["issues"]) == 3
    assert len(list_issues(db_path=db)["data"]["issues"]) <= 20
    with connect(db) as connection:
        connection.execute("UPDATE issues SET state='open'")
    assert list_issues("closed", 50, db) == {"ok": True, "data": {"issues": []}, "error": None}


@pytest.mark.parametrize("state,limit", [("deleted", 20), ("all", 0), ("all", 51),
                                         ("all", True), ("all", "20"), (None, 2)])
def test_invalid_listing_arguments(state, limit, tmp_path):
    db = tmp_path / "not-created.sqlite3"
    assert list_issues(state, limit, db)["error"]["code"] == "INVALID_ARGUMENTS"
    assert not db.exists()


def test_seed_adds_missing_issues_without_overwriting_existing_data(tmp_path):
    db = tmp_path / "full.sqlite3"
    seed_database(db)
    with connect(db) as connection:
        connection.execute("DELETE FROM issues WHERE id=50")
        connection.execute("UPDATE issues SET title='friend edit',version=3 WHERE id=1")
        connection.execute("DELETE FROM comments WHERE issue_id=11")
    assert seed_database(db)
    assert read_issue(1, db)["data"]["title"] == "friend edit"
    assert read_issue(1, db)["data"]["version"] == 3
    assert read_issue(11, db)["data"]["comments"] == []
    assert read_issue(50, db)["ok"]
    assert not seed_database(db)


def create_legacy(db):
    with sqlite3.connect(db) as connection:
        for sql in (
            "CREATE TABLE issues(id INTEGER PRIMARY KEY,title TEXT,body TEXT,state TEXT,version INTEGER)",
            "CREATE TABLE labels(name TEXT PRIMARY KEY)",
            "CREATE TABLE issue_labels(issue_id INTEGER,label TEXT,PRIMARY KEY(issue_id,label))",
            "CREATE TABLE comments(id INTEGER PRIMARY KEY,issue_id INTEGER,body TEXT)",
            "CREATE TABLE operations(operation_id TEXT PRIMARY KEY,action TEXT,request_hash TEXT,outcome TEXT)",
        ):
            connection.execute(sql)
        connection.execute("INSERT INTO issues VALUES (1,'friend title','friend body','open',4)")
        connection.executemany("INSERT INTO labels VALUES (?)",
                               [("enhancement",), ("feature",), ("priority:normal",), ("team:backend",)])
        connection.executemany("INSERT INTO issue_labels VALUES (1,?)",
                               [("enhancement",), ("feature",), ("priority:normal",), ("team:backend",)])
        connection.execute("INSERT INTO comments VALUES (101,1,'Preserve this existing comment')")
        connection.execute("INSERT INTO operations VALUES ('old-op','update_labels','old-hash','{}')")


def test_legacy_upgrade_preserves_data_maps_aliases_and_keeps_unrelated_labels(tmp_path):
    db = tmp_path / "legacy.sqlite3"
    create_legacy(db)
    seed_database(db)
    issue = read_issue(1, db)["data"]
    assert (issue["title"], issue["body"], issue["version"]) == ("friend title", "friend body", 4)
    assert issue["labels"] == ["feature", "medium", "team:backend"]
    assert issue["comments"][0]["id"] == 101
    assert issue["comments"][0]["body"] == "Preserve this existing comment"
    assert "team:backend" not in get_labels(db)["data"]["labels"]
    with connect(db) as connection:
        operation = dict(connection.execute("SELECT * FROM operations WHERE operation_id='old-op'").fetchone())
        assert operation["request_hash"] == "old-hash"
        assert operation["outcome"] == "{}"
        assert operation["status"] == "succeeded"
        assert connection.execute("SELECT COUNT(*) FROM issues").fetchone()[0] == 50
    assert not seed_database(db)


def test_failed_upgrade_rolls_back_original_database(tmp_path, monkeypatch):
    import sandbox.database as database
    db = tmp_path / "legacy.sqlite3"
    create_legacy(db)
    def fail_restore(connection, tables):
        raise RuntimeError("injected migration failure")
    monkeypatch.setattr(database, "_restore_legacy", fail_restore)
    with pytest.raises(RuntimeError, match="injected migration failure"):
        seed_database(db)
    with sqlite3.connect(db) as connection:
        assert connection.execute("PRAGMA user_version").fetchone()[0] == 0
        assert connection.execute("SELECT title FROM issues WHERE id=1").fetchone()[0] == "friend title"
        assert connection.execute("SELECT COUNT(*) FROM comments").fetchone()[0] == 1
        assert connection.execute("SELECT name FROM labels WHERE name='enhancement'").fetchone()


@pytest.mark.parametrize("change", [
    lambda value: value[0].update(id=True),
    lambda value: value[0].update(labels=["not-allowed"]),
    lambda value: value[0].update(labels=["bug", "bug"]),
    lambda value: value[1].update(id=1),
    lambda value: value[0].update(comments=[{"body": "", "created_at": "bad"}]),
    lambda value: value[0].update(comments=[{"body": "note", "created_at": "2026-11-06"}]),
    lambda value: value[16].update(source_url="https://not-the-source.invalid/"),
    lambda value: value[16].update(retrieved_at=None),
    lambda value: value[0].update(source_url="https://github.com/psf/requests/issues/1"),
])
def test_invalid_fixtures_fail_before_creating_database(tmp_path, change):
    data = load_seed()
    change(data)
    fixture = tmp_path / "invalid.json"
    fixture.write_text(json.dumps(data), encoding="utf-8")
    db = tmp_path / "not-created.sqlite3"
    with pytest.raises(ValueError):
        seed_database(db, seed_file=fixture)
    assert not db.exists()


@pytest.mark.parametrize("sql", [
    "INSERT INTO issue_labels VALUES (999,'bug')",
    "INSERT INTO issue_labels VALUES (1,'unknown')",
    "INSERT INTO issues(id,title,body,state,version) VALUES (99,'x','x','open',0)",
    "INSERT INTO issues(id,title,body,state,version) VALUES (99,'x','x','invalid',1)",
    "INSERT INTO comments(issue_id,body) VALUES (999,'comment')",
    "INSERT INTO comments(issue_id,body) VALUES (1,'')",
    "INSERT INTO operations(operation_id,action,request_hash,outcome) VALUES ('x','delete','hash','{}')",
    "INSERT INTO operations(operation_id,action,request_hash,outcome) VALUES ('x','update_labels','hash','not-json')",
])
def test_storage_constraints(sql, tmp_path):
    db = tmp_path / "full.sqlite3"
    seed_database(db)
    with pytest.raises(sqlite3.IntegrityError):
        with connect(db) as connection:
            connection.execute(sql)


def test_duplicate_operation_and_comment_ids_are_rejected(tmp_path):
    db = tmp_path / "full.sqlite3"
    seed_database(db)
    with connect(db) as connection:
        connection.execute("INSERT INTO operations(operation_id,action,request_hash,outcome) "
                           "VALUES ('op-1','post_comment','hash','{}')")
        connection.execute("INSERT INTO comments(issue_id,body,operation_id) VALUES (1,'one','op-1')")
        for table in ("comments", "operations"):
            timestamp = connection.execute(
                f"SELECT created_at FROM {table} WHERE operation_id='op-1'"
            ).fetchone()[0]
            assert timestamp.endswith("Z") and not timestamp.startswith("1970")
    for sql in (
        "INSERT INTO operations(operation_id,action,request_hash,outcome) "
        "VALUES ('op-1','post_comment','hash','{}')",
        "INSERT INTO comments(issue_id,body,operation_id) VALUES (1,'duplicate','op-1')",
    ):
        with pytest.raises(sqlite3.IntegrityError):
            with connect(db) as connection:
                connection.execute(sql)


def test_readonly_connections_cannot_mutate(tmp_path):
    db = tmp_path / "full.sqlite3"
    seed_database(db)
    with connect(db, readonly=True) as connection:
        with pytest.raises(sqlite3.OperationalError):
            connection.execute("UPDATE issues SET version=2 WHERE id=1")


def test_unrecognized_database_is_not_destroyed(tmp_path):
    db = tmp_path / "other.sqlite3"
    with sqlite3.connect(db) as connection:
        connection.execute("CREATE TABLE keep_me(value TEXT)")
        connection.execute("INSERT INTO keep_me VALUES ('important')")
    with pytest.raises(ValueError, match="Unrecognized"):
        seed_database(db)
    with sqlite3.connect(db) as connection:
        assert connection.execute("SELECT value FROM keep_me").fetchone()[0] == "important"


def test_legacy_with_unrecognized_columns_is_preserved(tmp_path):
    db = tmp_path / "legacy.sqlite3"
    create_legacy(db)
    with sqlite3.connect(db) as connection:
        connection.execute("ALTER TABLE issues ADD COLUMN friend_data TEXT")
        connection.execute("UPDATE issues SET friend_data='preserve' WHERE id=1")
    with pytest.raises(ValueError, match="Unrecognized legacy columns"):
        seed_database(db)
    with sqlite3.connect(db) as connection:
        assert connection.execute("SELECT friend_data FROM issues WHERE id=1").fetchone()[0] == "preserve"


def test_current_schema_priority_aliases_migrate_without_duplicate_links(tmp_path):
    db = tmp_path / "full.sqlite3"
    seed_database(db)
    with connect(db) as connection:
        connection.execute("UPDATE issues SET title='friend edit',version=4 WHERE id=1")
        connection.execute("INSERT INTO labels VALUES ('priority:high','priority','Old spelling')")
        connection.execute("INSERT INTO issue_labels VALUES (1,'priority:high')")
        connection.execute("INSERT INTO issue_labels VALUES (1,'high')")
    before = read_issue(1, db)["data"]
    assert not seed_database(db)
    after = read_issue(1, db)["data"]
    assert after["labels"] == ["bug", "high"]
    for field in ("title", "body", "version", "comments", "source_url"):
        assert after[field] == before[field]
    assert set(get_labels(db)["data"]["labels"]) == set(LABELS)


def test_priority_alias_migration_rolls_back_on_failure(tmp_path):
    db = tmp_path / "full.sqlite3"
    seed_database(db)
    with connect(db) as connection:
        connection.execute("INSERT INTO labels VALUES ('priority:high','priority','Old spelling')")
        connection.execute("INSERT INTO issue_labels VALUES (1,'priority:high')")
        connection.execute("CREATE TRIGGER reject_alias_delete BEFORE DELETE ON labels "
                           "WHEN old.name='priority:high' BEGIN SELECT RAISE(ABORT,'rename failed'); END")
    before = snapshot(db)
    with pytest.raises(sqlite3.IntegrityError, match="rename failed"):
        seed_database(db)
    assert snapshot(db) == before


if __name__ == "__main__":
    unittest.main()
