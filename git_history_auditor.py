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
        from src.html_review import confirm_thresholds
        from src.repository import RepositoryInspector
        args.repo = str(RepositoryInspector(auditor.runner).validate())
        auditor = GitLargeObjectAuditor(args)
        thresholds = confirm_thresholds(auditor.options)
        if thresholds is None:
            print("Threshold confirmation cancelled; analysis and cleanup were not started.")
            return 0
        args.min_size_mb = thresholds["min_size_mb"]
        args.min_age_days = thresholds["min_age_days"]
        auditor = GitLargeObjectAuditor(args)
        result = auditor.run()
        if args.execute_cleanup:
            GitHistoryRewriter(auditor.runner, auditor.options).execute(result)
        elif args.review_html:
            from src.html_review import review_in_browser
            review_in_browser(result, auditor.options)
    except (ValueError, OSError, RepositoryError, GitCommandError, CleanupError) as exc:
        parser.error(str(exc))

    print(f"Audit completed: {result.output_dir}")
    print(f"Large blobs: {len(result.blobs)}")
    print(f"Policy eligible: {result.cleanup_count}")
    print(f"Automatic local cleanup: {result.automatic_cleanup_count}")
    print(f"Gerrit administrator review: {result.gerrit_review_count}")
    print(f"Audit cache: {result.cache_info.get('mode')}; hits={result.cache_info.get('hits')}, misses={result.cache_info.get('misses')}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
