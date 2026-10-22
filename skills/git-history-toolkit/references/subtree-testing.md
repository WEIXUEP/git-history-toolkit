# Multiple subtree regression

Use this when asked to validate repeated subtree add/pull operations, or after
changing migration logic. The test data is the existing public
[git-auditor-test-repo](https://github.com/WEIXUEP/git-auditor-test-repo), shared
with large-object and Gerrit auditing. Do not replace it with an unrelated tiny
fixture. Test-only upstream projects are created inside disposable directories.

## Run the packaged test

Use Python 3.10+ with git-filter-repo 2.47+ installed, and Git with subtree.
Clone the fixture into a new directory if no suitable local copy exists.
Create the local branch from its published remote-tracking ref when needed:

```powershell
git clone https://github.com/WEIXUEP/git-auditor-test-repo.git C:\tests\git-auditor-test-repo
git -C C:\tests\git-auditor-test-repo branch fixture/subtree origin/fixture/subtree
$env:GIT_HISTORY_FIXTURE = 'C:\tests\git-auditor-test-repo'
python '<skill>/scripts/test_subtree_operations.py' -v
```

For the existing local fixture, set GIT_HISTORY_FIXTURE to its absolute path;
do not repeat clone/branch creation over an existing destination. The script
is packaged with its implementation modules and needs no toolkit checkout.
An absent fixture or local fixture/subtree ref causes a skipped test, which
must be reported as not run, not as successful validation. A successful run
ends in OK and prints a JSON summary with status passed.

The test's MIGRATE response applies only to its generated disposable clone.
It never authorizes a real migration or remote history replacement. The
source fixture refs remain unchanged, and temporary repositories are removed
when the test completes. Keep the terminal output for the result details.

## Scenarios and expected evidence

| Phase | Operations | Expected result |
| --- | --- | --- |
| Base | Existing projects a and b, squash imported and previously pulled | Local maintenance history and original split objects available |
| Before migration | Add c and d using native subtree add --squash | Both imports preserve all existing host files |
| Before migration | Three rotating pull rounds across a, b, c, d | 12 updates; upstream file added, modified, then deleted |
| Migration | Four source prefixes mapped to final directories | Every selected commit verified, including identity and ordered parents |
| After migration | Add e and f at new prefixes | New imports coexist with all migrated projects |
| After migration | Three rotating pull rounds across six projects | 18 updates, including existing and newly added projects |
| Every update | Repeat the same pull | No new commit; clean working tree |
| Completion | Whole-tree equality, local Alice edit, source refs and fsck | Unrelated content and local edits preserved; source unchanged |

The baseline run on 2026-10-20 passed: four actual new adds, four migrated
subtrees, six final projects, 30 upstream update pulls and 30 repeat checks.
These counts exclude migration's additional automatic synchronization probes.
The test retains the fixture's large-object history while exercising subtree
operations; it does not run a history cleanup.

## User repository acceptance

The focused protection regression is also packaged:

```powershell
python '<skill>/scripts/test_subtree_protection.py' -v
```

It constructs a separate disposable reproduction, supplementing the shared
fixture above: six synthetic squash commits, six missing real upstream splits,
212 source-bearing host snapshots, an unrelated subtree, a historical rename,
and a no-mainline relocation trailer naming the destination. Its expected
protected and affected sets come from fixture construction, independently of
the planner/verifier. Expected counts are 6 protected and 212 affected. It
checks every rewritten affected snapshot, original squash trees, unchanged
tip/source refs, path log and blame. The invalid destination-side trailer
also confuses native subtree synchronization, so that scenario must fail the
incremental check rather than report success. A control history omitting only
that metadata commit must complete migration with two incremental pulls and
two repeat checks. The test does not operate on a user's repository.

For a real plan, review protection domains before MIGRATE; destination-side
metadata must not protect host ancestry. Regenerate old plans in a new report
directory after the fix, even if an earlier migration verifier reported success.

The same focused regression covers historical submodule-to-directory changes.
Its additional scenario has three exact-source gitlink snapshots, an ordinary
directory copy with a nested gitlink, then squash-subtree history. It expects
6 protected and 209 affected commits, independently excluding the three root
gitlink snapshots. It verifies those snapshots and `.gitmodules` are unchanged,
the nested gitlink's path moves while mode/OID stay intact, and all 209 directory
snapshots are renamed. The earlier directory-only fixture still expects 212
affected commits. Unit cases also require ordinary files, executables, symlinks
and real path collisions to remain rejected. Both scenarios retain the
destination-metadata rejection/control checks described above.

The migration command automatically checks each selected ref where a migrated
target exists, using two synthetic additions from its real upstream split and
two repeated pulls. Inspect verification.json for the actual results; a target
marked not-present-at-tip has not passed an incremental pull check on that ref.

The packaged multi-project regression proves the tested association behavior.
It does not prove that arbitrary changes from a real upstream merge without
conflicts. If the user requests actual upstream revision validation, test the
requested source/branch in another independent consumer and compare expected
upstream changes plus existing local modifications. Preserve any conflicts
for review, and keep test commits out of the deliverable branch. Production
add/pull operations and remote publication need authorization for their scope.
