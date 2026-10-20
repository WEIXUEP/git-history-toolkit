"""Bounded, disposable SQLite cache of Git facts, never cleanup decisions."""

from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
import json
import logging
import os
from pathlib import Path
import sqlite3
import sys
import time

from .git_runner import GitCommandError, GitRunner

LOGGER = logging.getLogger(__name__)
# Bump when stored facts or Git traversal semantics change.
SCHEMA = 1
APPLICATION_ID = 0x4748544B
MAX_ENTRY_BYTES = 32 * 1024 * 1024


def digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, ensure_ascii=True).encode()).hexdigest()


def default_cache_root():
    if os.name == "nt":
        base = Path(os.environ.get("LOCALAPPDATA", Path.home() / "AppData/Local"))
    elif sys.platform == "darwin":
        base = Path.home() / "Library/Caches"
    else:
        base = Path(os.environ.get("XDG_CACHE_HOME", Path.home() / ".cache"))
    return base / "git-history-toolkit"


class AuditCache:
    def __init__(self, runner, options):
        self.connection = None
        self.hits = self.misses = self.writes = self.evicted = 0
        self.events = []
        self.inventory = {}
        self.mode = "disabled"
        self.path = None
        self.max_bytes = int(options.cache_max_mb * 1048576)
        self.max_age = options.cache_max_age_days * 86400
        self.context = ""
        if not options.cache or options.execute_cleanup:
            self.events.append("Fresh cleanup audit: cache bypassed" if options.execute_cleanup else "Cache disabled by option")
            return
        try:
            common = Path(runner.run("rev-parse", "--path-format=absolute", "--git-common-dir").strip()).resolve()
            identity = [str(common), str(runner.repo.resolve())]
            root = options.cache_dir or default_cache_root()
            self.path = Path(root).expanduser().resolve() / digest(identity) / "audit.sqlite3"
            if self.path.is_relative_to(runner.repo.resolve()) or self.path.is_relative_to(common):
                raise ValueError("Cache must be outside the worktree and Git directory")
            if self.path.is_symlink() or self.path.parent.is_symlink():
                raise ValueError("Cache file and repository namespace must not be symlinks")
            existed = self.path.exists()
            self.path.parent.mkdir(parents=True, exist_ok=True)
            self.connection = sqlite3.connect(self.path, timeout=.25, isolation_level=None)
            if existed and self.connection.execute("PRAGMA application_id").fetchone()[0] != APPLICATION_ID:
                raise ValueError("Unrecognized cache file; refusing to modify it")
            self.connection.execute(f"PRAGMA application_id={APPLICATION_ID}")
            self.connection.execute("CREATE TABLE IF NOT EXISTS entries (key TEXT PRIMARY KEY, value TEXT NOT NULL, checksum TEXT NOT NULL, created REAL NOT NULL, used REAL NOT NULL, bytes INTEGER NOT NULL)")
            version = self.connection.execute("PRAGMA user_version").fetchone()[0]
            if version != SCHEMA:
                self.connection.execute("DELETE FROM entries")
                self.connection.execute(f"PRAGMA user_version={SCHEMA}")
                self.events.append("Cache schema initialized/reset")
            # Config, replace refs, grafts and Git version can alter traversal semantics.
            grafts = common / "info/grafts"
            self.context = digest([SCHEMA, runner.run("--version"),
                runner.run("config", "--list", "--show-origin"),
                runner.run("for-each-ref", "refs/replace", "--format=%(refname) %(objectname)"),
                grafts.read_bytes().hex() if grafts.exists() else "",
                {k: v for k, v in os.environ.items() if k.startswith("GIT_")}])
            self.prune()
            self.mode = "ready"
        except (OSError, ValueError, sqlite3.Error, GitCommandError) as exc:
            self._disable(exc)

    def _disable(self, exc):
        LOGGER.warning("Audit cache unavailable; using fresh Git reads: %s", exc)
        self.events.append(f"Cache unavailable: {type(exc).__name__}; fresh reads")
        self.mode = "disabled"
        self.close()

    def _key(self, group, key):
        return digest([self.context, group, key])

    def get(self, group, key):
        if self.connection is None:
            self.misses += 1
            return None
        try:
            cache_key = self._key(group, key)
            row = self.connection.execute("SELECT value, checksum, created FROM entries WHERE key=?", (cache_key,)).fetchone()
            if row and time.time() - row[2] <= self.max_age:
                if hashlib.sha256(row[0].encode()).hexdigest() == row[1]:
                    value = json.loads(row[0])
                    self.connection.execute("UPDATE entries SET used=? WHERE key=?", (time.time(), cache_key))
                    self.hits += 1
                    return value
                self.events.append("Damaged entry discarded; fresh reads")
            if row:
                self.connection.execute("DELETE FROM entries WHERE key=?", (cache_key,))
                self.evicted += 1
            self.misses += 1
        except (OSError, sqlite3.Error, ValueError, TypeError) as exc:
            self._disable(exc)
            self.misses += 1
        return None

    def put(self, group, key, value):
        if self.connection is None:
            return
        try:
            data = json.dumps(value, ensure_ascii=True, separators=(",", ":"))
            size = len(data.encode())
            if size > min(MAX_ENTRY_BYTES, self.max_bytes):
                self.events.append(f"Oversized {group} entry skipped ({size} bytes)")
                return
            now = time.time()
            self.connection.execute("INSERT OR REPLACE INTO entries VALUES (?,?,?,?,?,?)",
                (self._key(group, key), data, hashlib.sha256(data.encode()).hexdigest(), now, now, size))
            self.writes += 1
            self.prune()
        except (OSError, sqlite3.Error, ValueError, TypeError) as exc:
            self._disable(exc)

    def prune(self):
        if self.connection is None:
            return
        self.evicted += self.connection.execute("DELETE FROM entries WHERE created < ?", (time.time() - self.max_age,)).rowcount
        total = self.connection.execute("SELECT COALESCE(SUM(bytes),0) FROM entries").fetchone()[0]
        if total > self.max_bytes:
            for key, size in self.connection.execute("SELECT key,bytes FROM entries ORDER BY used").fetchall():
                self.connection.execute("DELETE FROM entries WHERE key=?", (key,))
                self.evicted += 1
                total -= size
                if total <= self.max_bytes:
                    break

    def clear(self):
        if self.connection is None:
            return False
        try:
            self.connection.execute("DELETE FROM entries")
            self.connection.execute("VACUUM")
            self.events.append("Repository cache cleared; reports and backups untouched")
            return True
        except sqlite3.Error as exc:
            self._disable(exc)
            return False

    def info(self):
        info = {"mode": self.mode, "path": str(self.path) if self.path else None,
                "recorded_at": datetime.now(timezone.utc).isoformat(), "schema": SCHEMA,
                "inventory": dict(self.inventory),
                "hits": self.hits, "misses": self.misses, "writes": self.writes,
                "evicted": self.evicted, "max_bytes": self.max_bytes,
                "max_age_days": self.max_age / 86400, "events": list(self.events)}
        if self.connection is not None:
            try:
                count, size = self.connection.execute("SELECT COUNT(*), COALESCE(SUM(bytes),0) FROM entries").fetchone()
                info.update(entries=count, payload_bytes=size, database_bytes=self.path.stat().st_size)
                oldest, latest = self.connection.execute("SELECT MIN(created), MAX(used) FROM entries").fetchone()
                info["oldest_entry_at"] = datetime.fromtimestamp(oldest, timezone.utc).isoformat() if oldest else None
                info["last_access_at"] = datetime.fromtimestamp(latest, timezone.utc).isoformat() if latest else None
            except (OSError, sqlite3.Error) as exc:
                self._disable(exc)
                info["mode"] = "disabled"
        return info

    def close(self):
        if self.connection is not None:
            try:
                self.connection.close()
            except sqlite3.Error:
                pass
            self.connection = None


class CachedGitRunner(GitRunner):
    """Memoize successful read-only history queries under a frozen ref snapshot."""
    def __init__(self, runner, cache, ref_state):
        super().__init__(runner.repo)
        self.raw = runner
        self.cache = cache
        self.snapshot = digest(ref_state)
        self.expected_state = ref_state
        self.revisions = dict(line.split(" ", 1) for line in ref_state[2].splitlines())
        self.revisions["HEAD"] = ref_state[0]

    def run_with_status(self, *args):
        if self.cache.connection is None:
            return self.raw.run_with_status(*args)
        cacheable = args and args[0] in ("log", "ls-tree", "rev-list")
        if not cacheable:
            return self.raw.run_with_status(*args)
        frozen = []
        for arg in args:
            if arg == "--all":
                # Keep command lines bounded on hosts with many Gerrit refs.
                roots = sorted(set(self.revisions.values()))
                frozen.extend(roots if len(roots) <= 200 else ["--all"])
            else:
                frozen.append(self.revisions.get(arg, arg))
        key = [self.snapshot, args]
        result = self.cache.get("git-read", key)
        if result is not None:
            return 0, result, ""
        if self.raw.ref_state() != self.expected_state:
            raise GitCommandError(list(args), 1, "Refs changed before cached query; audit again")
        status, output, error = self.raw.run_with_status(*frozen)
        if status == 0:
            if self.raw.ref_state() != self.expected_state:
                raise GitCommandError(list(args), 1, "Refs changed during cached query; result not cached")
            self.cache.put("git-read", key, output)
        return status, output, error

    def run(self, *args, check=True):
        status, output, error = self.run_with_status(*args)
        if check and status:
            raise GitCommandError(list(args), status, error)
        return output


def main(argv=None):
    from argparse import Namespace
    from .models import AuditorOptions
    from .repository import RepositoryInspector
    parser = argparse.ArgumentParser(description="Manage only this repository's disposable audit cache.")
    parser.add_argument("action", choices=("status", "clear", "prune"))
    parser.add_argument("--repo", required=True)
    parser.add_argument("--cache-dir", type=Path)
    parser.add_argument("--cache-max-mb", type=float, default=256)
    parser.add_argument("--cache-max-age-days", type=int, default=30)
    args = parser.parse_args(argv)
    try:
        options = AuditorOptions.from_args(Namespace(**vars(args), cache=True))
        runner = GitRunner(options.repo)
        runner.repo = RepositoryInspector(runner).validate()
        cache = AuditCache(runner, options)
        try:
            success = cache.clear() if args.action == "clear" else cache.connection is not None
            if args.action == "prune" and success:
                cache.prune()
                cache.connection.execute("VACUUM")
            print(json.dumps(cache.info(), indent=2))
            return 0 if success else 1
        finally:
            cache.close()
    except (ValueError, OSError, RuntimeError, sqlite3.Error) as exc:
        parser.error(str(exc))
