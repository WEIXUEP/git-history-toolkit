"""Analysis of locally available Gerrit refs/changes/* references."""

from __future__ import annotations

import re
from contextlib import closing

from .git_runner import GitCommandError, GitRunner
from .models import BlobInfo, RefInfo, ScanCoverage


CHANGE_REF_PATTERN = re.compile(r"^refs/changes/\d{2}/(?P<change>\d+)/(?P<patchset>\d+)$")


class GerritAnalyzer:
    def __init__(self, runner: GitRunner, progress=None, workers=0) -> None:
        self.runner = runner
        self.progress = progress or (lambda *args, **kwargs: None)
        self.workers = workers

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
        queries = (("rev-list", "--objects", ref.name) for ref in gerrit_refs)
        with closing(self.runner.iter_queries(queries, self.workers)) as results:
            for index, (ref, (status, output, error)) in enumerate(zip(gerrit_refs, results)):
                if status:
                    raise GitCommandError(["rev-list", ref.name], status, error)
                match = CHANGE_REF_PATTERN.match(ref.name)
                change, patchset = match.group("change"), match.group("patchset")
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
