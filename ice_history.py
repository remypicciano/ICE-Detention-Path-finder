"""Persistent lookup history and bulk-search helpers.

History is stored as JSON under the user's home directory so past searches
survive restarts without any setup. Bulk search reads a JSON file of
identifiers and produces either a JSON bundle or a readable text report.

All storage is best-effort: a history write failure never breaks a search.
"""

from __future__ import annotations

import json
import os
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

HISTORY_SCHEMA_VERSION = 1
MAX_HISTORY_ENTRIES = 5000
BULK_SCHEMA_VERSION = 1

BulkResult = dict[str, Any]


@dataclass
class HistoryEntry:
    identifier: str
    searched_at: str
    n_stays: int = 0
    n_rows: int = 0
    label: str | None = None


def history_path() -> Path:
    """Return the history JSON path (its parent is created on write only)."""
    base = Path(os.environ.get("ICE_PATHWAY_DATA", Path.home() / ".ice_pathway"))
    return base / "history.json"


def load_history() -> list[dict[str, Any]]:
    """Return every recorded history entry, or [] on any read problem."""
    path = history_path()
    if not path.exists():
        return []
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return []
    entries = payload.get("entries", []) if isinstance(payload, dict) else []
    return [
        entry
        for entry in entries
        if isinstance(entry, dict) and entry.get("identifier")
    ]


def _write_history(entries: list[dict[str, Any]]) -> None:
    path = history_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {"schema_version": HISTORY_SCHEMA_VERSION, "entries": entries}
    path.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")


def append_history(entry: HistoryEntry) -> None:
    """Record one lookup, replacing an older entry for the same identifier."""
    entries = load_history()
    entries = [existing for existing in entries if existing.get("identifier") != entry.identifier]
    entries.append(asdict(entry))
    del entries[:-MAX_HISTORY_ENTRIES]
    _write_history(entries)


def append_history_many(entries: list[HistoryEntry]) -> None:
    """Record several lookups in one file rewrite."""
    if not entries:
        return
    merged = load_history()
    identifiers = {entry.identifier for entry in entries}
    merged = [existing for existing in merged if existing.get("identifier") not in identifiers]
    merged.extend(asdict(entry) for entry in entries)
    del merged[:-MAX_HISTORY_ENTRIES]
    _write_history(merged)


def recent_history(limit: int = 10) -> list[dict[str, Any]]:
    """Return the most recent entries, newest first."""
    return list(reversed(load_history()[-limit:]))


def parse_identifiers_payload(raw: str) -> list[tuple[str, str | None]]:
    """Parse a bulk-search JSON file into (identifier, label) pairs.

    Accepted shapes:

      ["id1", "id2"]
      {"identifiers": ["id1", "id2"]}
      [{"identifier": "id1", "label": "Case A"}, ...]
      {"identifiers": [{"identifier": "id1", "label": "Case A"}, ...]}

    Raises ValueError with a readable message for anything else.
    """
    try:
        data = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise ValueError(f"Not valid JSON: {exc}") from exc
    if isinstance(data, dict):
        data = data.get("identifiers")
    if not isinstance(data, list):
        raise ValueError(
            "Expected a JSON array of identifiers, or an object with an "
            "'identifiers' array."
        )
    items: list[tuple[str, str | None]] = []
    for index, item in enumerate(data, start=1):
        if isinstance(item, str):
            items.append((item, None))
        elif isinstance(item, dict) and isinstance(item.get("identifier"), str):
            label = item.get("label")
            items.append((item["identifier"], label if isinstance(label, str) else None))
        else:
            raise ValueError(
                f"Item {index} is not an identifier string or an object with an "
                "'identifier' field."
            )
    if not items:
        raise ValueError("The file contains no identifiers.")
    return items


def build_bulk_json(results: list[BulkResult]) -> dict[str, Any]:
    """Serialize bulk results as a standalone JSON bundle."""
    found = sum(1 for result in results if result["status"] == "ok")
    return {
        "schema_version": BULK_SCHEMA_VERSION,
        "exported_at": datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "total": len(results),
        "found": found,
        "failed": len(results) - found,
        "results": [
            {key: value for key, value in result.items() if key != "text"}
            for result in results
        ],
    }


def build_bulk_text(results: list[BulkResult]) -> str:
    """Render bulk results as a readable text report."""
    sections = []
    for result in results:
        identifier = result["identifier"]
        label = result.get("label")
        heading = f"===== {identifier}" + (f" ({label})" if label else "") + " ====="
        if result["status"] == "ok":
            body = result.get("text") or "(no pathway)"
        else:
            # Bulk search sets `error`; the release linker sets `detail`.
            # Neither is guaranteed, so fall back to the status rather than
            # printing `ERROR: None` for a failure that was explained elsewhere.
            reason = (
                result.get("error") or result.get("detail") or result.get("status")
            )
            body = f"ERROR: {reason}"
        sections.append(heading + "\n" + body)
    found = sum(1 for result in results if result["status"] == "ok")
    failed = len(results) - found
    sections.append(
        f"===== SUMMARY =====\n"
        f"Found: {found}    Failed: {failed}    Total: {len(results)}"
    )
    return "\n\n".join(sections)
