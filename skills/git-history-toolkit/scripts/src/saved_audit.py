"""Reopen completed audit facts without traversing history again."""

from argparse import Namespace
import argparse
from dataclasses import asdict, fields, replace
from datetime import datetime, timezone
import json
from pathlib import Path
import re

from .audit_cache import AuditCache, digest, git_context
from .blob_scanner import BlobScanner
from .classifier import BlobClassifier
from .git_runner import GitCommandError, GitRunner
from .models import AuditResult, AuditorOptions, BlobInfo, BlobStatus, CleanupAction, RepositoryStats, ScanCoverage
from .repository import RepositoryInspector

# Bump if the facts needed to safely resume cleanup change.
SCHEMA = 1


def _write(path, data):
    envelope = {"schema_version": SCHEMA, "sha256": digest(data), "data": data}
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(envelope, ensure_ascii=True, indent=2) + "\n", encoding="utf-8")
    temporary.replace(path)


def _read(path):
    envelope = json.loads(path.read_text(encoding="utf-8"))
    if envelope.get("schema_version") != SCHEMA or digest(envelope.get("data")) != envelope.get("sha256"):
        raise ValueError(f"Unsupported or modified saved report: {path}")
    return envelope["data"]


def _identity(runner):
    return {"repository": str(runner.repo.resolve()),
            "common_dir": str(Path(runner.run("rev-parse", "--path-format=absolute", "--git-common-dir").strip()).resolve()),
            "context": git_context(runner)}


def save_audit(result, options, runner, expected_context=None):
    identity = _identity(runner)
    if runner.ref_state() != result.ref_state or (expected_context and identity["context"] != expected_context):
        raise ValueError("Refs or Git context changed while saving audit; rerun analysis")
    data = {"identity": identity, "audit_time": result.audit_time.isoformat(),
            "ref_state": result.ref_state,
            "options": json.loads(json.dumps(asdict(replace(options, repo=result.repository)), default=str)),
            "blobs": [blob.to_dict() for blob in result.blobs],
            "coverage": asdict(result.coverage), "stats": result.repo_stats_before.values,
            "warnings": result.warnings, "cache_info": result.cache_info}
    _write(result.output_dir / "audit-session.json", data)


def save_review(result, payload):
    _write(result.output_dir / "review-payload.json", payload)


def _blob(record):
    values = {item.name: record[item.name] for item in fields(BlobInfo) if item.name in record}
    if not re.fullmatch(r"[0-9a-f]{40}|[0-9a-f]{64}", values.get("sha", "")):
        raise ValueError("Saved report contains an invalid object identity")
    if not isinstance(values.get("size_bytes"), int) or values["size_bytes"] < 0:
        raise ValueError("Saved report contains an invalid object size")
    for key in ("paths", "current_refs", "branch_refs", "remote_refs", "tag_refs", "gerrit_refs",
                "gerrit_change_numbers", "gerrit_patchsets"):
        values[key] = set(values.get(key, []))
    for key in ("first_seen_time", "last_seen_time", "branch_last_seen", "gerrit_last_seen"):
        if values.get(key) is not None:
            values[key] = datetime.fromisoformat(values[key])
            if values[key].tzinfo is None:
                raise ValueError("Saved history timestamps must have a timezone")
    values["status"] = BlobStatus(values["status"]) if values.get("status") else None
    values["action"] = CleanupAction(values.get("action", "NO_ACTION"))
    return BlobInfo(**values)


def _legacy(report):
    metadata = json.loads((report / "cache-events.json").read_text(encoding="utf-8"))
    summary = dict(line.split(": ", 1) for line in (report / "summary.txt").read_text(encoding="utf-8").splitlines()
                   if ": " in line)
    return {"identity": {"repository": metadata["repository"]},
            "audit_time": metadata["audit_time"], "ref_state": metadata.get("ref_state"),
            "options": {"repo": metadata["repository"],
                        "min_size_mb": max(float(summary["Large blob threshold"].split()[0]), 1 / 1048576),
                        "min_age_days": int(summary["Minimum age"].split()[0])},
            "blobs": json.loads((report / "blobs.json").read_text(encoding="utf-8")),
            "coverage": {}, "stats": {}, "cache_info": metadata, "warnings": []}


def _presence(report, result, options):
    saved = report / "review-payload.json"
    if saved.exists():
        payload = _read(saved)
        if (payload["repository"] != str(result.repository)
                or payload["audit_time"] != result.audit_time.isoformat()
                or payload["refs"] != list(result.ref_state or ())):
            raise ValueError("Saved review belongs to a different audit")
        return {row["sha"]: row["last_present"] for row in payload["rows"] if row.get("last_present")}
    # Older reports did not persist their HTML payload. Consult only cached
    # facts; a missing timestamp must never trigger another history walk here.
    runner = GitRunner(result.repository)
    if not result.repository.is_dir() or runner.ref_state() != result.ref_state:
        return {}
    cache_path = result.cache_info.get("path")
    cache_options = replace(options, cache=True, execute_cleanup=False,
                            cache_dir=Path(cache_path).parent.parent if cache_path else options.cache_dir)
    cache = AuditCache(runner, cache_options)
    try:
        key = digest(result.ref_state)
        return {blob.sha: value for blob in result.blobs
                if (value := cache.get("presence", [key, blob.sha])) is not None}
    finally:
        cache.close()


def validate_resume(result, options, identity):
    runner = GitRunner(result.repository)
    root = RepositoryInspector(runner).validate()
    if root != result.repository or _identity(runner) != identity or runner.ref_state() != result.ref_state:
        raise ValueError("Repository refs, identity or Git context changed; run a new audit before cleanup")
    metadata = BlobScanner(runner)._batch_check([blob.sha for blob in result.blobs])
    if any(metadata.get(blob.sha) != ("blob", blob.size_bytes) for blob in result.blobs):
        raise ValueError("Saved object metadata no longer matches; run a new audit")
    # Reapply the saved policy at the saved audit date. Reopening cannot add
    # newly aged candidates or weaken any original protection option.
    expected = [(blob.status, blob.action, blob.eligible) for blob in result.blobs]
    BlobClassifier(options, result.audit_time).classify(result.blobs)
    if [(blob.status, blob.action, blob.eligible) for blob in result.blobs] != expected:
        raise ValueError("Saved classification differs from the current policy; run a new audit")
    if runner.ref_state() != result.ref_state or _identity(runner) != identity:
        raise ValueError("Repository changed during saved-report validation; run a new audit")
    result.resume_context = identity["context"]


def load_report(report, *, execute_cleanup=False, output_dir=None, no_open_browser=False, review_timeout=600):
    report = Path(report).expanduser().resolve()
    modern = (report / "audit-session.json").exists()
    if execute_cleanup and not modern:
        raise ValueError("This legacy report is view-only; generate a new audit once before resuming cleanup")
    data = _read(report / "audit-session.json") if modern else _legacy(report)
    options = AuditorOptions.from_args(Namespace(**data["options"]))
    options = replace(options, execute_cleanup=execute_cleanup, push_after_cleanup=False,
                      review_html=True, no_open_browser=no_open_browser, review_timeout=review_timeout)
    result = AuditResult(repository=Path(data["identity"]["repository"]).resolve(),
        audit_time=datetime.fromisoformat(data["audit_time"]), blobs=[_blob(row) for row in data["blobs"]],
        output_dir=report, repo_stats_before=RepositoryStats(data["stats"]), coverage=ScanCoverage(**data["coverage"]),
        min_size_bytes=options.min_size_bytes, min_age_days=options.min_age_days,
        ref_state=tuple(data["ref_state"]) if data["ref_state"] else None,
        cache_info=data["cache_info"], warnings=list(data["warnings"]))
    if options.repo != result.repository or result.audit_time.tzinfo is None:
        raise ValueError("Saved report repository or audit date is inconsistent")
    if len({blob.sha for blob in result.blobs}) != len(result.blobs):
        raise ValueError("Saved report contains duplicate objects")
    if execute_cleanup:
        validate_resume(result, options, data["identity"])
    result.warnings.append(f"Saved audit: {result.audit_time.isoformat()} | {report}")
    if not modern:
        result.warnings.append("Legacy report: view only. A new audit is required before cleanup.")
    try:
        if GitRunner(result.repository).ref_state() != result.ref_state:
            result.warnings.append("Repository refs changed. Archived results only; cleanup requires a new audit.")
        elif modern and not execute_cleanup and _identity(GitRunner(result.repository)) != data["identity"]:
            result.warnings.append("Git context changed. Archived results only; cleanup requires a new audit.")
    except (OSError, GitCommandError):
        result.warnings.append("Repository unavailable. Displaying saved facts only.")
    try:
        result.review_presence = _presence(report, result, options)
    except (OSError, GitCommandError):
        result.review_presence = {}
        result.warnings.append("Cached timestamps unavailable. Displaying saved facts only.")
    if len(result.review_presence) < len(result.blobs):
        result.warnings.append("Some last-presence timestamps were not saved or cached; shown as Unknown. No history search ran.")
    destination = Path(output_dir).expanduser().resolve() if output_dir else report.with_name(
        report.name + "_review_" + datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S_%f"))
    if destination.is_relative_to(result.repository):
        raise ValueError("Review output must be outside the repository")
    destination.mkdir(parents=True, exist_ok=False)
    result.output_dir = destination
    options = replace(options, output_dir=destination)
    return result, options


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--report", required=True, help="Completed audit report directory")
    parser.add_argument("--execute-cleanup", action="store_true", help="Validate saved facts, then request new HTML CLEANUP approval")
    parser.add_argument("--output-dir", help="New external directory for this review's receipts and optional backup")
    parser.add_argument("--no-open-browser", action="store_true")
    parser.add_argument("--review-timeout", type=int, choices=range(1, 3601), default=600, metavar="1-3600")
    args = parser.parse_args(argv)
    try:
        result, options = load_report(**vars(args))
        print(f"Loaded completed audit: {args.report}; no history scan or threshold page")
        print(f"Review output: {result.output_dir}")
        if args.execute_cleanup:
            from .rewriter import GitHistoryRewriter
            GitHistoryRewriter(GitRunner(result.repository), options).execute(result)
        else:
            from .html_review import review_in_browser
            review_in_browser(result, options)
        return 0
    except (OSError, ValueError, RuntimeError, KeyError, TypeError) as exc:
        parser.error(str(exc))
