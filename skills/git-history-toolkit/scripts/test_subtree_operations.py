"""Exercise interleaved real subtree adds/pulls around a history migration."""

import json
import os
from pathlib import Path
import subprocess
import tempfile
import unittest

from src.subtree_migration import configure_identity, git, migrate, plan_migration, refs, text, tree


class MultipleSubtreeOperationsTests(unittest.TestCase):
    def test_add_and_interleaved_pull_before_and_after_migration(self):
        fixture = Path(os.environ.get("GIT_HISTORY_FIXTURE", str(Path(__file__).resolve().parents[2] / "git-auditor-test-repo")))
        if not fixture.exists() or "refs/heads/fixture/subtree" not in refs(fixture):
            self.skipTest("Requires the published git-auditor-test-repo subtree fixture")
        source_refs = refs(fixture)
        with tempfile.TemporaryDirectory(prefix="multi-subtree-operations-") as temporary:
            root = Path(temporary)
            host = root / "host"
            subprocess.run(["git", "clone", "--no-local", "--no-checkout", str(fixture), str(host)],
                           check=True, capture_output=True)
            text(host, "checkout", "-b", "operations", "origin/fixture/subtree")
            configure_identity(host)
            stamp = max(map(int, text(host, "log", "--all", "--format=%ct").splitlines())) + 3600
            env = os.environ.copy()
            counter = 0

            def command(repo, *args):
                nonlocal counter
                counter += 1
                env.update(GIT_AUTHOR_DATE=f"{stamp + counter * 60} +0000",
                           GIT_COMMITTER_DATE=f"{stamp + counter * 60} +0000")
                return text(repo, *args, env=env)

            upstreams = {}
            paths = {"a": "vendor/a", "b": "vendor/b", "c": "vendor/c", "d": "vendor/deep/d"}
            events = []
            expected = tree(host, "HEAD")

            def upstream(name):
                repo = root / ("upstream-" + name)
                repo.mkdir()
                text(repo, "init", "-b", "main")
                configure_identity(repo)
                if name in ("a", "b"):
                    split = text(fixture, "rev-parse", f"fixture-subtree/upstream-{name}-v2")
                    text(repo, "fetch", "--no-tags", str(fixture), split)
                    text(repo, "reset", "--hard", "FETCH_HEAD")
                else:
                    (repo / "lib.txt").write_text(f"Project {name} baseline\n", encoding="utf-8")
                    command(repo, "add", "--all")
                    command(repo, "commit", "-m", f"Initialize project {name}")
                upstreams[name] = repo
                return repo

            def add(name, prefix):
                repo = upstream(name)
                self.assertFalse(any(p.startswith(prefix.encode() + b"/") for p in expected))
                command(host, "subtree", "add", "--prefix=" + prefix, str(repo), "main", "--squash")
                expected.update({prefix.encode() + b"/" + p: value for p, value in tree(repo, "HEAD").items()})
                self.assertEqual(tree(host, "HEAD"), expected)
                events.append({"operation": "add", "project": name, "path": prefix, "status": "passed"})

            for name in ("a", "b"):
                upstream(name)
            for name in ("c", "d"):
                add(name, paths[name])
            # Host-only edits must survive every subsequent subtree update.
            for prefix in paths.values():
                (host / prefix / "host-local.txt").write_text("Locally maintained content\n", encoding="utf-8")
            command(host, "add", "--all")
            command(host, "commit", "-m", "Maintain local files across four imported projects")
            expected = tree(host, "HEAD")

            def rounds(phase):
                for iteration in (1, 2, 3):
                    # Rotate order so association lookup is exercised across projects.
                    names = list(paths)
                    names = names[iteration - 1:] + names[:iteration - 1]
                    for name in names:
                        repo, prefix = upstreams[name], paths[name]
                        before = tree(repo, "HEAD")
                        delta = repo / "upstream-cycle.txt"
                        if iteration == 3:
                            delta.unlink()
                        else:
                            delta.write_text(f"{phase}: project {name}, revision {iteration}\n", encoding="utf-8")
                        command(repo, "add", "--all")
                        command(repo, "commit", "-m", f"{phase} {name} upstream cycle {iteration}")
                        after = tree(repo, "HEAD")
                        command(host, "subtree", "pull", "--prefix=" + prefix, str(repo), "main", "--squash")
                        for path in before.keys() | after.keys():
                            if before.get(path) != after.get(path):
                                target = prefix.encode() + b"/" + path
                                if path in after:
                                    expected[target] = after[path]
                                else:
                                    expected.pop(target)
                        self.assertEqual(tree(host, "HEAD"), expected)
                        tip = text(host, "rev-parse", "HEAD")
                        command(host, "subtree", "pull", "--prefix=" + prefix, str(repo), "main", "--squash")
                        self.assertEqual(text(host, "rev-parse", "HEAD"), tip)
                        self.assertFalse(text(host, "status", "--porcelain"))
                        events.append({"operation": "pull", "project": name, "phase": phase,
                                       "iteration": iteration, "path": prefix, "repeat_idempotent": True,
                                       "whole_tree_verified": True, "status": "passed"})

            rounds("before-migration")
            moves = {"vendor/a": "components/a", "vendor/b": "third_party/deep/b",
                     "vendor/c": "components/c", "vendor/deep/d": "third_party/d"}
            plan = plan_migration(host, moves, ["refs/heads/operations"], root / "plan")
            migration = migrate(plan, root / "migrated", confirm=lambda prompt: "MIGRATE")
            self.assertEqual(migration["status"], "passed")
            host = root / "migrated"
            configure_identity(host)
            paths = {name: moves[prefix] for name, prefix in paths.items()}
            expected = tree(host, "HEAD")
            for name, prefix in (("e", "extras/e"), ("f", "extras/deep/f")):
                add(name, prefix)
                paths[name] = prefix
            rounds("after-migration")
            self.assertIn("locally maintained first line", git(host, "show", "HEAD:components/a/lib.txt").decode())
            self.assertEqual(refs(fixture), source_refs)
            text(host, "fsck", "--full")
            self.assertEqual(sum(event["operation"] == "add" for event in events), 4)
            self.assertEqual(sum(event["operation"] == "pull" for event in events), 30)
            summary = {"status": "passed", "fixture": str(fixture), "add_operations": 4,
                       "pull_updates": 30, "repeat_pull_checks": 30, "subtrees_after_migration": 6,
                       "migrated_subtrees": 4, "checked_migration_commits": migration["checked_commits"],
                       "source_refs_unchanged": True, "events": events}
            print(json.dumps(summary, indent=2))


if __name__ == "__main__":
    unittest.main()
