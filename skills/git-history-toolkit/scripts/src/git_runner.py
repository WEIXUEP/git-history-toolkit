"""The only module that directly invokes the Git executable."""

from __future__ import annotations

import subprocess
from concurrent.futures import ThreadPoolExecutor
from itertools import islice
import os
from pathlib import Path


def audit_workers(value=0):
    if not 0 <= value <= 16:
        raise ValueError("workers must be between 0 (automatic) and 16")
    return value or min(4, os.cpu_count() or 1)


def query_batches(queries, workers):
    source = iter(queries)
    while batch := list(islice(source, workers)):
        yield batch


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

    def ref_state(self) -> tuple[str, str, str]:
        return (
            self.run("rev-parse", "HEAD").strip(),
            self.run("symbolic-ref", "-q", "HEAD", check=False).strip(),
            self.run("for-each-ref", "--format=%(refname) %(objectname)"),
        )

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

    def iter_queries(self, queries, workers=0):
        """Bound read-only query concurrency and return results in input order."""
        workers = audit_workers(workers)
        if workers == 1:
            for args in queries:
                yield self.run_with_status(*args)
            return
        with ThreadPoolExecutor(max_workers=workers, thread_name_prefix="git-audit") as pool:
            for batch in query_batches(queries, workers):
                pending = [pool.submit(self.run_with_status, *args) for args in batch]
                for future in pending:
                    yield future.result()

