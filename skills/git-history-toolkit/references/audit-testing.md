# Published regression fixture

Fixture: https://github.com/WEIXUEP/git-auditor-test-repo

Use a disposable independent full clone. Its README and manifest are the
source of truth for seed, baseline date, scenarios and expected blob IDs.
Do not rewrite or push the published fixture as a normal regression check.

The fixture contains approximately 100 commits over two years and 23 large
objects across binary models, ZIP, PNG, source, documentation and structured
data. All blobs are below GitHub's 100 MiB file limit. Therefore the skill's
normal 100 MiB threshold yields zero candidates; use 5 MiB / 180 days here.

GitHub carries Gerrit commits in `fixture-archive/*` tags. Those tags protect
their trees during an ordinary audit. Only in an explicitly selected disposable
clone, run the fixture's documented `restore_gerrit.py --disposable-clone`
command to restore patch-set refs and remove the fixture archive tags locally.
This exception never applies to real release tags or production repositories.

The fixture's verifier does this in a temporary clone automatically:

```powershell
python "<fixture>/scripts/verify.py" --source "<fixture-path-or-URL>" --auditor "<skill>/scripts/git_history_auditor.py"
```

It runs read-only audits with a clock fixed to the manifest's 2026-10-20
baseline. Expected default classifications after restoring patch-set refs:

| Status | Count |
| --- | ---: |
| CURRENT | 6 |
| OLD_VERSION | 3 |
| DELETED_PATH | 10 |
| GERRIT_PATCHSET_ONLY | 3 |
| TOO_NEW | 1 |

Only 13 objects are automatically cleanable; the 3 Gerrit objects require
administrator review. Additional cases verify code/text protection, exclusion
rules, valid formats, commit chronology, full-object sizes and local Git
transport. Live audits will eventually age the recent object past 180 days;
do not rewrite fixture history to keep a timeless expected result.

Unit tests in the auditor repository test confirmation cancellation, stale
audits, state changes, backup verification, remote URL restoration and leases.
The rewrite subprocess is mocked in these tests: verification does not grant
approval for an actual filter-repo run, even on a fixture clone.
