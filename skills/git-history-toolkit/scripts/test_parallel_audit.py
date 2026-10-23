"""Bounded Git concurrency, deterministic timestamps and cache ownership."""

from argparse import Namespace
from contextlib import closing
from dataclasses import asdict
from pathlib import Path
import tempfile
import threading
import unittest
from unittest.mock import patch

from src.audit_cache import AuditCache
from src.auditor import GitLargeObjectAuditor
from src.git_runner import GitCommandError, GitRunner
from src.history_analyzer import HistoryAnalyzer
from src.html_review import historical_presence, review_payload
from src.models import AuditorOptions, BlobInfo
from src.options import build_parser


class ParallelQueryTests(unittest.TestCase):
    def test_worker_options(self):
        parser = build_parser()
        for value in (0, 1, 4, 16):
            options = AuditorOptions.from_args(parser.parse_args(["--repo", ".", "--workers", str(value)]))
            self.assertEqual(options.workers, value)
        for value in (-1, 17):
            with self.assertRaisesRegex(ValueError, "workers"):
                AuditorOptions.from_args(Namespace(repo=".", workers=value))

    def test_parallel_reads_are_bounded_and_keep_input_order(self):
        barrier = threading.Barrier(3, timeout=5)
        lock = threading.Lock()
        active = peak = 0
        caller = threading.get_ident()
        threads = set()

        class Runner(GitRunner):
            def run_with_status(self, *args):
                nonlocal active, peak
                with lock:
                    active += 1
                    peak = max(peak, active)
                    threads.add(threading.get_ident())
                barrier.wait()
                with lock:
                    active -= 1
                return 0, args[0], ""

        runner = Runner(Path("."))
        results = list(runner.iter_queries(((str(i),) for i in range(9)), workers=3))
        self.assertEqual([row[1] for row in results], list(map(str, range(9))))
        self.assertEqual(peak, 3)
        self.assertNotIn(caller, threads)

    def test_presence_uses_latest_timestamp_despite_out_of_order_completion(self):
        old_finished = threading.Event()
        queried = []
        completed = []
        caller = threading.get_ident()
        progress_threads = []
        sha = "a" * 40

        class Runner(GitRunner):
            def run(self, *args, **kwargs):
                return "".join(f"{100 - i} commit-{i}\n" for i in range(20))

            def run_with_status(self, *args):
                commit = args[-1]
                queried.append(commit)
                if commit == "commit-0":
                    if not old_finished.wait(5):
                        raise RuntimeError("Older query did not run concurrently")
                completed.append(commit)
                if commit == "commit-1":
                    old_finished.set()
                return 0, f"100644 blob {sha}\tfile.bin\0", ""

        result = historical_presence(Runner(Path(".")), [BlobInfo(sha, 100)],
                                     lambda *args: progress_threads.append(threading.get_ident()), workers=2)
        self.assertEqual(completed, ["commit-1", "commit-0"])
        self.assertEqual(result[sha]["commit"], "commit-0")
        self.assertEqual(set(queried), {"commit-0", "commit-1"})
        self.assertEqual(set(progress_threads), {caller})

    def test_worker_exceptions_and_git_failures_stop_analysis(self):
        class Runner(GitRunner):
            def run_with_status(self, *args):
                raise OSError("Cannot start Git")

        with closing(Runner(Path(".")).iter_queries([("log",)], workers=2)) as results:
            with self.assertRaisesRegex(OSError, "Cannot start Git"):
                next(results)
        with self.assertRaises(GitCommandError):
            HistoryAnalyzer._records((128, "", "bad object"))

    def test_serial_queries_stay_on_calling_thread(self):
        threads = []

        class Runner(GitRunner):
            def run_with_status(self, *args):
                threads.append(threading.get_ident())
                return 0, args[0], ""

        list(Runner(Path(".")).iter_queries([("first",), ("second",)], workers=1))
        self.assertEqual(threads, [threading.get_ident()] * 2)


class SharedFixtureParallelTests(unittest.TestCase):
    def test_serial_parallel_and_warm_cache_have_identical_facts(self):
        import os
        import time

        fixture = Path(os.environ.get("GIT_HISTORY_FIXTURE",
                       Path(__file__).resolve().parents[2] / "git-auditor-test-repo"))
        if not (fixture / ".git").exists():
            self.skipTest("Shared git-auditor-test-repo fixture not available")
        with tempfile.TemporaryDirectory(prefix="parallel-audit-") as temporary:
            root = Path(temporary)
            caller = threading.get_ident()
            cache_threads = set()
            original_get, original_put = AuditCache.get, AuditCache.put

            def get(cache, *args):
                cache_threads.add(threading.get_ident())
                return original_get(cache, *args)

            def put(cache, *args):
                cache_threads.add(threading.get_ident())
                return original_put(cache, *args)

            runs = []
            with patch.object(AuditCache, "get", get), patch.object(AuditCache, "put", put):
                for workers, cache in ((1, False), (4, False), (1, True), (4, True), (4, True)):
                    auditor = GitLargeObjectAuditor(Namespace(repo=str(fixture), workers=workers,
                        cache=cache, cache_dir=str(root / f"cache-{workers}"), output_dir=str(root / "reports"),
                        min_size_mb=5, min_age_days=180))
                    start = time.perf_counter()
                    result = auditor.run()
                    payload = review_payload(result, auditor.options, "review")
                    elapsed = time.perf_counter() - start
                    runs.append(([asdict(blob) for blob in result.blobs], payload["rows"], elapsed, result))
            for facts, rows, _, _ in runs[1:]:
                self.assertEqual(facts, runs[0][0])
                self.assertEqual(rows, runs[0][1])
            self.assertEqual(cache_threads, {caller})
            self.assertGreater(runs[-1][3].cache_info["hits"], 0)
            print("Audit + historical presence timings: " + ", ".join(
                f"{name}={run[2]:.2f}s" for name, run in zip(
                    ("serial", "parallel", "serial cold cache", "parallel cold cache", "parallel warm cache"), runs)))


if __name__ == "__main__":
    unittest.main()
