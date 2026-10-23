"""Saved HTML review and state-checked cleanup without another history walk."""

from argparse import Namespace
from contextlib import redirect_stdout
import io
import json
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest.mock import patch

from git_history_toolkit import main
from src.auditor import GitLargeObjectAuditor
from src.html_review import ReviewSession, review_payload
from src.rewriter import CleanupError, GitHistoryRewriter
from src.saved_audit import load_report


class SavedAuditTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix="saved-audit-")
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.repo = self.root / "repo with spaces"
        self.repo.mkdir()
        self.git("init", "-b", "main")
        self.git("config", "user.name", "Saved Audit Test")
        self.git("config", "user.email", "saved@example.invalid")
        self.git("config", "commit.gpgsign", "false")
        self.git("config", "core.autocrlf", "false")
        (self.repo / "old.bin").write_bytes(b"old data" * 100)
        (self.repo / "keep.txt").write_text("keep\n", encoding="utf-8")
        self.commit("add old")
        self.old = self.git("rev-parse", "HEAD:old.bin")
        (self.repo / "old.bin").unlink()
        self.commit("delete old")
        self.auditor = GitLargeObjectAuditor(Namespace(repo=str(self.repo), min_size_mb=.000001,
            min_age_days=0, output_dir=str(self.root / "reports"), cache=True,
            cache_dir=str(self.root / "cache"), no_open_browser=True))
        self.result = self.auditor.run()
        self.report = self.result.output_dir
        self.original = review_payload(self.result, self.auditor.options, "review")

    def git(self, *args):
        return subprocess.check_output(["git", "-C", str(self.repo), *args], stderr=subprocess.PIPE).decode().strip()

    def commit(self, message):
        self.git("add", "--all")
        self.git("commit", "-m", message)

    def test_reopen_complete_review_does_not_scan_or_replay_approval(self):
        (self.report / "browser-review.json").write_text(json.dumps({"mode": "cleanup", "selected": [self.old]}))
        with patch("src.auditor.GitLargeObjectAuditor.run", side_effect=AssertionError("No audit")), \
                patch("src.html_review.historical_presence", side_effect=AssertionError("No history search")):
            result, options = load_report(self.report)
            payload = review_payload(result, options, "review")
        self.assertEqual(payload["rows"], self.original["rows"])
        session = ReviewSession(payload)
        self.assertIsNone(session.selected)
        session.server.server_close()
        self.assertFalse(options.execute_cleanup)
        self.assertNotEqual(result.output_dir, self.report)
        self.assertTrue(payload["warnings"])

    def test_missing_saved_payload_uses_cache_without_history_search(self):
        (self.report / "review-payload.json").unlink()
        with patch("src.html_review.historical_presence", side_effect=AssertionError("No history search")):
            result, options = load_report(self.report)
            self.assertEqual(review_payload(result, options, "review")["rows"], self.original["rows"])

    def test_missing_presence_data_displays_unknown_without_history_search(self):
        (self.report / "review-payload.json").unlink()
        from src.audit_cache import AuditCache
        with patch.object(AuditCache, "get", return_value=None), \
                patch("src.html_review.historical_presence", side_effect=AssertionError("No history search")):
            result, options = load_report(self.report)
            payload = review_payload(result, options, "review")
        self.assertTrue(all(row["last_present"] is None for row in payload["rows"]))
        self.assertTrue(any("Unknown" in warning for warning in result.warnings))

    def test_changed_refs_allow_archived_view_but_block_cleanup(self):
        (self.repo / "later.txt").write_text("new\n")
        self.commit("later")
        result, options = load_report(self.report)
        self.assertEqual(review_payload(result, options, "review")["rows"], self.original["rows"])
        self.assertTrue(any("refs changed" in warning for warning in result.warnings))
        with self.assertRaisesRegex(ValueError, "changed"):
            load_report(self.report, execute_cleanup=True)

    def test_changed_git_config_blocks_resume_even_with_same_refs(self):
        self.git("config", "toolkit.savedAuditTest", "changed")
        with self.assertRaisesRegex(ValueError, "Git context changed"):
            load_report(self.report, execute_cleanup=True)

    def test_modified_snapshot_or_review_is_rejected(self):
        path = self.report / "audit-session.json"
        original = path.read_bytes()
        data = json.loads(original)
        data["data"]["options"]["min_age_days"] = 999
        path.write_text(json.dumps(data))
        with self.assertRaisesRegex(ValueError, "modified saved report"):
            load_report(self.report, execute_cleanup=True)
        path.write_bytes(original)
        path = self.report / "review-payload.json"
        data = json.loads(path.read_text())
        data["data"]["rows"][0]["size"] += 1
        path.write_text(json.dumps(data))
        with self.assertRaisesRegex(ValueError, "modified saved report"):
            load_report(self.report)

    def test_legacy_report_opens_but_cannot_authorize_cleanup(self):
        (self.report / "audit-session.json").unlink()
        result, options = load_report(self.report)
        self.assertEqual(review_payload(result, options, "review")["rows"], self.original["rows"])
        with self.assertRaisesRegex(ValueError, "legacy report is view-only"):
            load_report(self.report, execute_cleanup=True)

    def test_cli_goes_directly_to_html_and_cancellation_creates_no_backup(self):
        def cancel(result, options, **kwargs):
            self.assertTrue(options.execute_cleanup)
            self.assertEqual(kwargs["mode"], "cleanup")
            self.assertEqual(review_payload(result, options, "cleanup")["rows"], self.original["rows"])
            return None

        with patch("src.html_review.confirm_thresholds", side_effect=AssertionError("No threshold page")), \
                patch("src.history_analyzer.HistoryAnalyzer.analyze", side_effect=AssertionError("No history scan")), \
                patch("src.html_review.historical_presence", side_effect=AssertionError("No presence scan")), \
                patch("src.html_review.review_in_browser", side_effect=cancel), redirect_stdout(io.StringIO()):
            status = main(["review", "--report", str(self.report), "--execute-cleanup", "--no-open-browser"])
        self.assertEqual(status, 0)
        self.assertFalse(list(self.root.rglob("before_cleanup.bundle")))

    def test_real_disposable_resume_cleanup_preserves_tip_and_original_report(self):
        result, options = load_report(self.report, execute_cleanup=True)
        before_tree = self.git("rev-parse", "HEAD^{tree}")
        original = (self.report / "audit-session.json").read_bytes()
        with patch("src.html_review.review_in_browser", return_value=[self.old]):
            GitHistoryRewriter(self.auditor.runner, options, emit=lambda _: None).execute(result)
        self.assertEqual(self.git("rev-parse", "HEAD^{tree}"), before_tree)
        self.assertNotIn(self.old, self.git("rev-list", "--objects", "--all"))
        self.assertTrue((result.output_dir / "before_cleanup.bundle").exists())
        self.assertEqual((self.report / "audit-session.json").read_bytes(), original)

    def test_context_change_during_confirmation_stops_before_backup(self):
        result, options = load_report(self.report, execute_cleanup=True)

        def change(*args, **kwargs):
            self.git("config", "toolkit.savedAuditTest", "changed")
            return [self.old]

        with patch("src.html_review.review_in_browser", side_effect=change):
            with self.assertRaisesRegex(CleanupError, "Git context changed"):
                GitHistoryRewriter(self.auditor.runner, options, emit=lambda _: None).execute(result)
        self.assertFalse((result.output_dir / "before_cleanup.bundle").exists())

    def test_object_metadata_mismatch_blocks_resume(self):
        with patch("src.blob_scanner.BlobScanner._batch_check", return_value={}):
            with self.assertRaisesRegex(ValueError, "metadata no longer matches"):
                load_report(self.report, execute_cleanup=True)

    def test_offline_repository_retains_saved_timestamps(self):
        offline = self.repo.with_name("offline")
        self.repo.rename(offline)
        try:
            result, options = load_report(self.report)
            self.assertEqual(review_payload(result, options, "review")["rows"], self.original["rows"])
            self.assertTrue(any("Repository unavailable" in warning for warning in result.warnings))
        finally:
            offline.rename(self.repo)


if __name__ == "__main__":
    unittest.main()
