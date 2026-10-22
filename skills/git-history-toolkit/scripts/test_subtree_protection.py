"""Reproduce relocation trailers contaminating squash ancestry protection."""

import json
import os
from pathlib import Path
import tempfile
import unittest

from src.subtree_migration import MigrationError, configure_identity, git, migrate, plan_migration, refs, text, tree


class SubtreeProtectionTests(unittest.TestCase):
    def test_relocated_host_metadata_does_not_protect_host_history(self):
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

            def host_tree(prefix, library_tree):
                return make_tree(host, [("README", "100644", "blob", readme),
                                        (prefix, "040000", "tree", library_tree)])

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
                host, b"locally maintained first line\nversion 5\n"))])
            current_tree = make_tree(host, [("README", "100644", "blob", readme),
                                           ("palace", "040000", "tree", local_library),
                                           ("other", "040000", "tree", other_tree)])
            local_edit = commit(host, current_tree, [tip], "Maintain palace locally")
            tip = local_edit
            source_snapshots.append(tip)
            for index in range(202):
                tip = commit(host, current_tree, [tip], f"Host maintenance {index}")
                source_snapshots.append(tip)
            self.assertEqual(len(source_snapshots), 210)

            renamed_tree = make_tree(host, [("README", "100644", "blob", readme),
                                           ("fem_core", "040000", "tree", local_library),
                                           ("other", "040000", "tree", other_tree)])
            rename = commit(host, renamed_tree, [tip], "Rename palace")
            relocation = commit(host, renamed_tree, [rename], "Relocate palace subtree\n\n"
                                f"git-subtree-dir: fem_core\ngit-subtree-split: {upstream_tip}")
            side_tip = anchor
            side_tree = host_tree("palace", library_tree)
            for index in range(2):
                side_tip = commit(host, side_tree, [side_tip], f"Side maintenance {index}")
                source_snapshots.append(side_tip)
            tip = commit(host, renamed_tree, [relocation, side_tip], "Merge side work after relocation")
            text(host, "update-ref", "refs/heads/master", tip)
            text(host, "checkout", "master")
            before = refs(host)
            for split in splits:
                self.assertEqual(git(host, "rev-parse", "--verify", split + "^{commit}", check=False), b"")

            path = plan_migration(host, {"palace": "fem_core"}, ["refs/heads/master"], root / "plan",
                                  upstreams={"palace": str(upstream)})
            plan = json.loads(path.read_text())
            print(f"Protection regression: {len(plan['protected_commits'])} protected, "
                  f"{len(plan['affected_commits'])} affected")
            # Expected domains come from fixture construction, not the planner
            # or verifier's protected-set logic.
            self.assertEqual(set(plan["protected_commits"]), set(squash_commits))
            self.assertEqual(set(plan["affected_commits"]), set(source_snapshots))
            self.assertEqual(len(plan["affected_commits"]), 212)
            self.assertNotIn(other_squash, plan["protected_commits"])
            self.assertNotIn(relocation, plan["protected_commits"])

            # A destination-side host trailer is also invalid squash metadata
            # to native subtree. The independent pull check must reject it,
            # even though path migration itself is now correct.
            with self.assertRaisesRegex(MigrationError, "Incremental subtree pull modified"):
                migrate(path, root / "rewritten", confirm=lambda _: "MIGRATE")
            result = json.loads((path.parent / "verification.json").read_text())
            self.assertEqual(result["status"], "failed")
            self.assertFalse(result["push_executed"])
            workspace = root / "rewritten"
            mapping = dict(line.split() for line in (path.parent / "commit-map.txt").read_text().splitlines()[1:])
            for old in source_snapshots:
                actual = tree(workspace, mapping[old])
                self.assertNotIn(b"palace/lib.txt", actual)
                self.assertIn(b"fem_core/lib.txt", actual)
            for old in squash_commits:
                self.assertEqual(tree(workspace, mapping[old]), tree(host, old))
            self.assertEqual(tree(workspace, "master"), tree(host, "master"))
            self.assertIn(mapping[local_edit], text(workspace, "log", "--format=%H", "master", "--", "fem_core").splitlines())
            blame = text(workspace, "blame", "--line-porcelain", "master", "--", "fem_core/lib.txt")
            self.assertIn(mapping[local_edit], blame)
            self.assertIn("locally maintained first line", blame)
            self.assertEqual(refs(host), before)

            # Control history differs only by omitting the invalid relocation
            # metadata commit. Confirm normal incremental sync still succeeds.
            control_tip = commit(host, renamed_tree, [rename, side_tip], "Merge side work without metadata override")
            text(host, "update-ref", "refs/heads/control", control_tip)
            control_before = refs(host)
            control_path = plan_migration(host, {"palace": "fem_core"}, ["refs/heads/control"],
                                          root / "control-plan", upstreams={"palace": str(upstream)})
            control_plan = json.loads(control_path.read_text())
            self.assertEqual(set(control_plan["protected_commits"]), set(squash_commits))
            self.assertEqual(set(control_plan["affected_commits"]), set(source_snapshots))
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
