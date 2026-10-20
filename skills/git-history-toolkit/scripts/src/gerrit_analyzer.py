"""Analysis of locally available Gerrit refs/changes/* references."""

from __future__ import annotations

import re

from .git_runner import GitRunner
from .models import BlobInfo, RefInfo, ScanCoverage


CHANGE_REF_PATTERN = re.compile(r"^refs/changes/\d{2}/(?P<change>\d+)/(?P<patchset>\d+)$")


class GerritAnalyzer:
    def __init__(self, runner: GitRunner, progress=None) -> None:
        self.runner = runner
        self.progress = progress or (lambda *args, **kwargs: None)

    def analyze(
        self, refs: list[RefInfo], blobs: dict[str, BlobInfo], coverage: ScanCoverage
    ) -> None:
        gerrit_refs = [ref for ref in refs if ref.kind == "gerrit" and CHANGE_REF_PATTERN.match(ref.name)]
        coverage.gerrit_refs_available = bool(gerrit_refs)
        if not gerrit_refs:
            coverage.gerrit_refs = False
            coverage.warnings.append(
                "No local numeric Gerrit patch-set refs were found; Gerrit reachability is incomplete. "
                "Meta refs alone do not provide patch-set coverage."
            )
            return

        coverage.gerrit_refs = True
        self.progress("Gerrit patch sets", 0, len(gerrit_refs))
        for index, ref in enumerate(gerrit_refs):
            self.progress("Gerrit patch sets", index, len(gerrit_refs), ref.name)
            match = CHANGE_REF_PATTERN.match(ref.name)
            if not match:
                continue
            change, patchset = match.group("change"), match.group("patchset")
            output = self.runner.run("rev-list", "--objects", ref.name)
            for line in output.splitlines():
                sha, _, path = line.partition(" ")
                blob = blobs.get(sha)
                if blob is None:
                    continue
                blob.gerrit_refs.add(ref.name)
                blob.gerrit_change_numbers.add(change)
                blob.gerrit_patchsets.add(patchset)
                if path:
                    blob.paths.add(path)
            self.progress("Gerrit patch sets", index + 1, len(gerrit_refs), ref.name)
