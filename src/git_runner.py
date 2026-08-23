"""The only module that directly invokes the Git executable."""

from __future__ import annotations

import subprocess
from pathlib import Path


class GitCommandError(RuntimeError):
    """Raised when a Git command fails."""

    def __init__(self, args: list[str], returncode: int, stderr: str = "") -> None:
        self.args = args
        self.returncode = returncode
        self.stderr = stderr.strip()
        command = "git " + " ".join(args)
        detail = f": {self.stderr}" if self.stderr else ""
        super().__init__(f"Git command failed ({returncode}): {command}{detail}")


class GitRunner:
    """Small, testable wrapper around subprocess Git calls."""

    def __init__(self, repo: Path) -> None:
        self.repo = repo

    def run(self, *args: str, check: bool = True) -> str:
        command = [str(arg) for arg in args]
        completed = subprocess.run(
            ["git", *command],
            cwd=self.repo,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            encoding="utf-8",
            errors="surrogateescape",
            check=False,
        )
        if check and completed.returncode != 0:
            raise GitCommandError(command, completed.returncode, completed.stderr)
        return completed.stdout

    def run_with_status(self, *args: str) -> tuple[int, str, str]:
        command = [str(arg) for arg in args]
        completed = subprocess.run(
            ["git", *command],
            cwd=self.repo,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            encoding="utf-8",
            errors="surrogateescape",
            check=False,
        )
        return completed.returncode, completed.stdout, completed.stderr

