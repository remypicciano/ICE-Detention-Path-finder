"""Desktop interface for reconstructing recorded ICE detention pathways."""

from __future__ import annotations

import json
import sys
import threading
import tkinter as tk
import uuid
import webbrowser
from collections.abc import Sequence
from contextlib import suppress
from datetime import UTC, datetime
from pathlib import Path
from tkinter import filedialog, messagebox, ttk
from tkinter.scrolledtext import ScrolledText

import duckdb

from fetch_data import DownloadError, DownloadResult, download_all
from ice_detention_pathway import (
    APP_VERSION,
    EDITABLE_FIELDS,
    MANUAL_EDIT_MARK,
    ArrestEvent,
    LookupError,
    Pathway,
    ReleaseInfo,
    Stay,
    StaySummary,
    available_columns,
    dataset_release,
    edit_scope,
    export_pathway_json,
    fetch_pathway,
    format_pathway,
    override_key,
    pathway_to_dict,
)
from ice_history import (
    HistoryEntry,
    append_history,
    append_history_many,
    build_bulk_json,
    build_bulk_text,
    parse_identifiers_payload,
    recent_history,
)
from update_check import UpdateCheckError, UpdateStatus, check_for_update

APP_NAME = "ICE Detention Pathway"


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

ARRESTS_NAME = "arrests-latest.parquet"
DETENTION_NAME = "detention-stints-latest.parquet"


def bundled_asset(name: str) -> Path | None:
    """Locate an asset shipped with the app.

    Checked inside the frozen build (`_MEIPASS`, set for onefile and onedir
    alike), then beside the executable, then beside the source file. A missing
    asset returns None rather than raising: an icon problem must never stop a
    lookup.
    """
    candidates: list[Path] = []
    frozen_bundle = getattr(sys, "_MEIPASS", None)
    if frozen_bundle:
        candidates.append(Path(frozen_bundle) / "assets" / name)
    if getattr(sys, "frozen", False):
        candidates.append(Path(sys.executable).resolve().parent / "assets" / name)
    candidates.append(Path(__file__).resolve().parent / "assets" / name)
    return next((candidate for candidate in candidates if candidate.is_file()), None)
FACILITIES_NAME = "facilities-latest.parquet"


def detect_releases() -> dict[str, tuple[Path, Path, Path]]:
    """Find every folder holding a complete arrests + detention pair.

    The project folder and a separately downloaded copy of the same release
    would otherwise appear as two choices offering identical data, so folders
    are deduplicated on release generation and file size — metadata only, no
    table scan, and two different releases never share both.
    """
    directories = [PROJECT_DIR, *sorted(PROJECT_DIR.glob("ddp-*"))]
    releases: dict[str, tuple[Path, Path, Path]] = {}
    seen: set[tuple[str, int, int]] = set()
    connection = duckdb.connect(database=":memory:")
    try:
        for directory in directories:
            arrests = directory / ARRESTS_NAME
            detention = directory / DETENTION_NAME
            facilities = directory / FACILITIES_NAME
            if not (arrests.is_file() and detention.is_file()):
                continue
            columns = available_columns(connection, arrests)
            tag = "NEW" if "event_landmark" in columns else "OLD"
            key = (tag, arrests.stat().st_size, detention.stat().st_size)
            if key in seen:
                continue
            seen.add(key)
            where = "current folder" if directory == PROJECT_DIR else directory.name
            releases[f"{tag} — {where}"] = (arrests, detention, facilities)
    finally:
        connection.close()
    return releases

_release_cache: dict[tuple[str, str, int, int], ReleaseInfo] = {}


def release_info() -> ReleaseInfo | None:
    """Release these files belong to, or None when they cannot be read.

    Cached against the files' mtimes, because a search must not spend two full
    table scans on provenance it already computed — and a re-download changes
    the mtimes, so a refreshed release is never reported under its old name.
    """
    try:
        key = (
            str(ARRESTS_FILE),
            str(DETENTION_FILE),
            ARRESTS_FILE.stat().st_mtime_ns,
            DETENTION_FILE.stat().st_mtime_ns,
        )
    except OSError:
        return None
    cached = _release_cache.get(key)
    if cached is not None:
        return cached

    connection = duckdb.connect(database=":memory:")
    try:
        connection.execute("SET TimeZone = 'UTC'")
        info = dataset_release(connection, ARRESTS_FILE, DETENTION_FILE)
    finally:
        connection.close()
    _release_cache.clear()
    _release_cache[key] = info
    return info


def release_header() -> str:
    """The one-line watermark: generation, and how far the data runs."""
    info = release_info()
    return info.summary if info else ""


def manual_edit_line(edited: Sequence[str]) -> str:
    """`edited-manual=` naming the entries the reader typed, or nothing.

    Keys are printed as they were addressed, so `stay-2/location` says in one
    glance that only the second stay's location was corrected. Absence of this
    line is the claim: an untagged block is source data exactly as published, so
    a reader never has to inspect markers to know.
    """
    named = [(key, edit_scope(key)) for key in edited]
    named = [item for item in named if item[1][1] in EDITABLE_FIELDS]
    if not named:
        return ""
    # All-stays entries first, then grouped by the stay they are scoped to.
    named.sort(
        key=lambda item: (
            item[1][0] is not None,
            item[1][0] or 0,
            EDITABLE_FIELDS.index(item[1][1]),
        )
    )
    return "edited-manual=" + ",".join(key for key, _ in named)


def with_release_header(text: str, edited: Sequence[str] = ()) -> str:
    """Prefix a rendered pathway with its watermark and, if any, its edit tag."""
    lines = [line for line in (release_header(), manual_edit_line(edited)) if line]
    lines.append(text)
    return "\n".join(lines)


class LookupWindow:
    def __init__(self, root: tk.Tk) -> None:
        self.root = root
        self.root.title(f"{APP_NAME} v{APP_VERSION}")
        self.root.minsize(720, 570)
        # The window is fixed-size on purpose: results, status text and
        # release switches must scroll inside it, never stretch it. The exact
        # size is pinned once the widgets exist (`_freeze_window_size`).
        self.root.resizable(False, False)
        self._apply_window_icon()
        self.last_pathway: Pathway | None = None
        # Manual entries belong to the person they were typed for. Keyed by
        # identifier so switching people cannot carry one person's correction
        # into another's record.
        self.current_identifier: str | None = None
        self.overrides: dict[str, str] = {}
        self.overrides_by_id: dict[str, dict[str, str]] = {}

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

        self.releases = detect_releases()
        default_label = next(
            (
                label
                for label, files in self.releases.items()
                if files == (ARRESTS_FILE, DETENTION_FILE, FACILITIES_FILE)
            ),
            next(iter(self.releases), ""),
        )
        release_box = ttk.Frame(toolbar)
        release_box.grid(row=0, column=1, padx=(8, 0), sticky="e")
        ttk.Label(release_box, text="Release").grid(row=0, column=0, sticky="w")
        self.release_var = tk.StringVar(value=default_label)
        self.release_selector = ttk.Combobox(
            release_box,
            textvariable=self.release_var,
            values=list(self.releases),
            state="readonly",
            width=22,
        )
        self.release_selector.grid(row=0, column=1, padx=(6, 0))
        self.release_selector.bind("<<ComboboxSelected>>", self.change_release)

        self.download_button = ttk.Button(
            toolbar,
            text="Download / Update Data…",
            command=self.confirm_download,
        )
        self.download_button.grid(row=0, column=2, padx=(8, 0))
        self.bulk_button = ttk.Button(
            toolbar,
            text="Bulk Search…",
            command=self.show_bulk,
        )
        self.bulk_button.grid(row=0, column=3, padx=(8, 0))
        ttk.Button(toolbar, text="? Help", command=self.show_help).grid(
            row=0, column=4, padx=(8, 0)
        )
        self.check_update_button = ttk.Button(
            toolbar,
            text="Check for Updates…",
            command=self.start_update_check,
        )
        self.check_update_button.grid(row=0, column=5, padx=(8, 0))

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
        self.identifier_entry.bind("<KeyRelease>", self.on_identifier_change)
        self.identifier_entry.bind("<<Paste>>", self.on_identifier_change)

        self.search_button = ttk.Button(
            input_row, text="Search", command=self.start_search
        )
        self.search_button.grid(row=0, column=1)

        ttk.Label(
            container,
            text="Paste an identifier, then select Search or press Return.",
            foreground="#555555",
        ).grid(row=4, column=0, sticky="w", pady=(0, 12))

        manual_header = ttk.Frame(container)
        manual_header.grid(row=5, column=0, sticky="ew")
        manual_header.columnconfigure(0, weight=1)
        ttk.Label(manual_header, text="Manual arrest location (optional)").grid(
            row=0, column=0, sticky="w"
        )
        ttk.Button(
            manual_header, text="All manual entries…", command=self.show_overrides
        ).grid(row=0, column=1, sticky="e")

        self.manual_location_entry = ttk.Entry(container)
        self.manual_location_entry.grid(
            row=6, column=0, sticky="ew", pady=(5, 4)
        )
        ttk.Label(
            container,
            text=(
                "Shown in place of the recorded arrest location, marked "
                f"{MANUAL_EDIT_MARK} original=…]. Source data is not changed."
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

        recent = ttk.Frame(container)
        recent.grid(row=10, column=0, sticky="ew", pady=(0, 12))
        recent.columnconfigure(0, weight=1)

        recent_header = ttk.Frame(recent)
        recent_header.grid(row=0, column=0, sticky="ew")
        recent_header.columnconfigure(0, weight=1)
        ttk.Label(recent_header, text="Recent lookups").grid(
            row=0, column=0, sticky="w"
        )
        ttk.Button(recent_header, text="View all…", command=self.show_history).grid(
            row=0, column=1, sticky="e"
        )

        self.recent_tree = ttk.Treeview(
            recent,
            columns=("time", "identifier", "stays", "rows"),
            show="headings",
            height=4,
        )
        self.recent_tree.heading("time", text="Searched")
        self.recent_tree.heading("identifier", text="Identifier")
        self.recent_tree.heading("stays", text="Stays")
        self.recent_tree.heading("rows", text="Rows")
        self.recent_tree.column("time", width=150, anchor="w")
        self.recent_tree.column("identifier", width=330, anchor="w")
        self.recent_tree.column("stays", width=60, anchor="center")
        self.recent_tree.column("rows", width=60, anchor="center")
        self.recent_tree.grid(row=1, column=0, sticky="ew")
        self.recent_tree.bind("<Double-1>", self.load_recent)
        self.recent_tree.bind("<Return>", self.load_recent)
        ttk.Label(
            recent,
            text="Double-click a row to open that lookup.",
            foreground="#555555",
        ).grid(row=2, column=0, sticky="w", pady=(4, 0))

        footer = ttk.Frame(container)
        footer.grid(row=11, column=0, sticky="ew")
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

        self.export_button = ttk.Button(
            footer,
            text="Export JSON…",
            command=self.export_json,
            state=tk.DISABLED,
        )
        self.export_button.grid(row=0, column=2, sticky="e", padx=(8, 0))

        self.identifier_entry.focus_set()
        self._freeze_window_size()
        self.refresh_recent()

    def _apply_window_icon(self) -> None:
        """Show the melting-ice-cube icon in the title bar and taskbar."""
        asset = bundled_asset("ice_cube.png")
        if asset is None:
            return
        try:
            self.window_icon = tk.PhotoImage(file=str(asset))
        except tk.TclError:
            return
        self.root.iconphoto(True, self.window_icon)

    def _freeze_window_size(self) -> None:
        """Pin the window to its laid-out size so content can never resize it.

        Measured from the widgets' requested sizes, taking the larger of the
        minimum and the request so nothing is clipped. `minsize == maxsize`
        then holds the frame at that size: a long status line, a big result,
        or a release switch is clipped or scrolled, never allowed to stretch
        the window.
        """
        width = max(self.root.winfo_reqwidth(), 720)
        height = max(self.root.winfo_reqheight(), 570)
        self.root.geometry(f"{width}x{height}")
        self.root.minsize(width, height)
        self.root.maxsize(width, height)

    def set_result(self, value: str, *, editable: bool = False) -> None:
        self.result_box.configure(state=tk.NORMAL)
        self.result_box.delete("1.0", tk.END)
        self.result_box.insert("1.0", value)
        if not editable:
            self.result_box.configure(state=tk.DISABLED)

    def change_release(self, _event: tk.Event | None = None) -> None:
        """Point every lookup at the copy of the data the user selected.

        The module-level paths are read at call time by the single search, the
        bulk runner and the provenance header alike, so reassigning them here
        moves all three together.
        """
        global ARRESTS_FILE, DETENTION_FILE, FACILITIES_FILE
        selected = self.releases.get(self.release_var.get())
        if selected is None or selected == (
            ARRESTS_FILE,
            DETENTION_FILE,
            FACILITIES_FILE,
        ):
            return
        ARRESTS_FILE, DETENTION_FILE, FACILITIES_FILE = selected
        _release_cache.clear()
        if self.identifier_entry.get().strip():
            self.start_search()
        else:
            self.status.configure(
                text=f"Release set to {self.release_var.get()}."
            )

    def on_identifier_change(self, _event: tk.Event | None = None) -> None:
        """Move manual entries to the identifier they belong to.

        Corrections follow a person, not the window: an identifier already
        edited reloads its own entries, and one never edited starts empty, so
        nobody's correction can be printed onto someone else's record.
        """
        identifier = self.identifier_entry.get().strip()
        if identifier == self.current_identifier:
            return
        self.current_identifier = identifier
        self.overrides = self.overrides_by_id.setdefault(identifier, {})
        self.manual_location_entry.delete(0, tk.END)
        self.manual_location_entry.insert(0, self.overrides.get("location", ""))

    def sync_location_override(self) -> None:
        """Read the location box back into the current identifier's entries."""
        typed = " ".join(self.manual_location_entry.get().split())
        if typed:
            self.overrides["location"] = typed
        else:
            self.overrides.pop("location", None)

    def render_current(self) -> str:
        """The pathway as it should be shown, watermark and edit tag included."""
        if self.last_pathway is None:
            return ""
        return with_release_header(
            format_pathway(self.last_pathway, self.overrides), self.overrides
        )

    def start_search(self, _event: tk.Event | None = None) -> None:
        identifier = self.identifier_entry.get().strip()
        if not identifier:
            self.set_result("")
            self.status.configure(text="Enter an identifier first.")
            self.copy_button.configure(state=tk.DISABLED)
            return
        self.on_identifier_change()
        self.sync_location_override()
        overrides = dict(self.overrides)

        self.search_button.configure(state=tk.DISABLED)
        self.copy_button.configure(state=tk.DISABLED)
        self.status.configure(text="Searching…")
        self.set_result("")
        threading.Thread(
            target=self.run_search,
            args=(identifier, overrides),
            daemon=True,
        ).start()

    def run_search(self, identifier: str, overrides: dict[str, str]) -> None:
        try:
            # `pathway` stays the recorded data and only the rendered text
            # carries the manual entries, so an export cannot pass a typed
            # value off as one the publisher supplied.
            pathway = fetch_pathway(
                identifier,
                arrests_file=ARRESTS_FILE,
                detention_file=DETENTION_FILE,
                facilities_file=FACILITIES_FILE,
            )
            result = with_release_header(
                format_pathway(pathway, overrides), overrides
            )
        except (LookupError, duckdb.Error) as exc:
            self.root.after(0, self.show_error, str(exc))
            return
        self.root.after(0, self.show_result, result, pathway)

    def show_result(self, result: str, pathway: Pathway) -> None:
        self.last_pathway = pathway
        self.set_result(result, editable=True)
        summary = (
            f"Found {pathway.row_count} detention row(s) across "
            f"{len(pathway.stays)} stay(s)."
        )
        if len(pathway.stays) > 1:
            summary += " Separate stays are shown separately; they are not one continuous detention."
        if pathway.missing_fields:
            summary += (
                " Not in this dataset release: "
                + ", ".join(pathway.missing_fields)
                + " (absent from the source, not unknown)."
            )
        if self.overrides:
            summary += (
                " Manual entries applied: "
                + ", ".join(sorted(self.overrides))
                + " — each marked in place with the value it replaced."
            )
        else:
            summary += " No manual entries — this is source data as published."
        self.status.configure(text=summary)
        self.search_button.configure(state=tk.NORMAL)
        self.copy_button.configure(state=tk.NORMAL)
        self.export_button.configure(state=tk.NORMAL)
        with suppress(OSError):
            append_history(
                HistoryEntry(
                    identifier=pathway.identifier,
                    searched_at=datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ"),
                    n_stays=len(pathway.stays),
                    n_rows=pathway.row_count,
                )
            )
        self.refresh_recent()

    def show_overrides(self) -> None:
        """Edit every renderer entry for the person on screen, in one place.

        Each field appears once for all stays and once per stay, because a
        person may be detained more than once and a correction aimed at the
        second detention must not be printed onto the first. An empty box
        publishes the recorded value; a filled one replaces it and is tagged
        where it lands with the value it replaced, so the reader can always
        tell the source's words from their own.
        """
        if self.last_pathway is None:
            self.status.configure(
                text="Run a search first — manual entries apply to a lookup."
            )
            return
        self.sync_location_override()
        window = tk.Toplevel(self.root)
        window.title("Manual entries")
        window.minsize(600, 520)
        window.transient(self.root)

        outer = ttk.Frame(window, padding=16)
        outer.pack(fill=tk.BOTH, expand=True)

        ttk.Label(
            outer,
            text=(
                "Leave a box empty to publish the recorded value. A filled box "
                f"takes its place, tagged {MANUAL_EDIT_MARK} original=…] with "
                "what it replaced. “All stays” applies everywhere a stay does "
                "not override it; a stay box applies to that stay only."
            ),
            wraplength=560,
            foreground="#555555",
            justify=tk.LEFT,
        ).pack(fill=tk.X, pady=(0, 10))

        total = len(self.last_pathway.stays)
        scopes: list[tuple[str, int | None]] = [("All stays", None)]
        for number, stay in enumerate(self.last_pathway.stays, start=1):
            label = f"STAY {number} of {total}"
            if stay.start:
                label += f" — from {stay.start:%Y-%m-%d}"
            scopes.append((label, number))

        body = ttk.Frame(outer)
        body.pack(fill=tk.BOTH, expand=True)
        canvas = tk.Canvas(body, highlightthickness=0, borderwidth=0)
        scrollbar = ttk.Scrollbar(
            body, orient=tk.VERTICAL, command=canvas.yview
        )
        canvas.configure(yscrollcommand=scrollbar.set)
        scrollbar.pack(side=tk.RIGHT, fill=tk.Y)
        canvas.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        inner = ttk.Frame(canvas)
        holder = canvas.create_window((0, 0), window=inner, anchor="nw")
        inner.bind(
            "<Configure>",
            lambda _event: canvas.configure(scrollregion=canvas.bbox("all")),
        )
        canvas.bind(
            "<Configure>",
            lambda event: canvas.itemconfigure(holder, width=event.width),
        )

        def wheel(event: tk.Event) -> str:
            canvas.yview_scroll(int(-event.delta), "units")
            return "break"

        for widget in (window, canvas, inner):
            widget.bind("<MouseWheel>", wheel)
            widget.bind("<Button-4>", lambda _e: canvas.yview_scroll(-1, "units"))
            widget.bind("<Button-5>", lambda _e: canvas.yview_scroll(1, "units"))

        variables: dict[str, tk.StringVar] = {}
        row = 0
        for heading, scope in scopes:
            heading_label = ttk.Label(
                inner, text=heading, font=("TkDefaultFont", 11, "bold")
            )
            heading_label.grid(
                row=row, column=0, columnspan=2, sticky="w",
                pady=(14 if row else 4, 4),
            )
            row += 1
            for name in EDITABLE_FIELDS:
                key = override_key(scope, name)
                ttk.Label(inner, text=name).grid(
                    row=row, column=0, sticky="w", padx=(12, 10), pady=1
                )
                variable = tk.StringVar(value=self.overrides.get(key, ""))
                variables[key] = variable
                ttk.Entry(inner, textvariable=variable).grid(
                    row=row, column=1, sticky="ew", pady=1
                )
                row += 1
        inner.columnconfigure(1, weight=1)

        def apply() -> None:
            for key, variable in variables.items():
                value = " ".join(variable.get().split())
                if value:
                    self.overrides[key] = value
                else:
                    self.overrides.pop(key, None)
            self.manual_location_entry.delete(0, tk.END)
            self.manual_location_entry.insert(
                0, self.overrides.get("location", "")
            )
            window.destroy()
            self.refresh_result()

        def clear_all() -> None:
            for variable in variables.values():
                variable.set("")

        actions = ttk.Frame(outer)
        actions.pack(fill=tk.X, pady=(12, 0))
        ttk.Button(actions, text="Clear all", command=clear_all).pack(
            side=tk.LEFT
        )
        ttk.Button(actions, text="Cancel", command=window.destroy).pack(
            side=tk.RIGHT, padx=(8, 0)
        )
        ttk.Button(actions, text="Apply", command=apply).pack(side=tk.RIGHT)

    def refresh_result(self) -> None:
        """Re-render the lookup on screen with the entries now set."""
        if self.last_pathway is None:
            return
        self.set_result(self.render_current(), editable=True)
        if self.overrides:
            self.status.configure(
                text=(
                    "Manual entries applied: "
                    + ", ".join(sorted(self.overrides))
                    + " — tagged where they land; an empty box restores the "
                    "recorded value."
                )
            )
        else:
            self.status.configure(
                text="No manual entries — this is source data as published."
            )
        self.copy_button.configure(state=tk.NORMAL)
        self.export_button.configure(state=tk.NORMAL)

    def refresh_recent(self) -> None:
        for item in self.recent_tree.get_children():
            self.recent_tree.delete(item)
        for entry in recent_history(10):
            self.recent_tree.insert(
                "",
                tk.END,
                values=(
                    entry.get("searched_at", ""),
                    entry.get("identifier", ""),
                    entry.get("n_stays", ""),
                    entry.get("n_rows", ""),
                ),
            )

    def load_recent(self, _event: tk.Event | None = None) -> None:
        selection = self.recent_tree.selection()
        if not selection:
            return
        values = self.recent_tree.item(selection[0], "values")
        if len(values) > 1 and values[1]:
            self.load_identifier(str(values[1]))

    def load_identifier(self, identifier: str) -> None:
        self.identifier_entry.delete(0, tk.END)
        self.identifier_entry.insert(0, identifier)
        self.start_search()

    def show_history(self) -> None:
        HistoryWindow(self.root, on_load=self.load_identifier)

    def show_bulk(self) -> None:
        BulkWindow(self.root, on_history_changed=self.refresh_recent)

    def export_json(self) -> None:
        if self.last_pathway is None:
            self.status.configure(text="Nothing to export — run a search first.")
            return
        default_name = f"pathway-{self.last_pathway.identifier}.json"
        target = filedialog.asksaveasfilename(
            title="Export pathway as JSON",
            defaultextension=".json",
            initialfile=default_name,
            filetypes=[("JSON", "*.json")],
        )
        if not target:
            return
        try:
            export_pathway_json(self.last_pathway, Path(target))
        except OSError as exc:
            self.status.configure(text=f"Export failed: {exc}")
            return
        self.status.configure(text=f"Exported JSON: {target}")

    def show_error(self, message: str) -> None:
        self.last_pathway = None
        self.set_result(message)
        self.status.configure(text="Lookup failed.")
        self.search_button.configure(state=tk.NORMAL)
        self.copy_button.configure(state=tk.DISABLED)
        self.export_button.configure(state=tk.DISABLED)

    def show_demo(self) -> None:
        """Populate the window with a fabricated pathway for screenshots.

        The identifier and the rendered result are invented; nothing is read
        from the Parquet files and no network is used. The result still passes
        through the real `format_pathway` renderer, so the screenshot shows the
        exact output format the tool produces.
        """
        self.identifier_entry.delete(0, tk.END)
        self.identifier_entry.insert(0, "UFAKE-0001")
        # Drop any manual entries typed for a real identifier: they must not
        # be printed onto a fabricated pathway.
        self.on_identifier_change()
        self.last_pathway = demo_pathway()
        self.set_result(format_pathway(self.last_pathway), editable=True)
        self.status.configure(
            text="DEMO — fabricated pathway. Paste a real identifier and select "
            "Search to look up your data."
        )
        self.copy_button.configure(state=tk.NORMAL)
        self.export_button.configure(state=tk.NORMAL)
        self.identifier_entry.selection_range(0, tk.END)
        self.identifier_entry.focus_set()

    def confirm_download(self) -> None:
        confirmed = messagebox.askyesno(
            "Download the current datasets?",
            "Lookups never use the network. This is one of only two features "
            "that do (the other is Check for Updates…). It downloads the "
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

    def start_update_check(self) -> None:
        """Ask GitHub for the newest release without blocking the window."""
        self.check_update_button.configure(state=tk.DISABLED)
        self.status.configure(text="Checking for updates…")
        threading.Thread(target=self.run_update_check, daemon=True).start()

    def run_update_check(self) -> None:
        try:
            status = check_for_update(APP_VERSION)
        except UpdateCheckError as exc:
            self.root.after(0, self.show_update_check_error, str(exc))
            return
        self.root.after(0, self.show_update_result, status)

    def show_update_result(self, status: UpdateStatus) -> None:
        self.check_update_button.configure(state=tk.NORMAL)
        if not status.update_available:
            self.status.configure(
                text=f"You have the latest version ({APP_VERSION})."
            )
            messagebox.showinfo(
                "No update available",
                f"{APP_NAME} {APP_VERSION} is the latest published release.",
                parent=self.root,
            )
            return
        detail = (
            f"You have {APP_VERSION}; the latest release is "
            f"{status.latest.version}."
        )
        if status.asset_name:
            detail += f"\n\nMatching download: {status.asset_name}"
        detail += (
            "\n\nUpdating installs the new application only — your Parquet "
            "data files are never touched.\n\nOpen the download page?"
        )
        self.status.configure(text=f"Update available: {status.latest.version}")
        if messagebox.askyesno("Update available", detail, parent=self.root):
            webbrowser.open(status.latest.page_url)

    def show_update_check_error(self, message: str) -> None:
        self.check_update_button.configure(state=tk.NORMAL)
        self.status.configure(text="Update check failed.")
        messagebox.showerror("Update check failed", message, parent=self.root)

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
2. Optionally correct a field. The arrest location box handles the most common
one; "All manual entries…" opens every field this program renders. A correction
affects the displayed result only, never the source Parquet file, and is shown
inline as [MANUAL_EDIT original=...] with the value it replaced. Clear the box
and Apply to go back to the recorded value.
3. Select Search or press Return.
4. The program finds every detention and arrest record for that identifier,
groups the detention records into stays, and orders each stay by book-in time.
5. Select Copy to Clipboard to copy the text currently shown in the timeline.

Corrections belong to the identifier they were typed for: changing the
identifier reloads that person's entries, so nobody's correction is printed
onto someone else's record.

Impossible chronology is retained but marked with a (DISCREPANCY: ...) note. A
missing book-out date appears as UNKNOWN - CURRENTLY HELD (?) because the data
cannot confirm release.

Output format:

  dataset-NEW data-up-to=2026-08-06
  edited-manual=charge,location
  arrest date, arrest location [MANUAL_EDIT original=Dallas, TX]
    -> [Book-in: date/time][Book-out: date/time][Facility: facility:code]
    -> next stint
    [first stint — final_program: ...; book_in_aor: ...]
    [last stint — classification; case_status; threat_level; final_order;
     final_order_date; departed; charge]

The first line is the watermark: which generation of the data produced the
result, and how far its records run.

  dataset-       NEW is the release whose arrest records use the
                 event_landmark column, OLD the earlier one that still uses
                 apprehension_site_landmark. data-up-to is the newest date
                 either file holds, so "the data ends on 2026-08-06" is stated
                 outright rather than read off a timestamp.

The second line appears only when a field was corrected, and names exactly
which. Its absence is the claim: a block with no edited-manual= line is source
data as published, and a reader never has to scan for markers to know that.

Together they let a copied block still be attributed, and audited, after it is
separated from this program.

MANUAL ENTRIES

The arrest location box and "All manual entries…" set values this program
prints in place of the recorded ones. Every settable field:

  location, landmark, final_program, book_in_aor, classification, case_status,
  threat_level, final_order, final_order_date, departed, charge

A person may be detained more than once, so each field appears once for all
stays and once again under every [STAY n of total]. A stay box applies to that
stay only; where a stay is blank, the all-stays value is used. This is what lets
a correction aimed at move 2 stay out of move 1.

Each entry replaces its source value in the displayed timeline and is tagged
where it lands with the value it replaced, so an edited line is always
distinguishable from a recorded one:

  [STAY 2 of 3] 2025-05-29 19:35:00 UTC, Newark, NJ [MANUAL_EDIT original=New York, NEW YORK] -> ...
  edited-manual=stay-2/location

An empty box publishes the recorded value; (empty) is what the source held when
it had nothing. Entries are remembered per identifier, and the header names
what was changed, so a block with no edited-manual= line is source data as
published. Nothing is written to the Parquet files: the Export JSON action
exports the recorded pathway, never the displayed one.

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

RELEASES

The Release selector beside the title switches which copy of the data every
search reads. Any subfolder named ddp-* holding both a complete arrests file
and a complete detention file is offered beside the current folder, labelled
with the generation it holds: NEW uses the event_landmark arrest column, OLD
the older apprehension_site_landmark. Copies of the same release are listed
once. Switching re-runs whatever identifier is in the input box, and the
dataset- line at the top of every result names the generation actually read and
how far its records run.

Identifiers do not carry between releases: DDP re-issues a fresh
unique_identifier for each release, so an identifier found only in an older
release is reported as not found rather than matched to a similar person.

DEPENDENCIES

Use Python 3.14 with the packages in requirements-lock.txt. On macOS with
Homebrew Python, the desktop window also requires:

  brew install python-tk@3.14

HISTORY AND BULK SEARCH

Every successful search is saved to ~/.ice_pathway/history.json. The Recent
lookups panel shows the last 10; double-click a row to reopen it, or View
all… for the full history with a filter box.

Bulk Search… loads a JSON file of identifiers (a plain array, an
{"identifiers": [...]} object, or objects with an "identifier" and optional
"label"), searches each one, and lets you export the results as one JSON
bundle or a formatted text report. Lookups that fail are listed individually
and never stop the batch.
""",
        )
        text.configure(state=tk.DISABLED)
        ttk.Button(frame, text="Close", command=help_window.destroy).pack(
            anchor="e", pady=(10, 0)
        )


class HistoryWindow:
    """Full lookup history in a filterable table. Double-click loads."""

    def __init__(self, parent: tk.Misc, on_load) -> None:
        self.on_load = on_load
        self.window = tk.Toplevel(parent)
        self.window.title("Lookup history")
        self.window.geometry("760x440")
        self.window.minsize(560, 320)
        self.window.transient(parent)

        frame = ttk.Frame(self.window, padding=12)
        frame.pack(fill=tk.BOTH, expand=True)
        frame.columnconfigure(0, weight=1)
        frame.rowconfigure(1, weight=1)

        filter_row = ttk.Frame(frame)
        filter_row.grid(row=0, column=0, sticky="ew", pady=(0, 8))
        ttk.Label(filter_row, text="Filter:").pack(side=tk.LEFT)
        self.filter_entry = ttk.Entry(filter_row)
        self.filter_entry.pack(side=tk.LEFT, fill=tk.X, expand=True, padx=(8, 0))
        self.filter_entry.bind("<KeyRelease>", lambda _event: self.refresh())
        self.filter_entry.bind("<Return>", lambda _event: self.load_selected())

        self.tree = ttk.Treeview(
            frame,
            columns=("time", "identifier", "stays", "rows"),
            show="headings",
        )
        self.tree.heading("time", text="Searched")
        self.tree.heading("identifier", text="Identifier")
        self.tree.heading("stays", text="Stays")
        self.tree.heading("rows", text="Rows")
        self.tree.column("time", width=150, anchor="w")
        self.tree.column("identifier", width=340, anchor="w")
        self.tree.column("stays", width=60, anchor="center")
        self.tree.column("rows", width=60, anchor="center")
        self.tree.grid(row=1, column=0, sticky="nsew")
        scrollbar = ttk.Scrollbar(
            frame, orient=tk.VERTICAL, command=self.tree.yview
        )
        scrollbar.grid(row=1, column=1, sticky="ns")
        self.tree.configure(yscrollcommand=scrollbar.set)
        self.tree.bind("<Double-1>", lambda _event: self.load_selected())
        self.tree.bind("<Return>", lambda _event: self.load_selected())

        button_row = ttk.Frame(frame)
        button_row.grid(row=2, column=0, columnspan=2, sticky="ew", pady=(10, 0))
        ttk.Button(
            button_row, text="Open selected", command=self.load_selected
        ).pack(side=tk.RIGHT)
        ttk.Button(button_row, text="Close", command=self.window.destroy).pack(
            side=tk.RIGHT, padx=(0, 8)
        )

        self.refresh()
        self.filter_entry.focus_set()

    def refresh(self) -> None:
        needle = self.filter_entry.get().strip().lower()
        for item in self.tree.get_children():
            self.tree.delete(item)
        for entry in reversed(recent_history(5000)):
            if needle and needle not in str(entry.get("identifier", "")).lower():
                continue
            self.tree.insert(
                "",
                tk.END,
                values=(
                    entry.get("searched_at", ""),
                    entry.get("identifier", ""),
                    entry.get("n_stays", ""),
                    entry.get("n_rows", ""),
                ),
            )

    def load_selected(self) -> None:
        selection = self.tree.selection()
        if not selection:
            return
        values = self.tree.item(selection[0], "values")
        if len(values) > 1 and values[1]:
            self.on_load(str(values[1]))
            self.window.destroy()


class BulkWindow:
    """Bulk-search a JSON list of identifiers, then export JSON or text."""

    def __init__(self, parent: tk.Misc, on_history_changed) -> None:
        self.on_history_changed = on_history_changed
        self.items: list[tuple[str, str | None]] = []
        self.results: list[dict] = []
        self.running = False

        self.window = tk.Toplevel(parent)
        self.window.title("Bulk search")
        self.window.geometry("840x600")
        self.window.minsize(620, 420)
        self.window.transient(parent)

        top = ttk.Frame(self.window, padding=12)
        top.pack(fill=tk.X)
        ttk.Label(top, text="Bulk search").pack(side=tk.LEFT)
        self.load_button = ttk.Button(
            top, text="Load JSON file…", command=self.load_file
        )
        self.load_button.pack(side=tk.LEFT, padx=(12, 0))
        self.run_button = ttk.Button(
            top, text="Run", command=self.run, state=tk.DISABLED
        )
        self.run_button.pack(side=tk.LEFT, padx=(8, 0))
        self.file_label = ttk.Label(top, text="No file loaded.")
        self.file_label.pack(side=tk.LEFT, padx=(12, 0))

        self.progressbar = ttk.Progressbar(self.window, mode="determinate")
        self.progressbar.pack(fill=tk.X, padx=12)
        self.status = ttk.Label(self.window, text="")
        self.status.pack(anchor="w", padx=12, pady=(6, 0))

        body = ttk.Frame(self.window)
        body.pack(fill=tk.BOTH, expand=True, padx=12, pady=8)
        body.columnconfigure(0, weight=1)
        body.rowconfigure(0, weight=1)

        self.results_tree = ttk.Treeview(
            body,
            columns=("status", "identifier", "label", "detail"),
            show="headings",
        )
        self.results_tree.heading("status", text="Status")
        self.results_tree.heading("identifier", text="Identifier")
        self.results_tree.heading("label", text="Label")
        self.results_tree.heading("detail", text="Details")
        self.results_tree.column("status", width=70, anchor="center")
        self.results_tree.column("identifier", width=260, anchor="w")
        self.results_tree.column("label", width=120, anchor="w")
        self.results_tree.column("detail", width=300, anchor="w")
        self.results_tree.grid(row=0, column=0, sticky="nsew")
        scrollbar = ttk.Scrollbar(
            body, orient=tk.VERTICAL, command=self.results_tree.yview
        )
        scrollbar.grid(row=0, column=1, sticky="ns")
        self.results_tree.configure(yscrollcommand=scrollbar.set)
        self.results_tree.bind("<<TreeviewSelect>>", self.show_detail)

        ttk.Label(body, text="Detail").grid(row=1, column=0, sticky="w", pady=(8, 2))
        self.detail_box = ScrolledText(body, height=7, wrap=tk.WORD)
        self.detail_box.grid(row=2, column=0, sticky="ew")
        self.detail_box.configure(state=tk.DISABLED)

        bottom = ttk.Frame(self.window, padding=12)
        bottom.pack(fill=tk.X)
        self.export_json_button = ttk.Button(
            bottom, text="Export JSON…", command=self.export_json, state=tk.DISABLED
        )
        self.export_json_button.pack(side=tk.RIGHT)
        self.export_text_button = ttk.Button(
            bottom, text="Export Text…", command=self.export_text, state=tk.DISABLED
        )
        self.export_text_button.pack(side=tk.RIGHT, padx=(0, 8))

    def load_file(self) -> None:
        path = filedialog.askopenfilename(
            title="Load identifiers JSON",
            filetypes=[("JSON", "*.json"), ("Text", "*.txt")],
            parent=self.window,
        )
        if not path:
            return
        try:
            self.items = parse_identifiers_payload(Path(path).read_text(encoding="utf-8"))
        except (OSError, ValueError) as exc:
            messagebox.showerror("Load failed", str(exc), parent=self.window)
            return
        self.file_label.configure(text=f"{len(self.items)} identifier(s) loaded")
        self.run_button.configure(state=tk.NORMAL)
        self.results = []
        self.progressbar.configure(value=0)
        self.status.configure(text="")
        for item in self.results_tree.get_children():
            self.results_tree.delete(item)

    def run(self) -> None:
        if self.running or not self.items:
            return
        self.running = True
        self.run_button.configure(state=tk.DISABLED)
        self.load_button.configure(state=tk.DISABLED)
        self.export_json_button.configure(state=tk.DISABLED)
        self.export_text_button.configure(state=tk.DISABLED)
        self.progressbar.configure(maximum=len(self.items), value=0)
        self.status.configure(text="Running…")
        threading.Thread(target=self.run_bulk, daemon=True).start()

    def run_bulk(self) -> None:
        results: list[dict] = []
        history_entries: list[HistoryEntry] = []
        for index, (identifier, label) in enumerate(self.items, start=1):
            try:
                pathway = fetch_pathway(
                    identifier,
                    arrests_file=ARRESTS_FILE,
                    detention_file=DETENTION_FILE,
                    facilities_file=FACILITIES_FILE,
                )
                pathway_dict = pathway_to_dict(pathway)
                results.append(
                    {
                        "identifier": pathway.identifier,
                        "label": label,
                        "status": "ok",
                        "pathway": pathway_dict,
                        "text": with_release_header(format_pathway(pathway)),
                        "detail": (
                            f"{pathway_dict['totals']['stays']} stays, "
                            f"{pathway_dict['totals']['detention_rows']} rows"
                        ),
                    }
                )
                history_entries.append(
                    HistoryEntry(
                        identifier=pathway.identifier,
                        searched_at=datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ"),
                        n_stays=len(pathway.stays),
                        n_rows=pathway.row_count,
                        label=label,
                    )
                )
            except (LookupError, duckdb.Error) as exc:
                results.append(
                    {
                        "identifier": identifier,
                        "label": label,
                        "status": "error",
                        "pathway": None,
                        "text": None,
                        "error": str(exc),
                        "detail": f"error: {exc}",
                    }
                )
            self.window.after(0, self.on_progress, index)
        self.window.after(0, self.on_done, results, history_entries)

    def on_progress(self, index: int) -> None:
        self.progressbar.configure(value=index)
        self.status.configure(text=f"{index} of {len(self.items)} processed…")

    def on_done(self, results: list[dict], history_entries: list[HistoryEntry]) -> None:
        self.results = results
        self.running = False
        self.run_button.configure(state=tk.NORMAL)
        self.load_button.configure(state=tk.NORMAL)
        for row in self.results_tree.get_children():
            self.results_tree.delete(row)
        for index, result in enumerate(results):
            self.results_tree.insert(
                "",
                tk.END,
                iid=str(index),
                values=(
                    result["status"],
                    result["identifier"],
                    result.get("label") or "",
                    result.get("detail") or "",
                ),
            )
        found = sum(1 for result in results if result["status"] == "ok")
        failed = len(results) - found
        self.status.configure(
            text=f"Done: {found} found, {failed} failed of {len(results)}."
        )
        if results:
            self.export_json_button.configure(state=tk.NORMAL)
            self.export_text_button.configure(state=tk.NORMAL)
        with suppress(OSError):
            append_history_many(history_entries)
        self.on_history_changed()

    def show_detail(self, _event: tk.Event | None = None) -> None:
        selection = self.results_tree.selection()
        if not selection:
            return
        try:
            result = self.results[int(selection[0])]
        except (IndexError, ValueError):
            return
        self.detail_box.configure(state=tk.NORMAL)
        self.detail_box.delete("1.0", tk.END)
        if result["status"] == "ok":
            self.detail_box.insert("1.0", result.get("text") or "(no pathway)")
        else:
            self.detail_box.insert("1.0", f"ERROR: {result.get('error')}")
        self.detail_box.configure(state=tk.DISABLED)

    def export_json(self) -> None:
        if not self.results:
            return
        target = filedialog.asksaveasfilename(
            title="Export results as JSON",
            defaultextension=".json",
            initialfile="bulk-results.json",
            filetypes=[("JSON", "*.json")],
            parent=self.window,
        )
        if not target:
            return
        try:
            payload = json.dumps(build_bulk_json(self.results), indent=2) + "\n"
            Path(target).write_text(payload, encoding="utf-8")
        except OSError as exc:
            self.status.configure(text=f"Export failed: {exc}")
            return
        self.status.configure(text=f"Exported JSON: {target}")

    def export_text(self) -> None:
        if not self.results:
            return
        target = filedialog.asksaveasfilename(
            title="Export results as text",
            defaultextension=".txt",
            initialfile="bulk-results.txt",
            filetypes=[("Text", "*.txt")],
            parent=self.window,
        )
        if not target:
            return
        try:
            Path(target).write_text(build_bulk_text(self.results), encoding="utf-8")
        except OSError as exc:
            self.status.configure(text=f"Export failed: {exc}")
            return
        self.status.configure(text=f"Exported text: {target}")


def demo_pathway() -> Pathway:
    """A fabricated two-stay pathway rendered through the real formatter.

    Built from the exact `Stay`/`ArrestEvent` shapes the core produces so the
    `--demo` screenshot shows genuine output without touching the national
    Parquet files. Every identifier, location, and timestamp is invented.
    """
    utc = UTC
    stay_one = Stay(
        stay_id="UFK-2024-0001",
        arrest=None,
        rows=[
            (
                datetime(2024, 9, 16, 17, 56, tzinfo=utc),
                "Montgomery Processing Center:MTGPCTX",
                datetime(2024, 11, 29, 10, 44, tzinfo=utc),
            )
        ],
        entry_program="Border Patrol",
        entry_aor="Houston Area of Responsibility",
        release_reason="Paroled",
        stint_ids=("UFK-2024-0001-1",),
        all_programs=("Border Patrol",),
        all_aors=("Houston Area of Responsibility",),
    )
    stay_two = Stay(
        stay_id="UFK-2025-0002",
        arrest=ArrestEvent(
            datetime(2025, 12, 30, 10, 36, 35, tzinfo=utc),
            None,
            "New York, NEW YORK",
            landmark="DDC ICE DOCK GENERAL AREA, NON-SPECIFIC",
            state_source="Landmark appears in one state 99%",
        ),
        rows=[
            (
                datetime(2025, 12, 30, 11, 17, tzinfo=utc),
                "NYC Hold Room:NYCHOLD",
                datetime(2025, 12, 30, 18, 25, tzinfo=utc),
            ),
            (
                datetime(2025, 12, 30, 20, 26, tzinfo=utc),
                "MDC Brooklyn:BOPBRO",
                None,
            ),
        ],
        entry_program="ERO",
        entry_aor="New York City Area of Responsibility",
        stint_ids=("UFK-2025-0002-1", "UFK-2025-0002-2"),
        all_programs=("ERO",),
        all_aors=("New York City Area of Responsibility",),
        summary=StaySummary(
            classification="Low",
            case_status="ACTIVE",
            threat_level="NA",
            final_order="NO",
        ),
    )
    return Pathway(
        identifier="UFAKE-0001",
        stays=[stay_one, stay_two],
        arrests_without_stay=[],
        missing_fields=("book_in_aor", "detainee_classification"),
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
        window = LookupWindow(root)
        root.update_idletasks()
        # The window must be pinned: later content changes may not resize it.
        assert root.minsize() == root.maxsize(), (
            f"window size not frozen: minsize={root.minsize()} "
            f"maxsize={root.maxsize()}"
        )
        # And the melting-ice-cube icon must have loaded from the bundle.
        assert getattr(window, "window_icon", None) is not None, (
            "the bundled window icon did not load"
        )
        root.destroy()
        return 0
    root = tk.Tk()
    window = LookupWindow(root)
    if "--demo" in sys.argv:
        window.show_demo()
    root.mainloop()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
