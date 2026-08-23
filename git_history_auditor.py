"""Command-line entry point for the Git large-object auditor."""

from __future__ import annotations

import argparse
import logging
import sys

from src.auditor import GitLargeObjectAuditor
from src.git_runner import GitCommandError
from src.options import build_parser
from src.repository import RepositoryError
from src.rewriter import CleanupError, GitHistoryRewriter


def main(argv: list[str] | None = None) -> int:
    """Run an audit from command-line arguments."""
    parser = build_parser()
    args = parser.parse_args(argv)

    logging.basicConfig(
        level=logging.INFO if args.verbose else logging.WARNING,
        format="%(levelname)s: %(message)s",
    )

    try:
        auditor = GitLargeObjectAuditor(args)
        result = auditor.run()
        if args.execute_cleanup:
            GitHistoryRewriter(auditor.runner, auditor.options).execute(result)
    except (ValueError, RepositoryError, GitCommandError, CleanupError) as exc:
        parser.error(str(exc))

    print(f"Audit completed: {result.output_dir}")
    print(f"Large blobs: {len(result.blobs)}")
    print(f"Cleanup eligible: {result.cleanup_count}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
