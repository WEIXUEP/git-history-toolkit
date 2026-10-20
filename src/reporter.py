"""Write human-readable and machine-readable audit reports."""

from __future__ import annotations

import csv
import json
from collections import Counter
from datetime import datetime
from pathlib import Path

from .models import AuditResult, BlobInfo


class AuditReporter:
    def write(self, result: AuditResult) -> dict[str, Path]:
        result.output_dir.mkdir(parents=True, exist_ok=False)
        files = {
            "summary": result.output_dir / "summary.txt",
            "blobs_csv": result.output_dir / "blobs.csv",
            "blobs_json": result.output_dir / "blobs.json",
            "candidates": result.output_dir / "candidates.csv",
            "candidate_ids": result.output_dir / "candidate_blobs.txt",
            "protected_ids": result.output_dir / "protected_blobs.txt",
            "gerrit": result.output_dir / "gerrit_referenced.csv",
            "admin_plan": result.output_dir / "admin_cleanup_plan.txt",
            "cache": result.output_dir / "cache-events.json",
        }
        self._write_summary(files["summary"], result)
        files["cache"].write_text(json.dumps({"audit_time": result.audit_time.isoformat(),
            "repository": str(result.repository), "ref_state": result.ref_state,
            **result.cache_info}, indent=2) + "\n", encoding="utf-8")
        self._write_blob_csv(files["blobs_csv"], result.blobs, result.audit_time)
        files["blobs_json"].write_text(
            json.dumps([blob.to_dict() for blob in result.blobs], ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        self._write_blob_csv(
            files["candidates"], [b for b in result.blobs if b.eligible], result.audit_time
        )
        files["candidate_ids"].write_text(
            "".join(f"{blob.sha}\n" for blob in result.blobs if blob.eligible), encoding="utf-8"
        )
        files["protected_ids"].write_text(
            "".join(f"{blob.sha}\n" for blob in result.blobs if blob.is_protected), encoding="utf-8"
        )
        self._write_gerrit_csv(files["gerrit"], result.blobs, result.audit_time)
        self._write_admin_plan(files["admin_plan"], result)
        return files

    def _write_summary(self, path: Path, result: AuditResult) -> None:
        counts = Counter(blob.status.value for blob in result.blobs if blob.status)
        candidate_size = sum(blob.size_bytes for blob in result.blobs if blob.eligible)
        coverage = result.coverage
        lines = [
            "Git Large Object Audit",
            "=" * 50,
            "",
            f"Repository: {result.repository}",
            f"Audit Time: {result.audit_time.isoformat()}",
            "",
            f"Large blob threshold: {result.min_size_bytes / (1024 * 1024):.3f} MiB",
            f"Minimum age: {result.min_age_days} days",
            "",
            "Repository Summary",
            "-" * 50,
            f"Large blobs: {len(result.blobs)}",
            f"Current protected: {counts['CURRENT']}",
            f"Explicitly protected: {counts['PROTECTED']}",
            f"Too new: {counts['TOO_NEW']}",
            f"Old versions: {counts['OLD_VERSION']}",
            f"Deleted paths: {counts['DELETED_PATH']}",
            f"Gerrit referenced: {counts['GERRIT_PATCHSET_ONLY']}",
            f"Cleanup eligible: {result.cleanup_count}",
            f"Automatic local cleanup: {result.automatic_cleanup_count}",
            f"Gerrit administrator review: {result.gerrit_review_count}",
            f"Raw candidate blob size: {_format_size(candidate_size)}",
            "",
            "Scan Coverage",
            "-" * 50,
            f"HEAD protected: {'YES' if coverage.head else 'NO'}",
            f"Local branches protected: {'YES' if coverage.local_branches else 'NO'}",
            f"Remote branches protected: {'YES' if coverage.remote_branches else 'NO'}",
            f"Tags protected: {'YES' if coverage.tags else 'NO'}",
            f"Gerrit refs available locally: {'YES' if coverage.gerrit_refs_available else 'NO'}",
            f"Gerrit analysis: {'COMPLETE' if coverage.gerrit_refs else 'INCOMPLETE'}",
            "",
            "Repository Statistics Before Audit",
            "-" * 50,
        ]
        lines.extend(f"{key}: {value}" for key, value in result.repo_stats_before.values.items())
        if result.cache_info:
            lines.extend(["", "Audit Cache", "-" * 50,
                f"Mode: {result.cache_info.get('mode')}",
                f"Location: {result.cache_info.get('path')}",
                f"Hits / misses: {result.cache_info.get('hits')} / {result.cache_info.get('misses')}",
                f"Entries evicted: {result.cache_info.get('evicted')}"])
            lines.extend(result.cache_info.get("events", []))
        if result.warnings or coverage.warnings:
            lines.extend(["", "Warnings", "-" * 50])
            lines.extend(f"- {warning}" for warning in [*result.warnings, *coverage.warnings])
        path.write_text("\n".join(lines) + "\n", encoding="utf-8")

    def _write_blob_csv(self, path: Path, blobs: list[BlobInfo], now: datetime) -> None:
        fields = [
            "blob_sha", "size_bytes", "size_mb", "size_gb", "status", "eligible", "action",
            "primary_path", "all_paths", "first_seen", "last_seen", "age_days", "first_commit",
            "last_commit", "last_commit_subject", "current_refs", "branch_refs", "remote_refs",
            "tag_refs", "gerrit_refs", "gerrit_change_numbers", "gerrit_patchsets",
            "protection_reason", "candidate_reason",
        ]
        with path.open("w", newline="", encoding="utf-8-sig") as handle:
            writer = csv.DictWriter(handle, fieldnames=fields)
            writer.writeheader()
            for blob in blobs:
                data = blob.to_dict()
                row = {
                    "blob_sha": blob.sha,
                    "size_bytes": blob.size_bytes,
                    "size_mb": f"{data['size_mb']:.2f}",
                    "size_gb": f"{data['size_gb']:.3f}",
                    "status": data["status"],
                    "eligible": blob.eligible,
                    "action": blob.action.value,
                    "primary_path": blob.primary_path,
                    "all_paths": "; ".join(sorted(blob.paths)),
                    "first_seen": _iso(blob.first_seen_time),
                    "last_seen": _iso(blob.last_seen_time),
                    "age_days": blob.age_days(now),
                    "first_commit": blob.first_seen_commit or "",
                    "last_commit": blob.last_seen_commit or "",
                    "last_commit_subject": blob.last_commit_subject or "",
                    "current_refs": "; ".join(sorted(blob.current_refs)),
                    "branch_refs": "; ".join(sorted(blob.branch_refs)),
                    "remote_refs": "; ".join(sorted(blob.remote_refs)),
                    "tag_refs": "; ".join(sorted(blob.tag_refs)),
                    "gerrit_refs": "; ".join(sorted(blob.gerrit_refs)),
                    "gerrit_change_numbers": "; ".join(sorted(blob.gerrit_change_numbers)),
                    "gerrit_patchsets": "; ".join(sorted(blob.gerrit_patchsets)),
                    "protection_reason": blob.protection_reason,
                    "candidate_reason": blob.candidate_reason,
                }
                writer.writerow(row)

    def _write_gerrit_csv(self, path: Path, blobs: list[BlobInfo], now: datetime) -> None:
        fields = ["blob_sha", "size_bytes", "size_mb", "path", "change", "patchset", "age_days", "ref"]
        with path.open("w", newline="", encoding="utf-8-sig") as handle:
            writer = csv.DictWriter(handle, fieldnames=fields)
            writer.writeheader()
            for blob in blobs:
                for ref in sorted(blob.gerrit_refs):
                    for path_name in sorted(blob.paths or {""}):
                        changes = sorted(blob.gerrit_change_numbers or {""})
                        patchsets = sorted(blob.gerrit_patchsets or {""})
                        for change in changes:
                            for patchset in patchsets:
                                writer.writerow({
                                    "blob_sha": blob.sha,
                                    "size_bytes": blob.size_bytes,
                                    "size_mb": f"{blob.size_bytes / 1024 / 1024:.2f}",
                                    "path": path_name,
                                    "change": change,
                                    "patchset": patchset,
                                    "age_days": blob.age_days(now),
                                    "ref": ref,
                                })

    def _write_admin_plan(self, path: Path, result: AuditResult) -> None:
        lines = [
            "ADMIN CLEANUP PLAN",
            "=" * 50,
            "",
            "This report does NOT modify Git history or any remote repository.",
            "",
        ]
        candidates = [blob for blob in result.blobs if blob.eligible]
        if not candidates:
            lines.append("No cleanup candidates were found.")
        for blob in candidates:
            lines.extend([
                f"Candidate: {blob.sha}",
                f"Path: {blob.primary_path or '(path unavailable)'}",
                f"Size: {_format_size(blob.size_bytes)}",
                f"Last Seen: {_iso(blob.effective_last_seen) or 'unknown'}",
                f"Current Branch References: {', '.join(sorted(blob.current_refs)) or 'NONE'}",
                f"Gerrit References: {', '.join(sorted(blob.gerrit_refs)) or 'NONE'}",
                f"Recommended Action: {blob.action.value}",
                f"Reason: {blob.candidate_reason}",
                "",
            ])
        path.write_text("\n".join(lines), encoding="utf-8")


def _format_size(size_bytes: int) -> str:
    return f"{size_bytes / (1024 ** 3):.2f} GiB"


def _iso(value: datetime | None) -> str:
    return value.isoformat() if value else ""
