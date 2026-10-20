"""Path matching and candidate policy decisions."""

from __future__ import annotations

import fnmatch

from .models import AuditorOptions, BlobInfo


CODE_FILE_SUFFIXES = frozenset(
    {
        ".asm",
        ".bat",
        ".bash",
        ".c",
        ".cc",
        ".clj",
        ".cmd",
        ".cpp",
        ".cs",
        ".css",
        ".dart",
        ".dpr",
        ".ex",
        ".exs",
        ".fs",
        ".fsx",
        ".go",
        ".groovy",
        ".h",
        ".hpp",
        ".htm",
        ".html",
        ".java",
        ".jl",
        ".js",
        ".jsx",
        ".kt",
        ".kts",
        ".less",
        ".lua",
        ".m",
        ".mm",
        ".pas",
        ".php",
        ".pl",
        ".pm",
        ".ps1",
        ".psm1",
        ".py",
        ".pyw",
        ".r",
        ".rb",
        ".rs",
        ".sass",
        ".scala",
        ".scss",
        ".sh",
        ".sql",
        ".svelte",
        ".sv",
        ".svh",
        ".swift",
        ".tcl",
        ".ts",
        ".tsx",
        ".v",
        ".vb",
        ".vbs",
        ".vue",
        ".zsh",
    }
)
CODE_FILE_EXTENSIONS = frozenset(suffix[1:] for suffix in CODE_FILE_SUFFIXES)
TEXT_DATA_FILE_SUFFIXES = frozenset(
    {
        ".adoc",
        ".cfg",
        ".conf",
        ".csv",
        ".data",
        ".dat",
        ".env",
        ".ini",
        ".json",
        ".json5",
        ".jsonl",
        ".log",
        ".markdown",
        ".md",
        ".mdx",
        ".ndjson",
        ".properties",
        ".psv",
        ".rst",
        ".tab",
        ".tex",
        ".tsv",
        ".txt",
        ".toml",
        ".xml",
        ".yaml",
        ".yml",
        ".xsd",
        ".xsl",
    }
)
TEXT_DATA_FILE_EXTENSIONS = frozenset(suffix[1:] for suffix in TEXT_DATA_FILE_SUFFIXES)


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
        return self.protection_reason(blob) is not None

    def protection_reason(self, blob: BlobInfo) -> str | None:
        if blob.sha in self.options.keep_blobs:
            return "matched --keep-blob"
        if any(
            self.matches(path, pattern)
            for path in blob.paths
            for pattern in self.options.keep_paths
        ):
            return "matched --keep-path"
        if self.options.protect_code_files and any(self.is_code_file(path) for path in blob.paths):
            return "matched --protect-code-files by file suffix"
        if self.options.protect_text_data_files and any(
            self.is_text_data_file(path) for path in blob.paths
        ):
            return "matched --protect-text-data-files by file suffix"
        return None

    @staticmethod
    def is_code_file(path: str) -> bool:
        normalized = path.replace("\\", "/")
        filename = normalized.rsplit("/", 1)[-1]
        return "." in filename and filename.rsplit(".", 1)[-1].lower() in CODE_FILE_EXTENSIONS

    @staticmethod
    def is_text_data_file(path: str) -> bool:
        normalized = path.replace("\\", "/")
        filename = normalized.rsplit("/", 1)[-1]
        return "." in filename and filename.rsplit(".", 1)[-1].lower() in TEXT_DATA_FILE_EXTENSIONS

    @staticmethod
    def matches(path: str, pattern: str) -> bool:
        path = path.replace("\\", "/").lstrip("./")
        pattern = pattern.replace("\\", "/").lstrip("./")
        if pattern.endswith("/**"):
            prefix = pattern[:-3].rstrip("/")
            return path == prefix or path.startswith(prefix + "/")
        return fnmatch.fnmatchcase(path, pattern)
