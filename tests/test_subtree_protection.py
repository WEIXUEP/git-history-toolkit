"""Reproduce relocation trailers contaminating squash ancestry protection."""

import json
import os
from pathlib import Path
import tempfile
import unittest

from src.subtree_filter import neutralize_relocation
from src.subtree_migration import MigrationError, classify_subtree_metadata, commit_data, configure_identity, git, migrate, plan_migration, refs, text, transformed, tree


class SubtreeGitlinkUnitTests(unittest.TestCase):
    def test_neutralization_preserves_exact_values_prose_and_line_endings(self):
        message = (b"Relocate palace\r\n\r\nChange-Id: I123\r\n"
                   b"git-subtree-dir: fem_core\r\ngit-subtree-split: " + b"a" * 40 + b"\r\n")
        actual = neutralize_relocation(message)
        self.assertEqual(actual, message.replace(b"git-subtree-", b"git-history-toolkit-original-subtree-"))
        self.assertNotIn(b"\ngit-subtree-dir:", actual)
        self.assertIn(b"Change-Id: I123\r\n", actual)

    def test_exact_source_gitlink_stays_and_nested_gitlink_moves(self):
        link = b"160000 commit " + b"a" * 40
        modules = b"100644 blob " + b"b" * 40
        moves = {b"palace": b"fem_core"}
        self.assertEqual(transformed({b"palace": link, b".gitmodules": modules}, moves),
                         {b"palace": link, b".gitmodules": modules})
        self.assertEqual(transformed({b"palace/dep": link}, moves), {b"fem_core/dep": link})

    def test_blob_symlink_and_gitlink_collisions_remain_rejected(self):
        moves = {b"palace": b"fem_core"}
        for mode in (b"100644", b"100755", b"120000"):
            with self.subTest(mode=mode), self.assertRaisesRegex(MigrationError, "source directory was a file"):
                transformed({b"palace": mode + b" blob " + b"a" * 40}, moves)
        link = b"160000 commit " + b"a" * 40
        for entries in ({b"palace/dep": link, b"fem_core/dep": link},
                        {b"fem_core": link, b"palace/file": b"100644 blob " + b"b" * 40}):
            with self.subTest(entries=entries), self.assertRaisesRegex(MigrationError, "collision"):
                transformed(entries, moves)


class SubtreeProtectionTests(unittest.TestCase):
    def test_relocated_host_metadata_does_not_protect_host_history(self):
        self.exercise_history()

    def test_gitlink_directory_and_subtree_eras(self):
        self.exercise_history(include_gitlinks=True)

    def exercise_history(self, include_gitlinks=False):
        with tempfile.TemporaryDirectory(prefix="subtree-protection-") as temporary:
            root = Path(temporary)
            host, upstream = root / "host", root / "upstream"
            for repo in (host, upstream):
                repo.mkdir()
                text(repo, "init", "-b", "master")
                configure_identity(repo)

            def blob(repo, content):
                return git(repo, "hash-object", "-w", "--stdin", data=content).decode().strip()

            def make_tree(repo, entries):
                data = b"".join(f"{mode} {kind} {oid}\t{name}\n".encode()
                                for name, mode, kind, oid in sorted(entries))
                return git(repo, "mktree", data=data).decode().strip()

            commit_index = 0

            def commit(repo, tree_oid, parents, message):
                nonlocal commit_index
                commit_index += 1
                args = ["commit-tree", tree_oid]
                for parent in parents:
                    args.extend(["-p", parent])
                env = dict(os.environ, GIT_AUTHOR_DATE=f"{1600000000 + commit_index * 60} +0000",
                           GIT_COMMITTER_DATE=f"{1600000000 + commit_index * 60} +0000")
                return git(repo, *args, data=(message + "\n").encode(), env=env).decode().strip()

            readme = blob(host, b"Host project\n")
            base_tree = make_tree(host, [("README", "100644", "blob", readme)])
            base = commit(host, base_tree, [], "Initialize host")
            tip, upstream_tip, squash_tip = base, None, None
            squash_commits, splits, source_snapshots = [], [], []
            gitlink_snapshots = []

            def host_tree(prefix, library_tree):
                return make_tree(host, [("README", "100644", "blob", readme),
                                        (prefix, "040000", "tree", library_tree)])

            if include_gitlinks:
                modules = blob(host, b'[submodule "palace"]\n\tpath = palace\n\turl = https://example.invalid/palace.git\n')
                for index in range(2):
                    pointer = str(index + 1) * 40
                    link_tree = make_tree(host, [("README", "100644", "blob", readme),
                                                 (".gitmodules", "100644", "blob", modules),
                                                 ("palace", "160000", "commit", pointer)])
                    tip = commit(host, link_tree, [tip], f"Historical submodule revision {index}")
                    gitlink_snapshots.append(tip)
                tip = commit(host, base_tree, [tip], "Remove historical submodule")
                manual_tree = make_tree(host, [("lib.txt", "100644", "blob", blob(host, b"Manually copied library\n")),
                                               ("dep", "160000", "commit", "4" * 40)])
                manual_copy = commit(host, host_tree("palace", manual_tree), [tip], "Copy library as ordinary directory")
                tip = manual_copy
                source_snapshots.append(tip)
                link_tree = make_tree(host, [("README", "100644", "blob", readme),
                                             (".gitmodules", "100644", "blob", modules),
                                             ("palace", "160000", "commit", "3" * 40)])
                tip = commit(host, link_tree, [tip], "Replace copied directory with submodule")
                gitlink_snapshots.append(tip)
                tip = commit(host, base_tree, [tip], "Remove submodule before squash import")

            # Transfer only trees/blobs, as a squash import does. None of the
            # six real upstream commit objects is available in the host.
            for index in range(6):
                content = f"upstream first line\nversion {index}\n".encode()
                upstream_tree = make_tree(upstream, [("lib.txt", "100644", "blob", blob(upstream, content))])
                upstream_tip = commit(upstream, upstream_tree, [upstream_tip] if upstream_tip else [],
                                      f"Upstream revision {index}")
                splits.append(upstream_tip)
                library_tree = make_tree(host, [("lib.txt", "100644", "blob", blob(host, content))])
                squash_tip = commit(host, library_tree, [squash_tip] if squash_tip else [],
                                    f"Squashed palace {index}\n\ngit-subtree-dir: palace\n"
                                    f"git-subtree-split: {upstream_tip}")
                squash_commits.append(squash_tip)
                current_tree = host_tree("palace", library_tree)
                tip = commit(host, current_tree, [tip, squash_tip],
                             f"Import palace {index}\n\ngit-subtree-dir: palace\n"
                             f"git-subtree-mainline: {tip}\ngit-subtree-split: {upstream_tip}")
                source_snapshots.append(tip)
            text(upstream, "update-ref", "refs/heads/master", upstream_tip)
            anchor = tip

            other_blob = blob(host, b"Other subtree\n")
            other_tree = make_tree(host, [("other.txt", "100644", "blob", other_blob)])
            other_squash = commit(host, other_tree, [], "Other squash\n\ngit-subtree-dir: other\n"
                                  + "git-subtree-split: " + "f" * 40)
            current_tree = make_tree(host, [("README", "100644", "blob", readme),
                                           ("palace", "040000", "tree", library_tree),
                                           ("other", "040000", "tree", other_tree)])
            tip = commit(host, current_tree, [tip, other_squash], "Import unrelated subtree")
            source_snapshots.append(tip)
            local_library = make_tree(host, [("lib.txt", "100644", "blob", blob(
                host, b"locally maintained first line\nversion 5\n")),
                ("LOCAL_README.md", "100644", "blob", blob(host, b"Local guide before relocation\n")),
                ("LOCAL_CHANGELOG.md", "100644", "blob", blob(host, b"Local additions must never disappear\n"))])
            current_tree = make_tree(host, [("README", "100644", "blob", readme),
                                           ("palace", "040000", "tree", local_library),
                                           ("other", "040000", "tree", other_tree)])
            local_edit = commit(host, current_tree, [tip], "Maintain palace locally")
            tip = local_edit
            source_snapshots.append(tip)
            for index in range(198 if include_gitlinks else 202):
                tip = commit(host, current_tree, [tip], f"Host maintenance {index}")
                source_snapshots.append(tip)
            self.assertEqual(len(source_snapshots), 207 if include_gitlinks else 210)

            renamed_tree = make_tree(host, [("README", "100644", "blob", readme),
                                           ("fem_core", "040000", "tree", local_library),
                                           ("other", "040000", "tree", other_tree)])
            rename = commit(host, renamed_tree, [tip], "Rename palace")
            relocation = commit(host, renamed_tree, [rename], "Relocate palace subtree\n\n"
                                f"Change-Id: I123\ngit-subtree-dir: fem_core\ngit-subtree-split: {upstream_tip}")
            final_library = make_tree(host, [("lib.txt", "100644", "blob", blob(
                host, b"locally maintained first line\nversion 5 locally protected\n")),
                ("LOCAL_README.md", "100644", "blob", blob(host, b"Local guide modified after relocation\n")),
                ("LOCAL_CHANGELOG.md", "100644", "blob", blob(host, b"Local additions must never disappear\n"))])
            final_tree = make_tree(host, [("README", "100644", "blob", readme),
                                         ("fem_core", "040000", "tree", final_library),
                                         ("other", "040000", "tree", other_tree)])
            post_relocation = commit(host, final_tree, [relocation], "Protect local code after relocation")
            side_tip = anchor
            side_tree = host_tree("palace", library_tree)
            for index in range(2):
                side_tip = commit(host, side_tree, [side_tip], f"Side maintenance {index}")
                source_snapshots.append(side_tip)
            tip = commit(host, final_tree, [post_relocation, side_tip], "Merge side work after relocation")
            text(host, "update-ref", "refs/heads/master", tip)
            text(host, "checkout", "master")
            before = refs(host)
            for split in splits:
                self.assertEqual(git(host, "rev-parse", "--verify", split + "^{commit}", check=False), b"")

            with self.assertRaisesRegex(MigrationError, "--repair-relocation-metadata"):
                plan_migration(host, {"palace": "fem_core"}, ["refs/heads/master"], root / "blocked-plan",
                               upstreams={"palace": str(upstream)})
            self.assertFalse((root / "blocked-plan").exists())
            path = plan_migration(host, {"palace": "fem_core"}, ["refs/heads/master"], root / "plan",
                                  upstreams={"palace": str(upstream)}, repair_relocations=True)
            plan = json.loads(path.read_text())
            print(f"Protection regression: {len(plan['protected_commits'])} protected, "
                  f"{len(plan['affected_commits'])} affected")
            # Expected domains come from fixture construction, not the planner
            # or verifier's protected-set logic.
            self.assertEqual(set(plan["protected_commits"]), set(squash_commits))
            self.assertEqual(set(plan["affected_commits"]), set(source_snapshots))
            self.assertEqual(len(plan["affected_commits"]), 209 if include_gitlinks else 212)
            self.assertTrue(set(gitlink_snapshots).isdisjoint(plan["affected_commits"]))
            self.assertTrue(set(gitlink_snapshots).isdisjoint(plan["protected_commits"]))
            self.assertNotIn(other_squash, plan["protected_commits"])
            self.assertNotIn(relocation, plan["protected_commits"])
            self.assertEqual(len(plan["metadata_repairs"]), 1)
            repair = plan["metadata_repairs"][0]
            self.assertEqual(repair["commit"], relocation)
            self.assertEqual(repair["squash_commit"], squash_tip)
            self.assertEqual(bytes.fromhex(repair["original_message_hex"]), commit_data(host, relocation)[1])
            self.assertEqual(bytes.fromhex(repair["repaired_message_hex"]), neutralize_relocation(commit_data(host, relocation)[1]))

            # Same-prefix host metadata must not become a squash domain either.
            known = [dict(item) for item in plan["metadata"]]
            source_relocation = commit(host, renamed_tree, [rename], "Host source marker\n\n"
                                       f"git-subtree-dir: palace\ngit-subtree-split: {upstream_tip}")
            entry = {"commit": source_relocation, "dir": "palace", "split": upstream_tip, "mainline": None}
            domains, source_repairs = classify_subtree_metadata(host, [*known, entry], {"palace": "fem_core"}, set(), True)
            self.assertEqual(set(domains), set(squash_commits))
            self.assertEqual(source_repairs[0]["commit"], source_relocation)
            for invalid_tree, split, parents in ((final_tree, upstream_tip, [rename]),
                                                  (renamed_tree, "f" * 40, [rename]),
                                                  (renamed_tree, upstream_tip, [base])):
                invalid = commit(host, invalid_tree, parents, "Unproven relocation")
                bad = dict(entry, commit=invalid, dir="fem_core", split=split)
                with self.assertRaisesRegex(MigrationError, "Ambiguous no-mainline"):
                    classify_subtree_metadata(host, [*known, bad], {"palace": "fem_core"}, set(), True)

            self.assertIsNone(migrate(path, root / "cancelled", confirm=lambda _: ""))
            self.assertFalse((path.parent / "before_migration.bundle").exists())
            self.assertFalse((root / "cancelled").exists())
            result = migrate(path, root / "rewritten", confirm=lambda _: "MIGRATE")
            self.assertEqual(result["status"], "passed")
            self.assertFalse(result["push_executed"])
            workspace = root / "rewritten"
            mapping = dict(line.split() for line in (path.parent / "commit-map.txt").read_text().splitlines()[1:])
            for old in source_snapshots:
                actual = tree(workspace, mapping[old])
                self.assertNotIn(b"palace/lib.txt", actual)
                self.assertIn(b"fem_core/lib.txt", actual)
            for old in gitlink_snapshots:
                self.assertEqual(tree(workspace, mapping[old]), tree(host, old))
                self.assertIn(b".gitmodules", tree(workspace, mapping[old]))
                self.assertTrue(tree(workspace, mapping[old])[b"palace"].startswith(b"160000 commit "))
            if include_gitlinks:
                self.assertEqual(tree(workspace, mapping[manual_copy])[b"fem_core/dep"],
                                 tree(host, manual_copy)[b"palace/dep"])
            for old in squash_commits:
                self.assertEqual(tree(workspace, mapping[old]), tree(host, old))
            self.assertEqual(tree(workspace, "master"), tree(host, "master"))
            self.assertEqual(commit_data(workspace, mapping[relocation])[1], bytes.fromhex(repair["repaired_message_hex"]))
            self.assertEqual(len(result["metadata_repairs"]), 1)
            self.assertEqual(result["metadata_repairs"][0]["new"], mapping[relocation])
            for probe in result["incremental_checks"]:
                self.assertEqual(probe["status"], "passed")
                self.assertEqual(probe["incremental_pulls"], 2)
                self.assertEqual(probe["idempotency_checks"], 2)
            self.assertIn(mapping[local_edit], text(workspace, "log", "--format=%H", "master", "--", "fem_core").splitlines())
            blame = text(workspace, "blame", "--line-porcelain", "master", "--", "fem_core/lib.txt")
            self.assertIn(mapping[local_edit], blame)
            self.assertIn("locally maintained first line", blame)
            self.assertEqual(refs(host), before)

            # A control without the invalid marker needs no metadata repairs.
            control_post = commit(host, final_tree, [rename], "Protect local code without metadata override")
            control_tip = commit(host, final_tree, [control_post, side_tip], "Merge side work without metadata override")
            text(host, "update-ref", "refs/heads/control", control_tip)
            control_before = refs(host)
            control_path = plan_migration(host, {"palace": "fem_core"}, ["refs/heads/control"],
                                          root / "control-plan", upstreams={"palace": str(upstream)})
            control_plan = json.loads(control_path.read_text())
            self.assertEqual(set(control_plan["protected_commits"]), set(squash_commits))
            self.assertEqual(set(control_plan["affected_commits"]), set(source_snapshots))
            self.assertEqual(control_plan["metadata_repairs"], [])
            control_result = migrate(control_path, root / "control-rewritten", confirm=lambda _: "MIGRATE")
            self.assertEqual(control_result["status"], "passed")
            self.assertEqual(len(control_result["incremental_checks"]), 1)
            for item in control_result["incremental_checks"]:
                self.assertEqual(item["status"], "passed")
                self.assertEqual(item["incremental_pulls"], 2)
                self.assertEqual(item["idempotency_checks"], 2)
            self.assertEqual(refs(host), control_before)


if __name__ == "__main__":
    unittest.main()
