---
name: git-history-toolkit
description: Audit and clean large Git history objects with confirmation, or migrate squash-subtree directories while preserving host history and upstream synchronization. Supports Git/Gerrit analysis and multiple subtree add/pull verification. Use for oversized repositories, deleted large files, git_large_object_auditor and subtree history migration.
---

# Git History Toolkit

Use the self-contained CLI `scripts/git_history_toolkit.py`, resolved relative
to this skill. Respond in the user's language. Requires Python 3.10+ and Git;
history rewriting needs git-filter-repo 2.47+, and subtree operations need
`git subtree`. No GUI or separate skill installation is required.
Use a working Python interpreter, not an OS launcher placeholder. Cleanup
invokes `git filter-repo`, so its executable must also be on PATH; subtree
migration uses the module in the current Python environment. Check the
required command before proposing execution.

## Select the workflow

- For large objects, deleted files or old binary versions, read
  [references/audit-workflow.md](references/audit-workflow.md). Start with a
  read-only audit; use 100 MiB / 180 days unless the user specifies otherwise.
- For subtree directory/history migration or add/pull verification, read
  [references/subtree-workflow.md](references/subtree-workflow.md). Start with
  an explicit path/ref plan. Keep upstream split identities and root trees.
- When both are requested, inspect once but run sequentially. After either
  rewrite, regenerate the other operation's audit/plan from its resulting refs.
  Old SHA maps, candidates and remote leases cannot be reused as current state.
  Do not infer approval for both operations from approval of one.

```powershell
python '<skill>/scripts/git_history_toolkit.py' audit --repo '<repo>' --output-dir '<external-report>'
python '<skill>/scripts/git_history_toolkit.py' subtree plan --repo '<repo>' --move vendor/a=components/a --ref refs/heads/main --output-dir '<new-external-plan>'
```

The standalone `git_history_auditor.py` and `git_subtree_migrator.py` remain
available inside scripts for compatibility; both use the same bundled src.

## Mutation and publication

Audit and plan requests authorize inspection and reports. Before a real
rewrite, present the concrete repository, exact operation, scope, affected
refs/objects or paths, backup and validation plan, then obtain explicit user
confirmation. Cleanup uses CLEANUP; subtree migration uses MIGRATE and creates
an independent clone. Preserve work and verified bundles; do not automatically
stash, reset, expire reflogs or prune objects.

Local rewrite approval does not authorize remote history replacement. Obtain
separate confirmation of destination, selected refs, old/new OIDs and explicit
per-ref force-with-lease commands. Cleanup's optional push workflow additionally
prompts for PUSH; subtree publish-plan only prepares commands. Failed checks,
changed refs or a failed lease require renewed review, not a force retry.
Gerrit refs/changes and NoteDb are not ordinary branch history to rewrite.
Read [references/git-and-gerrit.md](references/git-and-gerrit.md) for auditing
coverage and [references/subtree-migration.md](references/subtree-migration.md)
for migration permissions and recovery.

## Verification

Use the same public git-auditor-test-repo fixture for both capabilities.
Read [references/audit-testing.md](references/audit-testing.md) for frozen-date
large-object classifications and [references/subtree-testing.md](references/subtree-testing.md)
for the packaged multiple-add/pull regression. An approved disposable test may
supply its own confirmation; it does not authorize a production rewrite.
Report audit, local rewrite, checks and remote publication status separately.
Distinguish automatic cleanup from Gerrit administrator review, and distinguish
synthetic migration probes from the full multi-project regression. Failed or
skipped checks are not successful validation.
