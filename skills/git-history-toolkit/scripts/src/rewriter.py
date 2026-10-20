"""Explicit local history cleanup and optional, confirmed push."""

from __future__ import annotations

import logging
import json
import importlib.util
import subprocess
import sys
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
        self._push_url: str | None = None
        self._filter_repo_via_module = False

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
        self._ensure_cleanup_prerequisites()
        if result.ref_state is None or self.runner.ref_state() != result.ref_state:
            raise CleanupError("audit refs are stale or missing; audit again before cleanup")
        approved_state = self._repository_state()
        self._print_candidates(clean_candidates)
        self.emit(f"Repository: {self.runner.repo.resolve()}")
        self.emit(f"Backup destination: {result.output_dir / 'before_cleanup.bundle'}")
        if self.options.review_html:
            from .html_review import review_in_browser
            selected = review_in_browser(result, self.options, mode="cleanup", emit=self.emit)
            allowed = {blob.sha for blob in clean_candidates}
            if selected and (len(selected) != len(set(selected)) or not set(selected) <= allowed):
                raise CleanupError("Browser selection contains ineligible or unknown objects")
            approved = bool(selected)
            clean_candidates = [blob for blob in clean_candidates if selected and blob.sha in selected]
        else:
            approved = self._confirmed("Type CLEANUP to rewrite local history: ", "CLEANUP")
        if not approved:
            self.emit("Cleanup cancelled; Git history was not modified.")
            return
        self._ensure_safe_workspace(result)
        if self._repository_state() != approved_state:
            raise CleanupError("repository refs or HEAD changed during confirmation; audit again and reconfirm")

        if self.options.push_after_cleanup:
            self._push_branch = self._branch_name()
            self._push_lease_oid = self._remote_branch_oid(self._push_branch)

        ids_file = result.output_dir / "cleanup_blobs.txt"
        ids_file.write_text("".join(f"{blob.sha}\n" for blob in clean_candidates), encoding="ascii")
        backup_bundle, _refs_file = self._create_backup(result)
        before = TreeSnapshot.capture(self.runner)
        remote_configuration = self._remote_configuration()
        (result.output_dir / "before_cleanup_remote_config.json").write_text(
            json.dumps(remote_configuration, indent=2), encoding="utf-8"
        )

        try:
            self._run_filter_repo(ids_file)
            self._restore_remote_configuration(remote_configuration)
            after = TreeSnapshot.capture(self.runner)
            differences = before.differences(after)
            if differences:
                raise CleanupError(
                    "current HEAD tree changed after history rewrite; stop and recover from the backup in a separate clone. "
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
        self.emit(f"Destination: {self._push_url}")
        self.emit(f"  {command}")
        push_state = self._repository_state()
        if not self._confirmed("Type PUSH to execute this force-with-lease push: ", "PUSH"):
            self.emit("Push cancelled; local cleanup remains in place.")
            return
        self._ensure_safe_workspace(result)
        if self._repository_state() != push_state:
            raise CleanupError("repository changed during push confirmation; review again before pushing")
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

    def _confirmed(self, prompt: str, token: str) -> bool:
        try:
            return self.confirm(prompt).strip() == token
        except (EOFError, KeyboardInterrupt):
            return False

    def _repository_state(self) -> tuple:
        return (*self.runner.ref_state(), self._remote_configuration())

    def _ensure_cleanup_prerequisites(self) -> None:
        if not self.options.protect_head:
            raise CleanupError("cleanup requires HEAD protection; audit with --protect-head")
        if len(self.runner.run("worktree", "list", "--porcelain").split("worktree ")) > 2:
            raise CleanupError("cleanup with multiple linked worktrees is unsupported; use an independent clone")
        if not self.options.scan_gerrit_refs and self.runner.run("for-each-ref", "refs/changes"):
            raise CleanupError("local Gerrit refs exist but scanning was disabled; enable Gerrit scanning")
        status, _stdout, stderr = self.runner.run_with_status("filter-repo", "--version")
        if status != 0:
            if importlib.util.find_spec("git_filter_repo") is None:
                raise CleanupError(f"git-filter-repo is required before cleanup: {stderr.strip()}")
            probe = subprocess.run([sys.executable, "-m", "git_filter_repo", "--version"],
                                   capture_output=True, cwd=self.runner.repo)
            if probe.returncode:
                raise CleanupError("git-filter-repo module could not run in the current Python environment")
            self._filter_repo_via_module = True

    def _create_backup(self, result: AuditResult) -> tuple[Path, Path]:
        bundle = result.output_dir / "before_cleanup.bundle"
        refs_file = result.output_dir / "before_cleanup_refs.txt"
        self.runner.run("bundle", "create", str(bundle), "--all")
        self.runner.run("bundle", "verify", str(bundle))
        refs_file.write_text(self.runner.run("show-ref"), encoding="utf-8")
        return bundle, refs_file

    def _run_filter_repo(self, ids_file: Path) -> None:
        if self._filter_repo_via_module:
            result = subprocess.run([sys.executable, "-m", "git_filter_repo", "--force",
                                     "--strip-blobs-with-ids", str(ids_file)],
                                    cwd=self.runner.repo, capture_output=True)
            if result.returncode:
                raise CleanupError(result.stderr.decode("utf-8", "replace"))
            return
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

    def _remote_configuration(self) -> dict[str, list[str]]:
        status, output, _stderr = self.runner.run_with_status(
            "config", "--local", "--null", "--get-regexp", r"^(remote|branch)\."
        )
        if status not in (0, 1):
            raise CleanupError("cannot read local remote configuration")
        configuration: dict[str, list[str]] = {}
        for record in output.split("\x00"):
            if record:
                key, separator, value = record.partition("\n")
                if not separator:
                    raise CleanupError("invalid Git config record")
                configuration.setdefault(key, []).append(value)
        return configuration

    def _restore_remote_configuration(self, configuration: dict[str, list[str]]) -> None:
        current = self._remote_configuration()
        for key, values in configuration.items():
            if current.get(key) == values:
                continue
            self.runner.run("config", "--local", "--unset-all", key, check=False)
            for value in values:
                self.runner.run("config", "--local", "--add", key, value)

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
        urls = self.runner.run("remote", "get-url", "--push", "--all", self.options.push_remote).splitlines()
        if not self._push_lease_oid or urls != [self._push_url]:
            raise CleanupError("push destination changed or explicit remote lease is missing; review again")
        arguments = ["push"]
        arguments.append(f"--force-with-lease=refs/heads/{branch}:{self._push_lease_oid}")
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
        self.runner.run("check-ref-format", f"refs/heads/{branch}")
        urls = self.runner.run("remote", "get-url", "--push", "--all", self.options.push_remote).splitlines()
        if len(urls) != 1:
            raise CleanupError("combined push requires exactly one push URL; review multiple destinations manually")
        self._push_url = urls[0]
        ref = f"refs/heads/{branch}"
        status, stdout, _stderr = self.runner.run_with_status("ls-remote", "--exit-code", urls[0], ref)
        lines = [line.split() for line in stdout.splitlines()]
        if status != 0 or len(lines) != 1 or len(lines[0]) != 2 or lines[0][1] != ref:
            raise CleanupError("cannot establish the existing destination branch OID; no cleanup or push performed")
        return lines[0][0]

    def _print_candidates(self, blobs: list[BlobInfo]) -> None:
        self.emit("The following historical blobs will be removed from local rewritten history:")
        for blob in blobs:
            self.emit(
                f"  - {blob.sha}  {blob.primary_path or '(path unavailable)'}  "
                f"{blob.size_bytes} bytes"
            )
        self.emit("Gerrit patch-set candidates are excluded and require administrator review.")
