"""Repository validation and read-only repository metadata."""

from __future__ import annotations

from pathlib import Path

from .git_runner import GitCommandError, GitRunner
from .models import RepositoryStats


class RepositoryError(RuntimeError):
    """Raised when a repository cannot safely be audited."""


class RepositoryInspector:
    def __init__(self, runner: GitRunner) -> None:
        self.runner = runner

    def validate(self) -> Path:
        if not self.runner.repo.exists():
            raise RepositoryError(f"repository path does not exist: {self.runner.repo}")
        if not self.runner.repo.is_dir():
            raise RepositoryError(f"repository path is not a directory: {self.runner.repo}")
        try:
            root = Path(self.runner.run("rev-parse", "--show-toplevel").strip()).resolve()
            self.runner.run("rev-parse", "--is-inside-work-tree")
            self.runner.run("rev-parse", "--verify", "HEAD")
        except GitCommandError as exc:
            raise RepositoryError(str(exc)) from exc
        if self.is_shallow():
            raise RepositoryError(
                "cannot perform a complete audit on a shallow repository; "
                "obtain a full clone first"
            )
        return root

    def is_shallow(self) -> bool:
        return self.runner.run("rev-parse", "--is-shallow-repository").strip().lower() == "true"

    def git_version(self) -> str:
        return self.runner.run("--version").strip()

    def worktree_clean(self) -> bool:
        return not bool(self.runner.run("status", "--porcelain", "--untracked-files=no").strip())

    def collect_stats(self) -> RepositoryStats:
        output = self.runner.run("count-objects", "-v")
        values: dict[str, str] = {}
        for line in output.splitlines():
            if ":" in line:
                key, value = line.split(":", 1)
                values[key.strip()] = value.strip()
        return RepositoryStats(values=values)

