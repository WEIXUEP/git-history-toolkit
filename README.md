# Git History Toolkit

Git history maintenance tools for large-object auditing/confirmed cleanup and
squash-subtree directory migration with history continuity and upstream sync
verification. The repository was previously named `git_large_object_auditor`.
Existing `git_history_auditor.py` commands remain valid. Both capabilities
are distributed as one `git-history-toolkit` skill.

## Unified command line

```powershell
python .\git_history_toolkit.py audit --repo C:\repos\application --output-dir C:\audit
python .\git_history_toolkit.py subtree plan --repo C:\repos\application --move vendor/old=components/new --ref refs/heads/main --output-dir C:\migration\plan
python .\git_history_toolkit.py subtree migrate --plan C:\migration\plan\plan.json --workspace C:\migration\rewritten
python .\git_history_toolkit.py subtree publish-plan --plan C:\migration\plan\plan.json --remote origin
```

Subtree migration rewrites an independent clone after confirmation, preserves
squash-root upstream trees, translates subtree trailers, verifies every selected
commit, and tests two incremental upstream pulls plus repeated-pull idempotency.
It never pushes remotely. See [the migration guide](docs/subtree-migration.md)
for historical aliases, multiple refs, permissions, collaboration and recovery.
The standalone `git_subtree_migrator.py` exposes the same subtree subcommands.

## Codex skill

The single self-contained, CLI-only skill is in `skills/git-history-toolkit/`.
Upload that entire folder, including `SKILL.md`, `scripts/` and `references/`.
It contains large-object audit/cleanup, subtree migration and the multiple
add/pull regression, with one shared implementation and command entry:
`python <skill>/scripts/git_history_toolkit.py audit ...` or `subtree ...`.
It needs no files outside its folder. PyYAML is only for metadata validation.
Audits/plans are read-only; actual cleanup or migration requires confirmation,
and remote history replacement requires separate confirmation. When combining
operations, regenerate the next audit/plan after the preceding rewrite.

The skill includes Git/Gerrit coverage and recovery guidance, plus instructions
for the public [long-history regression fixture](https://github.com/WEIXUEP/git-auditor-test-repo).
Reports distinguish automatic local cleanup from Gerrit administrator review.
Cleanup verifies its bundle backup and refuses stale audits or repository
changes during confirmation. The combined push workflow uses the actual push
destination's explicit commit ID as its lease, preserving separate fetch and
push URL settings. It requires an existing destination branch and a single
push URL. The built-in post-rewrite tree check covers HEAD; review other
protected refs before publishing. Linked worktrees require an independent clone.

A read-only Git history auditor that identifies large blobs, classifies their
current usage, analyzes locally available Gerrit `refs/changes/*` references,
and generates cleanup reports.

The project also supports an explicit local history cleanup workflow. Cleanup
is never performed by the normal audit command. Remote push is optional and
requires a second interactive confirmation.

## Features

- No JSON configuration file. All settings come from command-line arguments.
- Protects `HEAD`, local branches, remote-tracking branches, and tags by default.
- Distinguishes current blobs, protected blobs, old versions, deleted paths,
  and Gerrit patch-set references.
- Refuses to audit shallow repositories because their history is incomplete.
- Produces TXT, CSV, JSON, candidate SHA, and administrator review reports.
- Creates a Git bundle backup before any history rewrite.
- Verifies that the current `HEAD` tree is unchanged after rewriting history.
- Uses `git filter-repo` only in explicit cleanup mode.
- Uses `git push --force-with-lease` only after the user confirms the push.
- Includes a lightweight Tkinter desktop interface with no extra UI dependency.
- Can protect historical code-file blobs by recognized file suffix.

## Repository layout

```text
git_large_object_auditor/
|-- git_history_auditor.py       # CLI entry point
|-- git_history_auditor_gui.py   # Tkinter entry point
|-- README.md
|-- requirements.txt
|-- src/
|   |-- auditor.py               # Audit orchestration
|   |-- blob_scanner.py          # Large blob discovery
|   |-- classifier.py            # Classification policy
|   |-- current_tree_analyzer.py # Protected current trees
|   |-- gerrit_analyzer.py       # refs/changes analysis
|   |-- git_runner.py            # Git subprocess boundary
|   |-- history_analyzer.py      # Blob history analysis
|   |-- models.py                # Dataclasses and enums
|   |-- options.py               # CLI options
|   |-- policy.py                # Path and keep policies
|   |-- ref_analyzer.py          # Git ref discovery
|   |-- reporter.py              # Audit reports
|   |-- rewriter.py              # Explicit cleanup and push workflow
|   `-- ui.py                    # Desktop interface
`-- tests/
```

## Requirements

- Python 3.10 or newer.
- Git.
- Tkinter, normally included with Python on Windows.
- `git-filter-repo` for cleanup mode:

```powershell
python -m pip install -r requirements.txt
```

## Command-line audit

Run this from the `git_large_object_auditor` directory:

```powershell
python .\git_history_auditor.py `
    --repo H:\fem\PyZFEM `
    --min-size-mb 100 `
    --min-age-days 180 `
    --output-dir .\git_audit
```

The default command only reads the repository and writes reports.

To protect historical blobs associated with common source-code suffixes, add:

```powershell
python .\git_history_auditor.py `
    --repo H:\fem\PyZFEM `
    --protect-code-files
```

The option is disabled by default. When enabled, any blob with a known code
file suffix in its historical paths is protected as a whole. The suffix check
is case-insensitive and includes common extensions such as `.py`, `.c`,
`.cpp`, `.java`, `.js`, `.ts`, `.go`, `.rs`, `.sh`, `.ps1`, `.sql`, `.html`,
`.css`, `.vue`, and `.svelte`.

Text and structured data files use a separate option:

```powershell
python .\git_history_auditor.py `
    --repo H:\fem\PyZFEM `
    --protect-text-data-files
```

This protects common documentation and data formats such as `.md`, `.txt`,
`.rst`, `.csv`, `.tsv`, `.json`, `.jsonl`, `.yaml`, `.yml`, `.toml`, `.ini`,
`.xml`, `.env`, and `.log`. The code and text/data switches are independent;
enable both when both categories must be protected.

## Desktop interface

```powershell
python .\git_history_auditor_gui.py
```

The UI provides three operations:

- `Audit only`: read-only analysis and report generation.
- `Cleanup locally`: local history rewrite after a confirmation dialog.
- `Cleanup and push`: local cleanup followed by a separate push confirmation.

Long-running Git work runs in a background thread so the window remains
responsive. The cleanup and push confirmations cannot be bypassed by opening
the UI.

## Remote-tracking branches and multi-branch cleanup

For ordinary remote branches, the audit only needs local remote-tracking refs.
You do not need to create a separate local branch for every remote branch:

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
- `cleanup_blobs.txt`: only IDs selected for automatic local rewriting.
- `protected_blobs.txt`: blobs protected by current refs or explicit rules.
- `gerrit_referenced.csv`: blobs reachable from local Gerrit patch sets.
- `admin_cleanup_plan.txt`: administrator review guidance.
- `before_cleanup.bundle`: backup created before local rewriting.

The audit never claims that raw candidate size equals guaranteed disk space
savings. Git pack compression and delta compression affect the final size.

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
