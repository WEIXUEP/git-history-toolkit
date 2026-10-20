"""Plan and verify squash-subtree history migrations in independent clones."""

import argparse
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import re
import subprocess
import sys

from .subtree_filter import mapped_path, rewrite_message


class MigrationError(RuntimeError):
    pass


def git(repo, *args, data=None, check=True, env=None):
    result = subprocess.run(["git", "-C", str(repo), *map(str, args)], input=data,
                            stdout=subprocess.PIPE, stderr=subprocess.PIPE, env=env)
    if check and result.returncode:
        raise MigrationError(f"git {args[0]} failed: {result.stderr.decode('utf-8', 'replace').strip()}")
    return result.stdout


def text(repo, *args, env=None):
    return git(repo, *args, env=env).decode("utf-8", "surrogateescape").strip()


def write_json(path, data):
    Path(path).write_text(json.dumps(data, indent=2, ensure_ascii=True) + "\n", encoding="utf-8")


def refs(repo):
    return dict(line.split(" ", 1) for line in text(repo, "for-each-ref", "--format=%(refname) %(objectname)").splitlines())


def commit_data(repo, oid):
    headers, message = git(repo, "cat-file", "commit", oid).split(b"\n\n", 1)
    fields = {}
    for line in headers.splitlines():
        if not line.startswith(b" "):
            key, _, value = line.partition(b" ")
            fields.setdefault(key, []).append(value)
    return fields, message


def tree(repo, oid):
    result = {}
    for record in git(repo, "ls-tree", "-r", "-z", "--full-tree", oid).split(b"\0"):
        if record:
            header, path = record.split(b"\t", 1)
            result[path] = header
    return result


def path_value(value):
    value = value.replace("\\", "/").rstrip("/")
    if not value or value.startswith("/") or ":" in value or any(p in ("", ".", "..", ".git") for p in value.split("/")):
        raise MigrationError(f"Invalid repository-relative directory: {value!r}")
    return value


def pairs(values, paths=False):
    result = {}
    for value in values:
        old, separator, new = value.partition("=")
        if not separator or not old or not new:
            raise MigrationError("Use OLD=NEW for moves and OLD=UPSTREAM for upstream sources")
        old = path_value(old)
        new = path_value(new) if paths else new
        if old in result:
            raise MigrationError(f"Duplicate source: {old}")
        result[old] = new
    return result


def transformed(entries, moves):
    result = {}
    for path, header in entries.items():
        if path in moves:
            raise MigrationError("A source directory was a file in historical commits")
        new = mapped_path(path, moves)
        if new in result:
            raise MigrationError(f"Historical path collision: {new.decode('utf-8', 'replace')}")
        result[new] = header
    for path in result:
        pieces = path.split(b"/")
        if any(b"/".join(pieces[:i]) in result for i in range(1, len(pieces))):
            raise MigrationError(f"Historical file/directory collision: {path!r}")
    return result


def digest(plan):
    return hashlib.sha256(json.dumps(plan, sort_keys=True).encode()).hexdigest()


def plan_migration(repo, moves, selected_refs, output, upstreams=None, allow_signatures=False):
    repo = Path(repo).resolve()
    root = Path(text(repo, "rev-parse", "--show-toplevel")).resolve()
    if text(root, "rev-parse", "--is-shallow-repository") == "true":
        raise MigrationError("A full clone is required")
    if git(root, "config", "--get-regexp", r"^(extensions\.partialclone|remote\..*\.promisor)$", check=False).strip():
        raise MigrationError("Partial/promisor clones are unsupported; use a full clone")
    if text(root, "rev-parse", "--show-object-format") != "sha1":
        raise MigrationError("This filter-repo workflow currently requires a SHA-1 repository")
    output = Path(output).resolve()
    if output.is_relative_to(root):
        raise MigrationError("Report directory must be outside the source repository")
    if output.exists():
        raise MigrationError("Use a new report directory; existing plans/backups are never overwritten")
    if not moves:
        raise MigrationError("At least one directory mapping is required")
    for old, new in moves.items():
        if old == new or old.startswith(new + "/") or new.startswith(old + "/"):
            raise MigrationError("Source and destination must be distinct, non-nested directories")
    old_paths = list(moves)
    for i, old in enumerate(old_paths):
        if any(other.startswith(old + "/") or old.startswith(other + "/") for other in old_paths[i + 1:]):
            raise MigrationError("Overlapping historical source aliases are ambiguous")
    if any(target in moves for target in moves.values()):
        raise MigrationError("Chained directory mappings must be expressed as historical aliases to a final destination")
    snapshot = refs(root)
    selected_refs = list(dict.fromkeys(selected_refs))
    if not selected_refs:
        raise MigrationError("Select explicit full branch/tag refs with --ref")
    for ref in selected_refs:
        if not ref.startswith(("refs/heads/", "refs/tags/")) or ref not in snapshot:
            raise MigrationError(f"Select an existing full branch/tag ref: {ref}")
        text(root, "rev-parse", ref + "^{commit}")
    commits = text(root, "rev-list", "--reverse", "--topo-order", *selected_refs).splitlines()
    metadata, squash_roots, raw_upstream = [], [], set()
    signed = []
    for oid in commits:
        fields, message = commit_data(root, oid)
        if b"gpgsig" in fields or b"gpgsig-sha256" in fields:
            signed.append(oid)
        dirs = re.findall(rb"^git-subtree-dir: (.+?)\r?$", message, re.M)
        splits = re.findall(rb"^git-subtree-split: ([0-9a-f]{40})\r?$", message, re.M)
        mainlines = re.findall(rb"^git-subtree-mainline: ([0-9a-f]{40})\r?$", message, re.M)
        if len(dirs) > 1 or len(mainlines) > 1 or (dirs and len(splits) != 1):
            raise MigrationError(f"Ambiguous or incomplete subtree trailers in {oid}")
        if dirs:
            item = {"commit": oid, "dir": dirs[0].decode().rstrip("/"),
                    "split": splits[0].decode(), "mainline": mainlines[0].decode() if mainlines else None}
            metadata.append(item)
            if not mainlines:
                squash_roots.append(oid)
            elif subprocess.run(["git", "-C", str(root), "merge-base", "--is-ancestor", item["mainline"], oid],
                                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL).returncode:
                raise MigrationError(f"Subtree mainline must be an ancestor: {oid}")
            resolved = git(root, "rev-parse", "--verify", item["split"] + "^{commit}", check=False).strip()
            if resolved == item["split"].encode():
                raw_upstream.update(text(root, "rev-list", item["split"]).splitlines())
    squash_roots = [oid for oid in squash_roots if oid not in raw_upstream]
    protected = set(raw_upstream)
    if squash_roots:
        protected.update(text(root, "rev-list", *squash_roots).splitlines())
    relevant = [item for item in metadata if item["dir"] in moves and item["commit"] not in raw_upstream]
    if not relevant or any(old not in {item["dir"] for item in relevant} for old in moves):
        # An alias introduced with git mv may have no subtree trailer; allow it
        # if another alias of the same destination has a verified squash trailer.
        covered = {moves[item["dir"]] for item in relevant}
        if not relevant or any(target not in covered for target in moves.values()):
            raise MigrationError("Each target needs an existing squash-subtree trailer; non-squash imports are unsupported")
    if not any(item["commit"] in squash_roots for item in relevant):
        raise MigrationError("No squash-subtree roots found for the requested migration")
    for ref in selected_refs:
        if ref.startswith("refs/tags/"):
            raw_tag = git(root, "cat-file", "-p", ref)
            if b"-----BEGIN PGP SIGNATURE-----" in raw_tag or b"-----BEGIN SSH SIGNATURE-----" in raw_tag:
                signed.append(ref)
    if signed and not allow_signatures:
        raise MigrationError("Selected history contains signatures; explicitly acknowledge removal with --allow-signature-removal")
    byte_moves = {old.encode(): new.encode() for old, new in moves.items()}
    affected, seen = [], set()
    for oid in commits:
        if oid in protected:
            continue
        entries = tree(root, oid)
        expected = transformed(entries, byte_moves)
        if expected != entries:
            affected.append(oid)
            for old in moves:
                if any(path.startswith(old.encode() + b"/") for path in entries):
                    seen.add(old)
    if seen != set(moves):
        raise MigrationError(f"No main-repository directory history found for: {sorted(set(moves) - seen)}")
    if refs(root) != snapshot:
        raise MigrationError("Source refs changed during planning; retry without concurrent Git operations")
    plan = {"schema_version": 1, "source": str(root), "moves": moves, "refs": selected_refs,
            "source_refs": snapshot, "commits": commits, "protected_commits": sorted(protected),
            "metadata": relevant, "metadata_commits": [item["commit"] for item in relevant],
            "upstreams": upstreams or {}, "affected_commits": affected, "signed_objects": signed,
            "created_at": datetime.now(timezone.utc).isoformat(), "status": "planned"}
    output.mkdir(parents=True)
    write_json(output / "plan.json", plan)
    write_json(output / "plan.sha256.json", {"sha256": digest(plan)})
    print(f"Plan: {output / 'plan.json'}\nSelected commits: {len(commits)}; path changes: {len(affected)}")
    return output / "plan.json"


def configure_identity(repo):
    text(repo, "config", "user.name", "Subtree Verification")
    text(repo, "config", "user.email", "subtree-verification@example.invalid")
    text(repo, "config", "commit.gpgsign", "false")
    text(repo, "config", "core.hooksPath", ".git/disabled-hooks")


def verify_incremental(workspace, plan, report):
    probes = []
    by_commit = {item["commit"]: item for item in plan["metadata"]}
    for ref in plan["refs"]:
        old_tip = plan["source_refs"][ref]
        history = text(plan["source"], "log", "--format=%H", ref).splitlines()
        for target in sorted(set(plan["moves"].values())):
            if not any(path.startswith(target.encode() + b"/") for path in tree(workspace, ref)):
                probes.append({"ref": ref, "target": target, "status": "not-present-at-tip"})
                continue
            latest = next((by_commit[oid] for oid in history if oid in by_commit
                           and plan["moves"][by_commit[oid]["dir"]] == target), None)
            if latest is None:
                raise MigrationError(f"No reachable squash metadata for {ref}:{target}")
            directory = report / f"probe-{len(probes):02d}"
            upstream, consumer = directory / "upstream", directory / "consumer"
            directory.mkdir()
            upstream.mkdir()
            text(upstream, "init", "-b", "probe")
            text(upstream, "fetch", "--no-tags", str(workspace), latest["split"])
            text(upstream, "checkout", "--detach", latest["split"])
            configure_identity(upstream)
            # Probe commits have their own refs/index/worktree. Read-only object
            # alternates avoid copying an entire large host history per probe.
            subprocess.run(["git", "clone", "--shared", "--no-checkout", str(workspace), str(consumer)],
                           check=True, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE)
            text(consumer, "fetch", "--no-tags", str(workspace), latest["split"])
            text(consumer, "checkout", "--detach", text(workspace, "rev-parse", ref + "^{commit}"))
            configure_identity(consumer)
            expected = tree(consumer, "HEAD")
            marker = ".git-history-toolkit-probe-" + hashlib.sha256((ref + target + old_tip).encode()).hexdigest()[:16] + ".txt"
            if (upstream / marker).exists():
                raise MigrationError("Verification marker collision")
            latest_time = max(int(stamp) for stamp in text(workspace, "log", "--all", "--format=%ct").splitlines())
            for iteration in (1, 2):
                probe_env = os.environ.copy()
                probe_env.update(GIT_AUTHOR_DATE=f"{latest_time + iteration * 60} +0000",
                                 GIT_COMMITTER_DATE=f"{latest_time + iteration * 60} +0000")
                (upstream / marker).write_text(f"Synthetic upstream delta {iteration}\n", encoding="utf-8")
                text(upstream, "add", "--", marker)
                text(upstream, "commit", "-m", f"Synthetic upstream verification {iteration}", env=probe_env)
                tip = text(upstream, "rev-parse", "HEAD")
                text(consumer, "subtree", "pull", "--prefix=" + target, str(upstream), tip, "--squash", env=probe_env)
                marker_path = (target + "/" + marker).encode()
                expected[marker_path] = b"100644 blob " + text(upstream, "rev-parse", "HEAD:" + marker).encode()
                if tree(consumer, "HEAD") != expected:
                    raise MigrationError("Incremental subtree pull modified unrelated or existing local files")
                before = text(consumer, "rev-parse", "HEAD")
                text(consumer, "subtree", "pull", "--prefix=" + target, str(upstream), tip, "--squash", env=probe_env)
                if text(consumer, "rev-parse", "HEAD") != before:
                    raise MigrationError("Repeated upstream pull created an unexpected duplicate commit")
            probes.append({"ref": ref, "target": target, "status": "passed", "incremental_pulls": 2,
                           "idempotency_checks": 2, "upstream_base": latest["split"],
                           "kind": "synthetic-delta-from-real-split"})
    if not any(probe["status"] == "passed" for probe in probes):
        raise MigrationError("No tip had a subtree available for incremental verification")
    return probes


def migrate(plan_path, workspace, confirm=input):
    plan_path = Path(plan_path).resolve()
    report = plan_path.parent
    plan = json.loads(plan_path.read_text(encoding="utf-8"))
    if digest(plan) != json.loads((report / "plan.sha256.json").read_text())["sha256"]:
        raise MigrationError("Plan content changed; generate a new plan")
    if (report / "before_migration.bundle").exists():
        raise MigrationError("This plan was already executed; use a new plan/report directory")
    source, workspace = Path(plan["source"]), Path(workspace).resolve()
    if workspace.exists() or workspace.is_relative_to(source) or report.is_relative_to(workspace):
        raise MigrationError("Workspace must be a new independent directory outside the source and reports")
    if refs(source) != plan["source_refs"] or text(source, "status", "--porcelain"):
        raise MigrationError("Source refs changed or worktree is dirty; regenerate the plan after resolving changes")
    try:
        import git_filter_repo  # noqa: F401
    except ImportError:
        raise MigrationError("Install git-filter-repo in this Python environment first") from None
    print(json.dumps({"source": str(source), "workspace": str(workspace), "moves": plan["moves"],
                      "rewritten_refs": plan["refs"], "backup": str(report / "before_migration.bundle")}, indent=2))
    try:
        approved = confirm("Type MIGRATE to rewrite history in the independent clone: ").strip() == "MIGRATE"
    except (EOFError, KeyboardInterrupt):
        approved = False
    if not approved:
        print("Migration cancelled; no clone or history rewrite executed")
        return None
    if refs(source) != plan["source_refs"] or text(source, "status", "--porcelain"):
        raise MigrationError("Source changed during confirmation; plan again")
    text(source, "bundle", "create", str(report / "before_migration.bundle"), "--all")
    text(source, "bundle", "verify", str(report / "before_migration.bundle"))
    write_json(report / "before_refs.json", plan["source_refs"])
    subprocess.run(["git", "clone", "--no-local", "--no-checkout", str(source), str(workspace)],
                   check=True, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE)
    for ref in plan["refs"]:
        text(workspace, "update-ref", ref, plan["source_refs"][ref])
    # Fetch original upstream objects into private local refs; they are never published.
    for item in plan["metadata"]:
        split = item["split"]
        if git(workspace, "rev-parse", "--verify", split + "^{commit}", check=False).strip() != split.encode():
            remote = str(source)
            if git(source, "rev-parse", "--verify", split + "^{commit}", check=False).strip() != split.encode():
                remote = plan["upstreams"].get(item["dir"])
                if not remote:
                    raise MigrationError(f"Missing original upstream object {split}; plan again with --upstream {item['dir']}=SOURCE")
            text(workspace, "fetch", "--no-tags", remote, split)
        text(workspace, "update-ref", "refs/toolkit/upstream/" + split, split)
        if not item["mainline"] and text(workspace, "rev-parse", item["commit"] + "^{tree}") != text(workspace, "rev-parse", split + "^{tree}"):
            raise MigrationError("Squash tree does not match the declared original upstream split")
    initial_refs = refs(workspace)
    try:
        subprocess.run([sys.executable, str(Path(__file__).with_name("subtree_filter.py")), str(plan_path)],
                       cwd=workspace, check=True)
        git_dir = Path(text(workspace, "rev-parse", "--absolute-git-dir"))
        mapping_path = git_dir / "filter-repo" / "commit-map"
        mapping = dict(line.split() for line in mapping_path.read_text().splitlines()[1:])
        (report / "commit-map.txt").write_bytes(mapping_path.read_bytes())
        if set(plan["commits"]) - set(mapping) or any(mapping[oid] == "0" * 40 for oid in plan["commits"]):
            raise MigrationError("Commit mapping lost selected commits")
        if len({mapping[oid] for oid in plan["commits"]}) != len(plan["commits"]):
            raise MigrationError("Unexpected duplicate/collapsed commits")
        byte_moves = {old.encode(): new.encode() for old, new in plan["moves"].items()}
        protected, metadata = set(plan["protected_commits"]), set(plan["metadata_commits"])
        for old in plan["commits"]:
            new = mapping[old]
            expected = tree(source, old)
            if old not in protected:
                expected = transformed(expected, byte_moves)
            if tree(workspace, new) != expected:
                raise MigrationError(f"Unexpected content or mode changes at {old}")
            before, message = commit_data(source, old)
            after, new_message = commit_data(workspace, new)
            for field in (b"author", b"committer", b"encoding"):
                if before.get(field) != after.get(field):
                    raise MigrationError(f"Commit identity/date/encoding changed: {old}")
            expected_parents = [mapping.get(parent.decode(), parent.decode()).encode() for parent in before.get(b"parent", [])]
            if after.get(b"parent", []) != expected_parents:
                raise MigrationError(f"Commit topology changed: {old}")
            expected_message = rewrite_message(message, byte_moves, lambda sha: mapping.get(sha.decode(), sha.decode()).encode()) if old in metadata else message
            if new_message != expected_message:
                raise MigrationError(f"Unexpected commit message change: {old}")
        after_refs = refs(workspace)
        for ref, oid in initial_refs.items():
            if ref not in plan["refs"] and after_refs.get(ref) != oid:
                raise MigrationError(f"Unselected ref changed: {ref}")
        log_checks, blame_checks = [], []
        for ref in plan["refs"]:
            if ref.startswith("refs/heads/") and after_refs[ref] != mapping[plan["source_refs"][ref]]:
                raise MigrationError(f"Selected branch does not point to its mapped tip: {ref}")
            if ref.startswith("refs/tags/"):
                original_tip = text(source, "rev-parse", ref + "^{commit}")
                if text(workspace, "rev-parse", ref + "^{commit}") != mapping[original_tip]:
                    raise MigrationError(f"Selected tag does not point to its mapped commit: {ref}")
                if text(source, "cat-file", "-t", ref) == "tag":
                    old_tag = git(source, "cat-file", "tag", ref).split(b"\n", 1)[1]
                    new_tag = git(workspace, "cat-file", "tag", ref).split(b"\n", 1)[1]
                    if ref not in plan["signed_objects"] and old_tag != new_tag:
                        raise MigrationError(f"Annotated tag metadata changed: {ref}")
            for target in sorted(set(plan["moves"].values())):
                count = len(text(workspace, "log", "--format=%H", ref, "--", target).splitlines())
                log_checks.append({"ref": ref, "path": target, "commits": count})
                files = tree(workspace, ref)
                for path, header in files.items():
                    if path.startswith(target.encode() + b"/") and header.startswith(b"100644 blob "):
                        blob = git(workspace, "cat-file", "blob", header.split()[-1].decode())
                        if blob and b"\0" not in blob and len(blob) < 1024 * 1024:
                            git(workspace, "blame", "--line-porcelain", ref, "--", path.decode())
                            blame_checks.append({"ref": ref, "path": path.decode(), "status": "passed"})
                            break
        text(workspace, "fsck", "--full")
        probes = verify_incremental(workspace, plan, report)
        checkout = next((ref for ref in plan["refs"] if ref.startswith("refs/heads/")), plan["refs"][0])
        text(workspace, "checkout", checkout.removeprefix("refs/heads/") if checkout.startswith("refs/heads/") else "--detach", *([] if checkout.startswith("refs/heads/") else [checkout]))
        if refs(source) != plan["source_refs"]:
            raise MigrationError("Source refs changed while migration was running; do not publish")
        result = {"status": "passed", "plan_digest": digest(plan), "workspace": str(workspace), "checked_commits": len(plan["commits"]),
                  "commit_map": str(report / "commit-map.txt"), "moves": plan["moves"],
                  "log_checks": log_checks, "blame_checks": blame_checks, "incremental_checks": probes,
                  "remote_updates": [{"ref": ref, "old": plan["source_refs"][ref], "new": after_refs[ref]} for ref in plan["refs"]],
                  "source_unchanged": True, "push_executed": False}
        write_json(report / "verification.json", result)
        print(f"Migration verified: {report / 'verification.json'}; no remote push executed")
        return result
    except (MigrationError, subprocess.CalledProcessError) as exc:
        write_json(report / "verification.json", {"status": "failed", "error": str(exc), "push_executed": False})
        raise MigrationError(f"Migration verification failed; preserve workspace and backup: {exc}") from exc


def publication_plan(plan_path, remote):
    plan_path = Path(plan_path).resolve()
    plan = json.loads(plan_path.read_text(encoding="utf-8"))
    verification = json.loads((plan_path.parent / "verification.json").read_text(encoding="utf-8"))
    if verification.get("status") != "passed" or verification.get("plan_digest") != digest(plan):
        raise MigrationError("Publication requires a matching successful verification report")
    workspace = Path(verification["workspace"])
    if refs(plan["source"]) != plan["source_refs"]:
        raise MigrationError("Source refs changed after planning; review the migration again")
    if remote in text(plan["source"], "remote").splitlines():
        urls = text(plan["source"], "remote", "get-url", "--push", "--all", remote).splitlines()
        if len(urls) != 1:
            raise MigrationError("Prepare separate publication plans for multiple push URLs")
        remote = urls[0]
    commands = []
    for item in verification["remote_updates"]:
        ref, old, new = item["ref"], item["old"], item["new"]
        if text(workspace, "rev-parse", ref) != new:
            raise MigrationError(f"Verified workspace ref changed: {ref}")
        rows = text(workspace, "ls-remote", remote, ref).splitlines()
        if len(rows) > 1:
            raise MigrationError(f"Ambiguous destination: {ref}")
        current = rows[0].split()[0] if rows else None
        if current not in (None, old, new):
            raise MigrationError(f"Remote {ref} moved since planning; no publication command generated")
        if current == new:
            commands.append({"ref": ref, "action": "already-current", "argv": []})
            continue
        argv = ["git", "-C", str(workspace), "push"]
        if current is not None:
            argv.append(f"--force-with-lease={ref}:{current}")
        argv.extend([remote, f"{ref}:{ref}"])
        commands.append({"ref": ref, "action": "create" if current is None else "replace-history",
                         "expected_remote_oid": current, "new_oid": new, "argv": argv})
    result = {"status": "requires-separate-user-confirmation", "destination": remote, "commands": commands,
              "permissions": "not-proven-by-ls-remote; check platform protection and administrator policy",
              "push_executed": False}
    write_json(plan_path.parent / "publication-plan.json", result)
    print(json.dumps(result, indent=2))
    return result


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    plan = commands.add_parser("plan", help="Inspect selected history and write a read-only migration plan")
    plan.add_argument("--repo", required=True)
    plan.add_argument("--move", action="append", required=True, metavar="OLD=NEW")
    plan.add_argument("--ref", action="append", required=True, dest="selected_refs")
    plan.add_argument("--output-dir", required=True)
    plan.add_argument("--upstream", action="append", default=[], metavar="OLD=SOURCE")
    plan.add_argument("--allow-signature-removal", action="store_true")
    execute = commands.add_parser("migrate", help="Confirm, rewrite an independent clone, and verify it")
    execute.add_argument("--plan", required=True)
    execute.add_argument("--workspace", required=True)
    publish = commands.add_parser("publish-plan", help="Read destination refs and prepare commands; never pushes")
    publish.add_argument("--plan", required=True)
    publish.add_argument("--remote", required=True)
    args = parser.parse_args(argv)
    try:
        if args.command == "plan":
            plan_migration(args.repo, pairs(args.move, True), args.selected_refs, args.output_dir,
                           pairs(args.upstream), args.allow_signature_removal)
        elif args.command == "migrate":
            migrate(args.plan, args.workspace)
        else:
            publication_plan(args.plan, args.remote)
    except (MigrationError, subprocess.CalledProcessError, OSError, ValueError) as exc:
        parser.exit(2, f"Error: {exc}\n")
    return 0
