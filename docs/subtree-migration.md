# Squash-subtree directory and history migration

This workflow rewrites a new full clone, never the source repository. It uses
git-filter-repo 2.47+, retains all commits and parent order, and verifies every
selected commit's tree, author/committer identities, timestamps and messages.
The public test fixture is https://github.com/WEIXUEP/git-auditor-test-repo.

Run `python -m unittest discover -s tests -p test_subtree_operations.py -v`
to exercise real interleaved adds and pulls using an independent copy of that
fixture (set `GIT_HISTORY_FIXTURE` if it is not beside the toolkit). The test
adds two projects before migration and two afterward, migrates four subtree
paths, and runs three update rounds across four then six projects. Each
round adds, modifies, or deletes an upstream file. All 30 updates compare the
entire host tree, retain local edits and repeat the pull to check idempotency.
The original fixture refs are checked unchanged; no remote history is replaced.

## Why a path rename alone is insufficient

`git mv` changes only one snapshot. `git log -- new/path` normally does not
walk old directory names, and directory history has no general `--follow`.
This tool makes historical host-repository paths use one final directory.
Normal Git history simplification can still omit tree-identical commits from
path-limited output; `--full-history` exposes more merge topology. Blame and
path log are exercised separately in the regression fixture.

With `subtree --squash`, upstream changes are represented by synthetic
squash commits whose trees are rooted at the upstream project root. Those
root trees and original upstream split IDs must **not** undergo host-prefix
renaming. The tool identifies those ancestry domains before rewriting.
Squashing did not retain the upstream's original per-file author history in
the host directory: this migration cannot recreate it. Host development
history is preserved; original upstream history is queried in upstream refs.

## Plan first

Requires Python 3.10+, Git with `git subtree`, and git-filter-repo installed
in the same Python environment. Install with `python -m pip install -r requirements.txt`.

```powershell
python .\git_history_toolkit.py subtree plan `
  --repo C:\repos\application `
  --move vendor/engine=components/engine `
  --ref refs/heads/main `
  --ref refs/heads/release `
  --output-dir C:\migration\engine-plan
```

Multiple subtrees and historical aliases are supported. Repeat `--move`.
For a historical `git mv vendor/old vendor/intermediate`, pass both
`vendor/old=components/final` and `vendor/intermediate=components/final`.
Coexisting aliases mapping to the same file, file/directory collisions,
overlapping sources, chained/nested renames and malformed trailers are
rejected before modification. Explicit refs may be branches or tags; no
implicit all-branch/tag rewrite is performed. Unselected refs retain old SHAs.

Planning scans full selected ancestry, including merges. It writes a hashed
plan and identifies metadata-bearing commits. A source directory must have
an existing squash-subtree association; arbitrary non-squash imports are not
silently converted. Signed commits/tags require explicit acknowledgement
using `--allow-signature-removal`; signatures cannot survive SHA changes.
Reports and workspaces must be external to the source repository.

If old `git-subtree-split` objects are no longer local, add
`--upstream vendor/engine=UPSTREAM_URL_OR_PATH`. After migration approval,
the tool fetches the exact declared split objects into private local refs
in the clone. It does not re-import the project into the host directory.
Servers may disallow fetching an unadvertised or expired SHA: resolve such
cases by obtaining the required original upstream objects, not inventing IDs.

## Confirm and migrate

Review `plan.json`, selected refs, mappings, affected commits and signatures.
Coordinate a temporary publishing freeze with collaborators. Then run:

```powershell
python .\git_history_toolkit.py subtree migrate `
  --plan C:\migration\engine-plan\plan.json `
  --workspace C:\migration\application-rewritten
```

The exact confirmation is `MIGRATE`. Cancellation does not create a clone or
rewrite history. Source refs and cleanliness are rechecked after confirmation.
A verified `before_migration.bundle` and `before_refs.json` precede rewriting.
An executed plan is not reused; generate a fresh plan and workspace on retry.

Only exact `git-subtree-dir` trailer values change to the final prefix.
`git-subtree-mainline` is translated through the commit mapping;
`git-subtree-split` continues to name the original upstream commit. Prose,
Change-Id lines and unrelated messages are unchanged. Empty and degenerate
merge pruning are disabled. `--partial` keeps non-selected refs intact;
old refs can intentionally retain old objects and consume disk space.

## Verification artifacts

- `commit-map.txt`: the filter-repo old/new SHA mapping, including unchanged commits.
- `verification.json`: exact per-commit tree/identity/message/parent checks,
  selected tip mapping, unchanged unselected refs, path logs, blame samples,
  `git fsck`, and incremental subtree results.
- `probe-*`: isolated consumers/upstreams for sync verification. Consumer
  object alternates read the rewritten clone; probes create only their own
  refs, index and worktree. Keep the rewritten clone while inspecting probes.

For every selected ref where a target exists, verification starts from its
real original upstream split, creates two synthetic upstream additions, and
performs two `subtree pull --squash` operations. Each addition is pulled a
second time to assert idempotency. Existing/local files and unrelated paths
must stay byte-identical. Probe timestamps are newer than original history
to avoid native subtree log selection problems with clock skew.
No synthetic marker is committed to the deliverable migrated branch.

This proves the association mechanism and independent additions, not that
arbitrary future upstream edits cannot conflict with local changes. Real
upstream updates still need normal review, testing and conflict resolution.
Refs without the subtree at their tips are explicitly marked not present.

## Remote publication and collaboration

Prepare a read-only publication plan only after successful verification:

```powershell
python .\git_history_toolkit.py subtree publish-plan `
  --plan C:\migration\engine-plan\plan.json --remote origin
```

It resolves the source's **push** URL, reads current destination refs, rejects
diverged source/workspace/remote states, and writes `publication-plan.json`.
Each command is an argv list to avoid shell-quoting ambiguity. Existing refs
use explicit `--force-with-lease=FULL_REF:OLD_OID`; new refs can use ordinary
push. Review and confirm actual publication separately. The tool never pushes,
deletes remote refs, changes protection settings, or runs hosting-service GC.
Do not mirror-push private `refs/toolkit/upstream/*` or recovery refs.

GitHub/GitLab protected branches may require an administrator to temporarily
permit history replacement. Rewritten tags require separate release approval.
`ls-remote` proves read access, not write permission or policy compliance.

Gerrit requires direct branch updates under `refs/heads/*`, not review
submission to `refs/for/*`. Administrators must evaluate Push with Force,
Forge Author/Committer Identity (and Forge Server Identity when applicable),
protected refs, validation plugins and the project's receive policy. Exact
permission names/effects vary by version/configuration. Never rewrite
`refs/changes/*`, NoteDb or review state with this tool. Open changes and topic
branches need a coordinated rebase/cherry-pick plan; retaining Change-Id text
does not automatically make old patch sets compatible with rewritten ancestry.

After publication, prefer fresh clones. Use the SHA map when rebasing or
cherry-picking unpublished work. Avoid merging old mainline history back into
the migrated branch. VS Code and other clients may need a refresh/reindex;
the Git data is verified, but GUI cache behavior cannot be guaranteed.

## Recovery

The source repository is retained unchanged. On failure, do not publish the
workspace; preserve the bundle, plan and verification artifacts. Restore into
a new directory with `git clone before_migration.bundle recovered` and use
`before_refs.json` to inspect non-default refs. A bundle is not a backup of
untracked/ignored files, LFS content, submodule repositories or all settings.

If remote history was already replaced, restoring old refs is another history
replacement: coordinate collaborators and obtain a fresh, explicit lease
against the currently published OID. Never automatically reset the source,
force-push an old backup, or expire reflogs as a recovery shortcut.

## Current boundaries

Full, non-empty SHA-1 working clones and `--squash` associations are supported.
Bare repositories must be accessed through a working clone. Partial/shallow
clones, unresolved/colliding historical paths, non-squash conversion, automatic
Gerrit review rewrites, and automatic remote permission changes are outside
this workflow. Validation time grows with selected history size because every
selected commit is checked. Use explicit refs to keep the scope intentional.
