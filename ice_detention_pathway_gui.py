"""Desktop interface for reconstructing recorded ICE detention pathways."""

from __future__ import annotations

import threading
import tkinter as tk
import sys
import uuid
from pathlib import Path
from tkinter import messagebox, ttk
from tkinter.scrolledtext import ScrolledText

import duckdb

from fetch_data import DownloadError, DownloadResult, download_all
from ice_detention_pathway import (
    LookupError,
    fetch_pathway,
    format_pathway,
    override_pathway_arrest_location,
)


APP_NAME = "ICE Detention Pathway"
APP_VERSION = "3.1.0"


def application_directory() -> Path:
    """Locate external data beside the script, Windows exe, or macOS app."""
    if not getattr(sys, "frozen", False):
        source_directory = Path(__file__).resolve().parent
        working_directory = Path.cwd()
        expected_data = (
            "arrests-latest.parquet",
            "detention-stints-latest.parquet",
            "facilities-latest.parquet",
        )
        if any((working_directory / name).is_file() for name in expected_data):
            return working_directory
        return source_directory

    executable = Path(sys.executable).resolve()
    if sys.platform == "darwin" and executable.parents[2].suffix == ".app":
        return executable.parents[3]
    return executable.parent


PROJECT_DIR = application_directory()
ARRESTS_FILE = PROJECT_DIR / "arrests-latest.parquet"
DETENTION_FILE = PROJECT_DIR / "detention-stints-latest.parquet"
FACILITIES_FILE = PROJECT_DIR / "facilities-latest.parquet"


class LookupWindow:
    def __init__(self, root: tk.Tk) -> None:
        self.root = root
        self.root.title(f"{APP_NAME} v{APP_VERSION}")
        self.root.minsize(720, 570)

        container = ttk.Frame(root, padding=16)
        container.grid(row=0, column=0, sticky="nsew")
        root.rowconfigure(0, weight=1)
        root.columnconfigure(0, weight=1)
        container.columnconfigure(0, weight=1)
        container.rowconfigure(9, weight=1)

        toolbar = ttk.Frame(container)
        toolbar.grid(row=0, column=0, sticky="ew", pady=(0, 12))
        toolbar.columnconfigure(0, weight=1)

        ttk.Label(
            toolbar,
            text=f"{APP_NAME} v{APP_VERSION}",
            font=("TkDefaultFont", 15),
        ).grid(row=0, column=0, sticky="w")
        self.download_button = ttk.Button(
            toolbar,
            text="Download / Update Data…",
            command=self.confirm_download,
        )
        self.download_button.grid(row=0, column=1, padx=(8, 0))
        ttk.Button(toolbar, text="? Help", command=self.show_help).grid(
            row=0, column=2, padx=(8, 0)
        )

        ttk.Label(
            container,
            text=(
                '"Get that fish a lawyer"'
            ),
            font=("TkDefaultFont", 22, "italic"),
            justify=tk.CENTER,
            anchor=tk.CENTER,
        ).grid(row=1, column=0, sticky="ew", pady=(4, 18))

        ttk.Label(container, text="Unique identifier").grid(
            row=2, column=0, sticky="w"
        )

        input_row = ttk.Frame(container)
        input_row.grid(row=3, column=0, sticky="ew", pady=(5, 4))
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
        ).grid(row=4, column=0, sticky="w", pady=(0, 12))

        ttk.Label(container, text="Manual arrest location (optional)").grid(
            row=5, column=0, sticky="w"
        )

        self.manual_location_entry = ttk.Entry(container)
        self.manual_location_entry.grid(
            row=6, column=0, sticky="ew", pady=(5, 4)
        )
        ttk.Label(
            container,
            text=(
                "Overrides the arrest location in the displayed timeline only; "
                "source data is not changed."
            ),
            foreground="#555555",
        ).grid(row=7, column=0, sticky="w", pady=(0, 12))

        ttk.Label(container, text="Detention timeline (editable)").grid(
            row=8, column=0, sticky="w"
        )

        self.result_box = ScrolledText(
            container,
            height=8,
            wrap=tk.WORD,
            font=("TkDefaultFont", 12),
            padx=8,
            pady=8,
        )
        self.result_box.grid(row=9, column=0, sticky="nsew", pady=(5, 12))
        self.result_box.configure(state=tk.DISABLED)

        footer = ttk.Frame(container)
        footer.grid(row=10, column=0, sticky="ew")
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

    def set_result(self, value: str, *, editable: bool = False) -> None:
        self.result_box.configure(state=tk.NORMAL)
        self.result_box.delete("1.0", tk.END)
        self.result_box.insert("1.0", value)
        if not editable:
            self.result_box.configure(state=tk.DISABLED)

    def start_search(self, _event: tk.Event | None = None) -> None:
        identifier = self.identifier_entry.get().strip()
        manual_location = self.manual_location_entry.get()
        if not identifier:
            self.set_result("")
            self.status.configure(text="Enter an identifier first.")
            self.copy_button.configure(state=tk.DISABLED)
            return

        self.search_button.configure(state=tk.DISABLED)
        self.copy_button.configure(state=tk.DISABLED)
        self.status.configure(text="Searching…")
        self.set_result("")
        threading.Thread(
            target=self.run_search,
            args=(identifier, manual_location),
            daemon=True,
        ).start()

    def run_search(self, identifier: str, manual_location: str) -> None:
        try:
            pathway = fetch_pathway(
                identifier,
                arrests_file=ARRESTS_FILE,
                detention_file=DETENTION_FILE,
                facilities_file=FACILITIES_FILE,
            )
            pathway = override_pathway_arrest_location(pathway, manual_location)
            result = format_pathway(pathway)
        except (LookupError, duckdb.Error) as exc:
            self.root.after(0, self.show_error, str(exc))
            return
        self.root.after(
            0, self.show_result, result, pathway.row_count, len(pathway.stays)
        )

    def show_result(self, result: str, row_count: int, stay_count: int) -> None:
        self.set_result(result, editable=True)
        summary = f"Found {row_count} detention row(s) across {stay_count} stay(s)."
        if stay_count > 1:
            summary += " Separate stays are shown separately; they are not one continuous detention."
        self.status.configure(text=f"{summary} Edit the timeline if needed.")
        self.search_button.configure(state=tk.NORMAL)
        self.copy_button.configure(state=tk.NORMAL)

    def show_error(self, message: str) -> None:
        self.set_result(message)
        self.status.configure(text="Lookup failed.")
        self.search_button.configure(state=tk.NORMAL)
        self.copy_button.configure(state=tk.DISABLED)

    def confirm_download(self) -> None:
        confirmed = messagebox.askyesno(
            "Download the current datasets?",
            "This is the only feature that uses the network. It downloads the "
            "Parquet files listed in data-sources.json and replaces the local "
            "copies.\n\n"
            "Each file is checked for readable Parquet and the expected "
            "columns before anything is replaced, so a failed download leaves "
            "your existing data untouched. The files are large; this may take "
            "several minutes.\n\nContinue?",
            icon=messagebox.WARNING,
            parent=self.root,
        )
        if not confirmed:
            return

        self.search_button.configure(state=tk.DISABLED)
        self.download_button.configure(state=tk.DISABLED)
        self.copy_button.configure(state=tk.DISABLED)
        self.status.configure(text="Starting download…")
        threading.Thread(target=self.run_download, daemon=True).start()

    def run_download(self) -> None:
        def progress(filename: str, written: int, total: int | None) -> None:
            if total:
                text = (
                    f"{filename}: {written / 1e6:,.0f} MB of {total / 1e6:,.0f} MB "
                    f"({100.0 * written / total:.0f}%)"
                )
            else:
                text = f"{filename}: {written / 1e6:,.0f} MB"
            self.root.after(0, self.status.configure, {"text": text})

        try:
            results = download_all(PROJECT_DIR, progress=progress)
        except (DownloadError, OSError) as exc:
            self.root.after(0, self.show_download_error, str(exc))
            return
        self.root.after(0, self.show_download_result, results)

    def show_download_result(self, results: list[DownloadResult]) -> None:
        summary = "\n".join(
            f"{result.filename}: {result.rows:,} rows, "
            f"{result.bytes_written / 1e6:,.1f} MB"
            for result in results
        )
        self.status.configure(text="Download complete.")
        self.search_button.configure(state=tk.NORMAL)
        self.download_button.configure(state=tk.NORMAL)
        messagebox.showinfo("Download complete", summary, parent=self.root)

    def show_download_error(self, message: str) -> None:
        self.status.configure(text="Download failed; existing data unchanged.")
        self.search_button.configure(state=tk.NORMAL)
        self.download_button.configure(state=tk.NORMAL)
        messagebox.showerror("Download failed", message, parent=self.root)

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
        help_window.title(f"How to use {APP_NAME}")
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
2. Optionally enter a more precise arrest location. This changes only the
displayed result, not the source Parquet file.
3. Select Search or press Return.
4. The program finds every detention and arrest record for that identifier,
groups the detention records into stays, and orders each stay by book-in time.
5. Edit the generated timeline directly if you need to add or correct other
manual details.
6. Select Copy to Clipboard to copy the text currently shown in the timeline.

Impossible chronology is retained but marked with a (DISCREPANCY: ...) note. A
missing book-out date appears as UNKNOWN - CURRENTLY HELD (?) because the data
cannot confirm release.

Output format:

  arrest date, arrest location-> [Book-in: date/time][Book-out: date/time],
  facility:code -> next facility

Values after the first underscore are ignored. This lets a stay_ID or stint_ID
be reduced to its base unique_identifier.

SEPARATE STAYS

A person may be detained more than once. Each continuous period in custody is a
stay, and each facility placement inside a stay is a stint. When more than one
stay is found, each is labelled [STAY n of total] and the break between them is
shown as, for example:

  === RELEASED (Paroled); NOT IN ICE CUSTODY FOR 396 days ===

Separate stays are never joined into one pathway. Reading two stays as
continuous detention would overstate time in custody, often by many months.

MISSING RECORDS

A stay with no matching arrest record is labelled NO ARREST RECORD IN THIS
DATASET, followed by the program and area of responsibility that opened it.
This is normal and does not mean the record is wrong: the arrests table covers
ICE arrests, so someone transferred into ICE custody from Border Patrol has
detention records and no arrest record.

An arrest with no detention record is reported the same way, under
[ARREST WITH NO RECORDED DETENTION].

REQUIRED FILES

Place the downloaded Parquet files in the same directory as this script,
Windows .exe, or macOS .app and use these exact filenames:

  arrests-latest.parquet
  detention-stints-latest.parquet
  facilities-latest.parquet

Use complete national files. A locally reduced copy silently removes people and
stays, which cannot be detected from a search result.

The Download / Update Data… button fetches these three plus the optional
detention-stays-latest.parquet and joined-arrests-detention-stays-latest.parquet
files listed in data-sources.json.

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
        LookupWindow(root)
        root.update_idletasks()
        root.destroy()
        return 0
    root = tk.Tk()
    LookupWindow(root)
    root.mainloop()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
