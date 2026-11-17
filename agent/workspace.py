"""Isolated Git workspaces and narrow source-inspection operations."""
from __future__ import annotations

import hashlib
import os
import re
import shutil
import stat
import subprocess
import tempfile
from dataclasses import dataclass
from pathlib import Path, PurePosixPath


class WorkspaceError(RuntimeError):
    pass


SENSITIVE_PARTS = {
    ".git", ".env", ".ssh", "id_rsa", "id_ed25519", "credentials",
    "secrets", "secret", "private_key", "private-key",
}
SENSITIVE_SUFFIXES = {".pem", ".key", ".p12", ".pfx", ".jks", ".kdbx"}
ALLOWED_SUFFIXES = {
    ".py", ".md", ".txt", ".toml", ".ini", ".cfg", ".json", ".yaml",
    ".yml", ".js", ".ts", ".tsx", ".jsx", ".java", ".go", ".rs",
    ".c", ".h", ".cpp", ".hpp", ".sh", ".html", ".css",
}


def _run(args, *, cwd=None, env=None, timeout=120, check=True):
    completed = subprocess.run(
        args, cwd=cwd, env=env, text=True, capture_output=True, timeout=timeout,
        check=False,
    )
    if check and completed.returncode:
        message = (completed.stderr or completed.stdout or "command failed").strip()
        raise WorkspaceError(message[-1000:])
    return completed


def _safe_relative(value: str) -> PurePosixPath:
    if not isinstance(value, str) or not value or "\x00" in value or "\\" in value:
        raise WorkspaceError("unsafe repository path")
    path = PurePosixPath(value)
    if path.is_absolute() or any(part in ("", ".", "..") for part in path.parts):
        raise WorkspaceError("path must remain inside the workspace")
    lowered = {part.lower() for part in path.parts}
    if lowered & SENSITIVE_PARTS or any(part.startswith(".env") for part in lowered):
        raise WorkspaceError("sensitive paths are not model-readable")
    if path.suffix.lower() in SENSITIVE_SUFFIXES:
        raise WorkspaceError("sensitive paths are not model-readable")
    return path


@dataclass(frozen=True)
class WorkspaceIdentity:
    repository: str
    default_branch: str
    base_sha: str
    issue_number: int


class WorkspaceManager:
    def __init__(self, identity: WorkspaceIdentity, *, root=None):
        self.identity = identity
        self._owned_root = root is None
        self.root = Path(root or tempfile.mkdtemp(prefix="cs496-solve-")).resolve()
        self.repo = self.root / "repository"

    @staticmethod
    def _git_env():
        env = os.environ.copy()
        env.update({
            "GIT_TERMINAL_PROMPT": "0", "GIT_CONFIG_NOSYSTEM": "1",
            "GIT_CONFIG_GLOBAL": os.devnull,
        })
        return env

    def clone(self, *, token=None):
        if self.repo.exists():
            raise WorkspaceError("workspace repository already exists")
        self.root.mkdir(parents=True, exist_ok=True)
        env = self._git_env()
        askpass = None
        try:
            if token:
                askpass = self.root / ".git-askpass"
                askpass.write_text(
                    "#!/bin/sh\ncase \"$1\" in *Username*) echo x-access-token;; *) echo \"$GITHUB_TOKEN\";; esac\n",
                    encoding="utf-8",
                )
                askpass.chmod(stat.S_IRUSR | stat.S_IXUSR)
                env["GIT_ASKPASS"] = str(askpass)
                env["GITHUB_TOKEN"] = token
            _run([
                "git", "clone", "--no-checkout", "--filter=blob:none", "--",
                self.identity.repository, str(self.repo),
            ], env=env, timeout=180)
            origin = _run(["git", "remote", "get-url", "origin"], cwd=self.repo, env=env).stdout.strip().rstrip("/")
            if origin != self.identity.repository.rstrip("/"):
                raise WorkspaceError("cloned repository identity does not match configured repository")
            _run(["git", "checkout", "--detach", self.identity.base_sha], cwd=self.repo, env=env)
            actual = _run(["git", "rev-parse", "HEAD"], cwd=self.repo, env=env).stdout.strip()
            if actual != self.identity.base_sha:
                raise WorkspaceError("checkout does not match the requested base commit")
            return self
        finally:
            if askpass:
                askpass.unlink(missing_ok=True)
            env.pop("GITHUB_TOKEN", None)

    def _resolve(self, relative: str) -> Path:
        rel = _safe_relative(relative)
        target = (self.repo / Path(*rel.parts)).resolve(strict=False)
        try:
            target.relative_to(self.repo.resolve())
        except ValueError as error:
            raise WorkspaceError("path escapes the workspace") from error
        if target.exists() and target.is_symlink():
            raise WorkspaceError("symbolic links are not readable")
        return target

    def list_files(self, *, prefix="", limit=200):
        if prefix:
            base = self._resolve(prefix)
        else:
            base = self.repo
        if not base.exists() or not base.is_dir() or not 1 <= limit <= 500:
            raise WorkspaceError("invalid directory or limit")
        files = []
        for item in sorted(base.rglob("*")):
            if len(files) >= limit:
                break
            if item.is_symlink() or not item.is_file():
                continue
            rel = item.relative_to(self.repo).as_posix()
            try:
                _safe_relative(rel)
            except WorkspaceError:
                continue
            if item.suffix.lower() in ALLOWED_SUFFIXES or item.name in {"Dockerfile", "Makefile", "README"}:
                files.append(rel)
        return files

    def read_file(self, path: str, *, start_line=1, max_lines=200, max_bytes=30000):
        if not isinstance(start_line, int) or start_line < 1:
            raise WorkspaceError("invalid line range")
        if not 1 <= max_lines <= 400 or not 1 <= max_bytes <= 50000:
            raise WorkspaceError("read bounds exceeded")
        target = self._resolve(path)
        if not target.is_file() or target.suffix.lower() not in ALLOWED_SUFFIXES:
            raise WorkspaceError("file is not an allowed text source")
        raw = target.read_bytes()
        if len(raw) > 1_000_000 or b"\x00" in raw:
            raise WorkspaceError("file is binary or too large")
        lines = raw.decode("utf-8", errors="replace").splitlines()
        selected = lines[start_line - 1:start_line - 1 + max_lines]
        text = "\n".join(f"{start_line + index}: {line}" for index, line in enumerate(selected))
        return text[:max_bytes]

    def search(self, query: str, *, prefix="", limit=100):
        if not isinstance(query, str) or not query or len(query) > 200 or not 1 <= limit <= 200:
            raise WorkspaceError("invalid search query")
        # Literal search avoids model-supplied regular-expression denial of service.
        needle = query.casefold()
        matches = []
        for rel in self.list_files(prefix=prefix, limit=500):
            target = self._resolve(rel)
            try:
                lines = target.read_text(encoding="utf-8", errors="replace").splitlines()
            except OSError:
                continue
            for number, line in enumerate(lines, 1):
                if needle in line.casefold():
                    matches.append({"path": rel, "line": number, "text": line[:500]})
                    if len(matches) >= limit:
                        return matches
        return matches

    def apply_patch(self, patch: str, *, max_bytes=100_000, max_files=12):
        if not isinstance(patch, str) or not patch.strip() or len(patch.encode()) > max_bytes:
            raise WorkspaceError("patch is empty or exceeds the size limit")
        if "GIT binary patch" in patch or "Binary files " in patch:
            raise WorkspaceError("binary patches are prohibited")
        paths = re.findall(r"^\+\+\+ b/(.+)$", patch, flags=re.MULTILINE)
        old_paths = re.findall(r"^--- a/(.+)$", patch, flags=re.MULTILINE)
        if not paths or len(set(paths + old_paths)) > max_files:
            raise WorkspaceError("patch has no files or changes too many files")
        for path in paths + old_paths:
            _safe_relative(path)
            self._resolve(path)
        completed = subprocess.run(
            ["git", "apply", "--check", "--whitespace=error-all", "-"], cwd=self.repo,
            input=patch, text=True, capture_output=True,
        )
        if completed.returncode:
            raise WorkspaceError((completed.stderr or "invalid patch")[-1000:])
        applied = subprocess.run(
            ["git", "apply", "--whitespace=error-all", "-"], cwd=self.repo,
            input=patch, text=True, capture_output=True,
        )
        if applied.returncode:
            raise WorkspaceError((applied.stderr or "patch application failed")[-1000:])
        # Make newly created files visible to `git diff` without staging content.
        _run(["git", "add", "-N", "--", *sorted(set(paths))], cwd=self.repo)
        return self.diff()

    def diff(self):
        return _run(["git", "diff", "--no-ext-diff", "--binary", "--"], cwd=self.repo).stdout

    def changed_files(self):
        output = _run(["git", "diff", "--name-only", "--"], cwd=self.repo).stdout
        files = [line for line in output.splitlines() if line]
        for path in files:
            _safe_relative(path)
        return files

    def diff_hash(self):
        return hashlib.sha256(self.diff().encode()).hexdigest()

    def cleanup(self):
        if self._owned_root and self.root.exists():
            shutil.rmtree(self.root)

    def __enter__(self):
        return self

    def __exit__(self, *_exc):
        self.cleanup()
