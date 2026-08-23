"""Find first/last commits that reference each large blob."""

from __future__ import annotations

from datetime import datetime, timezone

from .git_runner import GitRunner
from .models import BlobInfo, BlobHistoryInfo, RefInfo


LOG_FORMAT = "%H%x00%ct%x00%s"


class HistoryAnalyzer:
    def __init__(self, runner: GitRunner) -> None:
        self.runner = runner

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

        for blob in blobs:
            branch_history = self._find(blob.sha, branch_revisions)
            gerrit_history = self._find(blob.sha, gerrit_revisions) if gerrit_revisions else []
            history = self._merge(branch_history, gerrit_history)
            blob.first_seen_time = history.first_seen_time
            blob.last_seen_time = history.last_seen_time
            blob.first_seen_commit = history.first_seen_commit
            blob.last_seen_commit = history.last_seen_commit
            blob.last_commit_subject = history.last_commit_subject
            blob.commit_count = history.commit_count
            blob.branch_last_seen = self._latest(branch_history)
            blob.gerrit_last_seen = self._latest(gerrit_history)

    def _find(self, sha: str, revisions: list[str]) -> list[tuple[str, datetime, str]]:
        if not revisions:
            return []
        status, stdout, _stderr = self.runner.run_with_status(
            "log",
            f"--format={LOG_FORMAT}",
            f"--find-object={sha}",
            "--no-renames",
            *revisions,
            "--",
        )
        if status != 0:
            return []
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

