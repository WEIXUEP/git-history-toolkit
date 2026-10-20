"""Loopback-only, single-use browser review for audited Git blob identities."""

from __future__ import annotations

from datetime import datetime, timezone
import hashlib
from http.server import BaseHTTPRequestHandler, HTTPServer
import json
import math
from pathlib import Path, PurePosixPath
import secrets
import threading
import webbrowser

from .git_runner import GitRunner
from .models import CleanupAction


def historical_presence(runner, blobs):
    """Find the latest committer timestamp of a snapshot containing each blob."""
    remaining = {blob.sha for blob in blobs}
    found = {}
    if not remaining:
        return found
    commits = [line.split() for line in runner.run("log", "--all", "HEAD", "--format=%ct %H").splitlines()]
    for stamp, commit in sorted(commits, key=lambda row: (int(row[0]), row[1]), reverse=True):
        if not remaining:
            break
        present = set()
        for record in runner.run("ls-tree", "-r", "-z", "--full-tree", commit).split("\0"):
            header = record.partition("\t")[0].split()
            if len(header) == 3 and header[1] == "blob" and header[2] in remaining:
                present.add(header[2])
        for sha in present:
            found[sha] = {"time": datetime.fromtimestamp(int(stamp), timezone.utc).isoformat(), "commit": commit}
        remaining.difference_update(present)
    return found


def review_payload(result, options, mode):
    runner = GitRunner(result.repository)
    if runner.ref_state() != result.ref_state:
        raise ValueError("Audit refs changed; run a fresh audit before browser review")
    presence = historical_presence(runner, result.blobs)
    rows = []
    for blob in result.blobs:
        suffixes = sorted({PurePosixPath(path).suffix.lower()
                           or (PurePosixPath(path).name.lower() if PurePosixPath(path).name.startswith(".")
                               else "(no suffix)") for path in blob.paths}) or ["(unknown)"]
        rows.append({"sha": blob.sha, "size": blob.size_bytes, "paths": sorted(blob.paths),
                     "suffixes": suffixes, "status": blob.status.value if blob.status else "UNKNOWN",
                     "eligible": blob.action == CleanupAction.CLEAN_HISTORY,
                     "reason": blob.protection_reason or blob.candidate_reason or blob.action.value,
                     "last_present": presence.get(blob.sha),
                     "last_change": blob.effective_last_seen.isoformat() if blob.effective_last_seen else None,
                     "age_days": blob.age_days(result.audit_time),
                     "refs": sorted(blob.current_refs | blob.gerrit_refs)})
    if runner.ref_state() != result.ref_state:
        raise ValueError("Refs changed while computing history timestamps; audit again")
    return {"repository": str(result.repository), "audit_time": result.audit_time.isoformat(),
            "mode": mode, "backup": str(result.output_dir / "before_cleanup.bundle"),
            "threshold_mb": options.min_size_mb, "threshold_days": options.min_age_days,
            "refs": result.ref_state, "rows": rows}


class LocalReviewHTTPServer(HTTPServer):
    def get_request(self):
        connection, address = super().get_request()
        connection.settimeout(5)
        return connection, address


class ReviewSession:
    """Serve one review; HTTP handlers only return IDs, never execute Git."""

    def __init__(self, payload):
        self.payload = payload
        self.token = secrets.token_urlsafe(32)
        self.nonce = secrets.token_urlsafe(24)
        self.done = threading.Event()
        self.lock = threading.Lock()
        self.selected = None
        self.allowed = {row["sha"] for row in payload.get("rows", []) if row["eligible"]}
        self.digest = hashlib.sha256(json.dumps(payload, sort_keys=True).encode()).hexdigest()
        self.server = LocalReviewHTTPServer(("127.0.0.1", 0), self._handler())
        self.origin = f"http://127.0.0.1:{self.server.server_port}"
        self.url = f"{self.origin}/review/{self.token}"

    def _handler(self):
        session = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *args):
                pass

            def reply(self, status, content, kind="application/json; charset=utf-8"):
                self.send_response(status)
                self.send_header("Content-Type", kind)
                self.send_header("Content-Length", str(len(content)))
                self.send_header("Cache-Control", "no-store")
                self.send_header("X-Content-Type-Options", "nosniff")
                self.send_header("Referrer-Policy", "no-referrer")
                self.send_header("Content-Security-Policy", "default-src 'none'; "
                    f"script-src 'nonce-{session.nonce}'; style-src 'nonce-{session.nonce}'; "
                    "connect-src 'self'; frame-ancestors 'none'; base-uri 'none'; form-action 'none'")
                self.end_headers()
                self.wfile.write(content)

            def valid_host(self):
                return self.headers.get("Host") == session.origin.removeprefix("http://")

            def do_GET(self):
                if not self.valid_host() or self.path != f"/review/{session.token}":
                    self.reply(404, b'{}')
                    return
                data = dict(session.payload, token=session.token, digest=session.digest)
                encoded = json.dumps(data, ensure_ascii=True).replace("<", "\\u003c").replace(">", "\\u003e").replace("&", "\\u0026")
                template_name = "thresholds.html" if session.payload["mode"] == "thresholds" else "review.html"
                template = Path(__file__).with_name(template_name).read_text(encoding="utf-8")
                page = template.replace("__NONCE__", session.nonce).replace("__DATA__", encoded)
                self.reply(200, page.encode("utf-8"), "text/html; charset=utf-8")

            def do_POST(self):
                if (not self.valid_host() or self.path != f"/decision/{session.token}"
                        or self.headers.get("Origin") != session.origin
                        or self.headers.get("X-Review-Token") != session.token):
                    self.reply(403, b'{"error":"Invalid review session"}')
                    return
                if session.done.is_set():
                    self.reply(409, b'{"error":"Review already finished"}')
                    return
                try:
                    length = int(self.headers.get("Content-Length", "0"))
                    if not 0 < length <= 1024 * 1024:
                        raise ValueError("Invalid request size")
                    self.connection.settimeout(5)
                    body = json.loads(self.rfile.read(length))
                    if body.get("digest") != session.digest:
                        raise ValueError("Review content changed")
                    if body.get("cancel") is True:
                        chosen = None
                    elif session.payload["mode"] == "thresholds":
                        size, days = body.get("min_size_mb"), body.get("min_age_days")
                        if (body.get("confirmation") != "THRESHOLDS"
                                or type(size) not in (int, float) or not math.isfinite(size * 1048576) or size <= 0
                                or type(days) is not int or not 0 <= days <= 365000):
                            raise ValueError("Confirm a positive finite MiB size and integer days from 0 to 365000")
                        chosen = {"min_size_mb": size, "min_age_days": days}
                    else:
                        ids = body.get("selected")
                        if (not isinstance(ids, list) or not ids or any(not isinstance(sha, str) for sha in ids)
                                or len(ids) != len(set(ids)) or not set(ids) <= session.allowed):
                            raise ValueError("Select eligible audited objects only")
                        expected = "CLEANUP" if session.payload["mode"] == "cleanup" else "REVIEW"
                        if body.get("confirmation") != expected:
                            raise ValueError(f"Type {expected} to confirm")
                        chosen = sorted(ids)
                except (ValueError, TypeError, AttributeError, OverflowError, OSError):
                    self.reply(400, b'{"error":"Invalid selection or confirmation"}')
                    return
                with session.lock:
                    if session.done.is_set():
                        self.reply(409, b'{"error":"Review already finished"}')
                        return
                    session.selected = chosen
                    session.done.set()
                self.reply(200, b'{"ok":true}')

        return Handler

    def start(self):
        self.thread = threading.Thread(target=self.server.serve_forever, kwargs={"poll_interval": 0.1}, daemon=True)
        self.thread.start()

    def close(self):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=5)


def _await_browser(session, options, emit):
    session.start()
    emit(f"Browser review ({session.payload['mode']}): {session.url}")
    emit("Closing the tab is not approval. Use Cancel or wait for timeout.")
    try:
        if not options.no_open_browser:
            try:
                if not webbrowser.open(session.url):
                    emit("No browser opened; open the local URL on this machine, or use terminal mode.")
            except (webbrowser.Error, OSError):
                emit("Browser unavailable; open the local URL on this machine, or use terminal mode.")
        if not session.done.wait(options.review_timeout):
            emit("Browser review timed out; no cleanup approved.")
            with session.lock:
                session.selected = None
                session.done.set()
    except KeyboardInterrupt:
        with session.lock:
            session.selected = None
            session.done.set()
        emit("Browser review cancelled.")
    finally:
        session.close()
    return session.selected


def confirm_thresholds(options, emit=print, prompt=input):
    """Require a manual parameter decision before scanning; never approve cleanup."""
    if options.review_html:
        payload = {"mode": "thresholds", "repository": str(options.repo),
                   "min_size_mb": options.min_size_mb, "min_age_days": options.min_age_days,
                   "as_of": datetime.now(timezone.utc).isoformat()}
        return _await_browser(ReviewSession(payload), options, emit)
    emit("Confirm audit thresholds. Age is since the last history reference change (including deletion), not filesystem mtime.")
    try:
        size = float(prompt(f"Minimum size in MiB [{options.min_size_mb}]: ").strip() or options.min_size_mb)
        days = int(prompt(f"Minimum age in days [{options.min_age_days}]: ").strip() or options.min_age_days)
        if not math.isfinite(size * 1048576) or size <= 0 or not 0 <= days <= 365000:
            raise ValueError("Size must be positive and finite; days must be from 0 to 365000")
        emit(f"Repository: {options.repo}; thresholds: {size} MiB / {days} days")
        if prompt("Type SCAN to confirm these thresholds: ").strip() != "SCAN":
            return None
        return {"min_size_mb": size, "min_age_days": days}
    except (EOFError, KeyboardInterrupt):
        return None


def review_in_browser(result, options, mode="review", emit=print):
    session = ReviewSession(review_payload(result, options, mode))
    _await_browser(session, options, emit)
    receipt = {"mode": mode, "status": "confirmed" if session.selected else "cancelled",
               "repository": str(result.repository), "plan_digest": session.digest,
               "ref_state": result.ref_state, "selected": session.selected or [],
               "confirmed_at": datetime.now(timezone.utc).isoformat(), "push_approved": False}
    (result.output_dir / "browser-review.json").write_text(json.dumps(receipt, indent=2) + "\n", encoding="utf-8")
    return session.selected
