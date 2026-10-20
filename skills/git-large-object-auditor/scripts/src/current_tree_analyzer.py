"""Collect blobs present in protected current ref tip trees."""

from __future__ import annotations

from collections import defaultdict

from .git_runner import GitRunner
from .models import RefInfo, ScanCoverage


class CurrentTreeAnalyzer:
    def __init__(self, runner: GitRunner) -> None:
        self.runner = runner

    def collect(
        self,
        refs: list[RefInfo],
        *,
        protect_head: bool,
        protect_local_branches: bool,
        protect_remote_branches: bool,
        protect_tags: bool,
    ) -> tuple[dict[str, set[str]], dict[str, set[str]], ScanCoverage]:
        usage: dict[str, set[str]] = defaultdict(set)
        paths: dict[str, set[str]] = defaultdict(set)
        coverage = ScanCoverage(
            head=protect_head,
            local_branches=protect_local_branches,
            remote_branches=protect_remote_branches,
            tags=protect_tags,
        )

        protected_refs: list[tuple[str, str]] = []
        if protect_head:
            protected_refs.append(("HEAD", "HEAD"))
        for ref in refs:
            if ref.kind == "local_branch" and protect_local_branches:
                protected_refs.append((ref.name, ref.name))
            elif ref.kind == "remote_branch" and protect_remote_branches:
                if not ref.name.endswith("/HEAD"):
                    protected_refs.append((ref.name, ref.name))
            elif ref.kind == "tag" and protect_tags:
                protected_refs.append((ref.name, ref.name))

        for label, revision in protected_refs:
            for sha, path in self._tree_blobs(revision):
                usage[sha].add(label)
                if path:
                    paths[sha].add(path)
        return dict(usage), dict(paths), coverage

    def _tree_blobs(self, revision: str) -> list[tuple[str, str]]:
        output = self.runner.run("ls-tree", "-r", "-z", revision)
        blobs: list[tuple[str, str]] = []
        for record in output.split("\x00"):
            if not record:
                continue
            header, separator, path = record.partition("\t")
            if not separator:
                continue
            fields = header.split()
            if len(fields) == 3 and fields[1] == "blob":
                blobs.append((fields[2], path))
        return blobs

