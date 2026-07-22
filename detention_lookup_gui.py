"""Minimal desktop interface for detention identifier lookups."""

from __future__ import annotations

import threading
import tkinter as tk
import sys
import uuid
from pathlib import Path
from tkinter import messagebox, ttk
from tkinter.scrolledtext import ScrolledText

import duckdb

from detention_lookup import LookupError, fetch_timeline, format_full_timeline
from nyc_filter import NYCFilterError, FilterResult, retain_nyc_aor_rows


def application_directory() -> Path:
    """Locate external data beside the script, Windows exe, or macOS app."""
    if not getattr(sys, "frozen", False):
        return Path(__file__).resolve().parent

    executable = Path(sys.executable).resolve()
    if sys.platform == "darwin" and executable.parents[2].suffix == ".app":
        return executable.parents[3]
    return executable.parent


PROJECT_DIR = application_directory()
ARRESTS_FILE = PROJECT_DIR / "arrests-latest.parquet"
DETENTION_FILE = PROJECT_DIR / "detention-stints-latest.parquet"


class LookupWindow:
    def __init__(self, root: tk.Tk) -> None:
        self.root = root
        self.root.title("NYC Detention Lookup")
        self.root.minsize(720, 390)

        container = ttk.Frame(root, padding=16)
        container.grid(row=0, column=0, sticky="nsew")
        root.rowconfigure(0, weight=1)
        root.columnconfigure(0, weight=1)
        container.columnconfigure(0, weight=1)
        container.rowconfigure(5, weight=1)

        toolbar = ttk.Frame(container)
        toolbar.grid(row=0, column=0, sticky="ew", pady=(0, 12))
        toolbar.columnconfigure(0, weight=1)

        ttk.Label(toolbar, text="NYC Detention Lookup", font=("TkDefaultFont", 15)).grid(
            row=0, column=0, sticky="w"
        )
        self.filter_button = ttk.Button(
            toolbar,
            text="Keep NYC AOR Rows Only…",
            command=self.confirm_nyc_filter,
        )
        self.filter_button.grid(row=0, column=1, padx=(8, 0))
        ttk.Button(toolbar, text="? Help", command=self.show_help).grid(
            row=0, column=2, padx=(8, 0)
        )

        ttk.Label(container, text="Unique identifier").grid(
            row=1, column=0, sticky="w"
        )

        input_row = ttk.Frame(container)
        input_row.grid(row=2, column=0, sticky="ew", pady=(5, 4))
        input_row.columnconfigure(0, weight=1)

        self.identifier_entry = ttk.Entry(input_row)
        self.identifier_entry.grid(row=0, column=0, sticky="ew", padx=(0, 8))
        self.identifier_entry.bind("<Return>", self.start_search)

        self.search_button = ttk.Button(
            input_row, text="Search", command=self.start_search
        )
        self.search_button.grid(row=0, column=1)

        ttk.Label(
            container,
            text="Paste an identifier, then select Search or press Return.",
            foreground="#555555",
        ).grid(row=3, column=0, sticky="w", pady=(0, 12))

        ttk.Label(container, text="Detention timeline").grid(
            row=4, column=0, sticky="w"
        )

        self.result_box = ScrolledText(
            container,
            height=8,
            wrap=tk.WORD,
            font=("TkDefaultFont", 12),
            padx=8,
            pady=8,
        )
        self.result_box.grid(row=5, column=0, sticky="nsew", pady=(5, 12))
        self.result_box.configure(state=tk.DISABLED)

        footer = ttk.Frame(container)
        footer.grid(row=6, column=0, sticky="ew")
        footer.columnconfigure(0, weight=1)

        self.status = ttk.Label(footer, text="Paste an identifier and select Search.")
        self.status.grid(row=0, column=0, sticky="w")

        self.copy_button = ttk.Button(
            footer,
            text="Copy to Clipboard",
            command=self.copy_result,
            state=tk.DISABLED,
        )
        self.copy_button.grid(row=0, column=1, sticky="e")

        self.identifier_entry.focus_set()

    def set_result(self, value: str) -> None:
        self.result_box.configure(state=tk.NORMAL)
        self.result_box.delete("1.0", tk.END)
        self.result_box.insert("1.0", value)
        self.result_box.configure(state=tk.DISABLED)

    def start_search(self, _event: tk.Event | None = None) -> None:
        identifier = self.identifier_entry.get().strip()
        if not identifier:
            self.set_result("")
            self.status.configure(text="Enter an identifier first.")
            self.copy_button.configure(state=tk.DISABLED)
            return

        self.search_button.configure(state=tk.DISABLED)
        self.filter_button.configure(state=tk.DISABLED)
        self.copy_button.configure(state=tk.DISABLED)
        self.status.configure(text="Searching…")
        self.set_result("")
        threading.Thread(
            target=self.run_search,
            args=(identifier,),
            daemon=True,
        ).start()

    def run_search(self, identifier: str) -> None:
        try:
            _base_identifier, arrest, rows = fetch_timeline(
                identifier,
                arrests_file=ARRESTS_FILE,
                detention_file=DETENTION_FILE,
            )
            result = format_full_timeline(arrest, rows)
        except (LookupError, duckdb.Error) as exc:
            self.root.after(0, self.show_error, str(exc))
            return
        self.root.after(0, self.show_result, result, len(rows))

    def show_result(self, result: str, row_count: int) -> None:
        self.set_result(result)
        self.status.configure(text=f"Found {row_count} detention row(s).")
        self.search_button.configure(state=tk.NORMAL)
        self.filter_button.configure(state=tk.NORMAL)
        self.copy_button.configure(state=tk.NORMAL)

    def show_error(self, message: str) -> None:
        self.set_result(message)
        self.status.configure(text="Lookup failed.")
        self.search_button.configure(state=tk.NORMAL)
        self.filter_button.configure(state=tk.NORMAL)
        self.copy_button.configure(state=tk.DISABLED)

    def copy_result(self) -> None:
        result = self.result_box.get("1.0", "end-1c")
        if not result:
            return
        self.root.clipboard_clear()
        self.root.clipboard_append(result)
        self.root.update_idletasks()
        self.status.configure(text="Timeline copied to clipboard.")

    def show_help(self) -> None:
        help_window = tk.Toplevel(self.root)
        help_window.title("How to use NYC Detention Lookup")
        help_window.minsize(650, 480)
        help_window.transient(self.root)

        frame = ttk.Frame(help_window, padding=16)
        frame.pack(fill=tk.BOTH, expand=True)
        text = ScrolledText(frame, wrap=tk.WORD, padx=10, pady=10)
        text.pack(fill=tk.BOTH, expand=True)
        text.insert(
            "1.0",
            """HOW TO SEARCH

1. Paste a unique_identifier, stay_ID, or stint_ID into the input field.
2. Select Search or press Return.
3. The program requires exactly one matching arrest row, then finds every
matching detention row and orders those rows by book-in time.
4. Select Copy to Clipboard to copy the complete timeline string.

The timeline begins with the arrest date and location, then moves from the
oldest detention event to the most recent. Impossible chronology is retained
but marked with a (DISCREPANCY: ...) note. A missing book-out date appears as
UNKNOWN - CURRENTLY HELD (?) because the data cannot confirm release.

Values after the first underscore are ignored. This lets a stay_ID or stint_ID
be reduced to its base unique_identifier.

REQUIRED FILES

Place the downloaded Parquet files in the same directory as this script,
Windows .exe, or macOS .app and use these exact filenames:

  arrests-latest.parquet
  detention-stints-latest.parquet
  joined-arrests-detention-stays-latest.parquet

The first two files are required for lookup. All three are required by the NYC
filtering option.

NYC AOR FILTER

Keep NYC AOR Rows Only permanently overwrites all three Parquet files. It keeps
only rows whose relevant AOR column exactly equals:

  New York City Area of Responsibility

Download fresh source files first if you may need the complete datasets later.

DEPENDENCIES

Use Python 3.14 with the packages in requirements-lock.txt. On macOS with
Homebrew Python, the desktop window also requires:

  brew install python-tk@3.14
""",
        )
        text.configure(state=tk.DISABLED)
        ttk.Button(frame, text="Close", command=help_window.destroy).pack(
            anchor="e", pady=(10, 0)
        )

    def confirm_nyc_filter(self) -> None:
        confirmed = messagebox.askyesno(
            "Keep only NYC AOR rows?",
            "This will permanently overwrite all three Parquet files and remove "
            "every row outside the New York City Area of Responsibility.\n\n"
            "This cannot be undone without downloading the source files again. "
            "Continue?",
            icon=messagebox.WARNING,
            parent=self.root,
        )
        if not confirmed:
            return

        self.search_button.configure(state=tk.DISABLED)
        self.filter_button.configure(state=tk.DISABLED)
        self.copy_button.configure(state=tk.DISABLED)
        self.status.configure(text="Filtering and validating all three files…")
        threading.Thread(target=self.run_nyc_filter, daemon=True).start()

    def run_nyc_filter(self) -> None:
        try:
            results = retain_nyc_aor_rows(PROJECT_DIR)
        except (NYCFilterError, duckdb.Error, OSError) as exc:
            self.root.after(0, self.show_filter_error, str(exc))
            return
        self.root.after(0, self.show_filter_result, results)

    def show_filter_result(self, results: list[FilterResult]) -> None:
        summary = "\n".join(
            f"{result.filename}: {result.retained_rows:,} retained; "
            f"{result.removed_rows:,} removed"
            for result in results
        )
        self.status.configure(text="NYC AOR filtering complete.")
        self.search_button.configure(state=tk.NORMAL)
        self.filter_button.configure(state=tk.NORMAL)
        messagebox.showinfo(
            "NYC AOR filtering complete", summary, parent=self.root
        )

    def show_filter_error(self, message: str) -> None:
        self.status.configure(text="NYC AOR filtering failed; originals unchanged.")
        self.search_button.configure(state=tk.NORMAL)
        self.filter_button.configure(state=tk.NORMAL)
        messagebox.showerror("Filtering failed", message, parent=self.root)


def bundled_self_test() -> None:
    """Verify critical bundled imports without opening a window or reading data."""
    connection = duckdb.connect(database=":memory:")
    try:
        assert connection.execute("SELECT 1").fetchone() == (1,)
        uuid_value, timestamp_value = connection.execute(
            """
            SELECT UUID '12345678-1234-5678-1234-567812345678',
                   TIMESTAMPTZ '2025-01-01 12:00:00+00'
            """
        ).fetchone()
        assert uuid_value == uuid.UUID("12345678-1234-5678-1234-567812345678")
        assert timestamp_value.tzinfo is not None
    finally:
        connection.close()


def main() -> int:
    if "--self-test" in sys.argv:
        bundled_self_test()
        return 0
    if "--gui-self-test" in sys.argv:
        bundled_self_test()
        root = tk.Tk()
        root.withdraw()
        root.update_idletasks()
        root.destroy()
        return 0
    root = tk.Tk()
    LookupWindow(root)
    root.mainloop()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
