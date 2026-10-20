from __future__ import annotations

from argparse import Namespace
from dataclasses import replace
from pathlib import Path
import os
import subprocess
import tempfile
import unittest
from unittest.mock import patch

from src.auditor import GitLargeObjectAuditor
from src.blob_scanner import BlobScanner
from src.gerrit_analyzer import GerritAnalyzer
from src.git_runner import GitRunner
from src.models import RefInfo, ScanCoverage
from src.policy import PathPolicy
from src.rewriter import CleanupError, GitHistoryRewriter


class SafetyTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix="auditor-safety-")
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.repo = self.root / "repo"
        self.repo.mkdir()
        self.git("init", "-b", "main")
        self.git("config", "user.name", "Safety Test")
        self.git("config", "user.email", "safety@example.invalid")
        self.git("config", "commit.gpgsign", "false")
        self.content = b"old binary payload\n" * 500
        (self.repo / "old.bin").write_bytes(self.content)
        self.commit("Add historical object")
        (self.repo / "old.bin").unlink()
        (self.repo / "current.txt").write_text("current\n")
        self.commit("Delete historical object")
        self.runner = GitRunner(self.repo)

    def git(self, *args):
        env = os.environ.copy()
        env.update(GIT_AUTHOR_DATE="2020-01-01T12:00:00Z", GIT_COMMITTER_DATE="2020-01-01T12:00:00Z")
        return subprocess.check_output(["git", "-C", str(self.repo), *args], env=env,
                                       stderr=subprocess.PIPE).decode().strip()

    def commit(self, message):
        self.git("add", "--all")
        self.git("commit", "-m", message)

    def audit(self, repo=None):
        auditor = GitLargeObjectAuditor(Namespace(repo=str(repo or self.repo), min_size_mb=0.001,
                                                 min_age_days=0, output_dir=str(self.root / "reports")))
        return auditor, auditor.run()

    def rewriter(self, auditor, confirm=lambda prompt: "", **kwargs):
        return GitHistoryRewriter(auditor.runner, auditor.options, confirm=confirm,
                                  emit=lambda message: None, **kwargs)

    def test_all_historical_aliases_support_suffix_and_path_protection(self):
        for name in ("alias.py", "space and dash.bin"):
            (self.repo / name).write_bytes(self.content)
        self.commit("Reintroduce same blob with multiple paths")
        for name in ("alias.py", "space and dash.bin"):
            (self.repo / name).unlink()
        self.commit("Delete historical aliases")
        blobs = BlobScanner(self.runner).scan(1024)
        self.assertEqual(len(blobs), 1)
        self.assertTrue({"old.bin", "alias.py", "space and dash.bin"}.issubset(blobs[0].paths))
        auditor, _ = self.audit()
        self.assertTrue(PathPolicy(replace(auditor.options, protect_code_files=True)).is_explicitly_protected(blobs[0]))
        self.assertTrue(PathPolicy(replace(auditor.options, keep_paths=("alias.py",))).is_explicitly_protected(blobs[0]))

    def test_meta_only_is_not_patchset_coverage(self):
        coverage = ScanCoverage()
        GerritAnalyzer(self.runner).analyze([RefInfo("refs/changes/01/1001/meta", "a" * 40, "gerrit")], {}, coverage)
        self.assertFalse(coverage.gerrit_refs)
        self.assertTrue(coverage.warnings)

    def test_audit_from_subdirectory_uses_repository_root(self):
        subdir = self.repo / "nested"
        subdir.mkdir()
        auditor, result = self.audit(subdir)
        self.assertEqual(auditor.runner.repo, self.repo.resolve())
        self.assertEqual(result.repository, self.repo.resolve())

    def test_stale_audit_refuses_before_confirmation(self):
        auditor, result = self.audit()
        self.git("branch", "new-ref")
        called = []
        rewriter = self.rewriter(auditor, lambda prompt: called.append(prompt) or "CLEANUP")
        with patch.object(rewriter, "_ensure_cleanup_prerequisites"), self.assertRaisesRegex(CleanupError, "stale"):
            rewriter.execute(result)
        self.assertFalse(called)
        self.assertFalse((result.output_dir / "before_cleanup.bundle").exists())

    def test_new_untracked_file_during_confirmation_prevents_rewrite(self):
        auditor, result = self.audit()
        def confirm(prompt):
            (self.repo / "user-work.txt").write_text("do not discard")
            return "CLEANUP"
        rewriter = self.rewriter(auditor, confirm)
        with patch.object(rewriter, "_ensure_cleanup_prerequisites"), \
             patch.object(rewriter, "_run_filter_repo") as rewrite, \
             self.assertRaisesRegex(CleanupError, "clean worktree"):
            rewriter.execute(result)
        rewrite.assert_not_called()

    def test_ref_change_during_confirmation_prevents_rewrite(self):
        auditor, result = self.audit()
        def confirm(prompt):
            self.git("branch", "changed-during-confirmation")
            return "CLEANUP"
        rewriter = self.rewriter(auditor, confirm)
        with patch.object(rewriter, "_ensure_cleanup_prerequisites"), \
             patch.object(rewriter, "_run_filter_repo") as rewrite, \
             self.assertRaisesRegex(CleanupError, "changed during confirmation"):
            rewriter.execute(result)
        rewrite.assert_not_called()

    def test_eof_and_cancel_never_create_backup_or_rewrite(self):
        auditor, result = self.audit()
        for response in ("", "no", EOFError(), KeyboardInterrupt()):
            def confirm(prompt):
                if isinstance(response, BaseException):
                    raise response
                return response
            rewriter = self.rewriter(auditor, confirm)
            with patch.object(rewriter, "_ensure_cleanup_prerequisites"), \
                 patch.object(rewriter, "_run_filter_repo") as rewrite:
                rewriter.execute(result)
                rewrite.assert_not_called()
        self.assertFalse((result.output_dir / "before_cleanup.bundle").exists())

    def test_verified_backup_and_distinct_remote_urls_survive_simulated_filter(self):
        self.git("remote", "add", "origin", "https://fetch.invalid/project")
        self.git("remote", "set-url", "--push", "origin", "ssh://push.invalid/project")
        self.git("config", "branch.main.remote", "origin")
        self.git("config", "branch.main.merge", "refs/heads/main")
        auditor, result = self.audit()
        rewriter = self.rewriter(auditor, lambda prompt: "CLEANUP")
        original = rewriter._remote_configuration()
        def simulated_filter(ids_file):
            self.git("bundle", "verify", str(result.output_dir / "before_cleanup.bundle"))
            self.assertTrue(ids_file.read_text().strip())
            self.git("remote", "remove", "origin")
        with patch.object(rewriter, "_ensure_cleanup_prerequisites"), \
             patch.object(rewriter, "_run_filter_repo", side_effect=simulated_filter):
            rewriter.execute(result)
        self.assertEqual(rewriter._remote_configuration(), original)
        self.assertEqual(self.git("remote", "get-url", "origin"), "https://fetch.invalid/project")
        self.assertEqual(self.git("remote", "get-url", "--push", "origin"), "ssh://push.invalid/project")

    def test_lease_uses_push_destination_not_stale_tracking_ref(self):
        self.git("remote", "add", "origin", "https://fetch.invalid/project")
        self.git("remote", "set-url", "--push", "origin", "ssh://push.invalid/project")
        self.git("update-ref", "refs/remotes/origin/main", self.git("rev-parse", "HEAD"))
        auditor, _ = self.audit()
        rewriter = self.rewriter(auditor)
        with patch.object(auditor.runner, "run_with_status", return_value=(0, "b" * 40 + "\trefs/heads/main\n", "")) as query:
            self.assertEqual(rewriter._remote_branch_oid("main"), "b" * 40)
        query.assert_called_once_with("ls-remote", "--exit-code", "ssh://push.invalid/project", "refs/heads/main")

    def test_missing_remote_lease_stops_push(self):
        self.git("remote", "add", "origin", "https://fetch.invalid/project")
        auditor, _ = self.audit()
        rewriter = self.rewriter(auditor)
        with patch.object(auditor.runner, "run_with_status", return_value=(0, "", "")), \
             self.assertRaisesRegex(CleanupError, "lease is missing"):
            rewriter._push()

    def test_cleanup_requires_head_protection(self):
        auditor, _ = self.audit()
        rewriter = GitHistoryRewriter(auditor.runner, replace(auditor.options, protect_head=False))
        with self.assertRaisesRegex(CleanupError, "HEAD protection"):
            rewriter._ensure_cleanup_prerequisites()

    def test_cleanup_rejects_disabled_gerrit_scan_when_refs_exist(self):
        self.git("update-ref", "refs/changes/01/1001/1", self.git("rev-parse", "HEAD"))
        auditor, _ = self.audit()
        rewriter = GitHistoryRewriter(auditor.runner, replace(auditor.options, scan_gerrit_refs=False))
        with self.assertRaisesRegex(CleanupError, "Gerrit refs exist"):
            rewriter._ensure_cleanup_prerequisites()

    def test_cleanup_rejects_multiple_worktrees(self):
        self.git("worktree", "add", "--detach", str(self.root / "linked"), "HEAD")
        auditor, _ = self.audit()
        rewriter = self.rewriter(auditor)
        with self.assertRaisesRegex(CleanupError, "linked worktrees"):
            rewriter._ensure_cleanup_prerequisites()


if __name__ == "__main__":
    unittest.main()
