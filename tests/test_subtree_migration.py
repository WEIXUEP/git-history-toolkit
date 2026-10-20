"""Integration tests using the published large-object fixture's subtree branch."""

import json
import os
from pathlib import Path
import tempfile
import unittest

from src.subtree_filter import mapped_path, rewrite_message
from src.subtree_migration import MigrationError, commit_data, git, migrate, plan_migration, publication_plan, refs, text, tree


class SubtreeUnitTests(unittest.TestCase):
    def test_only_directory_boundaries_are_mapped(self):
        moves = {b"vendor/a": b"components/a"}
        self.assertEqual(mapped_path(b"vendor/a/file", moves), b"components/a/file")
        self.assertEqual(mapped_path(b"vendor/ab/file", moves), b"vendor/ab/file")

    def test_trailers_preserve_upstream_split_and_unrelated_text(self):
        old, new = b"a" * 40, b"b" * 40
        message = (b"Mention vendor/a in prose\n\nChange-Id: I123\n"
                   b"git-subtree-dir: vendor/a\ngit-subtree-mainline: " + old +
                   b"\ngit-subtree-split: " + old + b"\n")
        actual = rewrite_message(message, {b"vendor/a": b"components/a"}, lambda sha: new)
        self.assertIn(b"Mention vendor/a in prose", actual)
        self.assertIn(b"Change-Id: I123", actual)
        self.assertIn(b"git-subtree-dir: components/a", actual)
        self.assertIn(b"git-subtree-mainline: " + new, actual)
        self.assertIn(b"git-subtree-split: " + old, actual)


class SubtreeFixtureTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.fixture = Path(os.environ.get("GIT_HISTORY_FIXTURE", str(Path(__file__).resolve().parents[2] / "git-auditor-test-repo")))
        if not cls.fixture.exists() or "refs/heads/fixture/subtree" not in refs(cls.fixture):
            raise unittest.SkipTest("Clone the published fixture and create local fixture/subtree refs to run integration tests")
        cls.temporary = tempfile.TemporaryDirectory(prefix="subtree-migration-tests-")
        cls.addClassCleanup(cls.temporary.cleanup)
        cls.root = Path(cls.temporary.name)
        cls.before = refs(cls.fixture)
        cls.fixture_manifest = json.loads(git(cls.fixture, "show", "fixture/subtree:subtree-fixture.json"))
        cls.moves = cls.fixture_manifest["moves"]
        cls.selected = ["refs/heads/fixture/subtree", "refs/heads/fixture/subtree-topic", "refs/tags/fixture-subtree/release"]
        cls.plan = plan_migration(cls.fixture, cls.moves, cls.selected, cls.root / "migration")
        cls.result = migrate(cls.plan, cls.root / "rewritten", confirm=lambda prompt: "MIGRATE")
        cls.workspace = cls.root / "rewritten"
        cls.mapping = dict(line.split() for line in (cls.root / "migration/commit-map.txt").read_text().splitlines()[1:])

    def test_all_commits_content_and_topology_verified(self):
        self.assertEqual(self.result["status"], "passed")
        self.assertGreater(self.result["checked_commits"], 100)
        self.assertTrue(self.result["source_unchanged"])
        self.assertFalse(self.result["push_executed"])
        self.assertEqual(refs(self.fixture), self.before)

    def test_default_log_and_blame_find_original_local_change(self):
        alice = self.mapping[self.fixture_manifest["alice_commit"]]
        log = text(self.workspace, "log", "--format=%H", "fixture/subtree", "--", "components/a/lib.txt")
        self.assertIn(alice, log.splitlines())
        blame = text(self.workspace, "blame", "--line-porcelain", "fixture/subtree", "--", "components/a/lib.txt")
        self.assertIn("author Subtree Alice", blame)
        self.assertIn("locally maintained first line", blame)

    def test_unselected_refs_and_raw_upstream_objects_stay_unchanged(self):
        for ref in ("refs/remotes/origin/fixture/subtree-original", "refs/tags/fixture-subtree/upstream-a-v2"):
            self.assertEqual(text(self.workspace, "rev-parse", ref), text(self.fixture, "rev-parse", ref.replace("refs/remotes/origin/", "refs/heads/")))
        split = self.fixture_manifest["upstream_commits"]["a"]["v2"]
        self.assertEqual(tree(self.fixture, split), tree(self.workspace, split))
        self.assertIn(b"vendor/a/embedded.txt", tree(self.workspace, split))
        tip = tree(self.workspace, "fixture/subtree")
        self.assertIn(b"components/a/vendor/a/embedded.txt", tip)
        self.assertFalse(any(path.startswith(b"vendor/a/") or path.startswith(b"vendor/b/") for path in tip))

    def test_incremental_updates_and_repeat_pulls_work_for_both_subtrees(self):
        checked = [item for item in self.result["incremental_checks"] if item["status"] == "passed"]
        self.assertGreaterEqual(len(checked), 6)
        self.assertTrue(all(item["incremental_pulls"] == 2 and item["idempotency_checks"] == 2 for item in checked))

    def test_historical_rename_aliases_are_supported_in_plan(self):
        plan = plan_migration(self.fixture, {"vendor/a": "components/a", "vendor/legacy-a": "components/a"},
                              ["refs/heads/fixture/subtree-renamed"], self.root / "alias-plan")
        self.assertTrue(plan.exists())
        result = migrate(plan, self.root / "alias-workspace", confirm=lambda prompt: "MIGRATE")
        self.assertEqual(result["status"], "passed")
        paths = tree(self.root / "alias-workspace", "fixture/subtree-renamed")
        self.assertIn(b"components/a/lib.txt", paths)
        self.assertFalse(any(p.startswith(b"vendor/legacy-a/") for p in paths))

    def test_publication_plan_uses_explicit_leases_without_pushing(self):
        remote = self.root / "publication.git"
        remote.mkdir()
        text(remote, "init", "--bare")
        for ref in self.selected:
            text(self.fixture, "push", str(remote), f"{ref}:{ref}")
        before = refs(remote)
        result = publication_plan(self.plan, str(remote))
        self.assertFalse(result["push_executed"])
        self.assertEqual(refs(remote), before)
        for item in result["commands"]:
            self.assertIn(f"--force-with-lease={item['ref']}:{before[item['ref']]}", item["argv"])
        text(remote, "update-ref", self.selected[0], text(self.fixture, "rev-parse", self.selected[0] + "^"))
        with self.assertRaises(MigrationError):
            publication_plan(self.plan, str(remote))

    def test_cancellation_does_not_create_workspace_or_backup(self):
        plan = plan_migration(self.fixture, self.moves, [self.selected[0]], self.root / "cancel-plan")
        target = self.root / "cancelled"
        self.assertIsNone(migrate(plan, target, confirm=lambda prompt: ""))
        self.assertFalse(target.exists())
        self.assertFalse((plan.parent / "before_migration.bundle").exists())

    def test_invalid_or_overlapping_paths_are_rejected(self):
        for moves in ({"vendor/a": "vendor/a/nested"}, {"vendor/a": "components/a", "vendor/a/nested": "other"}):
            with self.assertRaises(MigrationError):
                plan_migration(self.fixture, moves, [self.selected[0]], self.root / "invalid")


if __name__ == "__main__":
    unittest.main()
