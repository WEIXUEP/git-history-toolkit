"""Apply the ordered, conservative cleanup classification policy."""

from __future__ import annotations

from datetime import datetime, timezone

from .models import AuditorOptions, BlobInfo, BlobStatus, CleanupAction
from .policy import PathPolicy


class BlobClassifier:
    def __init__(self, options: AuditorOptions, audit_time: datetime | None = None) -> None:
        self.options = options
        self.audit_time = audit_time or datetime.now(timezone.utc)
        self.path_policy = PathPolicy(options)

    def classify(self, blobs: list[BlobInfo]) -> list[BlobInfo]:
        for blob in blobs:
            if blob.is_current:
                blob.status = BlobStatus.CURRENT
                blob.eligible = False
                blob.action = CleanupAction.KEEP
                blob.is_protected = True
                blob.protection_reason = "present in a protected current ref tree"
                continue

            if self.path_policy.is_explicitly_protected(blob):
                blob.status = BlobStatus.PROTECTED
                blob.eligible = False
                blob.action = CleanupAction.KEEP
                blob.is_protected = True
                blob.protection_reason = "matched --keep-path or --keep-blob"
                continue

            age_days = blob.age_days(self.audit_time)
            if age_days is None:
                blob.status = BlobStatus.TOO_NEW
                blob.eligible = False
                blob.action = CleanupAction.NO_ACTION
                blob.candidate_reason = "no complete history timestamp was found"
                continue
            if age_days < self.options.min_age_days:
                blob.status = BlobStatus.TOO_NEW
                blob.eligible = False
                blob.action = CleanupAction.NO_ACTION
                blob.candidate_reason = (
                    f"last seen {age_days} days ago, below the {self.options.min_age_days}-day threshold"
                )
                continue

            blob.eligible = True
            if blob.gerrit_refs:
                blob.status = BlobStatus.GERRIT_PATCHSET_ONLY
                blob.action = CleanupAction.GERRIT_ADMIN_REVIEW
                blob.candidate_reason = (
                    "not present in protected current trees, but reachable from local Gerrit refs"
                )
            elif blob.has_current_path_version:
                blob.status = BlobStatus.OLD_VERSION
                blob.action = CleanupAction.CLEAN_HISTORY
                blob.candidate_reason = "historical blob for a path that still exists with newer content"
            else:
                blob.status = BlobStatus.DELETED_PATH
                blob.action = CleanupAction.CLEAN_HISTORY
                blob.candidate_reason = "all known paths are absent from protected current trees"
        return blobs
