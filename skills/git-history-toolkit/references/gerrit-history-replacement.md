# Gerrit history publication and branch replacement

Use after verified cleanup, subtree migration, repository migration or
replacement with unrelated history. This is an operator workflow, not an
automatic CLI fallback. A request for diagnosis does not authorize remote
probes, ACL/configuration changes, default-HEAD changes or branch deletion.

## Select the operation

| Operation | Appropriate use | Requirements and effects |
| --- | --- | --- |
| Ordinary push | Old tip is an ancestor of new tip | Effective Push and server validation; retains ancestry |
| Leased replacement | Verified rewritten history replaces a branch | Effective Push with Force and receive policy allowing the update |
| Delete and recreate | Direct replacement is refused but approved lifecycle operations are supported | Create and Delete/Force, default-HEAD management if needed, write freeze and recovery; non-atomic |
| Administrator policy change | Receive policy prevents the intended operation | Separate authorization and administrator evidence |
| Merge histories | User explicitly wants both histories retained | Review merge/content effects; retains old objects and does not accomplish cleanup |

Unrelated histories explain why fast-forward is impossible; they do not prove
a permission fault. `non-fast forward` alone cannot distinguish ACLs, receive
settings, plugins or project policy. Never turn a rejected push into automatic
branch deletion, a wider force push or an unrelated-history merge.

## Record the plan and inspect

Record repository identity, deployed Gerrit version when available, account,
project, exact write URL, target full ref, old/new full OIDs, original symbolic
HEAD, unique backup/staging/probe refs, validation results, affected CI/users/
open changes, maintenance window and rollback plan. Validate names with
`git check-ref-format`; use explicit `refs/heads/*`, not an assumed `master`.
Redact credentials from reports and keep private configuration out of public
artifacts. Resolve one intended write endpoint; do not broadcast to push URLs.

Read refs from the actual write endpoint, not an independently lagging read
mirror. In an isolated working clone, fetch the target into a unique private
local ref to pin old objects; this local mutation must fit the requested scope.
Pin the validated new tip separately. Inspect using these command templates:

```text
git status --short
git remote -v
git ls-remote --symref WRITE_URL HEAD
git ls-remote WRITE_URL TARGET_REF
git merge-base OLD_OID NEW_OID
git merge-base --is-ancestor OLD_OID NEW_OID
git rev-list --left-right --count OLD_OID...NEW_OID
```

Replace uppercase arguments with recorded values. Pass separate argv elements
through Python `subprocess.run` or quote for the actual shell; these are not
portable shell variable assignments. Check every exit status. `--is-ancestor`
returns 0 for fast-forward, 1 otherwise; other codes are errors. `merge-base`
can return 1 for no common ancestor; do not confuse execution failure with
unrelated history. Parse `ls-remote` by exact full ref; reject missing,
duplicate or unexpected results. Uncommitted files are not pushed. Verify
new tree/content, commit maps and rewrite results before publication.

## Diagnose permissions and policy

| Check | What it establishes |
| --- | --- |
| Owner | Project administration; not every Git ref permission |
| Push / Push with Force | Fast-forward / non-fast-forward updates, subject to receive policy |
| Create Reference | Creation of the specific destination ref |
| Delete Reference / Force | Deletion under the deployed version and effective policy |
| Forge Author/Committer/Server Identity | Whether imported identities are acceptable when applicable |
| Inherited ACLs, BLOCK rules and account groups | Whether grants apply to this account and exact ref |
| Project state, default-branch protection, plugins and receive logs | Restrictions beyond grants |

Inspect accessible `refs/meta/config`/`project.config` and inherited rules.
Absence of a project-local grant is not proof of denial; reading a grant is
not proof of effective permission. Confirm account/group membership using
the deployed server's supported interface. Owner Access edits and direct
pushes to `refs/meta/config` have distinct permission requirements. Preserve
existing ACLs; avoid broad `refs/heads/*` grants for an exact-ref operation.

With explicit authorization, probe creation, deletion and a genuinely
non-fast-forward update independently on unique disposable refs. Verify
absence before creation, record resulting OIDs and use leases below. Success
proves only those refs at that time, not production permissions. A dry run or
read access does not prove all receive validation will pass. Stop if probe
cleanup fails; retain evidence and ask the administrator to handle the ref.

Have an administrator correlate rejection time, account, project and ref with
receive logs. `receive.denyNonFastForwards` may be relevant, but its effect
depends on the actual Gerrit/JGit receive path and version. Native Git config
alone does not establish Gerrit's effective policy. Do not assert it is enabled
without evidence, change it implicitly, or add an unsupported setting to
`project.config` as a supposed server fix. Check deployed-version documentation
for permission names and `gerrit set-head` syntax.

## Prefer explicit leased publication

Present destination, ref, old/new OIDs, verified backup and impact for approval.
Use one explicit refspec, not mirror/wildcard publication or plain force:

```text
git push --force-with-lease=TARGET_REF:OLD_OID WRITE_URL NEW_OID:TARGET_REF
```

A lease guards changes since observation; it grants no permissions and bypasses
no validation. A mismatch or refusal stops publication. Inspect fresh state
and renew the plan/approval before retrying. Do not substitute a local tracking
OID for the actual write endpoint's verified tip.

## Approved delete-and-recreate fallback

This leaves an interval with no target branch. It is neither atomic nor a
permission bypass: deletion and recreation can both fail. Cleanup PUSH and
subtree publish-plan do not automatically authorize this alternative. Obtain
approval naming deletion, default-HEAD changes, exact refs/OIDs, outage and
recovery before executing it.

1. Coordinate a freeze of writes, CI publication and relevant automation.
   Record original symbolic HEAD; do not assume it points to the target.
   Re-read the write endpoint; stop if target differs from OLD_OID.
2. Make an offline bundle containing a pinned old-tip ref; verify it with
   `git bundle verify` and `git bundle list-heads`. Save ref/config inventories.
   Bundles exclude LFS payloads, submodule repos, untracked files and full
   Gerrit metadata. Create unique remote backup and staging refs for OLD_OID
   and NEW_OID, then verify their exact OIDs.
3. If HEAD points to the target or policy requires it, switch HEAD to the
   verified live staging ref using the authorized Gerrit interface:
   `ssh -p PORT USER@HOST gerrit set-head PROJECT --new-head STAGING_REF`.
   Verify with `git ls-remote --symref WRITE_URL HEAD`. If HEAD cannot be
   read/changed as required, stop before deletion.
4. Recheck backup, staging, HEAD and target state. Delete only TARGET_REF
   with the expected-old lease below; any mismatch/refusal stops the flow.
5. Recreate TARGET_REF at NEW_OID using a must-not-exist lease. Verify its
   exact OID before restoring HEAD. Concurrent recreation must reject the
   operation, not get silently overwritten or fast-forwarded.
6. Restore the recorded original symbolic HEAD to its verified existing ref
   (normally TARGET_REF if originally default). Choosing a different default
   requires that change to be included in the approved plan.
7. Freshly clone/fetch from the write endpoint; verify tip, tree, integrity,
   affected refs and subtree incremental pull when applicable. Check Gerrit
   default branch, CI/build and open-change handling before ending the freeze.
   Retain backup/staging refs until separately retired.

Use these exact-state command shapes:

```text
git push --force-with-lease=BACKUP_REF: WRITE_URL OLD_OID:BACKUP_REF
git push --force-with-lease=STAGING_REF: WRITE_URL NEW_OID:STAGING_REF
git push --force-with-lease=TARGET_REF:OLD_OID WRITE_URL :TARGET_REF
git push --force-with-lease=TARGET_REF: WRITE_URL NEW_OID:TARGET_REF
```

The empty expected value requires an absent ref. The old OID on deletion
guards the race after the final read. Probe cleanup likewise uses its actual
recorded OID in the deletion lease. An unguarded `--delete` or check-then-push
is not equivalent. Verify server support; refusal is a stop condition, not
permission to remove the guard. Record each result and observed remote state
outside the worktree. After partial success, reconstruct current state and
prepare the next approved action instead of blindly restarting the sequence.

## Recovery and collaboration

| Observed state | Recovery plan |
| --- | --- |
| Target still OLD_OID | Restore original HEAD if changed and its ref exists; keep diagnostic refs |
| Target absent after deletion | Keep HEAD on a verified live staging ref; recreate OLD_OID with absent-ref lease, verify, then restore original HEAD |
| Target NEW_OID, rollback requested | Another approved history replacement against current OID; if direct updates remain blocked, another approved deletion/recreation window |
| Unexpected target OID or refused recreation | Stop; preserve live HEAD/backups and involve administrator; do not delete more refs |

Rollback is not guaranteed by recording OLD_OID: retain its objects in a
verified backup and establish restore capability. Never point HEAD to an absent
ref, automatically remove backups, run server GC, expire reflogs or reset
collaborators' worktrees. Backup refs intentionally retain old large objects;
publication alone does not establish disk savings.

Branch replacement does not migrate Changes, patch sets, NoteDb or review
state. Exclude `refs/changes/*`, `refs/meta/config` and other internal refs
from replacement refspecs. Change-Id text does not repair review ancestry.
Plan rebases/cherry-picks or re-review of open work, check affected tags and
signatures, prefer fresh clones and avoid merging old mainline back in.
Report publication and verification separately; local fixtures do not certify
a real Gerrit server's permissions or receive behavior.

Official references (select documentation matching the deployed version):

- [Access control](https://gerrit-review.googlesource.com/Documentation/access-control.html)
- [Non-fast-forward error](https://gerrit-review.googlesource.com/Documentation/error-non-fast-forward.html)
- [Set project HEAD](https://gerrit-review.googlesource.com/Documentation/cmd-set-head.html)
- [Git push leases](https://git-scm.com/docs/git-push)
