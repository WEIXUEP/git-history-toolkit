"""Command-line parser and option construction."""

from __future__ import annotations

import argparse


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Audit Git history blobs and optionally rewrite the local repository.",
    )
    parser.add_argument("--repo", required=True, help="Path to the Git repository.")
    parser.add_argument(
        "--min-size-mb", type=float, default=100.0, help="Minimum blob size in MiB."
    )
    parser.add_argument(
        "--min-age-days", type=int, default=180, help="Minimum age since last reference."
    )

    for name, default, help_text in (
        ("protect-head", True, "Protect the current HEAD tree."),
        ("protect-local-branches", True, "Protect local branch tip trees."),
        ("protect-remote-branches", True, "Protect remote-tracking branch tip trees."),
        ("protect-tags", True, "Protect tag target trees."),
        (
            "protect-code-files",
            False,
            "Protect historical blobs associated with recognized code-file suffixes.",
        ),
        (
            "protect-text-data-files",
            False,
            "Protect historical blobs associated with recognized text and data suffixes.",
        ),
        ("scan-gerrit-refs", True, "Analyze locally available refs/changes/* refs."),
        ("verbose", True, "Show informational progress messages."),
    ):
        parser.add_argument(
            f"--{name}",
            dest=name.replace("-", "_"),
            action=argparse.BooleanOptionalAction,
            default=default,
            help=help_text,
        )

    parser.add_argument("--include-path", dest="include_paths", action="append", default=[])
    parser.add_argument("--exclude-path", dest="exclude_paths", action="append", default=[])
    parser.add_argument("--keep-path", dest="keep_paths", action="append", default=[])
    parser.add_argument(
        "--keep-blob",
        dest="keep_blobs",
        action="append",
        default=[],
        help="Explicitly protect a blob SHA; may be repeated.",
    )
    parser.add_argument("--output-dir", default="./git_audit")
    parser.add_argument(
        "--execute-cleanup",
        "--cleanup",
        dest="execute_cleanup",
        action="store_true",
        help="After the audit, interactively rewrite local history using clean candidates.",
    )
    parser.add_argument(
        "--push-after-cleanup",
        action="store_true",
        help="After cleanup, ask for explicit confirmation before force-with-lease push.",
    )
    parser.add_argument(
        "--push-remote",
        default="origin",
        help="Remote used by --push-after-cleanup (default: origin).",
    )
    parser.add_argument(
        "--push-branch",
        help="Branch pushed after cleanup; defaults to the current branch.",
    )
    return parser
