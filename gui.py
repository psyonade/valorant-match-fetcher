"""Tkinter desktop UI for fetching Valorant match history."""

from __future__ import annotations

import os
import queue
import sys
import threading
import tkinter as tk
from pathlib import Path
from tkinter import filedialog, messagebox, ttk

from api import APIError
from service import (
    PAGE_SIZE, FetchCancelled, FetchOptions, FetchProgress, FetchResult,
    run_export_from_cache, run_fetch,
)


REGIONS = {
    "Asia Pacific (ap)": "ap",
    "Brazil (br)": "br",
    "Europe (eu)": "eu",
    "Korea (kr)": "kr",
    "Latin America (latam)": "latam",
    "North America (na)": "na",
}
MODES = (
    "All modes",
    "competitive",
    "unrated",
    "swiftplay",
    "spikerush",
    "deathmatch",
    "escalation",
    "replication",
    "teamdeathmatch",
    "premier",
    "Other queue ID...",
)


def _default_data_dir() -> Path:
    if getattr(sys, "frozen", False):
        local_app_data = Path(os.environ.get("LOCALAPPDATA", Path.home()))
        return local_app_data / "ValorantMatchFetcher" / "data"
    return Path(__file__).resolve().parent / "data"


class FetcherWindow:
    def __init__(self, root: tk.Tk) -> None:
        self.root = root
        self.root.title("Valorant Match Fetcher")
        self.root.geometry("610x700")
        self.root.minsize(540, 650)
        self.data_dir = tk.StringVar(value=str(_default_data_dir()))
        self.name = tk.StringVar()
        self.tag = tk.StringVar()
        self.region = tk.StringVar(value="Asia Pacific (ap)")
        self.act = tk.StringVar(value="current")
        self.mode = tk.StringVar(value="All modes")
        self.limit = tk.IntVar(value=100)
        self.mode_custom = tk.StringVar()
        self.api_key = tk.StringVar()
        self.export_csv = tk.BooleanVar(value=False)
        self.export_jsonl = tk.BooleanVar(value=False)
        self.export_digest = tk.BooleanVar(value=False)
        self.export_manifest = tk.BooleanVar(value=False)
        self.export_rounds = tk.BooleanVar(value=False)
        self.last_exports: list[str] = []
        self.cancel_event: threading.Event | None = None
        self.events: queue.Queue[tuple[str, object]] = queue.Queue()
        self._build_ui()
        self.root.after(100, self._poll_events)

    def _build_ui(self) -> None:
        outer = ttk.Frame(self.root, padding=22)
        outer.pack(fill="both", expand=True)
        ttk.Label(outer, text="Valorant Match Fetcher", font=("Segoe UI", 19, "bold")).pack(anchor="w")
        ttk.Label(
            outer,
            text="Fetch raw match JSON. Optional exports are off by default.",
            wraplength=550,
        ).pack(anchor="w", pady=(4, 18))

        player = ttk.LabelFrame(outer, text="Player", padding=12)
        player.pack(fill="x", pady=(0, 12))
        player.columnconfigure(1, weight=1)
        player.columnconfigure(3, weight=1)
        ttk.Label(player, text="Riot ID name").grid(row=0, column=0, sticky="w", padx=(0, 8), pady=6)
        ttk.Entry(player, textvariable=self.name).grid(row=0, column=1, sticky="ew", pady=6)
        ttk.Label(player, text="Tag").grid(row=0, column=2, sticky="w", padx=(14, 8), pady=6)
        ttk.Entry(player, textvariable=self.tag, width=12).grid(row=0, column=3, sticky="ew", pady=6)

        filters = ttk.LabelFrame(outer, text="Match filters", padding=12)
        filters.pack(fill="x", pady=(0, 12))
        filters.columnconfigure(1, weight=1)
        filters.columnconfigure(3, weight=1)
        ttk.Label(filters, text="Region").grid(row=0, column=0, sticky="w", padx=(0, 8), pady=6)
        ttk.Combobox(
            filters, textvariable=self.region, values=tuple(REGIONS), state="readonly", width=22
        ).grid(row=0, column=1, sticky="ew", pady=6)
        ttk.Label(filters, text="Act").grid(row=0, column=2, sticky="w", padx=(14, 8), pady=6)
        ttk.Entry(filters, textvariable=self.act, width=18).grid(row=0, column=3, sticky="ew", pady=6)
        ttk.Label(filters, text="Queue / mode").grid(row=1, column=0, sticky="w", padx=(0, 8), pady=6)
        mode_box = ttk.Combobox(filters, textvariable=self.mode, values=MODES, state="readonly")
        mode_box.grid(row=1, column=1, sticky="ew", pady=6)
        mode_box.bind("<<ComboboxSelected>>", self._mode_changed)
        ttk.Label(filters, text="Limit").grid(row=1, column=2, sticky="w", padx=(14, 8), pady=6)
        ttk.Spinbox(filters, from_=1, to=500, textvariable=self.limit, width=8).grid(
            row=1, column=3, sticky="w", pady=6
        )
        self.mode_entry = ttk.Entry(filters, textvariable=self.mode_custom)
        self.mode_entry.grid(row=2, column=1, columnspan=3, sticky="ew", pady=(2, 6))
        self.mode_entry.grid_remove()

        key_frame = ttk.LabelFrame(outer, text="HenrikDev API key", padding=12)
        key_frame.pack(fill="x", pady=(0, 12))
        ttk.Entry(key_frame, textvariable=self.api_key, show="*").pack(fill="x")
        ttk.Label(
            key_frame,
            text="Required to fetch. It is used only for this run and is not saved.",
            wraplength=540,
        ).pack(anchor="w", pady=(6, 0))

        exports = ttk.LabelFrame(outer, text="Optional exports", padding=12)
        exports.pack(fill="x", pady=(0, 12))
        ttk.Checkbutton(exports, text="matches.csv", variable=self.export_csv).grid(row=0, column=0, sticky="w", padx=(0, 20), pady=4)
        ttk.Checkbutton(exports, text="matches.jsonl", variable=self.export_jsonl).grid(row=0, column=1, sticky="w", pady=4)
        ttk.Checkbutton(exports, text="LLM digest Markdown", variable=self.export_digest).grid(row=1, column=0, sticky="w", padx=(0, 20), pady=4)
        ttk.Checkbutton(exports, text="kills.csv (one row per kill event)", variable=self.export_rounds).grid(row=1, column=1, sticky="w", pady=4)
        ttk.Checkbutton(exports, text="Fetch manifest (JSON)", variable=self.export_manifest).grid(row=2, column=0, sticky="w", pady=4)

        output = ttk.LabelFrame(outer, text="Output folder", padding=12)
        output.pack(fill="x", pady=(0, 14))
        output.columnconfigure(0, weight=1)
        ttk.Entry(output, textvariable=self.data_dir, state="readonly").grid(row=0, column=0, sticky="ew", padx=(0, 8))
        ttk.Button(output, text="Browse…", command=self._browse).grid(row=0, column=1)

        actions = ttk.Frame(outer)
        actions.pack(fill="x")
        self.fetch_button = ttk.Button(actions, text="Fetch matches", command=self._start_fetch)
        self.fetch_button.pack(side="left")
        self.regenerate_button = ttk.Button(
            actions, text="Regenerate exports from cache", command=self._start_regenerate
        )
        self.regenerate_button.pack(side="left", padx=(8, 0))
        self.cancel_button = ttk.Button(actions, text="Cancel fetch", command=self._cancel_fetch, state="disabled")
        self.cancel_button.pack(side="left", padx=(8, 0))
        ttk.Button(actions, text="Open output folder", command=self._open_output).pack(side="right")
        self.progress = ttk.Progressbar(outer, mode="indeterminate")
        self.progress.pack(fill="x", pady=(14, 6))
        self.status = tk.StringVar(value="Ready. Raw JSON will be cached; optional exports are off.")
        ttk.Label(outer, textvariable=self.status, wraplength=550).pack(anchor="w")

    def _mode_changed(self, _event: object = None) -> None:
        if self.mode.get() == "Other queue ID...":
            self.mode_entry.grid()
            self.mode_entry.focus_set()
        else:
            self.mode_entry.grid_remove()

    def _browse(self) -> None:
        selected = filedialog.askdirectory(initialdir=self.data_dir.get(), title="Choose output folder")
        if selected:
            self.data_dir.set(selected)

    def _open_output(self) -> None:
        path = Path(self.data_dir.get())
        path.mkdir(parents=True, exist_ok=True)
        os.startfile(path)  # Windows desktop app.

    def _collect_options(self, require_api_key: bool) -> tuple[FetchOptions, str] | None:
        name, tag, api_key = self.name.get().strip(), self.tag.get().strip(), self.api_key.get()
        if not name or not tag:
            messagebox.showerror("Missing Riot ID", "Enter both the Riot ID name and tag.", parent=self.root)
            return None
        if require_api_key and not api_key:
            messagebox.showerror("Missing API key", "Enter your HenrikDev API key.", parent=self.root)
            return None
        try:
            limit = int(self.limit.get())
        except (ValueError, tk.TclError):
            messagebox.showerror("Invalid limit", "Match limit must be a whole number from 1 to 500.", parent=self.root)
            return None
        if not 1 <= limit <= 500:
            messagebox.showerror("Invalid limit", "Match limit must be between 1 and 500.", parent=self.root)
            return None

        selected_mode = self.mode.get()
        if selected_mode == "Other queue ID...":
            selected_mode = self.mode_custom.get().strip()
            if not selected_mode:
                messagebox.showerror("Missing queue ID", "Enter the exact queue ID.", parent=self.root)
                return None
        mode = None if selected_mode == "All modes" else selected_mode
        act = self.act.get().strip() or "current"
        options = FetchOptions(
            name=name,
            tag=tag,
            region=REGIONS.get(self.region.get(), "ap"),
            act=act,
            mode=mode,
            limit=limit,
            rounds=self.export_rounds.get(),
            export_csv=self.export_csv.get(),
            export_jsonl=self.export_jsonl.get(),
            export_digest=self.export_digest.get(),
            export_manifest=self.export_manifest.get(),
            data_dir=Path(self.data_dir.get()).expanduser(),
        )
        return options, api_key

    def _start_fetch(self) -> None:
        collected = self._collect_options(require_api_key=True)
        if collected is None:
            return
        options, api_key = collected
        self._start_worker(options, api_key, from_cache=False)

    def _start_regenerate(self) -> None:
        collected = self._collect_options(require_api_key=False)
        if collected is None:
            return
        options, _ = collected
        if not any((options.export_csv, options.export_jsonl, options.export_digest, options.rounds, options.export_manifest)):
            messagebox.showerror(
                "Choose an export", "Select at least one optional export before regenerating from cache.", parent=self.root
            )
            return
        self._start_worker(options, "", from_cache=True)

    def _start_worker(self, options: FetchOptions, api_key: str, from_cache: bool) -> None:
        output_dir = options.data_dir
        self.last_exports = [] if from_cache else [f"raw JSON cache: {output_dir / 'raw'}"]
        self.last_exports.extend(
            str(output_dir / filename) for enabled, filename in (
                (options.export_csv, "matches.csv"),
                (options.export_jsonl, "matches.jsonl"),
                (options.export_digest, "llm_digest.md (or split parts)"),
                (options.rounds, "kills.csv"),
                (options.export_manifest, "fetch_manifest.json"),
            ) if enabled
        )
        if not from_cache:
            self.api_key.set("")
        self.cancel_event = threading.Event()
        self.fetch_button.configure(state="disabled")
        self.regenerate_button.configure(state="disabled")
        self.cancel_button.configure(state="normal" if not from_cache else "disabled")
        if from_cache:
            self.progress.configure(mode="indeterminate")
            self.progress.start(12)
        else:
            estimated_pages = max(
                1, (options.limit + PAGE_SIZE - 1) // PAGE_SIZE
                + int(options.act.casefold() == "current" and options.mode is not None),
            )
            self.progress.stop()
            self.progress.configure(mode="determinate", maximum=estimated_pages, value=0)
        self.status.set(
            "Regenerating selected exports from the raw cache…" if from_cache
            else "Fetching matches and updating the raw JSON cache…"
        )
        worker = threading.Thread(
            target=self._fetch_worker, args=(options, api_key, from_cache, self.cancel_event), daemon=True
        )
        worker.start()

    def _cancel_fetch(self) -> None:
        if self.cancel_event is not None and not self.cancel_event.is_set():
            self.cancel_event.set()
            self.cancel_button.configure(state="disabled")
            self.status.set("Cancellation requested; the current API request may finish first…")

    def _fetch_worker(
        self, options: FetchOptions, api_key: str, from_cache: bool = False,
        cancellation_event: threading.Event | None = None,
    ) -> None:
        try:
            result = run_export_from_cache(options) if from_cache else run_fetch(
                options,
                api_key=api_key,
                progress_callback=lambda progress: self.events.put(("progress", progress)),
                cancellation_event=cancellation_event,
            )
        except FetchCancelled as exc:
            self.events.put(("cancelled", str(exc)))
        except Exception as exc:
            self.events.put(("error", exc))
        else:
            self.events.put(("success", result))
        finally:
            api_key = ""

    def _poll_events(self) -> None:
        try:
            kind, value = self.events.get_nowait()
        except queue.Empty:
            self.root.after(100, self._poll_events)
            return
        if kind == "progress" and isinstance(value, FetchProgress):
            maximum = max(int(float(self.progress["maximum"])), value.pages_requested)
            self.progress.configure(maximum=maximum, value=value.pages_requested)
            self.status.set(
                f"Fetching… pages: {value.pages_requested}; matches seen: {value.matches_seen}; "
                f"cache hits: {value.cache_hits}; new raw files: {value.new_cache_files}."
            )
            self.root.after(100, self._poll_events)
            return
        self.progress.stop()
        self.fetch_button.configure(state="normal")
        self.regenerate_button.configure(state="normal")
        self.cancel_button.configure(state="disabled")
        if kind == "error":
            self.status.set("Fetch failed.")
            messagebox.showerror("Fetch failed", str(value), parent=self.root)
        elif isinstance(value, FetchResult):
            if value.digest_paths:
                self.last_exports = [
                    path for path in self.last_exports if "llm_digest.md" not in path
                ] + [str(path) for path in value.digest_paths]
            if value.manifest_path:
                self.last_exports = [
                    path for path in self.last_exports if "fetch_manifest.json" not in path
                ] + [str(value.manifest_path)]
            prefix = "Regenerated exports from cache for" if not any(
                output.startswith("raw JSON cache:") for output in self.last_exports
            ) else "Saved"
            self.status.set(
                f"{prefix} {value.match_count} matches for act {value.act}. "
                f"Cache hits: {value.cache_hits}; new raw files: {value.new_cache_files}; "
                f"API pages: {value.pages_requested}. Output: {'; '.join(self.last_exports)}"
            )
            if value.validation_warnings:
                details = "\n".join(value.validation_warnings[:5])
                if len(value.validation_warnings) > 5:
                    details += f"\n... and {len(value.validation_warnings) - 5} more"
                messagebox.showwarning(
                    "Data validation warnings",
                    f"{len(value.validation_warnings)} round-data warning(s):\n\n{details}",
                    parent=self.root,
                )
        elif kind == "cancelled":
            self.status.set(f"{value} Raw cache: {Path(self.data_dir.get()) / 'raw'}")
        self.root.after(100, self._poll_events)


def main() -> None:
    root = tk.Tk()
    FetcherWindow(root)
    root.mainloop()


if __name__ == "__main__":
    main()
