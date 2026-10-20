"""Browser approval, exact historical presence and selected-object cleanup."""

from argparse import Namespace
from dataclasses import replace
import http.client
import json
import os
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest.mock import patch

from src.auditor import GitLargeObjectAuditor
from src.html_review import ReviewSession, historical_presence, review_in_browser, review_payload
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
