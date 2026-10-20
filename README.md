# Git History Toolkit

One CLI and one self-contained skill for large-object audit/cleanup and
squash-subtree directory/history migration, including Git/Gerrit analysis
and multiple subtree add/pull verification.

Audits and plans do not rewrite history. Actual cleanup requires CLEANUP;
migration requires MIGRATE and runs in an independent clone. Remote history
replacement requires separate confirmation.

## Skill

The only skill is [git-history-toolkit](skills/git-history-toolkit/SKILL.md).
Distribute the complete `skills/git-history-toolkit/` directory, including
`scripts/` and `references/`. It needs no surrounding repository files.
Install that directory under your skills folder and invoke `$git-history-toolkit`.

```text
git-history-toolkit/
|-- git_history_toolkit.py           # Unified repository CLI
|-- requirements.txt
|-- docs/subtree-migration.md        # Migration, publication and recovery
|-- src/                            # Audit, cleanup and migration implementation
|-- tests/                          # Unit and fixture integration tests
`-- skills/git-history-toolkit/
    |-- SKILL.md                    # Single skill entry
    |-- scripts/git_history_toolkit.py
    |-- scripts/src/                # Self-contained implementation
    |-- scripts/test_subtree_operations.py
    `-- references/                 # Workflows and verification guides
```

Within an installed skill, use `<skill>/scripts/git_history_toolkit.py` instead
of the repository CLI below. Internal auditor and subtree wrappers share the
same implementation and remain available to regression tools.

## Requirements

Python 3.10+ and Git; migration also needs `git subtree`. Install
git-filter-repo 2.47+ for history rewriting:

```powershell
python -m pip install -r requirements.txt
```

Cleanup uses `git filter-repo` on PATH or falls back to its module in the
running Python interpreter. Migration uses the module in that interpreter.
PyYAML is only for skill metadata validation. The CLI has no GUI dependency.
Use full, non-empty working clones; clone bare remotes before analysis.
Subtree migration requires SHA-1 and rejects partial clones.

## Audit and Cleanup

Run from the `git-history-toolkit` repository directory:

```powershell
python .\git_history_toolkit.py audit `
  --repo C:\repos\application `
  --min-size-mb 100 --min-age-days 180 `
  --output-dir C:\audit\application
```

Audit reads local objects and writes reports; it does not fetch, rewrite,
push or run GC. Keep reports outside the repository. HEAD, local and
remote-tracking branch tips, and tags are protected by default. Optional
`--protect-code-files` and `--protect-text-data-files` protect historical
source and text/data blobs independently. Repeat `--keep-path`, `--keep-blob`,
`--include-path` or `--exclude-path` for scope. Rules apply to entire blob
identities, including historical aliases.

Review summary.txt, blobs.json, candidates.csv and admin_cleanup_plan.txt.
CLEAN_HISTORY candidates can enter confirmed cleanup; GERRIT_ADMIN_REVIEW
requires administrator action and is excluded from automatic rewriting.
Policy-eligible totals are not automatic cleanup totals. Raw blob totals
do not predict reclaimed local or server storage.

After reviewing and approving the exact candidates, options and ref scope,
repeat the audit command with `--execute-cleanup`. The HTML page requires
manual selection and CLEANUP confirmation. The tool verifies a bundle backup
and saves refs and remote configuration.
It rejects stale audits and changes during confirmation. Its built-in tree
check covers HEAD; inspect other protected branch/tag trees, the SHA map,
remaining reachability and `git fsck` before publication. Preserve failed
workspaces and backups; recover into another clone instead of resetting user work.

For separately confirmed single-branch publication, append
`--push-after-cleanup --push-remote origin --push-branch main`.
It requires an existing destination branch and one push URL, captures that
destination's actual old OID, and prompts for PUSH after cleanup. Review the
destination and exact `--force-with-lease=FULL_REF:OLD_OID` command.
Multi-branch/tag publication must enumerate each ref. Never mirror-push or
publish Gerrit internal refs implicitly.
See [Git/Gerrit coverage and recovery](skills/git-history-toolkit/references/git-and-gerrit.md).

## Browser Confirmation

Every CLI audit opens a Python-served local HTML review after analysis by default:

```powershell
python .\git_history_toolkit.py audit --repo C:\repos\application --output-dir C:\audit\application
```

Before analysis, an HTML parameter page requires the user to confirm minimum
size in MiB and age in whole days. Defaults (100 MiB / 180 days) and CLI flags
only prefill the fields. Editing a value resets the confirmation checkbox.
Age means time since the last observed history reference change, including
addition, replacement or deletion, not filesystem modification time. Cancel
or timeout stops before scanning. Confirmation starts analysis only; it never
authorizes cleanup. The object-selection page opens after analysis.

The page groups by file suffix and displays readable and exact byte sizes,
latest historical presence time, latest reference-change time, aliases and
protection reasons. Nothing is selected initially. Protected/current objects
and Gerrit administrator-review objects cannot be selected. REVIEW saves a
selection receipt without rewriting history.

To let the user approve a final subset for actual cleanup, add
`--execute-cleanup --review-html`. The user must select eligible objects, type
CLEANUP and submit. Only those IDs are stripped after backup and state checks.
No worktree files are deleted. Remote PUSH still requires separate approval.
Cancellation, timeout and stale refs stop cleanup. Use `--no-open-browser` to
print the local URL. Use `--no-review-html` for explicit terminal-only operation;
it still requires size/day confirmation and SCAN before analysis.
An unavailable browser never approves cleanup.
See [browser review](skills/git-history-toolkit/references/browser-review.md)
for timestamp semantics, SSH use and cross-platform dependencies.

## Subtree Migration

```powershell
python .\git_history_toolkit.py subtree plan `
  --repo C:\repos\application `
  --move vendor/a=components/a --move vendor/b=third_party/deep/b `
  --ref refs/heads/main --output-dir C:\migration\plan
python .\git_history_toolkit.py subtree migrate `
  --plan C:\migration\plan\plan.json --workspace C:\migration\rewritten
python .\git_history_toolkit.py subtree publish-plan `
  --plan C:\migration\plan\plan.json --remote origin
```

Repeat --move for multiple projects and historical aliases, and --ref for
selected full branch/tag refs. Confirmed migration verifies a backup, preserves
host identities and ordered parents, records the SHA map and checks every
selected commit's expected tree. Original upstream root trees and split IDs
remain distinct from host prefixes. Isolated upstream increments and repeated
pulls verify synchronization.

publish-plan reads destination refs and prepares explicit leased commands;
it never pushes. Platform protection and Gerrit force/forge permissions need
separate review. Squashed original upstream per-file history cannot be
reconstructed by renaming host paths. Future upstream edits may still conflict.
See the [migration guide](docs/subtree-migration.md).

When combining cleanup and migration, run sequentially and regenerate the
second audit/plan against the first operation's resulting refs. Approval and
leases for one operation do not authorize the other.

## Verification

Both capabilities use [git-auditor-test-repo](https://github.com/WEIXUEP/git-auditor-test-repo).
The frozen 2026-10-20 audit baseline uses 5 MiB / 180 days and 23 large objects,
all below GitHub's 100 MiB limit. Its verifier checks four audit policies,
Gerrit ref restoration, chronology, formats, integrity and transport.
See [audit regression](skills/git-history-toolkit/references/audit-testing.md).

```powershell
$env:GIT_HISTORY_FIXTURE = 'C:\tests\git-auditor-test-repo'
python -m unittest discover -s tests -v
```

Materialize the fixture's local subtree refs as documented in its README.
Missing inputs cause skipped integration tests, not successful validation.
The packaged [multi-subtree regression](skills/git-history-toolkit/references/subtree-testing.md)
can run independently: four new adds, four migrated paths, six final projects,
30 update pulls and 30 repeat checks, preserving local and unrelated content.
Tests run in disposable clones and do not replace remote history.
