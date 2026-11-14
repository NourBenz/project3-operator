from __future__ import annotations

import hashlib
import json
import os
import sqlite3
import urllib.error
import urllib.parse
import urllib.request
import uuid
from pathlib import Path

from dotenv import load_dotenv

from agent.approval import ApprovalError, ApprovalManager
from agent.logging_utils import CallContext, ToolLogger, logged_call
from agent.schemas import PostCommentArgs, UpdateLabelsArgs, WriteProposal
from sandbox.taxonomy import LABEL_DETAILS
from tools.common import failure
from tools.notifications import NotificationError, OwnerNotifier


class GitHubAPIError(Exception):
    def __init__(self, code, message, retryable=False):
        super().__init__(message)
        self.code = code
        self.retryable = retryable


def _version(updated_at):
    return int(hashlib.sha256(str(updated_at).encode()).hexdigest()[:12], 16) or 1


class GitHubClient:
    def __init__(self, repository, token=None, *, opener=None):
        load_dotenv()
        self.repository = repository.strip().rstrip("/")
        parsed = urllib.parse.urlparse(self.repository)
        parts = [part for part in parsed.path.split("/") if part]
        if parsed.netloc not in ("github.com", "www.github.com") or len(parts) < 2:
            raise ValueError("repository must be a GitHub URL such as https://github.com/owner/repo")
        self.owner, self.repo = parts[0], parts[1].removesuffix(".git")
        self.token = token or os.environ.get("GITHUB_TOKEN")
        if not self.token:
            raise GitHubAPIError("CONFIGURATION_ERROR", "GITHUB_TOKEN is not configured")
        self.base_url = f"https://api.github.com/repos/{self.owner}/{self.repo}"
        self.opener = opener or urllib.request.urlopen

    def request(self, method, path, payload=None):
        body = None if payload is None else json.dumps(payload).encode()
        request = urllib.request.Request(
            self.base_url + path,
            data=body,
            method=method,
            headers={
                "Accept": "application/vnd.github+json",
                "Authorization": f"Bearer {self.token}",
                "X-GitHub-Api-Version": "2022-11-28",
                "Content-Type": "application/json",
            },
        )
        try:
            with self.opener(request, timeout=20) as response:
                raw = response.read()
                return json.loads(raw.decode()) if raw else {}
        except urllib.error.HTTPError as error:
            retryable = error.code in (408, 409, 429) or error.code >= 500
            if error.code == 404:
                code = "ISSUE_NOT_FOUND"
            elif error.code in (401, 403):
                code = "GITHUB_AUTHORIZATION"
            elif error.code == 422:
                code = "INVALID_ARGUMENTS"
            else:
                code = "GITHUB_API_ERROR"
            raise GitHubAPIError(code, f"GitHub API request failed ({error.code})", retryable) from error
        except (urllib.error.URLError, TimeoutError) as error:
            raise GitHubAPIError("TOOL_UNAVAILABLE", "GitHub API is temporarily unavailable", True) from error


class GitHubService:
    def __init__(self, repository, *, token=None, ledger_path=None, approvals=None, logger=None, client=None, notifier=None):
        self.client = client or GitHubClient(repository, token)
        self.scope = self.client.repository
        self.approvals = approvals or ApprovalManager()
        self.logger = logger or ToolLogger()
        self.notifier = notifier or OwnerNotifier()
        self.ledger_path = Path(ledger_path or "sandbox/data/github_operations.sqlite3").resolve()
        self.ledger_path.parent.mkdir(parents=True, exist_ok=True)
        with sqlite3.connect(self.ledger_path) as db:
            db.execute("CREATE TABLE IF NOT EXISTS operations (operation_id TEXT PRIMARY KEY, action TEXT NOT NULL, request_hash TEXT NOT NULL, outcome TEXT NOT NULL)")

    def propose(self, tool, arguments, *, task_id):
        model = UpdateLabelsArgs if tool == "update_labels" else PostCommentArgs
        args = model.model_validate(arguments)
        return self.approvals.register(
            WriteProposal(proposal_id=uuid.uuid4().hex, task_id=task_id,
                          request={"tool": tool, "args": args.model_dump(mode="json")}),
            self.scope, audit=self.logger.emit,
        )

    def list_issues(self, state="open", limit=20, *, context=None):
        def run():
            if state not in ("open", "closed", "all") or not 1 <= int(limit) <= 50:
                return failure("INVALID_ARGUMENTS", "state or limit is invalid")
            params = urllib.parse.urlencode({"state": state, "per_page": int(limit), "page": 1})
            items = self.client.request("GET", "/issues?" + params)
            return {"ok": True, "data": {"issues": [self._summary(item) for item in items if "pull_request" not in item]}, "error": None}
        return logged_call("list_issues", {"state": state, "limit": limit}, run, context=context, logger=self.logger)

    def read_issue(self, issue_id, *, context=None):
        def run():
            try:
                item = self.client.request("GET", f"/issues/{int(issue_id)}")
                comments = self.client.request("GET", f"/issues/{int(issue_id)}/comments?per_page=100")
                return {"ok": True, "data": self._detail(item, comments), "error": None}
            except (ValueError, GitHubAPIError) as error:
                return failure(getattr(error, "code", "INVALID_ARGUMENTS"), str(error), getattr(error, "retryable", False))
        return logged_call("read_issue", {"issue_id": issue_id}, run, context=context, logger=self.logger)

    def get_labels(self, *, context=None):
        def run():
            try:
                labels = self.client.request("GET", "/labels?per_page=100")
                taxonomy = []
                for item in labels:
                    name = item["name"]
                    category, description = LABEL_DETAILS.get(name, ("other", item.get("description") or "Repository label"))
                    taxonomy.append({"name": name, "category": category, "description": description})
                return {"ok": True, "data": {"labels": [item["name"] for item in labels], "taxonomy": taxonomy}, "error": None}
            except GitHubAPIError as error:
                return failure(error.code, str(error), error.retryable)
        return logged_call("get_labels", {}, run, context=context, logger=self.logger)

    def call(self, tool, arguments, *, task_id="standalone", proposal_id=None, grant=None, context=None):
        if tool == "list_issues":
            return self.list_issues(**arguments, context=context)
        if tool == "read_issue":
            return self.read_issue(**arguments, context=context)
        if tool == "get_labels":
            return self.get_labels(context=context)
        if tool not in ("update_labels", "post_comment"):
            return failure("UNKNOWN_TOOL", "Tool is not allowed")
        if proposal_id is None:
            return failure("APPROVAL_REQUIRED", "Human approval is required")
        try:
            model = UpdateLabelsArgs if tool == "update_labels" else PostCommentArgs
            args = model.model_validate(arguments)
            proposal = WriteProposal(proposal_id=proposal_id, task_id=task_id,
                                     request={"tool": tool, "args": args.model_dump(mode="json")})
            self.approvals.authorize(proposal, self.scope, grant)
            payload = {"task_id": task_id, "tool": tool, "args": args.model_dump(mode="json")}
            request_hash = hashlib.sha256(json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()).hexdigest()
            with sqlite3.connect(self.ledger_path) as db:
                saved = db.execute("SELECT action,request_hash,outcome FROM operations WHERE operation_id=?", (args.operation_id,)).fetchone()
                if saved:
                    if saved[0] != tool or saved[1] != request_hash:
                        return failure("DUPLICATE_OPERATION", "Operation ID was used for another request")
                    return json.loads(saved[2])
                result = self._write(tool, args)
                if result["ok"]:
                    db.execute("INSERT INTO operations VALUES (?,?,?,?)", (args.operation_id, tool, request_hash, json.dumps(result)))
            self.approvals.consume(proposal_id)
            if result["ok"] and result["data"].get("changed"):
                try:
                    self.notifier.notify(repository=self.scope, issue_number=args.issue_id,
                                        action=tool, summary=json.dumps(result["data"], ensure_ascii=False))
                except NotificationError:
                    return failure("PARTIAL_FAILURE", "GitHub change committed but owner notification failed; verify the change", True)
            return result
        except ApprovalError as error:
            return failure(error.code, str(error))
        except GitHubAPIError as error:
            return failure(error.code, str(error), error.retryable)
        except (TypeError, ValueError) as error:
            return failure("INVALID_ARGUMENTS", str(error))

    def update_labels(self, issue_id, labels, operation_id, expected_version, *, task_id="standalone", proposal_id=None, grant=None, context=None, **extra):
        return self.call("update_labels", {"issue_id": issue_id, "labels": labels, "operation_id": operation_id, "expected_version": expected_version, **extra}, task_id=task_id, proposal_id=proposal_id, grant=grant, context=context)

    def post_comment(self, issue_id, body, operation_id, expected_version, *, task_id="standalone", proposal_id=None, grant=None, context=None, **extra):
        return self.call("post_comment", {"issue_id": issue_id, "body": body, "operation_id": operation_id, "expected_version": expected_version, **extra}, task_id=task_id, proposal_id=proposal_id, grant=grant, context=context)

    def _write(self, tool, args):
        issue = self.client.request("GET", f"/issues/{args.issue_id}")
        current_version = _version(issue.get("updated_at"))
        if issue.get("state") != "open":
            return failure("ISSUE_CLOSED", "Closed issues cannot be modified")
        if current_version != args.expected_version:
            return failure("STALE_VERSION", "Issue changed; read it again and obtain fresh approval")
        if tool == "update_labels":
            current = [label["name"] for label in issue.get("labels", [])]
            labels = list(dict.fromkeys(current + list(args.labels)))
            if labels == current:
                return {"ok": True, "data": {"issue_id": args.issue_id, "version": current_version, "changed": False, "labels": current}, "error": None}
            self.client.request("PUT", f"/issues/{args.issue_id}/labels", {"labels": labels})
            updated = self.client.request("GET", f"/issues/{args.issue_id}")
            return {"ok": True, "data": {"issue_id": args.issue_id, "version": _version(updated.get("updated_at")), "changed": True, "labels": labels}, "error": None}
        comment = self.client.request("POST", f"/issues/{args.issue_id}/comments", {"body": args.body})
        updated = self.client.request("GET", f"/issues/{args.issue_id}")
        return {"ok": True, "data": {"issue_id": args.issue_id, "version": _version(updated.get("updated_at")), "changed": True, "comment": {"id": comment.get("id"), "body": comment.get("body"), "operation_id": args.operation_id}}, "error": None}

    @staticmethod
    def _summary(item):
        return {"id": item["number"], "title": item["title"], "state": item["state"], "version": _version(item.get("updated_at")), "labels": [label["name"] for label in item.get("labels", [])]}

    @staticmethod
    def _detail(item, comments):
        return {"id": item["number"], "title": item["title"], "body": item.get("body") or "", "state": item["state"], "version": _version(item.get("updated_at")), "source_url": item.get("html_url"), "retrieved_at": item.get("updated_at"), "source_kind": "github", "labels": [label["name"] for label in item.get("labels", [])], "comments": [{"id": c.get("id"), "body": c.get("body") or "", "operation_id": None, "created_at": c.get("created_at")} for c in comments]}
