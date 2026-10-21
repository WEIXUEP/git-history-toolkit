# Git, Gerrit and recovery

For publishing rewritten/unrelated history, diagnosing rejected force pushes
or planning approved branch replacement, read
[gerrit-history-replacement.md](gerrit-history-replacement.md). It covers
effective permissions, exact-state leases, default HEAD and staged rollback.

## Establish coverage

Local remote-tracking refs are snapshots. An audit does not prove they match
the remote. Read-only `git ls-remote --heads --tags <remote>` can identify
differences; do not print credentials embedded in URLs. Fetch only within
the user's requested synchronization scope, then audit again. Never auto-prune
refs because doing so can remove protected tip trees and change candidates.

Ordinary branch updates can be fetched explicitly, for example:

```shell
git fetch origin '+refs/heads/*:refs/remotes/origin/*'
```

Fetch Gerrit patch-set refs only when that server/project is in scope:

```shell
git fetch origin '+refs/changes/*:refs/changes/*'
```

These are local mutations, not read-only inspection. Do not run them merely
because this reference contains examples. Avoid placing a broad Gerrit fetch
refspec in permanent user configuration without a request to do so.

The auditor analyzes numeric `refs/changes/NN/CHANGE/PATCHSET` refs. `meta`
alone does not provide patch-set coverage. Even a successful local scan only
covers the patch sets actually fetched; it is not a complete server inventory.
`refs/for/*` is a review submission interface, not a stored patch-set namespace.
Closed changes and older patch sets may still retain objects on the server.
Do not infer permissions to delete them, rewrite NoteDb, or run server GC.

No Gerrit refs is an expected coverage warning for ordinary Git repositories.
For a known Gerrit repository, missing patch-set coverage must be resolved or
explicitly acknowledged in the cleanup plan. Never hide known local Gerrit refs
using `--no-scan-gerrit-refs` to make objects appear automatically cleanable.

## Branches, tags and physical storage

Protecting a ref's **tip tree** keeps its current files, not every historical
version reachable from that ref. History rewriting can still change commit
IDs throughout branches and tags. Annotated tag signatures and signed commits
may become invalid or be removed by the rewrite; discuss this when present.
Do not delete release tags or disable tag protection to manufacture savings.

A blob shared between a `.bin` path and a protected code/data path is protected
as one object. Include/exclude filters control candidate selection; they do
not make a SHA removal path-specific.

The single-branch push helper is not a complete multi-branch publication plan.
Compare old/new ref mappings and list each branch or tag that would need a
remote update. Preserve separate fetch URLs, push URLs and refspecs. Remote
tracking values alone are not a lease against an independent push destination.

An unchanged commit/tag/Gerrit ref, reflog, host backup or another clone can
retain old objects. A successful push does not guarantee server disk savings.
Do not run immediate destructive GC, reflog expiration or hosting-service
cleanup actions as an implicit part of this skill.

## Failure and recovery

On any rewrite or verification failure, stop before pushing. Preserve the
external report directory. It contains a verified `before_cleanup.bundle`,
`before_cleanup_refs.txt`, selected IDs, and
`before_cleanup_remote_config.json`. Treat remote configuration as private;
it can contain credentials and must not be uploaded as a public report.

Check the bundle read-only:

```shell
git bundle verify /absolute/path/before_cleanup.bundle
git bundle list-heads /absolute/path/before_cleanup.bundle
```

Propose recovery into a **new** location:

```shell
git clone /absolute/path/before_cleanup.bundle /new/path/recovered
```

A bundle contains Git refs/objects, not ignored/untracked files, LFS payloads,
submodule repositories, hooks, all repository settings or every reflog entry.
Use the saved ref/config inventory to inspect recovery; it is not a full working
directory backup. Never automatically reset the original worktree or update
old refs to object IDs that filter-repo may already have pruned.
