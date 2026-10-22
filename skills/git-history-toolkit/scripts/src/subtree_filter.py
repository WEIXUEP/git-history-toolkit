"""Isolated git-filter-repo worker; invoked only on a disposable clone."""

import json
from pathlib import Path
import sys


def neutralize_relocation(message):
    """Retain misleading registration values without native subtree keys."""
    keys = (b"dir", b"split", b"mainline")
    result = []
    for line in message.splitlines(keepends=True):
        for key in keys:
            prefix = b"git-subtree-" + key + b": "
            if line.startswith(prefix):
                line = b"git-history-toolkit-original-subtree-" + key + b": " + line[len(prefix):]
                break
        result.append(line)
    return b"".join(result)


def rewrite_message(message, moves, translate):
    lines = message.splitlines(keepends=True)
    selected = False
    result = []
    for line in lines:
        body = line.rstrip(b"\r\n")
        ending = line[len(body):]
        if body.startswith(b"git-subtree-dir: "):
            old = body[len(b"git-subtree-dir: "):].rstrip(b"/")
            selected = old in moves
            if selected:
                body = b"git-subtree-dir: " + moves[old]
        elif selected and body.startswith(b"git-subtree-mainline: "):
            old = body[len(b"git-subtree-mainline: "):]
            new = translate(old)
            if not new:
                raise ValueError("Subtree mainline refers to a removed commit")
            body = b"git-subtree-mainline: " + new
        # split identifies the external upstream revision, not a main-repo SHA.
        result.append(body + ending)
    return b"".join(result)


def mapped_path(path, moves):
    for old, new in moves.items():
        if path.startswith(old + b"/"):
            return new + path[len(old):]
    return path


def run(plan_path):
    import git_filter_repo as filtering
    plan = json.loads(Path(plan_path).read_text(encoding="utf-8"))
    moves = {old.encode(): new.encode() for old, new in plan["moves"].items()}
    protected = {oid.encode() for oid in plan["protected_commits"]}
    metadata = {oid.encode() for oid in plan["metadata_commits"]}
    repairs = {item["commit"].encode(): item for item in plan.get("metadata_repairs", [])}

    def callback(commit, info):
        if commit.original_id not in protected:
            for change in commit.file_changes:
                if change.filename is not None:
                    change.filename = mapped_path(change.filename, moves)
            # A historical rename between aliases becomes D+M at one new path.
            commit.file_changes.sort(key=lambda change: change.type != b"D")
        if commit.original_id in repairs:
            repair = repairs[commit.original_id]
            if commit.message != bytes.fromhex(repair["original_message_hex"]):
                raise ValueError("Relocation message differs from the approved plan")
            commit.message = neutralize_relocation(commit.message)
            if commit.message != bytes.fromhex(repair["repaired_message_hex"]):
                raise ValueError("Relocation repair differs from the approved plan")
        elif commit.original_id in metadata:
            commit.message = rewrite_message(commit.message, moves, info["commit_rename_func"])

    args = filtering.FilteringOptions.parse_args([
        "--force", "--partial", "--prune-empty", "never", "--prune-degenerate", "never",
        "--preserve-commit-hashes", "--preserve-commit-encoding", "--refs", *plan["refs"],
    ])
    filtering.RepoFilter(args, commit_callback=callback).run()


if __name__ == "__main__":
    run(sys.argv[1])
