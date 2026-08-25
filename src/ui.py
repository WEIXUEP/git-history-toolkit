"""Lightweight Tkinter interface for the Git large-object auditor."""

from __future__ import annotations

import queue
import threading
import tkinter as tk
from argparse import Namespace
from tkinter import filedialog, messagebox, ttk
from typing import Callable

from .auditor import GitLargeObjectAuditor
from .git_runner import GitCommandError
from .repository import RepositoryError
from .rewriter import CleanupError, GitHistoryRewriter


class AuditorWindow:
    """Desktop UI that keeps Git work off the Tkinter event loop."""

    def __init__(self, root: tk.Tk) -> None:
        self.root = root
        self.root.title("Git Large Object Auditor")
        self.root.geometry("900x720")
        self.root.minsize(760, 600)

        self._messages: queue.Queue[str] = queue.Queue()
        self._busy = False
        self._build_variables()
        self._build_layout()
        self.root.after(100, self._drain_messages)

    def _build_variables(self) -> None:
        self.repo_var = tk.StringVar()
        self.output_var = tk.StringVar(value="./git_audit")
        self.min_size_var = tk.StringVar(value="100")
        self.min_age_var = tk.StringVar(value="180")
        self.remote_var = tk.StringVar(value="origin")
        self.branch_var = tk.StringVar()
        self.protect_head_var = tk.BooleanVar(value=True)
        self.protect_local_var = tk.BooleanVar(value=True)
        self.protect_remote_var = tk.BooleanVar(value=True)
        self.protect_tags_var = tk.BooleanVar(value=True)
        self.scan_gerrit_var = tk.BooleanVar(value=True)
        self.verbose_var = tk.BooleanVar(value=True)

    def _build_layout(self) -> None:
        root_frame = ttk.Frame(self.root, padding=12)
        root_frame.pack(fill=tk.BOTH, expand=True)
        root_frame.columnconfigure(1, weight=1)
        root_frame.rowconfigure(5, weight=1)

        ttk.Label(root_frame, text="Repository").grid(row=0, column=0, sticky="w", pady=4)
        ttk.Entry(root_frame, textvariable=self.repo_var).grid(
            row=0, column=1, sticky="ew", padx=8, pady=4
        )
        ttk.Button(root_frame, text="Browse...", command=self._browse_repository).grid(
            row=0, column=2, pady=4
        )

        ttk.Label(root_frame, text="Output directory").grid(row=1, column=0, sticky="w", pady=4)
        ttk.Entry(root_frame, textvariable=self.output_var).grid(
            row=1, column=1, sticky="ew", padx=8, pady=4
        )
        ttk.Button(root_frame, text="Browse...", command=self._browse_output).grid(
            row=1, column=2, pady=4
        )

        thresholds = ttk.LabelFrame(root_frame, text="Thresholds", padding=8)
        thresholds.grid(row=2, column=0, columnspan=3, sticky="ew", pady=(10, 4))
        ttk.Label(thresholds, text="Minimum size (MiB)").grid(row=0, column=0, sticky="w")
        ttk.Entry(thresholds, width=12, textvariable=self.min_size_var).grid(
            row=0, column=1, padx=(8, 24)
        )
        ttk.Label(thresholds, text="Minimum age (days)").grid(row=0, column=2, sticky="w")
        ttk.Entry(thresholds, width=12, textvariable=self.min_age_var).grid(
            row=0, column=3, padx=8
        )

        protection = ttk.LabelFrame(root_frame, text="Protection and analysis", padding=8)
        protection.grid(row=3, column=0, columnspan=3, sticky="ew", pady=4)
        checks = [
            ("Protect HEAD", self.protect_head_var),
            ("Protect local branches", self.protect_local_var),
            ("Protect remote-tracking branches", self.protect_remote_var),
            ("Protect tags", self.protect_tags_var),
            ("Scan Gerrit refs", self.scan_gerrit_var),
            ("Verbose logging", self.verbose_var),
        ]
        for index, (label, variable) in enumerate(checks):
            ttk.Checkbutton(protection, text=label, variable=variable).grid(
                row=index // 3, column=index % 3, sticky="w", padx=(0, 20), pady=2
            )

        paths = ttk.LabelFrame(root_frame, text="Path and object rules (one entry per line)", padding=8)
        paths.grid(row=4, column=0, columnspan=3, sticky="ew", pady=4)
        paths.columnconfigure(0, weight=1)
        paths.columnconfigure(1, weight=1)
        paths.columnconfigure(2, weight=1)
        paths.columnconfigure(3, weight=1)
        self.include_text = self._path_text(paths, "Include paths", 0)
        self.exclude_text = self._path_text(paths, "Exclude paths", 1)
        self.keep_text = self._path_text(paths, "Keep paths", 2)
        self.keep_blob_text = self._path_text(paths, "Keep blob IDs", 3)

        push_frame = ttk.LabelFrame(root_frame, text="Push target", padding=8)
        push_frame.grid(row=5, column=0, columnspan=3, sticky="new", pady=4)
        ttk.Label(push_frame, text="Remote").grid(row=0, column=0, sticky="w")
        ttk.Entry(push_frame, width=16, textvariable=self.remote_var).grid(
            row=0, column=1, padx=(8, 24)
        )
        ttk.Label(push_frame, text="Branch (optional)").grid(row=0, column=2, sticky="w")
        ttk.Entry(push_frame, width=20, textvariable=self.branch_var).grid(
            row=0, column=3, padx=8
        )

        actions = ttk.Frame(root_frame)
        actions.grid(row=6, column=0, columnspan=3, sticky="ew", pady=(10, 4))
        self.audit_button = ttk.Button(actions, text="Audit only", command=lambda: self._start("audit"))
        self.audit_button.pack(side=tk.LEFT, padx=(0, 8))
        self.cleanup_button = ttk.Button(
            actions, text="Cleanup locally", command=lambda: self._start("cleanup")
        )
        self.cleanup_button.pack(side=tk.LEFT, padx=8)
        self.push_button = ttk.Button(
            actions, text="Cleanup and push", command=lambda: self._start("push")
        )
        self.push_button.pack(side=tk.LEFT, padx=8)

        log_frame = ttk.LabelFrame(root_frame, text="Activity log", padding=6)
        log_frame.grid(row=7, column=0, columnspan=3, sticky="nsew", pady=(4, 0))
        root_frame.rowconfigure(7, weight=1)
        log_frame.rowconfigure(0, weight=1)
        log_frame.columnconfigure(0, weight=1)
        self.log_text = tk.Text(log_frame, height=12, state=tk.DISABLED, wrap=tk.WORD)
        self.log_text.grid(row=0, column=0, sticky="nsew")
        scrollbar = ttk.Scrollbar(log_frame, orient=tk.VERTICAL, command=self.log_text.yview)
        scrollbar.grid(row=0, column=1, sticky="ns")
        self.log_text.configure(yscrollcommand=scrollbar.set)

    @staticmethod
    def _path_text(parent: ttk.LabelFrame, label: str, column: int) -> tk.Text:
        ttk.Label(parent, text=label).grid(row=0, column=column, sticky="w", padx=4)
        widget = tk.Text(parent, height=3, width=28, wrap=tk.WORD)
        widget.grid(row=1, column=column, sticky="ew", padx=4, pady=(4, 0))
        return widget

    def _browse_repository(self) -> None:
        selected = filedialog.askdirectory(title="Select Git repository")
        if selected:
            self.repo_var.set(selected)

    def _browse_output(self) -> None:
        selected = filedialog.askdirectory(title="Select output directory")
        if selected:
            self.output_var.set(selected)

    def _start(self, action: str) -> None:
        if self._busy:
            return
        try:
            args = self._make_args(action)
        except (TypeError, ValueError) as exc:
            messagebox.showerror("Invalid options", str(exc), parent=self.root)
            return

        self._busy = True
        self._set_buttons(False)
        self._append_log(f"Starting: {action}")
        thread = threading.Thread(target=self._run_task, args=(action, args), daemon=True)
        thread.start()

    def _make_args(self, action: str) -> Namespace:
        repo = self.repo_var.get().strip()
        if not repo:
            raise ValueError("Select a Git repository first.")
        min_size = float(self.min_size_var.get().strip())
        min_age = int(self.min_age_var.get().strip())
        if min_size <= 0:
            raise ValueError("Minimum size must be greater than zero.")
        if min_age < 0:
            raise ValueError("Minimum age cannot be negative.")

        return Namespace(
            repo=repo,
            min_size_mb=min_size,
            min_age_days=min_age,
            protect_head=self.protect_head_var.get(),
            protect_local_branches=self.protect_local_var.get(),
            protect_remote_branches=self.protect_remote_var.get(),
            protect_tags=self.protect_tags_var.get(),
            scan_gerrit_refs=self.scan_gerrit_var.get(),
            include_paths=self._read_paths(self.include_text),
            exclude_paths=self._read_paths(self.exclude_text),
            keep_paths=self._read_paths(self.keep_text),
            keep_blobs=self._read_paths(self.keep_blob_text),
            output_dir=self.output_var.get().strip() or "./git_audit",
            verbose=self.verbose_var.get(),
            execute_cleanup=action != "audit",
            push_after_cleanup=action == "push",
            push_remote=self.remote_var.get().strip() or "origin",
            push_branch=self.branch_var.get().strip() or None,
        )

    @staticmethod
    def _read_paths(widget: tk.Text) -> list[str]:
        return [line.strip() for line in widget.get("1.0", tk.END).splitlines() if line.strip()]

    def _run_task(self, action: str, args: Namespace) -> None:
        try:
            auditor = GitLargeObjectAuditor(args)
            result = auditor.run()
            self._emit(f"Audit completed: {result.output_dir}")
            self._emit(f"Large blobs: {len(result.blobs)}")
            self._emit(f"Cleanup eligible: {result.cleanup_count}")
            if action != "audit":
                rewriter = GitHistoryRewriter(
                    auditor.runner,
                    auditor.options,
                    confirm=self._confirm,
                    emit=self._emit,
                )
                rewriter.execute(result)
            self._post(lambda: messagebox.showinfo("Completed", "Operation completed.", parent=self.root))
        except (ValueError, RepositoryError, GitCommandError, CleanupError) as exc:
            self._emit(f"ERROR: {exc}")
            self._post(
                lambda error=str(exc): messagebox.showerror(
                    "Operation failed", error, parent=self.root
                )
            )
        except Exception as exc:  # Keep unexpected worker errors visible in the UI.
            self._emit(f"ERROR: {exc}")
            self._post(
                lambda error=str(exc): messagebox.showerror(
                    "Unexpected error", error, parent=self.root
                )
            )
        finally:
            self._post(lambda: self._set_buttons(True))
            self._busy = False

    def _confirm(self, prompt: str) -> str:
        event = threading.Event()
        answer: list[bool] = [False]

        def ask() -> None:
            is_push = "PUSH" in prompt
            title = "Confirm remote push" if is_push else "Confirm history cleanup"
            message = (
                "This will force-with-lease push rewritten history to the remote. Continue?"
                if is_push
                else "This will rewrite local Git history. A backup will be created first. Continue?"
            )
            answer[0] = messagebox.askyesno(title, message, parent=self.root)
            event.set()

        self._post(ask)
        event.wait()
        return "PUSH" if "PUSH" in prompt and answer[0] else "CLEANUP" if answer[0] else ""

    def _emit(self, message: str) -> None:
        self._messages.put(message)

    def _append_log(self, message: str) -> None:
        self.log_text.configure(state=tk.NORMAL)
        self.log_text.insert(tk.END, message + "\n")
        self.log_text.see(tk.END)
        self.log_text.configure(state=tk.DISABLED)

    def _drain_messages(self) -> None:
        while True:
            try:
                message = self._messages.get_nowait()
            except queue.Empty:
                break
            self._append_log(message)
        self.root.after(100, self._drain_messages)

    def _post(self, callback: Callable[[], None]) -> None:
        self.root.after(0, callback)

    def _set_buttons(self, enabled: bool) -> None:
        state = tk.NORMAL if enabled else tk.DISABLED
        for button in (self.audit_button, self.cleanup_button, self.push_button):
            button.configure(state=state)


def launch() -> None:
    """Start the desktop application."""
    root = tk.Tk()
    AuditorWindow(root)
    root.mainloop()
