"""Exact-approved Git/GitHub publication and controller-owned verification."""
from __future__ import annotations

import hashlib
import json
import stat
import uuid
from pathlib import Path

from agent.approval import ApprovalError, ApprovalManager
from agent.schemas import PublicationProposal, PublicationRequest
from agent.workspace import WorkspaceError, WorkspaceManager, _run
from tools.github_service import GitHubAPIError


class PublicationError(RuntimeError):
    def __init__(self, code, message, *, branch=None):
        super().__init__(message)
        self.code = code
        self.branch = branch


def evidence_hash(evidence):
    raw = json.dumps(evidence, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return hashlib.sha256(raw.encode()).hexdigest()


class PublicationService:
    def __init__(self, github_service, *, approvals=None):
        self.github = github_service
        self.client = github_service.client
        self.approvals = approvals or ApprovalManager()
        self.scope = github_service.scope

    def prepare(self, *, task_id, issue, workspace: WorkspaceManager, root_cause,
                test_evidence, limitations=()):
        files = tuple(workspace.changed_files())
        if not files:
            raise PublicationError("EMPTY_PATCH", "there are no changed files to publish")
        short_id = uuid.uuid4().hex[:8]
        branch = f"cs496/issue-{issue['id']}-{short_id}"
        tests = json.dumps(test_evidence, indent=2, ensure_ascii=False)
        limitation_text = "\n".join(f"- {item}" for item in limitations) or "- None reported"
        title = f"Fix #{issue['id']}: {issue['title']}"[:240]
        body = (
            f"Closes #{issue['id']}\n\n## Root cause\n{root_cause}\n\n"
            f"## Changes\n" + "\n".join(f"- `{name}`" for name in files) +
            f"\n\n## Test evidence\n```json\n{tests}\n```\n\n## Limitations\n{limitation_text}\n"
        )
        request = PublicationRequest(
            repository=self.scope, issue_number=issue["id"], issue_url=issue["source_url"],
            base_branch=workspace.identity.default_branch, base_sha=workspace.identity.base_sha,
            patch_hash=workspace.diff_hash(), test_evidence_hash=evidence_hash(test_evidence),
            changed_files=files, branch=branch,
            commit_message=f"Fix #{issue['id']}: {issue['title']}"[:200],
            pr_title=title, pr_body=body,
        )
        proposal = PublicationProposal(
            proposal_id="publish-" + uuid.uuid4().hex, task_id=task_id, request=request,
        )
        return self.approvals.register(proposal, self.scope)

    def publish(self, proposal, grant, workspace: WorkspaceManager, test_evidence):
        if not isinstance(proposal, PublicationProposal):
            raise PublicationError("APPROVAL_MISMATCH", "invalid publication proposal")
        try:
            self.approvals.authorize(proposal, self.scope, grant)
        except ApprovalError as error:
            raise PublicationError(error.code, str(error)) from error
        request = proposal.request
        if workspace.identity.repository != request.repository:
            raise PublicationError("APPROVAL_MISMATCH", "repository changed after approval")
        if workspace.identity.base_sha != request.base_sha or workspace.diff_hash() != request.patch_hash:
            raise PublicationError("STALE_APPROVAL", "base commit or patch changed after approval")
        if tuple(workspace.changed_files()) != request.changed_files:
            raise PublicationError("STALE_APPROVAL", "changed-file set changed after approval")
        if evidence_hash(test_evidence) != request.test_evidence_hash:
            raise PublicationError("STALE_APPROVAL", "test evidence changed after approval")

        metadata = self.client.request("GET", "")
        if metadata.get("default_branch") != request.base_branch:
            raise PublicationError("STALE_BASE", "repository default branch changed")
        permissions = metadata.get("permissions") or {}
        if permissions.get("push") is not True:
            raise PublicationError("GITHUB_AUTHORIZATION", "token lacks repository Contents write permission")
        ref_path = "/git/ref/heads/" + request.base_branch.replace("/", "%2F")
        remote_ref = self.client.request("GET", ref_path)
        remote_sha = ((remote_ref.get("object") or {}).get("sha"))
        if remote_sha != request.base_sha:
            raise PublicationError("STALE_BASE", "default branch advanced after approval")
        if request.branch in {request.base_branch, "main", "master"}:
            raise PublicationError("UNSAFE_BRANCH", "refusing to publish to a default branch")

        repo = workspace.repo
        _run(["git", "checkout", "-b", request.branch, request.base_sha], cwd=repo)
        _run(["git", "reset"], cwd=repo)
        _run(["git", "add", "--", *request.changed_files], cwd=repo)
        staged = _run(["git", "diff", "--cached", "--no-ext-diff", "--binary", "--"], cwd=repo).stdout
        if hashlib.sha256(staged.encode()).hexdigest() != request.patch_hash:
            raise PublicationError("APPROVAL_MISMATCH", "staged patch differs from approved patch")
        _run([
            "git", "-c", "core.hooksPath=/dev/null", "-c", "user.name=CS496 Issue Agent",
            "-c", "user.email=cs496-agent@users.noreply.github.com", "commit", "--no-gpg-sign",
            "-m", request.commit_message,
        ], cwd=repo)
        commit_sha = _run(["git", "rev-parse", "HEAD"], cwd=repo).stdout.strip()
        self._push(repo, request.branch, commit_sha)

        try:
            created = self.client.request("POST", "/pulls", {
                "title": request.pr_title, "body": request.pr_body,
                "head": request.branch, "base": request.base_branch, "draft": True,
            })
        except GitHubAPIError as error:
            existing = self.client.request(
                "GET", f"/pulls?state=open&head={self.client.owner}%3A{request.branch}",
            )
            if not existing:
                raise PublicationError("PARTIAL_FAILURE", "branch pushed but draft PR creation failed",
                                       branch=request.branch) from error
            created = existing[0]
        number = created.get("number")
        verified = self.client.request("GET", f"/pulls/{number}")
        head = verified.get("head") or {}
        base = verified.get("base") or {}
        if not (verified.get("draft") is True and head.get("ref") == request.branch
                and head.get("sha") == commit_sha and base.get("ref") == request.base_branch):
            raise PublicationError("VERIFICATION_FAILED", "draft PR read-back did not match approved publication",
                                   branch=request.branch)
        self.approvals.consume(proposal.proposal_id)
        return {
            "pr_number": number, "pr_url": verified.get("html_url"), "draft": True,
            "branch": request.branch, "base_branch": request.base_branch,
            "commit_sha": commit_sha, "verified": True,
        }

    def _push(self, repo: Path, branch: str, commit_sha: str):
        token = getattr(self.client, "token", None)
        env = WorkspaceManager._git_env()
        askpass = repo.parent / ".push-askpass"
        try:
            if token:
                askpass.write_text(
                    "#!/bin/sh\ncase \"$1\" in *Username*) echo x-access-token;; *) echo \"$GITHUB_TOKEN\";; esac\n",
                    encoding="utf-8",
                )
                askpass.chmod(stat.S_IRUSR | stat.S_IXUSR)
                env["GIT_ASKPASS"] = str(askpass)
                env["GITHUB_TOKEN"] = token
            _run(["git", "push", "--porcelain", "origin", f"refs/heads/{branch}:refs/heads/{branch}"],
                 cwd=repo, env=env, timeout=180)
        except WorkspaceError as error:
            # A timeout can occur after GitHub accepted the ref. Reconcile with a
            # fresh API read before declaring failure or allowing a retry.
            try:
                encoded = branch.replace("/", "%2F")
                remote = self.client.request("GET", f"/git/ref/heads/{encoded}")
                if ((remote.get("object") or {}).get("sha")) == commit_sha:
                    return
            except Exception:
                pass
            raise PublicationError("PUSH_FAILED", "safe branch push failed and remote ref was not verified",
                                   branch=branch) from error
        finally:
            askpass.unlink(missing_ok=True)
            env.pop("GITHUB_TOKEN", None)
