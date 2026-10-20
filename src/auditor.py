"""Top-level orchestration for the read-only audit."""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from pathlib import Path

from .blob_scanner import BlobScanner
from .classifier import BlobClassifier
from .current_tree_analyzer import CurrentTreeAnalyzer
from .gerrit_analyzer import GerritAnalyzer
from .git_runner import GitRunner
from .history_analyzer import HistoryAnalyzer
from .models import AuditResult, AuditorOptions
from .policy import PathPolicy
from .ref_analyzer import RefAnalyzer
from .reporter import AuditReporter
from .repository import RepositoryInspector

LOGGER = logging.getLogger(__name__)


class GitLargeObjectAuditor:
    """Coordinate scanning, analysis, classification, and report generation."""

    def __init__(self, args) -> None:
        self.options = AuditorOptions.from_args(args)
        self.runner = GitRunner(self.options.repo)

    def run(self) -> AuditResult:
        from .audit_cache import AuditCache, CachedGitRunner
        root = RepositoryInspector(self.runner).validate()
        self.runner.repo = root
        raw = self.runner
        cache = AuditCache(raw, self.options)
        state = raw.ref_state()
        self.runner = CachedGitRunner(raw, cache, state)
        try:
            return self._run(cache, state)
        finally:
            self.runner = raw
            cache.close()

    def _run(self, cache, expected_state) -> AuditResult:
        audit_time = datetime.now(timezone.utc)
        repository = RepositoryInspector(self.runner)
        root = repository.validate()
        self.runner.repo = root
        ref_state = self.runner.ref_state()
        if ref_state != expected_state:
            from .repository import RepositoryError
            raise RepositoryError("Refs changed before cached analysis; rerun the audit")
        if root != self.options.repo:
            LOGGER.info("Using Git worktree root: %s", root)

        stats = repository.collect_stats()
        refs = RefAnalyzer(self.runner).list_refs()
        current_usage, current_paths, coverage = CurrentTreeAnalyzer(self.runner).collect(
            refs,
            protect_head=self.options.protect_head,
            protect_local_branches=self.options.protect_local_branches,
            protect_remote_branches=self.options.protect_remote_branches,
            protect_tags=self.options.protect_tags,
        )

        LOGGER.info("Scanning reachable Git objects and filtering large blobs")
        path_policy = PathPolicy(self.options)
        blobs = [
            blob
            for blob in BlobScanner(self.runner, cache).scan(self.options.min_size_bytes)
            if path_policy.is_in_scope(blob)
        ]
        blob_map = {blob.sha: blob for blob in blobs}
        self._apply_current_usage(blobs, current_usage, current_paths)

        gerrit_refs = [ref for ref in refs if ref.kind == "gerrit"]
        if self.options.scan_gerrit_refs:
            GerritAnalyzer(self.runner).analyze(refs, blob_map, coverage)
        else:
            coverage.warnings.append("Gerrit ref scanning was disabled by command-line option.")

        LOGGER.info("Analyzing history for %d large blobs", len(blobs))
        HistoryAnalyzer(self.runner).analyze(blobs, refs, gerrit_refs if self.options.scan_gerrit_refs else [])
        BlobClassifier(self.options, audit_time).classify(blobs)

        output_dir = self._new_output_dir(root.name, audit_time)
        if self.runner.ref_state() != ref_state:
            from .repository import RepositoryError
            raise RepositoryError("repository refs changed during the audit; rerun with no concurrent Git operations")
        result = AuditResult(
            repository=root,
            audit_time=audit_time,
            blobs=sorted(blobs, key=lambda blob: (-blob.size_bytes, blob.sha)),
            output_dir=output_dir,
            repo_stats_before=stats,
            coverage=coverage,
            min_size_bytes=self.options.min_size_bytes,
            min_age_days=self.options.min_age_days,
            warnings=[] if repository.worktree_clean() else ["Worktree is dirty; the audit itself is read-only."],
            ref_state=ref_state,
            cache_info=cache.info(),
        )
        result.report_files = AuditReporter().write(result)
        return result

    @staticmethod
    def _apply_current_usage(blobs, current_usage, current_paths) -> None:
        for blob in blobs:
            refs = current_usage.get(blob.sha, set())
            blob.current_refs.update(refs)
            blob.is_current = bool(refs)
            blob.has_current_path_version = False
            for path in current_paths.get(blob.sha, set()):
                blob.paths.add(path)
            # A path is an old-version candidate when the current tree contains
            # the same path but maps it to a different blob.
        path_to_current_sha: dict[str, str] = {}
        for sha, paths in current_paths.items():
            for path in paths:
                path_to_current_sha[path] = sha
        for blob in blobs:
            blob.has_current_path_version = any(
                path in path_to_current_sha
                for path in blob.paths
                if path in path_to_current_sha and path_to_current_sha[path] != blob.sha
            )

        for blob in blobs:
            for ref in blob.current_refs:
                if ref.startswith("refs/heads/"):
                    blob.branch_refs.add(ref)
                elif ref.startswith("refs/remotes/"):
                    blob.remote_refs.add(ref)
                elif ref.startswith("refs/tags/"):
                    blob.tag_refs.add(ref)

    def _new_output_dir(self, repo_name: str, audit_time: datetime) -> Path:
        safe_name = "".join(char if char.isalnum() or char in "._-" else "_" for char in repo_name)
        root = self.options.output_dir
        name = f"{safe_name}_{audit_time.strftime('%Y%m%d_%H%M%S')}"
        candidate = root / name
        suffix = 1
        while candidate.exists():
            candidate = root / f"{name}_{suffix:02d}"
            suffix += 1
        return candidate


def audit_git_repository(args) -> AuditResult:
    """Convenience function for programmatic callers."""
    return GitLargeObjectAuditor(args).run()
