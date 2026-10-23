"""Unified command line for Git history auditing and subtree migration."""

import argparse
import sys


def main(argv=None):
    argv = sys.argv[1:] if argv is None else argv
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=("audit", "review", "subtree", "cache"))
    if not argv or argv[0] in ("-h", "--help"):
        parser.print_help()
        return 0
    args = parser.parse_args(argv[:1])
    remaining = argv[1:]
    if args.command == "review":
        from src.saved_audit import main as review
        return review(remaining)
    if args.command == "cache":
        from src.audit_cache import main as cache
        return cache(remaining)
    if args.command == "audit":
        from git_history_auditor import main as audit
        return audit(remaining)
    from src.subtree_migration import main as subtree
    return subtree(remaining)


if __name__ == "__main__":
    raise SystemExit(main())
