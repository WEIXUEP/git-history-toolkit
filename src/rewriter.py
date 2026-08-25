"""Explicit local history cleanup and optional, confirmed push."""

from __future__ import annotations

import logging
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

from .git_runner import GitCommandError, GitRunner
from .models import AuditResult, AuditorOptions, BlobInfo, CleanupAction

LOGGER = logging.getLogger(__name__)


class CleanupError(RuntimeError):
    """Raised when a cleanup safety check fails."""


@dataclass(frozen=True)
class TreeSnapshot:
    """The protected current tree before or after rewriting history."""

    tree_sha: str
    entries: tuple[tuple[str, str], ...]

    @classmethod
    def capture(cls, runner: GitRunner) -> "TreeSnapshot":
        tree_sha = runner.run("rev-parse", "HEAD^{tree}").strip()
        output = runner.run("ls-tree", "-r", "-z", "HEAD")
        entries: list[tuple[str, str]] = []
        for record in output.split("\x00"):
            if not record:
                continue
            header, separator, path = record.partition("\t")
            if separator:
                entries.append((path, header))
        return cls(tree_sha=tree_sha, entries=tuple(sorted(entries)))

    def differences(self, other: "TreeSnapshot") -> list[str]:
        if self.tree_sha == other.tree_sha and self.entries == other.entries:
            return []
        before = dict(self.entries)
        after = dict(other.entries)
        paths = sorted(set(before) | set(after))
        return [path for path in paths if before.get(path) != after.get(path)]


class GitHistoryRewriter:
    """Rewrite local history only after explicit interactive confirmation."""

    def __init__(
        self,
        runner: GitRunner,
        options: AuditorOptions,
        confirm: Callable[[str], str] = input,
        emit: Callable[[str], None] = print,
    ) -> None:
        self.runner = runner
        self.options = options
        self.confirm = confirm
        self.emit = emit
        self._push_branch: str | None = None
        self._push_lease_oid: str | None = None

    def execute(self, result: AuditResult) -> None:
        clean_candidates = [
            blob for blob in result.blobs if blob.action == CleanupAction.CLEAN_HISTORY
        ]
        if not clean_candidates:
            self.emit(
                "No automatic cleanup candidates. "
                "Gerrit candidates require administrator review."
            )
            return

        self._ensure_safe_workspace(result)
        self._print_candidates(clean_candidates)
        if self.confirm("Type CLEANUP to rewrite local history: ").strip() != "CLEANUP":
            self.emit("Cleanup cancelled; Git history was not modified.")
            return

        ids_file = result.output_dir / "cleanup_blobs.txt"
        ids_file.write_text("".join(f"{blob.sha}\n" for blob in clean_candidates), encoding="ascii")
        backup_bundle, refs_file = self._create_backup(result)
        before = TreeSnapshot.capture(self.runner)
        remote_url = self._remote_url()
        if self.options.push_after_cleanup:
            self._push_branch = self._branch_name()
            self._push_lease_oid = self._remote_branch_oid(self._push_branch)

        try:
            self._run_filter_repo(ids_file)
            self._restore_remote_if_needed(remote_url)
            after = TreeSnapshot.capture(self.runner)
            differences = before.differences(after)
            if differences:
                self._restore_refs(refs_file)
                raise CleanupError(
                    "current HEAD tree changed after history rewrite; refs were restored. "
                    f"Changed paths: {', '.join(differences[:10])}"
                )
        except (GitCommandError, CleanupError) as exc:
            raise CleanupError(
                f"cleanup failed; backup bundle is available at {backup_bundle}: {exc}"
            ) from exc

        self.emit("\nLocal history cleanup completed successfully.")
        self.emit(f"Backup bundle: {backup_bundle}")
        self.emit("Confirmed historical paths/blobs removed from rewritten history:")
        for blob in clean_candidates:
            self.emit(f"  - {blob.sha}  {blob.primary_path or '(path unavailable)'}")

        if not self.options.push_after_cleanup:
            self.emit("No push was executed.")
            self.emit("Review the repository, then push manually with the command shown below.")
            try:
                self.emit(f"  {self._push_command()}")
            except CleanupError:
                self.emit(
                    "  Push command unavailable because the repository is in detached HEAD state."
                )
            return

        command = self._push_command()
        self.emit("\nThe following push will replace the remote branch history:")
        self.emit(f"  {command}")
        if self.confirm("Type PUSH to execute this force-with-lease push: ").strip() != "PUSH":
            self.emit("Push cancelled; local cleanup remains in place.")
            return
        self._push()
        self.emit("Push completed.")

    def _ensure_safe_workspace(self, result: AuditResult) -> None:
        status = self.runner.run("status", "--porcelain")
        if status.strip():
            raise CleanupError(
                "cleanup requires a completely clean worktree, including untracked files"
            )
        try:
            result.output_dir.resolve().relative_to(self.runner.repo.resolve())
        except ValueError:
            return
        raise CleanupError(
            "--output-dir must be outside the repository for cleanup; "
            "otherwise generated reports become untracked worktree files"
        )

    def _create_backup(self, result: AuditResult) -> tuple[Path, Path]:
        bundle = result.output_dir / "before_cleanup.bundle"
        refs_file = result.output_dir / "before_cleanup_refs.txt"
        self.runner.run("bundle", "create", str(bundle), "--all")
        refs_file.write_text(self.runner.run("show-ref"), encoding="utf-8")
        return bundle, refs_file

    def _run_filter_repo(self, ids_file: Path) -> None:
        status, _stdout, stderr = self.runner.run_with_status("filter-repo", "--version")
        if status != 0:
            raise CleanupError(
                "git-filter-repo is required for cleanup but was not found. "
                f"Install it first: {stderr.strip()}"
            )
        self.runner.run(
            "filter-repo",
            "--force",
            "--strip-blobs-with-ids",
            str(ids_file),
        )

    def _remote_url(self) -> str | None:
        status, stdout, _stderr = self.runner.run_with_status(
            "remote", "get-url", "--push", self.options.push_remote
        )
        return stdout.strip() if status == 0 and stdout.strip() else None

    def _restore_remote_if_needed(self, remote_url: str | None) -> None:
        if not remote_url:
            return
        status, stdout, _stderr = self.runner.run_with_status(
            "remote", "get-url", self.options.push_remote
        )
        if status != 0:
            self.runner.run("remote", "add", self.options.push_remote, remote_url)
        elif stdout.strip() != remote_url:
            self.runner.run("remote", "set-url", self.options.push_remote, remote_url)

    def _restore_refs(self, refs_file: Path) -> None:
        original: dict[str, str] = {}
        for line in refs_file.read_text(encoding="utf-8").splitlines():
            oid, ref = line.split(" ", 1)
            original[ref] = oid
        current = self.runner.run("for-each-ref", "--format=%(refname)").splitlines()
        for ref in current:
            if ref not in original:
                self.runner.run("update-ref", "-d", ref)
        for ref, oid in original.items():
            self.runner.run("update-ref", ref, oid)

    def _push_command(self) -> str:
        branch = self._push_branch or self._branch_name()
        lease = (
            f"=refs/heads/{branch}:{self._push_lease_oid}"
            if self._push_lease_oid
            else ""
        )
        return (
            f"git push --force-with-lease{lease} {self.options.push_remote} "
            f"HEAD:refs/heads/{branch}"
        )

    def _push(self) -> None:
        branch = self._push_branch or self._branch_name()
        if not branch:
            raise CleanupError("cannot determine push branch from detached HEAD")
        status, _stdout, _stderr = self.runner.run_with_status(
            "remote", "get-url", self.options.push_remote
        )
        if status != 0:
            raise CleanupError(f"push remote does not exist: {self.options.push_remote}")
        arguments = ["push"]
        if self._push_lease_oid:
            arguments.append(f"--force-with-lease=refs/heads/{branch}:{self._push_lease_oid}")
        else:
            arguments.append("--force-with-lease")
        arguments.extend([self.options.push_remote, f"HEAD:refs/heads/{branch}"])
        self.runner.run(
            *arguments,
        )

    def _branch_name(self) -> str:
        branch = self.options.push_branch or self.runner.run(
            "symbolic-ref", "--short", "HEAD", check=False
        ).strip()
        if branch.startswith("refs/heads/"):
            branch = branch[len("refs/heads/") :]
        if not branch:
            raise CleanupError("cannot determine push branch from detached HEAD")
        return branch

    def _remote_branch_oid(self, branch: str) -> str | None:
        status, stdout, _stderr = self.runner.run_with_status(
            "rev-parse", "--verify", f"refs/remotes/{self.options.push_remote}/{branch}"
        )
        return stdout.strip() if status == 0 and stdout.strip() else None

    def _print_candidates(self, blobs: list[BlobInfo]) -> None:
        self.emit("The following historical blobs will be removed from local rewritten history:")
        for blob in blobs:
            self.emit(
                f"  - {blob.sha}  {blob.primary_path or '(path unavailable)'}  "
                f"{blob.size_bytes} bytes"
            )
        self.emit("Gerrit patch-set candidates are excluded and require administrator review.")
