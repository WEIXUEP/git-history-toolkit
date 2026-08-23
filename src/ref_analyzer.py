"""Enumeration of local Git refs."""

from __future__ import annotations

from .git_runner import GitRunner
from .models import RefInfo


class RefAnalyzer:
    def __init__(self, runner: GitRunner) -> None:
        self.runner = runner

    def list_refs(self) -> list[RefInfo]:
        output = self.runner.run(
            "for-each-ref",
            "--format=%(refname)\t%(objectname)\t%(objecttype)",
            "refs/heads",
            "refs/remotes",
            "refs/tags",
            "refs/changes",
        )
        refs: list[RefInfo] = []
        for record in output.splitlines():
            fields = record.split("\t")
            if len(fields) != 3 or not fields[0]:
                continue
            name, object_id, _object_type = fields
            refs.append(RefInfo(name=name, object_id=object_id, kind=self.kind_for(name)))
        return refs

    @staticmethod
    def kind_for(name: str) -> str:
        if name.startswith("refs/heads/"):
            return "local_branch"
        if name.startswith("refs/remotes/"):
            return "remote_branch"
        if name.startswith("refs/tags/"):
            return "tag"
        if name.startswith("refs/changes/"):
            return "gerrit"
        return "other"
