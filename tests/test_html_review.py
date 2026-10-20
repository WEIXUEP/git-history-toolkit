"""Browser approval, exact historical presence and selected-object cleanup."""

from argparse import Namespace
from dataclasses import replace
import http.client
import json
import os
from pathlib import Path
import subprocess
import tempfile
import threading
import unittest
from unittest.mock import patch

from src.auditor import GitLargeObjectAuditor
from src.html_review import ReviewSession, confirm_thresholds, historical_presence, review_in_browser, review_payload
from src.models import CleanupAction
from src.rewriter import CleanupError, GitHistoryRewriter


class BrowserReviewTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix="browser-review-tests-")
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.repo = self.root / "repo with spaces"
        self.repo.mkdir()
        self.git("init", "-b", "main")
        self.git("config", "user.name", "Review Test")
        self.git("config", "user.email", "review@example.invalid")
        self.git("config", "commit.gpgsign", "false")
        self.git("config", "core.autocrlf", "false")
        (self.repo / "old.bin").write_bytes(b"old payload" * 100)
        (self.repo / "alias.ZIP").write_bytes(b"old payload" * 100)
        (self.repo / "other.log").write_bytes(b"other history" * 100)
        (self.repo / "keep.txt").write_bytes(b"current version" * 100)
        self.commit("2020-01-01T12:00:00Z")
        (self.repo / "small.txt").write_text("unrelated", encoding="utf-8")
        self.last_present = self.commit("2020-01-02T12:00:00Z")
        for name in ("old.bin", "alias.ZIP", "other.log"):
            (self.repo / name).unlink()
        self.commit("2020-01-03T12:00:00Z")
        self.auditor = GitLargeObjectAuditor(Namespace(repo=str(self.repo), min_size_mb=.0001,
            min_age_days=0, output_dir=str(self.root / "reports"), review_html=True,
            no_open_browser=True, review_timeout=1))
        self.result = self.auditor.run()
        self.options = self.auditor.options
        self.old = next(b for b in self.result.blobs if "old.bin" in b.paths)
        self.keep = next(b for b in self.result.blobs if "keep.txt" in b.paths)

    def git(self, *args, env=None):
        return subprocess.check_output(["git", "-C", str(self.repo), *args], env=env,
                                       stderr=subprocess.PIPE).decode().strip()

    def commit(self, date):
        self.git("add", "--all")
        env = dict(os.environ, GIT_AUTHOR_DATE=date, GIT_COMMITTER_DATE=date)
        self.git("commit", "-m", "Fixture history", env=env)
        return self.git("rev-parse", "HEAD")

    def session(self, mode="cleanup"):
        session = ReviewSession(review_payload(self.result, self.options, mode))
        session.start()
        self.addCleanup(session.close)
        return session

    def post(self, session, body, **overrides):
        connection = http.client.HTTPConnection("127.0.0.1", session.server.server_port, timeout=10)
        headers = {"Origin": session.origin, "X-Review-Token": session.token, "Content-Type": "application/json"}
        headers.update(overrides)
        connection.request("POST", "/decision/" + session.token, json.dumps(dict(digest=session.digest, **body)), headers)
        response = connection.getresponse()
        response.read()
        status = response.status
        connection.close()
        return status

    def test_last_presence_is_not_deletion_time_and_aliases_share_one_row(self):
        payload = review_payload(self.result, self.options, "review")
        row = next(r for r in payload["rows"] if r["sha"] == self.old.sha)
        self.assertEqual(row["last_present"]["commit"], self.last_present)
        self.assertEqual(row["last_present"]["time"], "2020-01-02T12:00:00+00:00")
        self.assertEqual(row["last_change"], "2020-01-03T12:00:00+00:00")
        self.assertEqual(row["suffixes"], [".bin", ".zip"])
        self.assertEqual(row["size"], len(b"old payload" * 100))

    def test_thresholds_require_manual_confirmation_and_valid_numbers(self):
        session = ReviewSession({"mode": "thresholds", "repository": str(self.repo),
                                 "min_size_mb": 100, "min_age_days": 180,
                                 "as_of": "2026-10-20T00:00:00+00:00"})
        session.start()
        self.addCleanup(session.close)
        for size, days, word in ((5, 90, ""), (0, 90, "THRESHOLDS"),
                                  (True, 90, "THRESHOLDS"), (5, -1, "THRESHOLDS"),
                                  (5, 1.5, "THRESHOLDS"), (5, True, "THRESHOLDS"),
                                  (float("inf"), 90, "THRESHOLDS"), (5, 365001, "THRESHOLDS")):
            self.assertEqual(self.post(session, {"min_size_mb": size, "min_age_days": days,
                                                "confirmation": word}), 400)
        self.assertFalse(session.done.is_set())
        self.assertEqual(self.post(session, {"min_size_mb": 5.5, "min_age_days": 90,
                                            "confirmation": "THRESHOLDS"}), 200)
        self.assertEqual(session.selected, {"min_size_mb": 5.5, "min_age_days": 90})

    def test_terminal_threshold_cancel_and_explicit_scan_confirmation(self):
        options = replace(self.options, review_html=False)
        self.assertIsNone(confirm_thresholds(options, emit=lambda _: None,
                                            prompt=lambda _: (_ for _ in ()).throw(EOFError())))
        for token, expected in (("", None), ("SCAN", {"min_size_mb": 6.5, "min_age_days": 30})):
            answers = iter(["6.5", "30", token])
            self.assertEqual(confirm_thresholds(options, emit=lambda _: None,
                                               prompt=lambda _: next(answers)), expected)

    def get_progress(self, session, path=None, host=None):
        connection = http.client.HTTPConnection("127.0.0.1", session.server.server_port, timeout=5)
        connection.request("GET", path or "/progress/" + session.token,
                           headers={"Host": host or session.origin.removeprefix("http://")})
        response = connection.getresponse()
        value = json.loads(response.read())
        status = response.status
        connection.close()
        return status, value

    def test_progress_survives_confirmation_and_handoff_requires_new_review_consent(self):
        session = ReviewSession({"mode": "thresholds", "repository": str(self.repo)})
        session.start()
        self.addCleanup(session.close)
        self.assertEqual(self.get_progress(session)[1]["state"], "waiting")
        threshold_body = {"min_size_mb": .0001, "min_age_days": 0, "confirmation": "THRESHOLDS"}
        self.assertEqual(self.post(session, threshold_body), 200)
        self.assertEqual(self.post(session, threshold_body), 409)
        session.update_progress("Object history", 2, 8, "Searching history")
        status, value = self.get_progress(session)
        self.assertEqual(status, 200)
        self.assertEqual((value["state"], value["completed"], value["total"]), ("running", 2, 8))
        self.assertGreaterEqual(value["elapsed_seconds"], 0)
        self.assertEqual(self.get_progress(session, host="untrusted.invalid")[0], 404)
        self.assertEqual(self.get_progress(session, path="/progress/invalid")[0], 404)
        old_digest = session.digest
        session.show_review(review_payload(self.result, self.options, "cleanup"))
        self.assertEqual(self.get_progress(session)[1]["state"], "ready")
        self.assertNotEqual(old_digest, session.digest)
        self.assertFalse(session.done.is_set())
        self.assertIsNone(session.selected)
        self.assertEqual(self.post(session, threshold_body), 400)
        self.assertEqual(self.post(session, {"selected": [self.old.sha], "confirmation": "REVIEW"}), 400)
        self.assertEqual(self.post(session, {"selected": [self.old.sha], "confirmation": "CLEANUP"}), 200)

    def test_real_history_reports_counts_and_failure_is_visible_without_approval(self):
        events = []
        self.auditor.progress = lambda stage, completed=None, total=None, detail="": events.append(
            (stage, completed, total))
        result = self.auditor.run()
        review_payload(result, self.options, "review", self.auditor.progress)
        history = [event for event in events if event[0] == "Object history"]
        self.assertEqual(history[0][1:], (0, len(result.blobs)))
        self.assertEqual(history[-1][1:], (len(result.blobs), len(result.blobs)))
        snapshots = [event for event in events if event[0] == "Historical snapshots"]
        self.assertTrue(snapshots)
        self.assertTrue(all(0 <= done <= total for _, done, total in snapshots))
        events.clear()
        historical_presence(self.auditor.runner, [self.keep], self.auditor.progress)
        self.assertEqual(events[-1][1:], (1, 3))
        session = self.session("review")
        worker = threading.Thread(target=session.finish_progress, kwargs={"error": "Git scan failed"})
        worker.start()
        status, value = self.get_progress(session)
        worker.join(timeout=3)
        self.assertFalse(worker.is_alive())
        self.assertEqual((status, value["state"], value["detail"]), (200, "failed", "Git scan failed"))
        self.assertIsNone(session.selected)

    def test_cancelled_persistent_threshold_session_is_closed_without_scanning(self):
        observed = []
        def cancel(session, options, emit, **kwargs):
            session.start()
            observed.append(session)
            self.assertFalse(kwargs["close"])
            self.assertEqual(self.post(session, {"cancel": True}), 200)
            return None
        with patch("src.html_review._await_browser", side_effect=cancel):
            self.assertIsNone(confirm_thresholds(self.options, emit=lambda _: None, keep_open=True))
        self.assertFalse(observed[0].thread.is_alive())

    def test_cli_cancelled_thresholds_never_scan_and_confirmed_values_reach_auditor(self):
        import git_history_auditor
        with patch("src.html_review.confirm_thresholds", return_value=None), \
                patch.object(GitLargeObjectAuditor, "run") as scan:
            self.assertEqual(git_history_auditor.main(["--repo", str(self.repo)]), 0)
            scan.assert_not_called()
        observed = []
        original = GitLargeObjectAuditor.run
        def run(auditor):
            observed.append((auditor.options.min_size_mb, auditor.options.min_age_days))
            return original(auditor)
        with patch("src.html_review.confirm_thresholds", return_value={"min_size_mb": .001, "min_age_days": 30}), \
                patch.object(GitLargeObjectAuditor, "run", run), \
                patch("src.html_review.review_in_browser") as review:
            self.assertEqual(git_history_auditor.main(["--repo", str(self.repo),
                             "--output-dir", str(self.root / "cli-reports")]), 0)
            review.assert_called_once()
        self.assertEqual(observed, [(.001, 30)])

    def test_unknown_protected_duplicate_and_wrong_token_are_rejected(self):
        session = self.session()
        for ids, confirmation in (([self.keep.sha], "CLEANUP"), (["f" * 40], "CLEANUP"),
                                    ([self.old.sha] * 2, "CLEANUP"), ([self.old.sha], "REVIEW"), ([], "CLEANUP")):
            self.assertEqual(self.post(session, {"selected": ids, "confirmation": confirmation}), 400)
        self.assertFalse(session.done.is_set())
        self.assertEqual(self.post(session, {"selected": [self.old.sha], "confirmation": "CLEANUP"}), 200)
        self.assertEqual(session.selected, [self.old.sha])
        self.assertEqual(self.post(session, {"cancel": True}), 409)

    def test_cross_origin_and_bad_session_headers_cannot_approve(self):
        session = self.session()
        body = {"selected": [self.old.sha], "confirmation": "CLEANUP"}
        self.assertEqual(self.post(session, body, Origin="https://other.example"), 403)
        self.assertEqual(self.post(session, body, **{"X-Review-Token": "invalid"}), 403)
        self.assertFalse(session.done.is_set())

    def test_cancel_and_timeout_do_not_approve(self):
        session = self.session()
        self.assertEqual(self.post(session, {"cancel": True}), 200)
        self.assertIsNone(session.selected)
        self.assertIsNone(review_in_browser(self.result, self.options, emit=lambda _: None))
        receipt = json.loads((self.result.output_dir / "browser-review.json").read_text())
        self.assertEqual(receipt["status"], "cancelled")
        self.assertFalse(receipt["push_approved"])

    def test_html_escapes_untrusted_paths_and_restricts_resources(self):
        payload = review_payload(self.result, self.options, "review")
        payload["rows"][0]["paths"] = ['</script><script>alert("bad")</script>.bin']
        session = ReviewSession(payload)
        session.start()
        self.addCleanup(session.close)
        connection = http.client.HTTPConnection("127.0.0.1", session.server.server_port)
        connection.request("GET", "/review/" + session.token)
        response = connection.getresponse()
        page = response.read().decode()
        self.assertEqual(response.status, 200)
        self.assertNotIn('<script>alert("bad")', page)
        self.assertIn("\\u003c/script", page)
        self.assertIn("frame-ancestors 'none'", response.getheader("Content-Security-Policy"))
        connection.close()

    def test_stale_audit_is_rejected_before_display(self):
        self.git("branch", "changed")
        with self.assertRaisesRegex(ValueError, "refs changed"):
            review_payload(self.result, self.options, "review")

    def test_real_disposable_cleanup_strips_selected_blob_only(self):
        before = self.git("rev-parse", "HEAD^{tree}")
        rewriter = GitHistoryRewriter(self.auditor.runner, self.options, emit=lambda _: None)
        # Simulate this test's explicit approval; never run against user history.
        with patch("src.html_review.review_in_browser", return_value=[self.old.sha]):
            rewriter.execute(self.result)
        reachable = self.git("rev-list", "--objects", "--all")
        self.assertNotIn(self.old.sha, reachable)
        other = next(b for b in self.result.blobs if "other.log" in b.paths)
        self.assertIn(other.sha, reachable)
        self.assertEqual(self.git("rev-parse", "HEAD^{tree}"), before)
        self.assertEqual((self.result.output_dir / "cleanup_blobs.txt").read_text().strip(), self.old.sha)
        self.assertTrue((self.result.output_dir / "before_cleanup.bundle").exists())

    def test_changes_during_browser_confirmation_stop_before_backup(self):
        def approve(*args, **kwargs):
            self.git("branch", "new-ref")
            return [self.old.sha]
        rewriter = GitHistoryRewriter(self.auditor.runner, self.options, emit=lambda _: None)
        with patch("src.html_review.review_in_browser", side_effect=approve), self.assertRaisesRegex(CleanupError, "changed"):
            rewriter.execute(self.result)
        self.assertFalse((self.result.output_dir / "before_cleanup.bundle").exists())


if __name__ == "__main__":
    unittest.main()
