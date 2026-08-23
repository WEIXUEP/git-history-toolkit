# Git Large Object Auditor

A read-only Git history auditor that identifies large blobs, classifies their
current usage, analyzes locally available Gerrit `refs/changes/*` references,
and generates cleanup reports.

The project also supports an explicit local history cleanup workflow. Cleanup
is never performed by the normal audit command. Remote push is optional and
requires a second interactive confirmation.

## Features

- No JSON configuration file. All settings come from `argparse` options.
- Protects `HEAD`, local branches, remote-tracking branches, and tags by default.
- Distinguishes current blobs, protected blobs, old versions, deleted paths,
  and Gerrit patch-set references.
- Refuses to audit shallow repositories because their history is incomplete.
- Produces TXT, CSV, JSON, candidate SHA, and administrator review reports.
- Creates a Git bundle backup before any history rewrite.
- Verifies that the current `HEAD` tree is unchanged after rewriting history.
- Uses `git filter-repo` only in the explicit cleanup mode.
- Uses `git push --force-with-lease` only after the user types `PUSH`.

## Repository layout

```text
git_large_object_auditor/
├── git_history_auditor.py       # CLI entry point
├── README.md
├── .gitignore
├── src/
│   ├── auditor.py               # Audit orchestration
│   ├── blob_scanner.py          # Large blob discovery
│   ├── classifier.py            # Classification policy
│   ├── current_tree_analyzer.py # Protected current trees
│   ├── gerrit_analyzer.py       # refs/changes analysis
│   ├── git_runner.py            # Git subprocess boundary
│   ├── history_analyzer.py      # Blob history analysis
│   ├── models.py                # Dataclasses and enums
│   ├── options.py               # CLI options
│   ├── policy.py                # Path and keep policies
│   ├── ref_analyzer.py          # Git ref discovery
│   ├── reporter.py              # Audit reports
│   └── rewriter.py              # Explicit cleanup and push workflow
└── tests/
```

## Requirements

- Python 3.10 or newer.
- Git.
- `git-filter-repo` for cleanup mode:

```powershell
python -m pip install -r requirements.txt
```

## Audit only

Run this from the `git_large_object_auditor` directory:

```powershell
python .\git_history_auditor.py `
    --repo H:\fem\PyZFEM `
    --min-size-mb 100 `
    --min-age-days 180 `
    --output-dir .\git_audit
```

The default command only reads the repository and writes reports.

## Remote-tracking branches and multi-branch cleanup

For ordinary remote branches, the audit only needs the local remote-tracking
refs. You do not need to create a separate local branch for every remote
branch:

```text
refs/remotes/origin/main
refs/remotes/origin/feature
```

Remote-tracking refs contain the remote branch tips and their reachable
history. The auditor can use them to:

- scan branch history;
- protect the current tree of each branch;
- determine whether a blob is still used by another branch;
- include all locally visible branches in one history rewrite.

Remote-tracking refs are local snapshots, so fetch the latest state before an
audit or cleanup:

```powershell
git fetch origin --prune
```

After rewriting history, each affected remote branch must be explicitly
updated. A remote-tracking ref can be mapped directly to its remote branch:

```bash
git push --force-with-lease origin \
    refs/remotes/origin/feature:refs/heads/feature
```

Gerrit `refs/changes/*` are not ordinary remote-tracking branches. Fetch and
analyze them separately when they are in scope. The auditor does not fetch
automatically and does not push every remote branch automatically.

## Local cleanup without push

```powershell
python .\git_history_auditor.py `
    --repo H:\fem\PyZFEM `
    --min-size-mb 100 `
    --min-age-days 180 `
    --output-dir C:\audit\PyZFEM `
    --execute-cleanup
```

The cleanup workflow:

1. Requires a completely clean worktree, including untracked files.
2. Shows every historical blob selected for automatic cleanup.
3. Requires the exact confirmation `CLEANUP`.
4. Writes `cleanup_blobs.txt` and creates `before_cleanup.bundle`.
5. Runs `git filter-repo --strip-blobs-with-ids` locally.
6. Verifies that the current `HEAD` tree is unchanged.
7. Prints the confirmed historical paths and a manual push command.

Only `CLEAN_HISTORY` candidates are rewritten automatically. Objects that are
still reachable from local Gerrit patch sets are reported for administrator
review and are not included in the automatic rewrite.

The output directory must be outside the repository during cleanup. This
prevents generated reports from making the worktree dirty.

## Cleanup followed by confirmed push

```powershell
python .\git_history_auditor.py `
    --repo H:\fem\PyZFEM `
    --output-dir C:\audit\PyZFEM `
    --execute-cleanup `
    --push-after-cleanup `
    --push-remote origin `
    --push-branch main
```

After cleanup, the tool prints the rewritten historical paths and the exact
push command. It executes the push only when the user types `PUSH`.

The push target is:

```text
git push --force-with-lease origin HEAD:refs/heads/main
```

If `--push-branch` is omitted, the current branch is used. If the user does
not provide `PUSH`, the local rewrite remains in place and no push occurs.

## Reports

Each audit creates a timestamped directory containing:

- `summary.txt`: coverage, repository statistics, and classification totals.
- `blobs.csv` and `blobs.json`: detailed records for large blobs.
- `candidates.csv`: all policy-eligible candidates.
- `candidate_blobs.txt`: SHA IDs for eligible candidates.
- `cleanup_blobs.txt`: only the IDs selected for automatic local rewriting.
- `protected_blobs.txt`: blobs protected by current refs or explicit rules.
- `gerrit_referenced.csv`: blobs reachable from local Gerrit patch sets.
- `admin_cleanup_plan.txt`: administrator review guidance.
- `before_cleanup.bundle`: backup created before local rewriting.

The audit never claims that the raw candidate size equals guaranteed disk
space savings. Git pack compression and delta compression affect the final
size.

## Programmatic use

```python
from argparse import Namespace

from src.auditor import GitLargeObjectAuditor

args = Namespace(
    repo=r"H:\\fem\\PyZFEM",
    min_size_mb=100.0,
    min_age_days=180,
    protect_head=True,
    protect_local_branches=True,
    protect_remote_branches=True,
    protect_tags=True,
    scan_gerrit_refs=True,
    include_paths=[],
    exclude_paths=[],
    keep_paths=[],
    keep_blobs=[],
    output_dir=r"C:\\audit\\PyZFEM",
    verbose=True,
)

result = GitLargeObjectAuditor(args).run()
```

## Tests

```powershell
python -m unittest discover -s tests -v
```
