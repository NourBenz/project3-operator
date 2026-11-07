import hashlib
import json
import os
import uuid
from pathlib import Path

from pydantic import ValidationError

from agent.approval import ApprovalError, ApprovalManager
from agent.logging_utils import CallContext, ToolLogger, logged_call
from agent.schemas import PostCommentArgs, TOOL_REQUEST_ADAPTER, ToolResult, UpdateLabelsArgs, WriteProposal
from sandbox.database import DEFAULT_DB, connect
from tools.common import failure


class ServiceError(Exception):
    def __init__(self, code, message, retryable=False):
        super().__init__(message)
        self.code, self.retryable = code, retryable


class MockGitHub:
    def __init__(self, db_path=DEFAULT_DB, *, approvals=None, logger=None):
        self.db_path = Path(db_path).resolve()
        self.scope = os.path.normcase(str(self.db_path))
        self.approvals = approvals or ApprovalManager()
        self.logger = logger or ToolLogger()

    @staticmethod
    def _arguments(tool, arguments):
        model = {"update_labels": UpdateLabelsArgs, "post_comment": PostCommentArgs}.get(tool)
        if model is None:
            raise ServiceError("UNKNOWN_TOOL", "Only label/comment write proposals are supported")
        return model.model_validate(arguments)

    def propose(self, tool, arguments, *, task_id):
        args = self._arguments(tool, arguments)
        proposal = WriteProposal(
            proposal_id=uuid.uuid4().hex, task_id=task_id,
            request={"tool": tool, "args": args.model_dump(mode="json")},
        )
        return self.approvals.register(proposal, self.scope, audit=self.logger.emit)

    def call(self, tool, arguments, *, task_id="standalone", proposal_id=None, grant=None, context=None):
        context = context or CallContext(task_id=task_id)
        if tool not in ("list_issues", "read_issue", "get_labels", "update_labels", "post_comment"):
            return logged_call(tool, arguments, lambda: failure("UNKNOWN_TOOL", "Tool is not allowed"),
                               context=context, logger=self.logger)
        try:
            request = TOOL_REQUEST_ADAPTER.validate_python({"tool": tool, "args": arguments})
        except ValidationError as error:
            def reject(error=error):
                raise error
            return logged_call(tool, arguments, reject, context=context, logger=self.logger)
        payload = request.args.model_dump(mode="json")
        if tool in ("update_labels", "post_comment"):
            return self._write(tool, payload, task_id, proposal_id, grant, context)
        method = {"list_issues": self.list_issues, "read_issue": self.read_issue,
                  "get_labels": self.get_labels}[tool]
        return method(**payload, context=context)

    def update_labels(self, issue_id, labels, operation_id, expected_version, *,
                      task_id="standalone", proposal_id=None, grant=None, context=None, **extra):
        arguments = {"issue_id": issue_id, "labels": labels, "operation_id": operation_id,
                     "expected_version": expected_version, **extra}
        return self._write("update_labels", arguments, task_id, proposal_id, grant, context)

    def post_comment(self, issue_id, body, operation_id, expected_version, *,
                     task_id="standalone", proposal_id=None, grant=None, context=None, **extra):
        arguments = {"issue_id": issue_id, "body": body, "operation_id": operation_id,
                     "expected_version": expected_version, **extra}
        return self._write("post_comment", arguments, task_id, proposal_id, grant, context)

    def _write(self, tool, arguments, task_id, proposal_id, grant, context):
        context = context or CallContext(task_id=task_id)
        def execute():
            try:
                if context.task_id != task_id:
                    raise ServiceError("INVALID_ARGUMENTS", "Call context and trusted task ID must match")
                args = self._arguments(tool, arguments)
                if proposal_id is None:
                    raise ApprovalError("APPROVAL_REQUIRED", "Show the exact proposal and obtain human approval first")
                proposal = WriteProposal(
                    proposal_id=proposal_id, task_id=task_id,
                    request={"tool": tool, "args": args.model_dump(mode="json")},
                )
                with self.approvals.synchronized():
                    self.approvals.authorize(proposal, self.scope, grant, replay=True)
                    if not self.db_path.is_file():
                        raise ServiceError("TOOL_UNAVAILABLE", "Initialize this local sandbox before using tools")
                    payload = {"task_id": task_id, "tool": tool, "args": args.model_dump(mode="json")}
                    request_hash = hashlib.sha256(json.dumps(
                        payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False,
                    ).encode("utf-8")).hexdigest()
                    replayed = False
                    with connect(self.db_path) as db:
                        db.execute("BEGIN IMMEDIATE")
                        saved = db.execute("SELECT * FROM operations WHERE operation_id=?",
                                           (args.operation_id,)).fetchone()
                        if saved is not None:
                            if saved["action"] != tool or saved["request_hash"] != request_hash:
                                raise ServiceError("DUPLICATE_OPERATION", "Operation ID was already used for a different request")
                            result = ToolResult.model_validate_json(saved["outcome"]).model_dump(mode="json")
                            replayed = True
                        else:
                            self.approvals.authorize(proposal, self.scope, grant)
                            result = self._apply(db, tool, args)
                            db.execute(
                                "INSERT INTO operations(operation_id,action,request_hash,outcome,status) "
                                "VALUES (?,?,?,?,'succeeded')",
                                (args.operation_id, tool, request_hash,
                                 json.dumps(result, sort_keys=True, ensure_ascii=False)),
                            )
                            self._before_commit(db, tool, args)
                    self.approvals.consume(proposal_id)
                    if not replayed:
                        try:
                            self._after_commit(tool, args)
                        except TimeoutError:
                            raise
                        except Exception:
                            return failure("PARTIAL_FAILURE", "Write committed but response processing failed; retry the same operation ID and verify state", True)
                    return result
            except ServiceError as error:
                return failure(error.code, str(error), error.retryable)
        return logged_call(tool, arguments, execute, context=context, logger=self.logger)

    def _apply(self, db, tool, args):
        issue = db.execute("SELECT state,version FROM issues WHERE id=?", (args.issue_id,)).fetchone()
        if issue is None:
            raise ServiceError("ISSUE_NOT_FOUND", "Issue does not exist")
        if issue["state"] != "open":
            raise ServiceError("ISSUE_CLOSED", "Closed issues require human review; no write was made")
        if issue["version"] != args.expected_version:
            raise ServiceError("STALE_VERSION", "Issue changed; read it again and obtain approval for a fresh proposal")
        changed = True
        if tool == "update_labels":
            current = db.execute(
                "SELECT il.label,l.category FROM issue_labels il JOIN labels l ON l.name=il.label "
                "WHERE il.issue_id=?", (args.issue_id,),
            ).fetchall()
            for category in ("type", "priority"):
                old = {row["label"] for row in current if row["category"] == category}
                new = {label for label in args.labels if db.execute(
                    "SELECT 1 FROM labels WHERE name=? AND category=?", (label, category),
                ).fetchone()}
                if len(old | new) > 1:
                    raise ServiceError("INVALID_LABEL", "Existing or proposed classification conflicts; request human review")
            for label in args.labels:
                if db.execute("SELECT 1 FROM labels WHERE name=? AND category IN ('type','priority')",
                              (label,)).fetchone() is None:
                    raise ServiceError("INVALID_LABEL", "Label is not allowed in this sandbox")
            existing = {row["label"] for row in current}
            changed = bool(set(args.labels) - existing)
            db.executemany("INSERT OR IGNORE INTO issue_labels(issue_id,label) VALUES (?,?)",
                           [(args.issue_id, label) for label in args.labels])
        else:
            cursor = db.execute("INSERT INTO comments(issue_id,body,operation_id) VALUES (?,?,?)",
                                (args.issue_id, args.body, args.operation_id))
            comment = dict(db.execute(
                "SELECT id,body,operation_id,created_at FROM comments WHERE id=?", (cursor.lastrowid,),
            ).fetchone())
        version = issue["version"]
        if changed:
            updated = db.execute("UPDATE issues SET version=version+1 WHERE id=? AND version=?",
                                 (args.issue_id, args.expected_version))
            if updated.rowcount != 1:
                raise ServiceError("STALE_VERSION", "Issue changed before saving; obtain fresh approval")
            version += 1
        data = {"issue_id": args.issue_id, "version": version, "changed": changed}
        if tool == "update_labels":
            data["labels"] = [row[0] for row in db.execute(
                "SELECT label FROM issue_labels WHERE issue_id=? ORDER BY label", (args.issue_id,),
            )]
        else:
            data["comment"] = comment
        return {"ok": True, "data": data, "error": None}

    def _before_commit(self, db, tool, args):
        pass

    def _after_commit(self, tool, args):
        pass

    def read_issue(self, issue_id, *, context=None):
        from tools.read_issue import read_issue
        return read_issue(issue_id, self.db_path, context=context, logger=self.logger)

    def get_labels(self, *, context=None):
        from tools.get_labels import get_labels
        return get_labels(self.db_path, context=context, logger=self.logger)

    def list_issues(self, state="open", limit=20, *, context=None):
        from tools.list_issues import list_issues
        return list_issues(state, limit, self.db_path, context=context, logger=self.logger)
