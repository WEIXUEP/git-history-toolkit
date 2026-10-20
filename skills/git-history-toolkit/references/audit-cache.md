# Incremental audit cache

CLI audits use a Python-standard-library SQLite cache by default. Programmatic
callers opt in with cache=True. The cache is disposable: it contains Git facts,
not cleanup decisions, user approvals, bundles or rewritten repositories.

## Reuse and invalidation

- A namespace hashes the canonical Git common directory and worktree root.
  Different clones and worktrees do not share cached reachability. Default
  roots use LOCALAPPDATA on Windows, Library/Caches on macOS, or XDG_CACHE_HOME
  (falling back to ~/.cache) on Linux. --cache-dir selects an external root;
  paths inside the worktree or Git directory are rejected for caching.
  Symlinked namespace/database files and databases without the toolkit's
  ownership marker are ignored, not cleared or repurposed.
- The context includes schema version, Git version, Git configuration,
  replacement refs, grafts and GIT_* environment values. Changes create fresh
  cache keys. Context values are hashed, not copied into cache reports.
- With unchanged refs, the object/size/path inventory is reused. It includes
  small objects too, so lowering a size threshold does not hide earlier files.
- With added refs or fast-forward updates, rev-list and raw history scan only
  the new reachable range, excluding prior tips. Previously known objects
  retain aliases; newly introduced aliases are collected from raw diffs.
- Removed refs, moved tags, non-fast-forward updates or changed contexts
  trigger a full inventory rebuild. Reflogs/unfetched remote refs are outside
  audit coverage as before. HEAD is included, including detached HEAD.
- Successful history queries and exact historical-presence results are reused
  only for their matching ref snapshot/context. Named revisions use frozen
  OIDs and cache writes recheck refs. Scopes above 200 unique prior tips fall
  back to a full inventory on ref changes to bound exclusion command lines.
  On ref changes, history queries/presence may need full traversal again;
  this is incremental object scanning, not a promise of zero full Git walks.
- Age, protection, path scope and classification are recomputed on every run.
  Ref-state checks still reject changes during analysis and confirmation.
  Cleanup audits always bypass cache, even when --cache is set; the confirmed
  workflow clears cached data before filter-repo, after verified backup.

Checksum failure discards the entry. Unsupported schema resets disposable
entries. Database corruption, permission errors and lock failures fall back to
fresh Git reads with a warning. A corrupt database is not recursively removed
or repaired automatically; --no-cache or another external --cache-dir remains
available. No cache state can approve cleanup, migration or remote publication.

## Retention and records

Defaults are 30 days from entry creation and 256 MiB of payload per repository.
Reads update LRU access time, not the hard expiration time. Expired entries are
removed when opened and written. If payload exceeds the limit, least-recently
used entries are evicted. Individual entries above 32 MiB (or the configured
total limit) are skipped, with an event; very large inventories may therefore
need fresh enumeration. SQLite metadata/free pages add physical overhead;
explicit prune/clear compacts the database. No global retention across all
repositories is implied, and old clone namespaces may remain under the root.

Configure --cache-max-mb and --cache-max-age-days, or use --no-cache. Disabled
caching does not erase existing data. Reports include cache-events.json and
summary.txt with location, mode (full/incremental/reused/disabled), hit/miss
counts, object IDs checked/total, writes, eviction count, limits, captured refs
and invalidation events.
HTML history-presence reuse is logged separately in presence-cache-events.json.
Cache maintenance prints JSON including payload and physical database bytes.

## Maintenance

```powershell
python '<skill>/scripts/git_history_toolkit.py' cache status --repo '<repo>'
python '<skill>/scripts/git_history_toolkit.py' cache prune --repo '<repo>'
python '<skill>/scripts/git_history_toolkit.py' cache clear --repo '<repo>'
```

Use the same --cache-dir if the audit used a custom root. prune accepts
--cache-max-mb / --cache-max-age-days. clear deletes entries and vacuums only
that repository's SQLite database; it does not remove a directory, Git object,
report, receipt or backup. Other repository namespaces are untouched. Stop
concurrent audits if the intent is to keep the namespace empty; another audit
can populate it again after a clear. Status/prune also expire old entries.

For a missing or corrupt database after a clone was deleted, locate its exact
namespace from an earlier cache report before manual housekeeping. Never
derive broad recursive deletion commands from a repository name or user input.
