# Reopen completed audits

When the user wants an earlier completed object table, open its report instead
of launching another audit. A live HTTP 200 response only proves the old local
server responds; its page may still be waiting for thresholds or scanning.
Do not submit thresholds or consent on the user's behalf to reach another page.

```powershell
python '<skill>/scripts/git_history_toolkit.py' review --report '<completed-report-directory>'
```

This opens the object table directly, without a threshold page or history
traversal. It starts a new loopback session with no selection and a new token.
Dates, ages, thresholds, classifications and paths belong to the original audit,
whose date/path are displayed in the page. Reopening does not make old results
current and never replays browser-review.json or CLEANUP/PUSH approval.
Use --no-open-browser to print the URL or --review-timeout 1-3600 as needed.

New scans save checksummed audit-session.json (full facts, options, refs and
Git context) after core analysis. HTML preparation also saves
review-payload.json before waiting for a decision, retaining exact historical
presence times independently of cache expiry. Keep the full original report
directory; disposable cache maintenance does not remove these files.

## Older reports

Reports without audit-session.json can be opened using their blobs.json,
cache-events.json and summary.txt. They are view-only because their complete
policy/context was not recorded. Existing matching presence-cache facts are
read without new history queries; absent times show Unknown. No full scan is
silently started to fill them. If exact original presence times were neither
saved nor cached, recovering them requires a separately requested audit.

Archived reports can be viewed after refs change or when the original repository
is unavailable. Saved review-payload.json still supplies dates when offline.
Warnings identify archived or unavailable repository state. Never promote an
old report to a resumable format by inventing options, context or checksums.

## Resume cleanup

For a new-format report, prepare this command and let the user confirm their
selection in HTML:

```powershell
python '<skill>/scripts/git_history_toolkit.py' review --report '<completed-report-directory>' --execute-cleanup
```

It checks the manifest checksum/schema, canonical repository/Git directory,
HEAD and all refs, Git version/configuration, replace refs, grafts and GIT_*
environment context. One cat-file batch checks saved blob types/sizes. The
saved policy is reapplied at the original audit time and must produce the
same classifications; elapsed days cannot add candidates. It does not rerun
object enumeration, per-object log searches or historical tree traversal.

Changed/missing state, unavailable objects, incompatible classifications or
legacy reports block cleanup and require a new audit through the usual manual
threshold confirmation. Do not bypass these checks. Changed Git context and
refs are checked again before/after the new HTML confirmation, alongside the
normal clean-worktree and backup prerequisites. Only a new eligible selection
and CLEANUP submission authorize rewriting. The tool never selects or submits
for the user.

Every reopening creates a new external sibling report directory for receipts
and optional verified backups; --output-dir can choose another new directory.
Original reports, receipts and backups are not overwritten. Cancelling creates
no backup or rewrite. Resume never performs a remote push. Publication still
needs a separate reviewed destination/ref/lease and explicit approval.
