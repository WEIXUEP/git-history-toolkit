"""Efficiently enumerate reachable blobs and filter by size."""

from __future__ import annotations

from collections import defaultdict

from .git_runner import GitRunner
from .models import BlobInfo


class BlobScanner:
    def __init__(self, runner: GitRunner) -> None:
        self.runner = runner

    def scan(self, min_size_bytes: int) -> list[BlobInfo]:
        object_paths: dict[str, set[str]] = defaultdict(set)
        for line in self.runner.run("rev-list", "--objects", "--all").splitlines():
            sha, _, path = line.partition(" ")
            if sha:
                if path:
                    object_paths[sha].add(path)
                else:
                    object_paths.setdefault(sha, set())

        if not object_paths:
            return []

        object_ids = list(object_paths)
        metadata = self._batch_check(object_ids)
        return [
            BlobInfo(sha=sha, size_bytes=size, paths=object_paths.get(sha, set()))
            for sha, (object_type, size) in metadata.items()
            if object_type == "blob" and size >= min_size_bytes
        ]

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
        return result

