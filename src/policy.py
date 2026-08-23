"""Path matching and candidate policy decisions."""

from __future__ import annotations

import fnmatch

from .models import AuditorOptions, BlobInfo


class PathPolicy:
    def __init__(self, options: AuditorOptions) -> None:
        self.options = options

    def is_in_scope(self, blob: BlobInfo) -> bool:
        paths = blob.paths or {""}
        if self.options.include_paths and not any(
            self.matches(path, pattern) for path in paths for pattern in self.options.include_paths
        ):
            return False
        if self.options.exclude_paths and paths and all(
            any(self.matches(path, pattern) for pattern in self.options.exclude_paths)
            for path in paths
        ):
            return False
        return True

    def is_explicitly_protected(self, blob: BlobInfo) -> bool:
        if blob.sha in self.options.keep_blobs:
            return True
        return any(
            self.matches(path, pattern)
            for path in blob.paths
            for pattern in self.options.keep_paths
        )

    @staticmethod
    def matches(path: str, pattern: str) -> bool:
        path = path.replace("\\", "/").lstrip("./")
        pattern = pattern.replace("\\", "/").lstrip("./")
        if pattern.endswith("/**"):
            prefix = pattern[:-3].rstrip("/")
            return path == prefix or path.startswith(prefix + "/")
        return fnmatch.fnmatchcase(path, pattern)

