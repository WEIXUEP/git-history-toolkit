"""Efficiently enumerate reachable blobs and filter by size."""

from __future__ import annotations

from collections import defaultdict

from .git_runner import GitRunner
from .models import BlobInfo


class BlobScanner:
    def __init__(self, runner: GitRunner, cache=None) -> None:
        self.runner = runner
        self.cache = cache

    def scan(self, min_size_bytes: int) -> list[BlobInfo]:
        state = self.runner.ref_state()
        tips = dict(line.split(" ", 1) for line in state[2].splitlines())
        tips["HEAD"] = state[0]
        previous = self.cache.get("scanner", "latest") if self.cache else None
        incremental = False
        if previous and previous.get("tips") != tips:
            # Only unchanged refs plus fast-forward additions preserve all cached reachability.
            incremental = all(name in tips and (oid == tips[name] or
                (not name.startswith("refs/tags/") and self.runner.run_with_status(
                    "merge-base", "--is-ancestor", oid, tips[name])[0] == 0))
                for name, oid in previous["tips"].items())
            if not incremental:
                previous = None
                if self.cache:
                    self.cache.events.append("Refs removed/rewritten; full object and path rebuild")
        if incremental and len(set(previous["tips"].values())) > 200:
            incremental = False
            previous = None
            if self.cache:
                self.cache.events.append("Large baseline ref set; full inventory rebuild keeps command lines bounded")
        if previous and previous["tips"] == tips:
            if self.cache and self.cache.connection is not None:
                self.cache.mode = "reused"
                self.cache.inventory = {"object_ids_checked": 0, "total_object_ids": len(previous["objects"])}
            return [BlobInfo(sha, row[1], set(row[2])) for sha, row in previous["objects"].items()
                    if row[0] == "blob" and row[1] >= min_size_bytes]

        revisions = ["--all", "HEAD"]
        if incremental:
            revisions += ["--not", *sorted(set(previous["tips"].values()))]
        if self.cache and self.cache.connection is not None:
            self.cache.mode = "incremental" if incremental else "full"
        object_paths: dict[str, set[str]] = defaultdict(set)
        for line in self.runner.run("rev-list", "--objects", *revisions).splitlines():
            sha, _, path = line.partition(" ")
            if sha:
                if path:
                    object_paths[sha].add(path)
                else:
                    object_paths.setdefault(sha, set())

        object_ids = list(object_paths)
        metadata = self._batch_check(object_ids)
        if incremental:
            for sha, (kind, size, paths) in previous["objects"].items():
                metadata[sha] = kind, size
                object_paths[sha].update(paths)
        blobs = [
            BlobInfo(sha=sha, size_bytes=size, paths=object_paths.get(sha, set()))
            for sha, (object_type, size) in metadata.items()
            if object_type == "blob"
        ]
        self._collect_historical_paths({blob.sha: blob for blob in blobs}, revisions)
        for blob in blobs:
            object_paths[blob.sha] = blob.paths
        if self.runner.ref_state() != state:
            from .repository import RepositoryError
            raise RepositoryError("Refs changed during object scanning; cache snapshot not saved")
        if self.cache:
            self.cache.inventory = {"object_ids_checked": len(object_ids), "total_object_ids": len(metadata)}
            self.cache.put("scanner", "latest", {"tips": tips, "objects": {
                sha: [kind, size, sorted(object_paths[sha])] for sha, (kind, size) in metadata.items()}})
        return [blob for blob in blobs if blob.size_bytes >= min_size_bytes]

    def _collect_historical_paths(self, blobs: dict[str, BlobInfo], revisions=None) -> None:
        if not blobs:
            return
        # rev-list assigns only one representative path per object. Raw NUL
        # records preserve aliases, deletions and paths containing whitespace.
        records = iter(self.runner.run(
            "log", "--raw", "-z", "--format=", "--no-abbrev", "--no-renames", "-m", *(revisions or ["--all", "HEAD"]), "--",
        ).split("\x00"))
        for record in records:
            header = record.lstrip("\n")
            if not header.startswith(":"):
                continue
            fields = header.split()
            path = next(records, None)
            if len(fields) != 5 or path is None:
                continue
            for sha in fields[2:4]:
                if sha in blobs:
                    blobs[sha].paths.add(path)

    def _batch_check(self, object_ids: list[str]) -> dict[str, tuple[str, int]]:
        # A single batch-check invocation avoids one subprocess per object.
        import subprocess

        process = subprocess.Popen(
            [
                "git",
                "cat-file",
                "--batch-check=%(objectname) %(objecttype) %(objectsize)",
            ],
            cwd=self.runner.repo,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            encoding="utf-8",
            errors="surrogateescape",
        )
        assert process.stdin is not None
        assert process.stdout is not None
        assert process.stderr is not None
        stdout, stderr = process.communicate("".join(f"{sha}\n" for sha in object_ids))
        if process.returncode != 0:
            from .git_runner import GitCommandError

            raise GitCommandError(
                ["cat-file", "--batch-check"], process.returncode, stderr
            )
        result: dict[str, tuple[str, int]] = {}
        for line in stdout.splitlines():
            fields = line.split()
            if len(fields) != 3 or fields[1] == "missing":
                continue
            try:
                result[fields[0]] = (fields[1], int(fields[2]))
            except ValueError:
                continue
        missing = set(object_ids) - result.keys()
        if missing:
            from .git_runner import GitCommandError
            raise GitCommandError(["cat-file", "--batch-check"], 1,
                                  f"Object metadata missing; incomplete inventory was not cached: {next(iter(missing))}")
        return result

