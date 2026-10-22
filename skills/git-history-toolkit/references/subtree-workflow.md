# Git Subtree Migrator

Use the self-contained `scripts/git_subtree_migrator.py` relative to the skill root. Requires Python 3.10+, Git with subtree, and git-filter-repo 2.47+. Read [subtree-migration.md](subtree-migration.md) for operational details, limitations, publication permissions and rollback.

Inspect the full working clone, explicit branch/tag scope, upstream sources and all historical source directory aliases. Use repeatable `--move OLD=NEW` mappings and full `--ref` names. Squashed upstream commits cannot provide their original per-file history in the host repository; this migration preserves host maintenance history.

For multiple subtrees, record each project's old aliases, final prefix, upstream source and branch/split separately. Native upstream paths that happen to match a host prefix must retain their original tree. Keep `git-subtree-split` as the external upstream identity; translate `git-subtree-mainline` through the host commit map. Do not replace paths globally in messages or upstream trees. Report ambiguous associations, overlapping paths or unavailable upstream objects before attempting migration.

Start with the read-only plan, in a new report directory outside the source:

```shell
python <skill>/scripts/git_subtree_migrator.py plan --repo <source> --move vendor/a=components/a --ref refs/heads/main --output-dir <new-report>
```

Present the concrete plan: source, mappings, selected refs, affected commits, signature removal if any, backup and independent workspace paths. Actual history rewriting requires the user's explicit confirmation of that plan. Request confirmation immediately before mutation; do not pipe MIGRATE automatically for real repositories. An approved disposable integration test may supply its own confirmation.

Independently review protected ancestry and source-bearing host snapshots;
destination-side no-mainline metadata must not protect the host chain. Plans
created before the relocation-protection fix must be regenerated in a new
report directory. Correct counts alone do not establish upstream sync: malformed
destination-side trailers can also confuse native subtree pull. Preserve failed
verification evidence and review that metadata separately, without bypassing
checks or silently modifying additional commit messages.

```shell
python <skill>/scripts/git_subtree_migrator.py migrate --plan <new-report>/plan.json --workspace <new-independent-clone>
```

The command prompts for MIGRATE, creates a verified bundle, rewrites the independent clone, validates each selected commit and tests two upstream increments plus repeat-pull idempotency. Review verification.json and commit-map.txt. Failed checks block publication; retain reports, backup and failed workspace for diagnosis. Never bypass checks with a force push.

For multi-project add/pull validation or skill regression, read [subtree-testing.md](subtree-testing.md). The bundled regression uses the existing public large-object fixture in disposable clones: four additional adds, four migrated subtrees, six final projects, 30 update pulls and 30 repeat-pull checks. It tests additions, modifications and deletions in rotating project order, comparing the entire host tree after each update. This is separate from the migration command's automatic synthetic-addition probes. Do not claim the full regression ran merely because verification.json passed.

For a user's repository, report results per selected ref and target, including absent targets, old/new upstream split and failed/skipped checks. Check path log and blame for representative local edits; history simplification can hide tree-identical merges. Future upstream edits may conflict with local changes even when association checks pass. Do not resolve conflicts by discarding either side or add unrelated projects to the user's deliverable clone as a test.

Prepare a publication plan only after successful verification:

```shell
python <skill>/scripts/git_subtree_migrator.py publish-plan --plan <new-report>/plan.json --remote origin
```

This only reads remote refs and generates explicit push argv with per-ref leases. Read access does not prove write/force/forge permissions. Present destination, old/new OIDs, affected collaborators and platform protection requirements, then obtain separate confirmation before executing any remote replacement. Never mirror push, rewrite Gerrit refs/changes or NoteDb, or reset the user's original clone. Recover from the bundle into a new directory if needed.
