"""Dataset facts shared by the offline build pipeline.

The query code stays the project's own ``ice_detention_pathway`` module. This
module finds local releases, builds watermarks, constructs the demo pathway,
and defines which manual-entry fields the web app may expose. The Worker does
not import it.
"""

from __future__ import annotations

import json
import os
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import duckdb

from ice_detention_pathway import (
    APP_VERSION,
    EDITABLE_FIELDS,
    ArrestEvent,
    LookupError,
    Pathway,
    Stay,
    StaySummary,
    available_columns,
    dataset_release,
    edit_scope,
    fetch_pathway,
    format_pathway,
    pathway_to_dict,
)
from ice_history import (
    HistoryEntry,
    build_bulk_json,
    build_bulk_text,
    parse_identifiers_payload,
)
from update_check import UpdateCheckError, check_for_update, format_report

PROJECT_DIR = Path(__file__).resolve().parents[2]

ARRESTS_NAME = "arrests-latest.parquet"
DETENTION_NAME = "detention-stints-latest.parquet"
FACILITIES_NAME = "facilities-latest.parquet"
DATASET_FILES = (ARRESTS_NAME, DETENTION_NAME, FACILITIES_NAME)

# Arrest-location free text is deliberately not offered on the web: a public
# visitor must not be able to print an invented address into a pathway. The
# remaining source-backed corrections still work exactly as in the desktop app.
_HIDDEN_EDIT_FIELDS = frozenset({"location", "landmark"})
WEB_EDITABLE_FIELDS: tuple[str, ...] = tuple(
    field for field in EDITABLE_FIELDS if field not in _HIDDEN_EDIT_FIELDS
)

MAX_IDENTIFIER_LENGTH = 200
MAX_OVERRIDE_LENGTH = 200
MAX_BULK_ITEMS = 200

_release_cache: dict[tuple[str, str, int, int], str] = {}


def data_root() -> Path:
    """Folder holding the Parquet files. Override with ICE_WEB_DATA."""
    configured = os.environ.get("ICE_WEB_DATA")
    return Path(configured).expanduser() if configured else PROJECT_DIR


def discover_releases() -> list[dict[str, Any]]:
    """List every folder with a complete arrests + detention pair.

    Mirrors the desktop app's release picker. Copies of the same release are
    collapsed so a visitor never sees two identical choices.
    """
    root = data_root()
    directories = [root, *sorted(root.glob("ddp-*"))]
    connection = duckdb.connect(database=":memory:")
    releases: list[dict[str, Any]] = []
    seen: set[tuple[str, int, int]] = set()
    try:
        for directory in directories:
            arrests = directory / ARRESTS_NAME
            detention = directory / DETENTION_NAME
            facilities = directory / FACILITIES_NAME
            if not (arrests.is_file() and detention.is_file()):
                continue
            columns = available_columns(connection, arrests)
            tag = "NEW" if "event_landmark" in columns else "OLD"
            identity = (tag, arrests.stat().st_size, detention.stat().st_size)
            if identity in seen:
                continue
            seen.add(identity)
            where = "current" if directory == root else directory.name
            releases.append(
                {
                    "key": f"{tag.lower()}-{directory.name}",
                    "label": f"{tag} — {where}",
                    "tag": tag,
                    "arrests": arrests,
                    "detention": detention,
                    "facilities": facilities,
                }
            )
    finally:
        connection.close()
    return releases


def release_by_key(key: str, releases: list[dict[str, Any]] | None = None) -> dict[str, Any] | None:
    """Find one release by its stable key."""
    choices = releases if releases is not None else discover_releases()
    return next((release for release in choices if release["key"] == key), None)


def release_header(release: dict[str, Any]) -> str:
    """Watermark line for one release, cached against file mtimes."""
    arrests: Path = release["arrests"]
    detention: Path = release["detention"]
    try:
        key = (str(arrests), str(detention), arrests.stat().st_mtime_ns, detention.stat().st_mtime_ns)
    except OSError:
        return ""
    cached = _release_cache.get(key)
    if cached is not None:
        return cached
    connection = duckdb.connect(database=":memory:")
    try:
        connection.execute("SET TimeZone = 'UTC'")
        summary = dataset_release(connection, arrests, detention).summary
    except (duckdb.Error, OSError):
        return ""
    finally:
        connection.close()
    _release_cache.clear()
    _release_cache[key] = summary
    return summary


def sanitize_overrides(raw: Any) -> dict[str, str]:
    """Keep only fields the web app exposes, with bounded values."""
    if not isinstance(raw, dict):
        return {}
    clean: dict[str, str] = {}
    for key, value in raw.items():
        if not isinstance(key, str) or not isinstance(value, str):
            continue
        _scope, field = edit_scope(key)
        if field not in WEB_EDITABLE_FIELDS:
            continue
        text = " ".join(value.split())[:MAX_OVERRIDE_LENGTH]
        if text:
            clean[key] = text
    return clean


def manual_edit_line(edited: dict[str, str]) -> str:
    """`edited-manual=` naming manual entries, as in the desktop app."""
    if not edited:
        return ""
    named = [(key, edit_scope(key)) for key in edited]
    named.sort(
        key=lambda item: (
            item[1][0] is not None,
            item[1][0] or 0,
            WEB_EDITABLE_FIELDS.index(item[1][1]),
        )
    )
    return "edited-manual=" + ",".join(key for key, _scope in named)


def render(pathway: Pathway, overrides: dict[str, str], release: dict[str, Any] | None) -> str:
    """Rendered pathway with watermark and edit tag, exactly like the desktop."""
    lines = [line for line in (release_header(release) if release else "", manual_edit_line(overrides)) if line]
    lines.append(format_pathway(pathway, overrides))
    return "\n".join(lines)


def status_text(pathway: Pathway, overrides: dict[str, str]) -> str:
    """Status sentence under the result pane."""
    summary = f"Found {pathway.row_count} detention row(s) across {len(pathway.stays)} stay(s)."
    if len(pathway.stays) > 1:
        summary += " Separate stays are shown separately; they are not one continuous detention."
    if pathway.missing_fields:
        summary += (
            " Not in this dataset release: "
            + ", ".join(pathway.missing_fields)
            + " (absent from the source, not unknown)."
        )
    if overrides:
        summary += (
            " Manual entries applied: "
            + ", ".join(sorted(overrides))
            + " — each marked in place with the value it replaced."
        )
    else:
        summary += " No manual entries — this is source data as published."
    return summary


def _history_entry(pathway: Pathway, label: str | None = None) -> dict[str, Any]:
    return {
        "identifier": pathway.identifier,
        "searched_at": datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "n_stays": len(pathway.stays),
        "n_rows": pathway.row_count,
        "label": label,
    }


def _result(
    pathway: Pathway,
    overrides: dict[str, str],
    release: dict[str, Any] | None,
    status: str,
    label: str | None = None,
) -> dict[str, Any]:
    """Shape one lookup for the templates."""
    return {
        "identifier": pathway.identifier,
        "text": render(pathway, overrides, release),
        "status": status,
        "overrides": overrides,
        "overrides_json": json.dumps(overrides, separators=(",", ":")),
        "n_stays": len(pathway.stays),
        "n_rows": pathway.row_count,
        "pathway_json": json.dumps(pathway_to_dict(pathway), indent=2) + "\n",
        "history_entry": _history_entry(pathway, label),
    }


def search(identifier: str, release: dict[str, Any], overrides: dict[str, str] | None = None) -> dict[str, Any]:
    """Run one lookup through the unchanged core engine."""
    identifier = " ".join(identifier.split())
    if not identifier:
        raise LookupError("Enter an identifier first.")
    if len(identifier) > MAX_IDENTIFIER_LENGTH:
        raise LookupError(f"Identifier is too long (limit {MAX_IDENTIFIER_LENGTH} characters).")
    edits = sanitize_overrides(overrides or {})
    pathway = fetch_pathway(
        identifier,
        arrests_file=release["arrests"],
        detention_file=release["detention"],
        facilities_file=release["facilities"],
    )
    return _result(pathway, edits, release, status_text(pathway, edits))


def export_json_text(identifier: str, release: dict[str, Any]) -> tuple[str, str]:
    """Export the recorded pathway, never the displayed/edited one."""
    pathway = fetch_pathway(
        identifier,
        arrests_file=release["arrests"],
        detention_file=release["detention"],
        facilities_file=release["facilities"],
    )
    return json.dumps(pathway_to_dict(pathway), indent=2) + "\n", pathway.identifier


def demo() -> dict[str, Any]:
    """Fabricated two-stay pathway rendered through the real formatter."""
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
    pathway = Pathway(
        identifier="UFAKE-0001",
        stays=[stay_one, stay_two],
        arrests_without_stay=[],
        missing_fields=("book_in_aor", "detainee_classification"),
    )
    return _result(
        pathway,
        {},
        None,
        "DEMO — fabricated pathway. Paste a real identifier and select Search.",
    )


def bulk(raw: str, release: dict[str, Any]) -> tuple[list[dict[str, Any]], dict[str, int]]:
    """Search a list of identifiers; failures are reported per row."""
    items = parse_identifiers_payload(raw)
    if len(items) > MAX_BULK_ITEMS:
        raise ValueError(f"Bulk search is limited to {MAX_BULK_ITEMS} identifiers.")
    results: list[dict[str, Any]] = []
    for identifier, label in items:
        try:
            pathway = fetch_pathway(
                identifier,
                arrests_file=release["arrests"],
                detention_file=release["detention"],
                facilities_file=release["facilities"],
            )
            result = _result(pathway, {}, release, status_text(pathway, {}), label)
            results.append(
                {
                    "identifier": pathway.identifier,
                    "label": label,
                    "status": "ok",
                    "pathway": json.loads(result["pathway_json"]),
                    "text": result["text"],
                    "detail": f"{len(pathway.stays)} stays, {pathway.row_count} rows",
                    "history_entry": result["history_entry"],
                }
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
                    "history_entry": None,
                }
            )
    found = sum(1 for result in results if result["status"] == "ok")
    return results, {"found": found, "failed": len(results) - found, "total": len(results)}


def bulk_export_json(results: list[dict[str, Any]]) -> str:
    """Standalone JSON bundle for a finished bulk run."""
    return json.dumps(build_bulk_json(results), indent=2) + "\n"


def bulk_export_text(results: list[dict[str, Any]]) -> str:
    """Readable text report for a finished bulk run."""
    return build_bulk_text(results) + "\n"


def check_updates() -> str:
    """Report the newest published release (desktop releases, not the web)."""
    try:
        status = check_for_update(APP_VERSION)
    except UpdateCheckError as exc:
        return f"Update check failed: {exc}"
    lines = [format_report(status)]
    lines.append("The website is served from the project source; it is not replaced by a desktop asset.")
    return "\n".join(lines)


def parse_overrides(raw: str) -> dict[str, str]:
    """Parse the hidden overrides field without letting bad input fail a page."""
    try:
        value = json.loads(raw or "{}")
    except json.JSONDecodeError:
        return {}
    return sanitize_overrides(value)


def history_payload(results: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """History entries from a bulk run, for the browser to store locally."""
    return [result["history_entry"] for result in results if result.get("history_entry")]


__all__ = [
    "APP_VERSION",
    "HistoryEntry",
    "MAX_BULK_ITEMS",
    "MAX_IDENTIFIER_LENGTH",
    "WEB_EDITABLE_FIELDS",
    "bulk",
    "bulk_export_json",
    "bulk_export_text",
    "check_updates",
    "data_root",
    "demo",
    "discover_releases",
    "export_json_text",
    "history_payload",
    "manual_edit_line",
    "parse_overrides",
    "release_by_key",
    "release_header",
    "search",
]
