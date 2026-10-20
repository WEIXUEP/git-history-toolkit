"""Data models used throughout the audit pipeline."""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import datetime
from enum import Enum
from pathlib import Path
from typing import Any


class BlobStatus(str, Enum):
    """Describes what was observed about a blob."""

    CURRENT = "CURRENT"
    PROTECTED = "PROTECTED"
    OLD_VERSION = "OLD_VERSION"
    DELETED_PATH = "DELETED_PATH"
    GERRIT_PATCHSET_ONLY = "GERRIT_PATCHSET_ONLY"
    TOO_SMALL = "TOO_SMALL"
    TOO_NEW = "TOO_NEW"
    CANDIDATE = "CANDIDATE"


class CleanupAction(str, Enum):
    """Recommended action; no action is performed automatically."""

    KEEP = "KEEP"
    NO_ACTION = "NO_ACTION"
    CLEAN_HISTORY = "CLEAN_HISTORY"
    GERRIT_ADMIN_REVIEW = "GERRIT_ADMIN_REVIEW"


@dataclass(frozen=True)
class AuditorOptions:
    """Validated, immutable options used by internal modules."""

    repo: Path
    min_size_mb: float = 100.0
    min_age_days: int = 180
    protect_head: bool = True
    protect_local_branches: bool = True
    protect_remote_branches: bool = True
    protect_tags: bool = True
    protect_code_files: bool = False
    protect_text_data_files: bool = False
    scan_gerrit_refs: bool = True
    include_paths: tuple[str, ...] = ()
    exclude_paths: tuple[str, ...] = ()
    keep_paths: tuple[str, ...] = ()
    keep_blobs: tuple[str, ...] = ()
    output_dir: Path = Path("./git_audit")
    verbose: bool = True
    execute_cleanup: bool = False
    push_after_cleanup: bool = False
    push_remote: str = "origin"
    push_branch: str | None = None
    review_html: bool = False
    review_timeout: int = 600
    no_open_browser: bool = False

    @property
    def min_size_bytes(self) -> int:
        return int(self.min_size_mb * 1024 * 1024)

    @classmethod
    def from_args(cls, args: Any) -> "AuditorOptions":
        """Convert an argparse Namespace (or compatible object) to options."""
        min_size_mb = float(getattr(args, "min_size_mb", 100.0))
        min_age_days = int(getattr(args, "min_age_days", 180))
        if min_size_mb <= 0:
            raise ValueError("min_size_mb must be greater than 0")
        if min_age_days < 0:
            raise ValueError("min_age_days must be greater than or equal to 0")

        repo = Path(getattr(args, "repo")).expanduser().resolve()
        output_dir = Path(getattr(args, "output_dir", "./git_audit")).expanduser()
        if not output_dir.is_absolute():
            output_dir = (Path.cwd() / output_dir).resolve()

        def values(name: str, fallback: tuple[str, ...] = ()) -> tuple[str, ...]:
            value = getattr(args, name, fallback)
            return tuple(value or ())

        keep_blobs = values("keep_blobs", values("keep_blob"))
        for sha in keep_blobs:
            if not _looks_like_object_id(sha):
                raise ValueError(f"invalid Git object id in --keep-blob: {sha!r}")

        execute_cleanup = bool(getattr(args, "execute_cleanup", False))
        push_after_cleanup = bool(getattr(args, "push_after_cleanup", False))
        if push_after_cleanup and not execute_cleanup:
            raise ValueError("--push-after-cleanup requires --execute-cleanup")
        review_timeout = int(getattr(args, "review_timeout", 600))
        if not 1 <= review_timeout <= 3600:
            raise ValueError("review_timeout must be between 1 and 3600 seconds")

        return cls(
            repo=repo,
            min_size_mb=min_size_mb,
            min_age_days=min_age_days,
            protect_head=bool(getattr(args, "protect_head", True)),
            protect_local_branches=bool(getattr(args, "protect_local_branches", True)),
            protect_remote_branches=bool(getattr(args, "protect_remote_branches", True)),
            protect_tags=bool(getattr(args, "protect_tags", True)),
            protect_code_files=bool(getattr(args, "protect_code_files", False)),
            protect_text_data_files=bool(getattr(args, "protect_text_data_files", False)),
            scan_gerrit_refs=bool(getattr(args, "scan_gerrit_refs", True)),
            include_paths=values("include_paths", values("include_path")),
            exclude_paths=values("exclude_paths", values("exclude_path")),
            keep_paths=values("keep_paths", values("keep_path")),
            keep_blobs=keep_blobs,
            output_dir=output_dir,
            verbose=bool(getattr(args, "verbose", True)),
            execute_cleanup=execute_cleanup,
            push_after_cleanup=push_after_cleanup,
            push_remote=str(getattr(args, "push_remote", "origin")),
            push_branch=getattr(args, "push_branch", None) or None,
            review_html=bool(getattr(args, "review_html", False)),
            review_timeout=review_timeout,
            no_open_browser=bool(getattr(args, "no_open_browser", False)),
        )


@dataclass
class BlobHistoryInfo:
    """History facts collected for one blob."""

    first_seen_time: datetime | None = None
    last_seen_time: datetime | None = None
    first_seen_commit: str | None = None
    last_seen_commit: str | None = None
    last_commit_subject: str | None = None
    commit_count: int = 0
    branch_last_seen: datetime | None = None
    gerrit_last_seen: datetime | None = None


@dataclass
class BlobInfo:
    """Complete audit record for one large Git blob."""

    sha: str
    size_bytes: int
    paths: set[str] = field(default_factory=set)
    first_seen_time: datetime | None = None
    last_seen_time: datetime | None = None
    first_seen_commit: str | None = None
    last_seen_commit: str | None = None
    last_commit_subject: str | None = None
    commit_count: int = 0
    branch_last_seen: datetime | None = None
    gerrit_last_seen: datetime | None = None
    current_refs: set[str] = field(default_factory=set)
    branch_refs: set[str] = field(default_factory=set)
    remote_refs: set[str] = field(default_factory=set)
    tag_refs: set[str] = field(default_factory=set)
    gerrit_refs: set[str] = field(default_factory=set)
    gerrit_change_numbers: set[str] = field(default_factory=set)
    gerrit_patchsets: set[str] = field(default_factory=set)
    is_current: bool = False
    is_protected: bool = False
    has_current_path_version: bool = False
    status: BlobStatus | None = None
    eligible: bool = False
    action: CleanupAction = CleanupAction.NO_ACTION
    protection_reason: str = ""
    candidate_reason: str = ""

    @property
    def primary_path(self) -> str:
        return sorted(self.paths)[0] if self.paths else ""

    @property
    def effective_last_seen(self) -> datetime | None:
        dates = [date for date in (self.branch_last_seen, self.gerrit_last_seen) if date]
        return max(dates) if dates else self.last_seen_time

    def to_dict(self) -> dict[str, Any]:
        """Return a JSON-serializable representation."""
        result = asdict(self)
        for key in (
            "paths",
            "current_refs",
            "branch_refs",
            "remote_refs",
            "tag_refs",
            "gerrit_refs",
            "gerrit_change_numbers",
            "gerrit_patchsets",
        ):
            result[key] = sorted(result[key])
        for key in (
            "first_seen_time",
            "last_seen_time",
            "branch_last_seen",
            "gerrit_last_seen",
        ):
            if result[key] is not None:
                result[key] = result[key].isoformat()
        result["status"] = self.status.value if self.status else None
        result["action"] = self.action.value
        result["size_mb"] = self.size_bytes / (1024 * 1024)
        result["size_gb"] = self.size_bytes / (1024 * 1024 * 1024)
        result["age_days"] = self.age_days()
        result["effective_last_seen"] = (
            self.effective_last_seen.isoformat() if self.effective_last_seen else None
        )
        return result

    def age_days(self, now: datetime | None = None) -> int | None:
        if self.effective_last_seen is None:
            return None
        now = now or datetime.now(self.effective_last_seen.tzinfo)
        return max(0, (now - self.effective_last_seen).days)


@dataclass(frozen=True)
class RefInfo:
    name: str
    object_id: str
    kind: str


@dataclass
class RepositoryStats:
    values: dict[str, str] = field(default_factory=dict)


@dataclass
class ScanCoverage:
    head: bool = False
    local_branches: bool = False
    remote_branches: bool = False
    tags: bool = False
    gerrit_refs: bool = False
    gerrit_refs_available: bool = False
    warnings: list[str] = field(default_factory=list)


@dataclass
class AuditResult:
    repository: Path
    audit_time: datetime
    blobs: list[BlobInfo]
    output_dir: Path
    repo_stats_before: RepositoryStats
    coverage: ScanCoverage
    min_size_bytes: int
    min_age_days: int
    warnings: list[str] = field(default_factory=list)
    report_files: dict[str, Path] = field(default_factory=dict)
    ref_state: tuple[str, str, str] | None = None

    @property
    def cleanup_count(self) -> int:
        return sum(blob.eligible for blob in self.blobs)

    @property
    def automatic_cleanup_count(self) -> int:
        return sum(blob.action == CleanupAction.CLEAN_HISTORY for blob in self.blobs)

    @property
    def gerrit_review_count(self) -> int:
        return sum(blob.action == CleanupAction.GERRIT_ADMIN_REVIEW for blob in self.blobs)


def _looks_like_object_id(value: str) -> bool:
    value = value.strip().lower()
    return len(value) in (40, 64) and all(char in "0123456789abcdef" for char in value)
