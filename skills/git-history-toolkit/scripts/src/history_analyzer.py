"""Find first/last commits that reference each large blob."""

from __future__ import annotations

from datetime import datetime, timezone
from contextlib import closing

from .git_runner import GitCommandError, GitRunner, audit_workers
from .models import BlobInfo, BlobHistoryInfo, RefInfo


LOG_FORMAT = "%H%x00%ct%x00%s"


class HistoryAnalyzer:
    def __init__(self, runner: GitRunner, progress=None, workers=0) -> None:
        self.runner = runner
        self.progress = progress or (lambda *args, **kwargs: None)
        self.workers = audit_workers(workers)

    def analyze(
        self,
        blobs: list[BlobInfo],
        refs: list[RefInfo],
        gerrit_refs: list[RefInfo],
    ) -> None:
        branch_revisions = [ref.name for ref in refs if ref.kind != "gerrit"]
        if not branch_revisions:
            branch_revisions = ["HEAD"]
        gerrit_revisions = [ref.name for ref in gerrit_refs]

        queries = (("log", f"--format={LOG_FORMAT}", f"--find-object={blob.sha}",
                    "--no-renames", *revisions, "--")
                   for blob in blobs for revisions in (branch_revisions, gerrit_revisions) if revisions)
        self.progress("Object history", 0, len(blobs),
                      f"Searching additions, replacements and deletions; up to {self.workers} Git queries in parallel")
        with closing(self.runner.iter_queries(queries, self.workers)) as results:
            for index, blob in enumerate(blobs):
                branch_history = self._records(next(results))
                gerrit_history = self._records(next(results)) if gerrit_revisions else []
                history = self._merge(branch_history, gerrit_history)
                blob.first_seen_time = history.first_seen_time
                blob.last_seen_time = history.last_seen_time
                blob.first_seen_commit = history.first_seen_commit
                blob.last_seen_commit = history.last_seen_commit
                blob.last_commit_subject = history.last_commit_subject
                blob.commit_count = history.commit_count
                blob.branch_last_seen = self._latest(branch_history)
                blob.gerrit_last_seen = self._latest(gerrit_history)
                self.progress("Object history", index + 1, len(blobs), f"Searched object {blob.sha[:12]}")

    @staticmethod
    def _records(result) -> list[tuple[str, datetime, str]]:
        status, stdout, stderr = result
        if status != 0:
            raise GitCommandError(["log", "--find-object"], status, stderr)
        records: list[tuple[str, datetime, str]] = []
        for line in stdout.splitlines():
            fields = line.split("\x00", 2)
            if len(fields) != 3:
                continue
            commit, timestamp, subject = fields
            try:
                date = datetime.fromtimestamp(int(timestamp), tz=timezone.utc)
            except ValueError:
                continue
            records.append((commit, date, subject))
        return records

    @staticmethod
    def _latest(records: list[tuple[str, datetime, str]]) -> datetime | None:
        return max((record[1] for record in records), default=None)

    @staticmethod
    def _merge(
        branch: list[tuple[str, datetime, str]], gerrit: list[tuple[str, datetime, str]]
    ) -> BlobHistoryInfo:
        records = branch + gerrit
        if not records:
            return BlobHistoryInfo()
        first = min(records, key=lambda item: item[1])
        last = max(records, key=lambda item: item[1])
        return BlobHistoryInfo(
            first_seen_time=first[1],
            last_seen_time=last[1],
            first_seen_commit=first[0],
            last_seen_commit=last[0],
            last_commit_subject=last[2],
            commit_count=len({record[0] for record in records}),
        )

