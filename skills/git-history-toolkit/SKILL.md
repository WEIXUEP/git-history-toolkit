---
name: git-history-toolkit
description: Audit large Git history objects with default HTML review and confirmed cleanup, migrate squash-subtree directories while preserving host history and upstream synchronization, and plan Gerrit history publication or branch replacement. Use for oversized repositories, deleted large files, subtree history migration and Gerrit non-fast-forward diagnosis.
---

# Git History Toolkit

Use the self-contained CLI `scripts/git_history_toolkit.py`, resolved relative
to this skill. Respond in the user's language. Requires Python 3.10+ and Git;
history rewriting needs git-filter-repo 2.47+, and subtree operations need
`git subtree`. Default HTML confirmation uses Python's standard library and
a local browser; no GUI framework or separate skill installation is needed.
Use a working Python interpreter, not an OS launcher placeholder. Cleanup
uses git-filter-repo on PATH or the module in that interpreter; migration
uses the module. Check the required command before proposing execution.

## Select the workflow

- For large objects, deleted files or old binary versions, read
  [references/audit-workflow.md](references/audit-workflow.md). Start with a
  read-only audit. Before scanning, the user must manually confirm minimum
  size in MiB and minimum age in days in the default HTML parameter page.
  100 MiB / 180 days are suggestions, never automatically accepted values;
  command-line values also need confirmation. Age is since the last observed
  history reference change, including addition/replacement/deletion, not
  filesystem mtime or latest containing snapshot. Never fill or submit the
  threshold page for the user. Cancel/timeout means no scan and no cleanup.
  After confirmation, keep the HTML page open for live analysis progress:
  stage, processed/total counts when known, and elapsed time. History and
  historical-snapshot searches report real counts; unknown totals remain
  indeterminate. The same tab switches to object review when ready.
  Analysis must end with the default HTML review for manual suffix-based
  selection, readable sizes and exact historical presence times. Read
  [references/browser-review.md](references/browser-review.md). Open the page
  and give the user its live local URL; JSON/CSV or a chat summary alone does
  not complete the workflow. Review alone never executes cleanup. Use
  --no-review-html only when the user explicitly chooses terminal-only work
  or the environment cannot support a reachable browser; explain that limit.
- For subtree directory/history migration or add/pull verification, read
  [references/subtree-workflow.md](references/subtree-workflow.md). Start with
  an explicit path/ref plan. Keep upstream split identities and root trees.
- When both are requested, inspect once but run sequentially. After either
  rewrite, regenerate the other operation's audit/plan from its resulting refs.
  Old SHA maps, candidates and remote leases cannot be reused as current state.
  Do not infer approval for both operations from approval of one.
- For Gerrit history publication, unrelated-history replacement or rejected
  non-fast-forward updates, read
  [references/gerrit-history-replacement.md](references/gerrit-history-replacement.md).
  Diagnose permissions and the actual write endpoint first. Prefer explicit
  leases. Delete/recreate is a separately approved, non-atomic fallback with
  verified backups, default-HEAD handling and staged recovery; ordinary push
  or rewrite approval does not authorize it.

```powershell
python '<skill>/scripts/git_history_toolkit.py' audit --repo '<repo>' --output-dir '<external-report>'
python '<skill>/scripts/git_history_toolkit.py' subtree plan --repo '<repo>' --move vendor/a=components/a --ref refs/heads/main --output-dir '<new-external-plan>'
```

The standalone `git_history_auditor.py` and `git_subtree_migrator.py` remain
available inside scripts for compatibility; both use the same bundled src.

## Incremental audit cache

CLI audits cache Git facts by default in the OS user cache directory, outside
the worktree. Read [references/audit-cache.md](references/audit-cache.md) for
reuse, invalidation, limits and maintenance. Report cache mode and events with
the audit. Recompute classifications from this run's thresholds/protection
rules/time; cached candidates and approval receipts are never authorization.
Cleanup audits always bypass cache, and confirmed cleanup clears its namespace
before rewriting. Subtree clones have separate identities; there is no cached
migration or publication approval. Use --no-cache when comparing fresh reads.
Cache maintenance removes only disposable cache data, never reports/backups.

## Mutation and publication

Audit and plan requests authorize inspection and reports. Before a real
rewrite, present the concrete repository, exact operation, scope, affected
refs/objects or paths, backup and validation plan, then obtain explicit user
confirmation. Cleanup uses terminal CLEANUP or the user's deliberate HTML
selection plus CLEANUP submission; never automate their browser confirmation.
Use HTML by default for cleanup too. REVIEW only saves a selection and never
authorizes cleanup; do not replay its receipt as CLEANUP approval.
Subtree migration uses MIGRATE and creates
an independent clone. Preserve work and verified bundles; do not automatically
stash, reset, expire reflogs or prune objects.

Local rewrite approval does not authorize remote history replacement. Obtain
separate confirmation of destination, selected refs, old/new OIDs and explicit
per-ref force-with-lease commands. Cleanup's optional push workflow additionally
prompts for PUSH; subtree publish-plan only prepares commands. Failed checks,
changed refs or a failed lease require renewed review, not a force retry.
Remote permission probes, ACL/server edits and default-HEAD changes must fit
the user's explicit remote-operation authorization. Existing CLI publication
helpers do not execute the delete/recreate fallback.
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
