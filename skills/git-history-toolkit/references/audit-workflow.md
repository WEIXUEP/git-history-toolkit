# Git Large Object Auditor

Use this skill's `scripts/git_history_auditor.py`, resolved relative to the skill directory. Requires Python 3.10+ and Git; actual cleanup also requires git-filter-repo. HTML selection and confirmation is the default final step after analysis; read [browser-review.md](browser-review.md). Respond in the user's language.

## Inspect and audit

Identify the absolute repository root, requested thresholds, branch/tag scope, and whether Gerrit is involved. Inspect worktree status, shallow status, refs, and configured fetch/push destinations. Use an existing working Python interpreter; an OS Python launcher placeholder is not an interpreter. This CLI requires a full, non-empty working clone; audit bare remotes through an independent full clone.

Suggest **100 MiB / 180 days** unless the user specifies other values. The CLI
opens an editable threshold-confirmation page before scanning; the user must
manually confirm size and days, including values supplied on the command line.
Changing either field resets confirmation. Cancellation/timeout stops before
analysis. Explain that days measure the latest observed historical reference
change (including deletion), not filesystem mtime or the last snapshot that
retains an object. Never select the confirmation checkbox or submit for the
user. Explain a zero-result threshold before changing it. For the published
test fixture, suggest **5 MiB / 180 days**, as documented in
[audit-testing.md](audit-testing.md).

```powershell
python "<skill>/scripts/git_history_auditor.py" --repo "<repository-root>" --output-dir "<external-report-directory>" --min-size-mb 100 --min-age-days 180
```

Audit reads local objects and writes reports. It does not fetch, rewrite, push, or run GC. Use reports outside the repository. Read `summary.txt` and `blobs.json`, then candidate/Gerrit reports as needed. Present separately:

- Protected/current objects and why they are kept.
- **Automatic local cleanup** (`CLEAN_HISTORY`) objects, bytes, IDs and historical paths.
- **Gerrit administrator review** (`GERRIT_ADMIN_REVIEW`) objects.
- Recent objects, omitted scope and coverage warnings.

The CLI then opens an HTML review by default. Keep the local review server
available while the user inspects it, open the page and provide its live URL.
The table must expose file sizes, latest historical presence and suffix-based
selection. Do not stop at text/CSV reports, even when there are no candidates:
the HTML still shows protected results or an empty result. Never fill REVIEW
or select/submit on the user's behalf. REVIEW saves a receipt only. For a
headless host, arrange a reachable loopback URL or explain the limitation;
--no-open-browser prints the URL while retaining HTML confirmation.
Use --no-review-html for an explicitly requested terminal-only workflow or
when a browser cannot be reached. The terminal still asks for size and days
and requires SCAN before analysis. This is a fallback, not implicit approval.

`Policy eligible` includes Gerrit review objects; it is not the automatic cleanup count. Raw blob totals do not predict disk savings. Age measures the latest observed introducing/removing change from local history, not last filesystem access.

Protect HEAD, local branch tips, remote-tracking tips and tags by default. Optional `--protect-code-files` and `--protect-text-data-files` protect historical suffixes. Repeat `--keep-path`, `--keep-blob`, `--include-path`, or `--exclude-path` to express user scope. Path/extension rules operate on whole blob IDs: one object can have multiple paths and aliases. Never silently weaken protections.

For Git/Gerrit coverage, fetching, multiple branches, archive tags, or remote publication, read [git-and-gerrit.md](git-and-gerrit.md).

## Confirm before cleanup

Invoking this skill or asking for an audit/plan does not authorize actual cleanup. Before modification, show a concrete plan containing the absolute repository, exact command and options, local ref scope, `CLEAN_HISTORY` IDs and paths (link a complete report if long), excluded Gerrit objects, backup destination, and the fact that commit IDs change. Obtain the user's explicit confirmation for this plan. In terminal mode, wait for their reply; when they request HTML confirmation, their deliberate eligible selection and CLEANUP submission is the approval. Never submit it for them. A previous approval remains valid only for the same concrete plan and unchanged scope.

Default cleanup uses `--execute-cleanup` with HTML still enabled: the user selects the final eligible subset and types CLEANUP in the page; that submission is local approval. Never fill or submit it for them. REVIEW from the earlier read-only page is not cleanup approval and its receipt cannot be replayed. For terminal confirmation, only after approval rerun the same arguments with `--execute-cleanup --no-review-html` interactively. Compare the printed candidates against the approved list before entering `CLEANUP`. New candidates, changed options, or changed refs require a new audit and confirmation. The CLI rejects stale audit refs, changes during confirmation, a dirty worktree, disabled HEAD protection, multiple linked worktrees, missing filter-repo, and disabled Gerrit scanning when local Gerrit refs exist.

Do not discard or stash user work automatically. Do not replace confirmation callbacks, pipe confirmation tokens, or introduce a yes/force-approval switch. If interactive input is unavailable, give the exact command to run and state that cleanup has not executed. EOF, cancellation or no reply is not approval.

Before rewriting, the CLI creates and verifies `before_cleanup.bundle`, saves refs and remote/branch configuration, and writes the selected IDs. It rewrites locally with git-filter-repo and verifies the HEAD tree. It does not promise automatic rollback after a failure. Preserve backups; inspect failures and recover in a separate clone before proposing any further mutation. See recovery guidance in [git-and-gerrit.md](git-and-gerrit.md).

## Verify and confirm push separately

After cleanup, inspect HEAD, all protected branch/tag trees, the commit map, remaining candidate reachability, remote configuration and `git fsck`. The built-in tree check covers HEAD; other protected refs require explicit review. Report actual object statistics without promising server-side space reclamation.

Local cleanup approval does not authorize a push. Show the actual push URL, destination branch, old remote OID and exact `--force-with-lease=refs/heads/<branch>:<oid>` command, then obtain separate user confirmation. Do not use plain `--force`, mirror pushes, blanket branch/tag pushes, or publish Gerrit internal refs implicitly. For multi-branch/tag cleanup, enumerate and confirm each affected destination; the combined CLI only pushes HEAD to one explicitly selected branch.

If the user requests the combined CLI workflow, add `--push-after-cleanup --push-remote <remote> --push-branch <branch>`. It requires one existing destination branch and one push URL, queries that destination's actual OID before cleanup, and pauses for `PUSH` after cleanup. Enter `PUSH` only after the user's separate approval. Changed destinations or lease failures require review, never a force retry.

Conclude with audit, rewrite and push status separately, verification results, report/backup paths, and any remaining administrator actions. Do not run additional reflog expiration or pruning automatically.
