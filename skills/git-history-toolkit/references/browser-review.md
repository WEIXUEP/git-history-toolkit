# Browser confirmation

Use Python's loopback HTTP review by default after every large-object audit,
including zero-candidate results. Open the page and provide its live URL for
the user's manual inspection and selection. It has no Flask, Node, GUI toolkit, CDN or network
asset dependency. A modern local browser is the only UI requirement.

## Review without rewriting

```powershell
python '<skill>/scripts/git_history_toolkit.py' audit --repo '<repo>' --output-dir '<external-report>'
```

The default browser opens a local URL with an English desktop interface.
The table prioritizes historical paths, readable MiB/GiB and exact byte sizes,
latest presence and status. Expand Details for object SHA, all historical
paths, protection/candidate reason and reference-change time. The two UTC
timestamps have different meanings:

- Historical latest presence: the maximum committer time among all locally
  reachable commit snapshots that contain that blob, including HEAD. It can
  be later than the last content edit because unrelated commits retain files.
- Latest reference change: the last introducing/removing change observed by
  the audit. This is the existing cleanup age-policy time, not presence time.

Unknown times are shown explicitly. Unfetched remote commits cannot contribute.
Presence calculation scans historical trees only when HTML review is requested;
on long histories this adds work before the URL becomes available.

Filter by suffix or status, search paths/SHA, sort by size/date/path, select
filtered eligible objects, or clear the selection. Nothing starts selected.
Suffixes use all known historical aliases, case-insensitively; one shared blob
can belong to multiple suffix groups but is selected only once. Selecting it
means stripping the entire blob across its historical aliases, not deleting
one displayed path. Current/protected and Gerrit administrator-review objects
remain disabled. Size sums are raw object bytes, not promised disk savings.

In review mode, type REVIEW and save the selection. This writes
browser-review.json beside the audit reports and does not rewrite or push.
The receipt is a record, not an authorization file to replay against later refs.

## Confirm selected-object cleanup

First show the concrete audit scope, eligible candidates, exact command and
backup path. The user chooses the final subset and explicitly approves it
in the default browser confirmation:

```powershell
python '<skill>/scripts/git_history_toolkit.py' audit --repo '<repo>' --output-dir '<external-report>' --review-html --execute-cleanup
```

The page shows the repository, thresholds, backup path and selected count/bytes.
Only selecting eligible objects and typing CLEANUP enables the final command.
That deliberate submission replaces the local terminal CLEANUP prompt; do not
automate browser approval, select objects, fill the token or click confirmation
for the user. Approved disposable tests may simulate their own confirmation.

The HTTP handler only returns selected IDs. The Python cleanup workflow checks
those IDs against audited eligible blobs, rechecks refs/configuration and the
clean worktree, verifies a bundle, then strips only that subset. Other IDs stay
out of cleanup_blobs.txt. This removes historical objects, not working files.
The terminal remains the source of execution success/failure after submission.

PUSH remains a separate terminal confirmation with an explicit remote lease.
Browser cleanup approval never authorizes remote replacement or subtree work.

## Cancellation and other environments

- Cancel, Ctrl+C, no selection and timeout never authorize cleanup. Closing a
  browser tab is not approval; the default timeout is 600 seconds.
- Set --review-timeout from 1 to 3600 seconds. Use --no-open-browser to print
  the URL without launching a browser. Copying the exact URL is necessary
  because it contains a temporary session token; keep it private.
- On a remote/SSH host, the URL belongs to that host. Use an explicitly arranged
  loopback tunnel with the same port, or explicitly use --no-review-html for terminal
  CLEANUP confirmation. The tool never binds the review service to a public IP.
- Browser failure does not silently approve or switch confirmation modes.
  Explain the limitation and rerun with --no-review-html when a local browser
  cannot reach the review service, or when the user requests terminal-only work.
- Cleanup first tries git filter-repo on PATH, then the git_filter_repo module
  in the current Python interpreter. Install in that interpreter with
  `python -m pip install 'git-filter-repo>=2.47'`. No PATH edit is needed for
  the module fallback. EOF/cancellation remains fail-closed in terminal mode.

The service validates its exact loopback host, origin, session token, payload
digest and allowed IDs. Decisions are single-use, requests are size-limited,
and all path data is rendered as text. The server stops after a decision or
timeout. Do not treat saved HTML or a crafted HTTP request as user consent.

Developer-only visual smoke test: with Playwright and its browser installed,
run `node tests/browser_review_smoke.cjs <read-only-review-URL> <screenshot-directory>`
from the repository. BROWSER_CHANNEL may select an installed channel such as
msedge. This exercises filtering, selection, disabled confirmation and two
desktop viewport sizes, then cancels the read-only session. Add --save to test saving
a review-only selection instead. Node/Playwright are not runtime dependencies
of the skill.
