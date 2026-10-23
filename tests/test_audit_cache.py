"""Cache parity, incremental reachability and bounded/disposable lifecycle."""
from argparse import Namespace
from dataclasses import replace
from contextlib import redirect_stdout
import io
import json
import os
from pathlib import Path
import sqlite3
import subprocess
import tempfile
import time
import unittest
from unittest.mock import patch

from src.auditor import GitLargeObjectAuditor
from src.audit_cache import AuditCache, CachedGitRunner
from src.blob_scanner import BlobScanner
from src.git_runner import GitCommandError, GitRunner
from src.models import AuditorOptions
from src.html_review import review_payload


class AuditCacheTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix="audit-cache-")
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.repo = self.root / "repository with spaces"
        self.repo.mkdir()
        self.git("init", "-b", "main")
        self.git("config", "user.name", "Cache Test")
        self.git("config", "user.email", "cache@example.invalid")
        self.git("config", "commit.gpgsign", "false")
        self.git("config", "core.autocrlf", "false")
        (self.repo / "old.bin").write_bytes(b"old blob" * 100)
        (self.repo / "small.txt").write_bytes(b"small")
        self.commit("initial")
        self.first = self.git("rev-parse", "HEAD")
        (self.repo / "old.bin").unlink()
        (self.repo / "current.txt").write_bytes(b"current" * 100)
        self.commit("delete old")
        self.options = AuditorOptions(repo=self.repo, cache=True, cache_dir=self.root / "cache",
                                      min_size_mb=.0001, min_age_days=0, output_dir=self.root / "reports")
        self.runner = GitRunner(self.repo)

    def git(self, *args):
        return subprocess.check_output(["git", "-C", str(self.repo), *args], stderr=subprocess.PIPE).decode().strip()

    def commit(self, message):
        self.git("add", "--all")
        self.git("commit", "-m", message)

    def cache(self, **overrides):
        cache = AuditCache(self.runner, replace(self.options, **overrides))
        self.addCleanup(cache.close)
        return cache

    def audit(self, **overrides):
        options = replace(self.options, **overrides)
        return GitLargeObjectAuditor(Namespace(**vars(options))).run()

    def facts(self, result):
        return sorted((b.sha, b.size_bytes, sorted(b.paths), b.status.value, b.action.value,
                       b.first_seen_commit, b.last_seen_commit, b.commit_count,
                       sorted(b.current_refs), sorted(b.gerrit_refs)) for b in result.blobs)

    def test_warm_run_skips_full_enumeration_and_reclassifies_thresholds(self):
        cold = self.audit()
        original = GitRunner.run_with_status
        calls = []
        def track(runner, *args):
            calls.append(args)
            return original(runner, *args)
        with patch.object(GitRunner, "run_with_status", track), \
                patch.object(BlobScanner, "_batch_check", side_effect=AssertionError("metadata should be reused")):
            warm = self.audit()
        self.assertEqual(self.facts(cold), self.facts(warm))
        self.assertEqual(warm.cache_info["mode"], "reused")
        self.assertEqual(warm.cache_info["inventory"]["object_ids_checked"], 0)
        self.assertGreater(warm.cache_info["hits"], 0)
        self.assertFalse(any(args[0] == "log" and any("--find-object" in arg for arg in args) for args in calls))
        self.assertEqual(self.audit(min_size_mb=1).blobs, [])
        self.assertTrue(any("small.txt" in b.paths for b in self.audit(min_size_mb=.000001).blobs))
        protected = self.audit(protect_text_data_files=True)
        self.assertEqual(self.facts(protected), self.facts(self.audit(cache=False, protect_text_data_files=True)))

    def test_append_alias_merge_and_branch_match_fresh_scan(self):
        self.audit()
        self.git("branch", "side")
        (self.repo / "alias.ZIP").write_bytes(b"old blob" * 100)
        self.commit("restore blob under alias")
        incremental = self.audit()
        self.assertEqual(incremental.cache_info["mode"], "incremental")
        self.assertLess(incremental.cache_info["inventory"]["object_ids_checked"],
                        incremental.cache_info["inventory"]["total_object_ids"])
        self.assertEqual(self.facts(incremental), self.facts(self.audit(cache=False)))
        old = next(b for b in incremental.blobs if "old.bin" in b.paths)
        self.assertIn("alias.ZIP", old.paths)
        self.git("checkout", "side")
        (self.repo / "side.bin").write_bytes(b"side payload" * 100)
        self.commit("unmerged branch")
        self.git("checkout", "main")
        self.git("merge", "--no-ff", "side", "-m", "merge side")
        merged = self.audit()
        self.assertEqual(merged.cache_info["mode"], "incremental")
        self.assertEqual(self.facts(merged), self.facts(self.audit(cache=False)))

    def test_ref_removal_and_rewrite_trigger_full_rebuild(self):
        self.git("branch", "keep")
        self.audit()
        self.git("branch", "-D", "keep")
        removed = self.audit()
        self.assertEqual(removed.cache_info["mode"], "full")
        self.assertTrue(any("removed/rewritten" in e for e in removed.cache_info["events"]))
        self.git("reset", "--hard", self.first)
        rewritten = self.audit()
        self.assertEqual(rewritten.cache_info["mode"], "full")
        self.assertEqual(self.facts(rewritten), self.facts(self.audit(cache=False)))

    def test_expiry_lru_clear_and_corruption_fallback(self):
        cache = self.cache(cache_max_mb=.001)
        cache.put("test", "old", "x" * 600)
        cache.put("test", "new", "y" * 600)
        self.assertIsNone(cache.get("test", "old"))
        self.assertEqual(cache.get("test", "new"), "y" * 600)
        cache.connection.execute("UPDATE entries SET created=?", (time.time() - 31 * 86400,))
        cache.prune()
        self.assertEqual(cache.info()["entries"], 0)
        cache.put("test", "bad", "ok")
        cache.connection.execute("UPDATE entries SET value='broken'")
        self.assertIsNone(cache.get("test", "bad"))
        cache.put("test", "clear", 123)
        sentinel = self.root / "do-not-delete.txt"
        sentinel.write_text("keep")
        self.assertTrue(cache.clear())
        self.assertEqual(cache.info()["entries"], 0)
        self.assertTrue(sentinel.exists())
        path = cache.path
        cache.close()
        path.write_bytes(b"not a SQLite database")
        broken = self.cache()
        self.assertIsNone(broken.connection)
        self.assertEqual(self.facts(self.audit()), self.facts(self.audit(cache=False)))

    def test_config_schema_and_cleanup_bypass(self):
        self.audit()
        self.git("config", "audit.test", "changed")
        self.assertEqual(self.audit().cache_info["mode"], "full")
        cache = self.cache()
        cache.connection.execute("PRAGMA user_version=999")
        cache.close()
        self.assertEqual(self.audit().cache_info["mode"], "full")
        fresh = self.audit(execute_cleanup=True)
        self.assertEqual(fresh.cache_info["mode"], "disabled")
        self.assertEqual(self.facts(fresh), self.facts(self.audit(cache=False)))

    def test_read_query_uses_frozen_oids_and_rejects_in_worktree_cache(self):
        cache = self.cache()
        cached = CachedGitRunner(self.runner, cache, self.runner.ref_state())
        before = cached.run("log", "--format=%H", "HEAD")
        (self.repo / "new.txt").write_text("new")
        self.commit("later")
        self.assertEqual(cached.run("log", "--format=%H", "HEAD"), before)
        unsafe = self.cache(cache_dir=self.repo / "cache")
        self.assertIsNone(unsafe.connection)
        self.assertFalse((self.repo / "cache").exists())

    def test_gerrit_and_moved_tags_match_uncached_results(self):
        self.audit()
        self.git("update-ref", "refs/changes/01/101/1", self.first)
        added = self.audit()
        self.assertEqual(added.cache_info["mode"], "incremental")
        self.assertEqual(self.facts(added), self.facts(self.audit(cache=False)))
        self.git("tag", "archived", self.first)
        self.audit()
        self.git("tag", "-f", "archived", "HEAD")
        self.assertEqual(self.audit().cache_info["mode"], "full")
        self.git("update-ref", "-d", "refs/changes/01/101/1")
        removed = self.audit()
        self.assertEqual(removed.cache_info["mode"], "full")
        self.assertEqual(self.facts(removed), self.facts(self.audit(cache=False)))

    def test_parallel_batch_ref_changes_reject_all_new_cache_writes(self):
        cache = self.cache()
        state = self.runner.ref_state()
        cached = CachedGitRunner(self.runner, cache, state)
        queries = [("log", "--format=%H", "HEAD"), ("ls-tree", "-r", "HEAD")]
        before = cache.writes
        changed = ("0" * 40, state[1], state[2])
        with patch.object(self.runner, "ref_state", side_effect=[state, changed]):
            with self.assertRaisesRegex(GitCommandError, "Refs changed during"):
                list(cached.iter_queries(queries, workers=2))
        self.assertEqual(cache.writes, before)

    def test_parallel_failed_query_is_not_cached(self):
        cache = self.cache()
        cached = CachedGitRunner(self.runner, cache, self.runner.ref_state())
        query = ("log", "--format=%H", "no-such-ref")
        before = cache.writes
        result = list(cached.iter_queries([query], workers=2))[0]
        self.assertNotEqual(result[0], 0)
        self.assertEqual(cache.writes, before)

    def test_presence_reused_only_for_matching_refs(self):
        result = self.audit()
        payload = review_payload(result, self.options, "review")
        with patch("src.html_review.historical_presence", side_effect=AssertionError("presence should be reused")):
            self.assertEqual(review_payload(result, self.options, "review"), payload)
        (self.repo / "unrelated.txt").write_text("new unrelated commit")
        self.git("add", "--all")
        env = dict(os.environ, GIT_AUTHOR_DATE="2030-01-01T00:00:00Z", GIT_COMMITTER_DATE="2030-01-01T00:00:00Z")
        subprocess.check_output(["git", "-C", str(self.repo), "commit", "-m", "new snapshot"], env=env)
        result = self.audit()
        payload = review_payload(result, self.options, "review")
        row = next(r for r in payload["rows"] if "current.txt" in r["paths"])
        self.assertEqual(row["last_present"]["commit"], self.git("rev-parse", "HEAD"))

    def test_locked_database_falls_back_and_clear_is_repository_scoped(self):
        cache = self.cache()
        cache.put("test", "keep", 123)
        other_repo = self.root / "other repo"
        subprocess.check_output(["git", "clone", "--no-local", str(self.repo), str(other_repo)], stderr=subprocess.PIPE)
        other = AuditCache(GitRunner(other_repo), replace(self.options, repo=other_repo))
        self.addCleanup(other.close)
        other.put("test", "keep", 456)
        cache.clear()
        self.assertEqual(other.get("test", "keep"), 456)
        cache.connection.execute("BEGIN EXCLUSIVE")
        try:
            busy = self.cache()
            self.assertIsNone(busy.connection)
            fresh = self.audit()
            self.assertEqual(fresh.cache_info["mode"], "disabled")
        finally:
            cache.connection.execute("ROLLBACK")
        self.assertEqual(self.facts(fresh), self.facts(self.audit(cache=False)))

    def test_unowned_database_is_not_repurposed(self):
        cache = self.cache()
        cache.put("test", "sentinel", "preserve")
        path = cache.path
        cache.connection.execute("PRAGMA application_id=0")
        cache.close()
        unowned = self.cache()
        self.assertIsNone(unowned.connection)
        connection = sqlite3.connect(path)
        try:
            self.assertEqual(connection.execute("SELECT COUNT(*) FROM entries").fetchone()[0], 1)
        finally:
            connection.close()

    def test_packaged_cache_command_lifecycle(self):
        from git_history_toolkit import main
        self.audit()
        for action in ("status", "prune", "clear"):
            output = io.StringIO()
            with redirect_stdout(output):
                status = main(["cache", action, "--repo", str(self.repo),
                               "--cache-dir", str(self.options.cache_dir)])
            self.assertEqual(status, 0)
            info = json.loads(output.getvalue())
            self.assertIn("database_bytes", info)
            if action == "clear":
                self.assertEqual(info["entries"], 0)


if __name__ == "__main__":
    unittest.main()
