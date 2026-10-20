from __future__ import annotations

import unittest
from argparse import Namespace
from datetime import datetime, timedelta, timezone
from pathlib import Path

try:
    from src.classifier import BlobClassifier
    from src.models import AuditorOptions, BlobInfo, BlobStatus, CleanupAction
    from src.options import build_parser
    from src.policy import PathPolicy
    from src.rewriter import TreeSnapshot
except ModuleNotFoundError:
    from git_large_object_auditor.src.classifier import BlobClassifier
    from git_large_object_auditor.src.models import (
        AuditorOptions,
        BlobInfo,
        BlobStatus,
        CleanupAction,
    )
    from git_large_object_auditor.src.options import build_parser
    from git_large_object_auditor.src.policy import PathPolicy
    from git_large_object_auditor.src.rewriter import TreeSnapshot


class CoreTests(unittest.TestCase):
    def test_options_are_normalized_and_validated(self) -> None:
        options = AuditorOptions.from_args(
            Namespace(
                repo=".",
                min_size_mb=10,
                min_age_days=3,
                include_paths=["dataset/**"],
                exclude_paths=[],
                keep_paths=[],
                keep_blobs=[],
                output_dir="audit",
            )
        )
        self.assertEqual(options.repo, Path.cwd())
        self.assertEqual(options.min_size_bytes, 10 * 1024 * 1024)
        self.assertEqual(options.include_paths, ("dataset/**",))

    def test_path_policy_distinguishes_include_exclude_and_keep(self) -> None:
        options = AuditorOptions(
            repo=Path("."),
            include_paths=("dataset/**",),
            exclude_paths=("dataset/generated/**",),
            keep_paths=("dataset/reference/**",),
        )
        policy = PathPolicy(options)
        self.assertTrue(policy.is_in_scope(BlobInfo("a" * 40, 100, {"dataset/a.bin"})))
        self.assertFalse(
            policy.is_in_scope(BlobInfo("b" * 40, 100, {"dataset/generated/a.bin"}))
        )
        self.assertTrue(
            policy.is_explicitly_protected(BlobInfo("c" * 40, 100, {"dataset/reference/a.bin"}))
        )

    def test_classifier_marks_current_blob_as_keep(self) -> None:
        now = datetime.now(timezone.utc)
        blob = BlobInfo("a" * 40, 200, {"model.bin"}, last_seen_time=now - timedelta(days=365))
        blob.is_current = True
        result = BlobClassifier(
            AuditorOptions(repo=Path("."), min_size_mb=0.001, min_age_days=180), now
        ).classify([blob])[0]
        self.assertEqual(result.status, BlobStatus.CURRENT)
        self.assertEqual(result.action, CleanupAction.KEEP)
        self.assertFalse(result.eligible)

    def test_tree_snapshot_detects_current_tree_changes(self) -> None:
        before = TreeSnapshot("tree-a", (("file.txt", "100644 blob abc"),))
        unchanged = TreeSnapshot("tree-a", (("file.txt", "100644 blob abc"),))
        changed = TreeSnapshot("tree-b", (("file.txt", "100644 blob def"),))
        self.assertEqual(before.differences(unchanged), [])
        self.assertEqual(before.differences(changed), ["file.txt"])

    def test_cleanup_and_push_are_explicit_cli_options(self) -> None:
        args = build_parser().parse_args(
            [
                "--repo",
                ".",
                "--execute-cleanup",
                "--push-after-cleanup",
                "--push-remote",
                "upstream",
                "--push-branch",
                "main",
            ]
        )
        self.assertTrue(args.execute_cleanup)
        self.assertTrue(args.push_after_cleanup)
        self.assertEqual(args.push_remote, "upstream")
        self.assertEqual(args.push_branch, "main")

    def test_code_file_protection_uses_suffixes(self) -> None:
        options = AuditorOptions(repo=Path("."), protect_code_files=True)
        policy = PathPolicy(options)
        code_blob = BlobInfo("a" * 40, 100, {"src/legacy/module.py"})
        data_blob = BlobInfo("b" * 40, 100, {"data/model.bin"})
        self.assertTrue(policy.is_explicitly_protected(code_blob))
        self.assertEqual(
            policy.protection_reason(code_blob),
            "matched --protect-code-files by file suffix",
        )
        self.assertFalse(policy.is_explicitly_protected(data_blob))

    def test_text_data_protection_is_separate_from_code_protection(self) -> None:
        text_options = AuditorOptions(repo=Path("."), protect_text_data_files=True)
        text_policy = PathPolicy(text_options)
        markdown_blob = BlobInfo("a" * 40, 100, {"docs/README.md"})
        json_blob = BlobInfo("b" * 40, 100, {"config/settings.json"})
        source_blob = BlobInfo("c" * 40, 100, {"src/main.py"})
        self.assertTrue(text_policy.is_explicitly_protected(markdown_blob))
        self.assertTrue(text_policy.is_explicitly_protected(json_blob))
        self.assertFalse(text_policy.is_explicitly_protected(source_blob))
        self.assertEqual(
            text_policy.protection_reason(markdown_blob),
            "matched --protect-text-data-files by file suffix",
        )


if __name__ == "__main__":
    unittest.main()
