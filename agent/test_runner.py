"""Fail-closed, container-only execution of repository tests."""
from __future__ import annotations

import os
import shlex
import subprocess
import time
from dataclasses import dataclass, asdict
from pathlib import Path


class TestIsolationError(RuntimeError):
    pass


ALLOWED_TEST_PREFIXES = (
    ("python", "-m", "pytest"),
    ("python3", "-m", "pytest"),
    ("pytest",),
)


@dataclass(frozen=True)
class TestEvidence:
    __test__ = False
    command: tuple[str, ...]
    exit_code: int
    duration_seconds: float
    passed: bool
    output: str
    isolated: bool = True

    def to_dict(self):
        return asdict(self)


class ContainerTestRunner:
    def __init__(self, *, image=None, timeout=180, output_limit=30_000, runner=subprocess.run):
        self.image = image or os.environ.get("CS496_TEST_IMAGE")
        self.timeout = timeout
        self.output_limit = output_limit
        self._runner = runner

    @staticmethod
    def validate_command(command):
        parts = tuple(shlex.split(command) if isinstance(command, str) else command)
        if not parts or not any(parts[:len(prefix)] == prefix for prefix in ALLOWED_TEST_PREFIXES):
            raise TestIsolationError("test command is not allowlisted")
        forbidden = {";", "&&", "||", "|", ">", "<", "-c", "--pdb", "--trace"}
        if any(part in forbidden or ".." in part or part.startswith("/") for part in parts):
            raise TestIsolationError("unsafe test argument")
        return parts

    def run(self, workspace: Path, command):
        parts = self.validate_command(command)
        if not self.image:
            raise TestIsolationError(
                "CS496_TEST_IMAGE is not configured; refusing to execute repository code on the host"
            )
        workspace = Path(workspace).resolve()
        if not (workspace / ".git").is_dir():
            raise TestIsolationError("test workspace is not an isolated Git checkout")
        docker = [
            "docker", "run", "--rm", "--network", "none", "--read-only",
            "--cap-drop", "ALL", "--security-opt", "no-new-privileges",
            "--pids-limit", "256", "--memory", "1g", "--cpus", "1.5",
            "--tmpfs", "/tmp:rw,noexec,nosuid,size=256m",
            "--tmpfs", "/home/runner:rw,noexec,nosuid,size=64m",
            "-e", "PYTHONDONTWRITEBYTECODE=1", "-e", "PYTEST_ADDOPTS=-p no:cacheprovider",
            "-v", f"{workspace}:/workspace:ro", "-w", "/workspace", self.image,
            *parts,
        ]
        started = time.monotonic()
        try:
            result = self._runner(
                docker, text=True, capture_output=True, timeout=self.timeout, check=False,
            )
            output = ((result.stdout or "") + (result.stderr or ""))[-self.output_limit:]
            return TestEvidence(parts, result.returncode, time.monotonic() - started,
                                result.returncode == 0, output)
        except subprocess.TimeoutExpired as error:
            output = "".join(value.decode(errors="replace") if isinstance(value, bytes) else value or ""
                             for value in (error.stdout, error.stderr))[-self.output_limit:]
            return TestEvidence(parts, 124, time.monotonic() - started, False,
                                output + "\nTEST TIMEOUT")
