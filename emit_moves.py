#!/usr/bin/env python3
"""Render new-release identifiers in the MOVES text format used by saved outputs.

The delimiter contract is exact, because these lines are re-parsed by hand and by
other tools:

    dataset-<NEW|OLD> data-up-to=<newest date the files hold>
    edited-manual=<comma-separated field names, only when one was typed>
    dob -<birth year>
    age-<age at apprehension>
    case_title-<case caption>
    district-<federal court district>
    MOVES: <arrest> <location> -> [Book-in: ...][Book-out: ...], <Facility>:<CODE>
    <identifier>
    //

Dataset marking
---------------
Every block opens with one watermark naming its generation and how far its data
runs, for example ``dataset-NEW data-up-to=2026-08-06``. A block without it
cannot be attributed to a release, so it is never omitted. The row counts, newest
timestamps and per-file date span behind that watermark are written to
``--manifest`` instead of repeating them on every block.

Manual edits
------------
``--edits EDITS.json`` overrides individual fields:

    {"<identifier>": {"case_title": "Marca Lema v Soto", "district": "NJ"}}

Overridable fields are ``dob``, ``age``, ``case_title``, ``district``, ``location``
(the arrest location inside MOVES, so every segment stays intact) and ``moves``
(whole-line replacement, applied last).

Each edited field is marked inline with ``[MANUAL_EDIT]`` and, where short, the
value it replaced. A block carrying any edit also carries an ``edited-manual=``
line naming them, so a reader never has to scan for markers to know whether a
block is source data. The full original is always written to ``--manifest``,
which is enough to reconstruct the untouched dataset output byte for byte:

    district-NJ [MANUAL_EDIT original=Southern District of New York]

An open final stint renders as ``UNKNOWN - CURRENTLY HELD (?)``. Nothing is
invented: fields the publisher does not supply stay empty until edited.

Usage:

    python emit_moves.py IDS.txt --new-dir NEW_DIR -o moves.txt \\
        --edits edits.json --manifest moves.manifest.json

IDS.txt holds one identifier per line, or saved output to read identifiers from.
"""

from __future__ import annotations

import argparse
import json
import sys
from dataclasses import dataclass, field
from pathlib import Path

import duckdb

from ice_detention_pathway import MANUAL_EDIT_MARK, dataset_release, release_stamp

ARRESTS_FILE = "arrests-latest.parquet"
DETENTION_FILE = "detention-stints-latest.parquet"
FACILITIES_FILE = "facilities-latest.parquet"

MOVES_PREFIX = "MOVES: "
SEGMENT_SEP = " -> "
SEPARATOR = "//"
MAX_INLINE_ORIGINAL = 120

# Rendered in this order. `moves` is assembled last and may override the line.
FIELD_ORDER = ("dob", "age", "case_title", "district")


@dataclass
class Record:
    """One identifier's block, holding original dataset values before any edit."""

    uid: str
    dob: str = ""
    age: str = ""
    case_title: str = ""
    district: str = ""
    location: str = ""
    arrest_stamp: str = ""
    segments: list[str] = field(default_factory=list)

    def head(self, location: str) -> str:
        return f"{MOVES_PREFIX}{self.arrest_stamp} {location}".rstrip()

    def moves_line(self, location: str | None = None) -> str:
        """Full MOVES line, rebuilt from `location` when one is supplied."""
        line = self.head(self.location if location is None else location)
        if not self.segments:
            return line + SEGMENT_SEP + "(NO DETENTION RECORD IN THIS DATASET)"
        return line + SEGMENT_SEP + SEGMENT_SEP.join(self.segments)

    def originals(self) -> dict[str, str]:
        values = {name: getattr(self, name) for name in FIELD_ORDER}
        values["location"] = self.location
        values["moves"] = self.moves_line()
        return values


def _clean(value: object) -> str:
    return "" if value is None else str(value).strip()


def _int(value: object) -> str:
    """Age and similar counts render bare, without a trailing `.0`."""
    if value is None:
        return ""
    try:
        return str(int(float(value)))
    except (TypeError, ValueError):
        return ""


def _fmt_ts(value: object) -> str:
    """`2025-10-07 22:20:00 UTC` for a timestamp, `UNKNOWN - CURRENTLY HELD (?)` when open."""
    if value is None:
        return "UNKNOWN - CURRENTLY HELD (?)"
    return f"{release_stamp(value)} UTC"


def _first(columns: set[str], names: tuple[str, ...], fallback: str = "NULL") -> str:
    """First candidate column that this release actually publishes."""
    for name in names:
        if name in columns:
            return name
    return fallback


def _describe(
    connection: duckdb.DuckDBPyConnection, path: Path
) -> set[str]:
    return {
        row[0]
        for row in connection.execute(
            "DESCRIBE SELECT * FROM read_parquet(?)", [str(path)]
        ).fetchall()
    }


def collect(
    connection: duckdb.DuckDBPyConnection, new_dir: Path, ids: list[str]
) -> list[Record]:
    """Read dataset values for the identifiers. Raises on any id absent from the release.

    Column selection adapts to the release, so the same code reads both the 2025-08
    files (``apprehension_site_landmark``, no court district) and the current ones
    (``event_landmark``, ``federal_court_district_of_confinement``).
    """
    if not ids:
        return []
    arrest_file = new_dir / ARRESTS_FILE
    detention_file = new_dir / DETENTION_FILE
    facilities = new_dir / FACILITIES_FILE
    for path in (arrest_file, detention_file):
        if not path.is_file():
            raise SystemExit(f"Missing release file: {path}")

    arrest_cols = _describe(connection, arrest_file)
    stint_cols = _describe(connection, detention_file)

    facility_names: dict[str, str] = {}
    facility_districts: dict[str, str] = {}
    if facilities.is_file():
        facility_cols = _describe(connection, facilities)
        district_col = _first(facility_cols, ("federal_court_district_of_confinement",))
        for row in connection.execute(
            f"SELECT detention_facility_code, name, {district_col} AS district "
            "FROM read_parquet(?) WHERE detention_facility_code IS NOT NULL",
            [str(facilities)],
        ).fetchall():
            code, name, district = row
            if name is not None:
                facility_names[str(code)] = str(name)
            if district is not None:
                facility_districts[str(code)] = str(district)

    city_expr = _first(arrest_cols, ("apprehension_city_original", "apprehension_city_common_case"))
    state_expr = _first(
        arrest_cols, ("apprehension_state_original", "apprehension_state_filled_in")
    )
    landmark_expr = _first(
        arrest_cols, ("event_landmark", "apprehension_site_landmark")
    )
    arrest_age_expr = _first(
        arrest_cols, ("age_at_apprehension_approximate",), "NULL::DOUBLE"
    )
    arrest_birth_expr = _first(arrest_cols, ("birth_year",), "NULL")

    placeholders = ", ".join("?" for _ in ids)
    arrest_by_id = {
        row[0]: row
        for row in connection.execute(
            f"""
            SELECT unique_identifier, apprehension_date_time,
                   {city_expr} AS city, {state_expr} AS state,
                   {landmark_expr} AS landmark, {arrest_age_expr} AS age,
                   {arrest_birth_expr} AS birth_year
            FROM read_parquet(?) WHERE unique_identifier IN ({placeholders})
            """,
            [str(arrest_file), *ids],
        ).fetchall()
    }

    stint_age_expr = _first(
        stint_cols, ("age_at_stint_book_in_approximate",), "NULL::INTEGER"
    )
    district_expr = _first(
        stint_cols, ("federal_court_district_of_confinement",)
    )
    stint_birth_expr = _first(stint_cols, ("birth_year",), "NULL")

    stints_by_id: dict[str, list[tuple]] = {}
    for row in connection.execute(
        f"""
        SELECT unique_identifier, detention_facility, detention_facility_code,
               book_in_date_time, book_out_date_time,
               {stint_age_expr} AS age, {district_expr} AS district,
               {stint_birth_expr} AS birth_year
        FROM read_parquet(?)
        WHERE unique_identifier IN ({placeholders})
        ORDER BY unique_identifier, book_in_date_time NULLS LAST,
                 book_out_date_time NULLS LAST, detention_facility_code
        """,
        [str(detention_file), *ids],
    ).fetchall():
        stints_by_id.setdefault(row[0], []).append(row)

    missing = [uid for uid in ids if uid not in stints_by_id]
    if missing:
        raise SystemExit(
            f"Not present in this release ({len(missing)}): " + ", ".join(missing)
        )

    records: list[Record] = []
    for uid in ids:
        stints = stints_by_id[uid]
        arrest = arrest_by_id.get(uid)
        record = Record(uid=uid)
        # Birth year comes from the arrest row when there is one, otherwise
        # from the detention rows themselves. It is never taken from the court
        # district: a person with no arrest record still has a birth year, and
        # a district in the dob line would be a fabricated fact.
        record.dob = _clean(arrest[6] if arrest else None) or _clean(stints[0][7])
        record.age = _int(arrest[5] if arrest else None) or _int(stints[0][5])
        record.district = next((_clean(row[6]) for row in stints if _clean(row[6])), "")

        for _, facility, code, book_in, book_out, _, _, _ in stints:
            display = facility_names.get(_clean(code)) or _clean(facility)
            record.segments.append(
                f"[Book-in: {_fmt_ts(book_in)}][Book-out: {_fmt_ts(book_out)}], "
                f"{display}:{_clean(code)}"
            )

        # Older releases publish no per-stint court district; the facility knows its own.
        if not record.district:
            for _, _, code, _, _, _, _, _ in stints:
                from_facility = facility_districts.get(_clean(code))
                if from_facility:
                    record.district = from_facility
                    break

        if arrest is None:
            record.arrest_stamp = "(NO ARREST RECORD IN THIS DATASET)"
            record.location = ""
        else:
            _, when, city, state, landmark, _, _ = arrest
            record.arrest_stamp = release_stamp(when)
            city_text, state_text, landmark_text = _clean(city), _clean(state), _clean(landmark)
            if city_text:
                location = ", ".join(p for p in (city_text, state_text) if p)
            else:
                # Older releases carry no arrest city, only a landmark.
                location = landmark_text or state_text or "(LOCATION NOT RECORDED)"
            record.location = location
        records.append(record)
    return records


def load_edits(path: Path | None) -> dict[str, dict[str, str]]:
    if path is None:
        return {}
    if not path.is_file():
        raise SystemExit(f"Edits file not found: {path}")
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise SystemExit("Edits file must be a JSON object keyed by identifier.")
    edits: dict[str, dict[str, str]] = {}
    for uid, fields in payload.items():
        if not isinstance(fields, dict):
            raise SystemExit(f"Edits for {uid} must be a JSON object of field: value.")
        for name, value in fields.items():
            if name not in (*FIELD_ORDER, "location", "moves"):
                raise SystemExit(
                    f"Unknown editable field {name!r} for {uid}. "
                    f"Allowed: {', '.join((*FIELD_ORDER, 'location', 'moves'))}."
                )
            edits[uid] = edits.get(uid, {})
            edits[uid][name] = "" if value is None else str(value)
    return edits


def _mark(line: str, original: str) -> str:
    """Flag an edited line, keeping the replaced value inline when it is short."""
    if len(original) > MAX_INLINE_ORIGINAL:
        return f"{line} {MANUAL_EDIT_MARK}]"
    return f"{line} {MANUAL_EDIT_MARK} original={original or '(empty)'}]"


def render(
    records: list[Record], summary: str, edits: dict[str, dict[str, str]],
) -> tuple[str, dict[str, dict[str, dict[str, object]]]]:
    """Build the text output and the manifest that makes every edit reversible."""
    blocks: list[str] = []
    manifest: dict[str, dict[str, dict[str, object]]] = {}

    for record in records:
        original = record.originals()
        applied = edits.get(record.uid, {})
        value = dict(original)
        value.update(applied)
        # Rebuild MOVES from an edited location, but never clobber a real
        # whole-line override (one that actually differs from the dataset line).
        if (
            value["location"] != original["location"]
            and value["moves"] == original["moves"]
        ):
            value["moves"] = record.moves_line(value["location"])
        # `edited` is derived from the result, so it cannot disagree with the output.
        edited = {name for name in value if value[name] != original[name]}

        lines = [summary]
        if edited:
            names = [
                name
                for name in (*FIELD_ORDER, "location", "moves")
                if name in edited
            ]
            lines.append("edited-manual=" + ",".join(names))
        raw = {
            "dob": f"dob -{value['dob']}",
            "age": f"age-{value['age']}",
            "case_title": f"case_title-{value['case_title']}",
            "district": f"district-{value['district']}",
        }
        for name in FIELD_ORDER:
            lines.append(_mark(raw[name], original[name]) if name in edited else raw[name])

        # An explicit whole-line override wins; otherwise a location edit marks
        # just its own token, leaving every segment date and facility untouched.
        if "moves" in applied and value["moves"] != original["moves"]:
            lines.append(_mark(value["moves"], original["moves"]))
        elif "location" in edited:
            marked = f"{MOVES_PREFIX}{record.arrest_stamp} " + _mark(
                value["location"], original["location"]
            )
            line = marked.rstrip()
            if record.segments:
                line += SEGMENT_SEP + SEGMENT_SEP.join(record.segments)
            lines.append(line)
        else:
            lines.append(value["moves"])

        lines.append(record.uid)
        blocks.append("\n".join(lines))

        manifest[record.uid] = {
            name: {
                "value": value[name],
                "original": original[name],
                "edited": name in edited,
            }
            for name in value
        }

    return ("\n" + SEPARATOR + "\n").join(blocks) + "\n", manifest


def read_ids(path: Path) -> list[str]:
    """Identifiers from a plain list, a JSON list, or saved output text."""
    text = path.read_text(encoding="utf-8", errors="replace").strip()
    if not text:
        return []
    if text[0] in "[{":
        payload = json.loads(text)
        if isinstance(payload, dict):
            payload = payload.get("identifiers") or payload.get("results") or []
        return [
            str(
                (item.get("identifier") or item.get("new_identifier"))
                if isinstance(item, dict)
                else item
            )
            for item in payload
        ]

    ids: list[str] = []
    for line in text.splitlines():
        line = line.strip()
        if not line or line == SEPARATOR:
            continue
        if line.startswith("Identifier:"):
            ids.append(line.split(":", 1)[1].strip())
        elif len(line) == 40 and all(ch in "0123456789abcdef" for ch in line):
            ids.append(line)
    return [uid for uid in ids if uid]


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Render MOVES blocks marked with their source dataset.",
        epilog=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("ids", type=Path, help="list of identifiers or saved output")
    parser.add_argument("--new-dir", type=Path, required=True, help="release directory")
    parser.add_argument(
        "--dataset-tag",
        default=None,
        help="value for the dataset- line (default: derived from the release)",
    )
    parser.add_argument("--edits", type=Path, help="JSON of manual per-field overrides")
    parser.add_argument("--manifest", type=Path, help="write original/edited values here")
    parser.add_argument("-o", "--out", type=Path, help="write here instead of stdout")
    args = parser.parse_args()

    ids = read_ids(args.ids)
    edits = load_edits(args.edits)
    unknown = sorted(set(edits) - set(ids))
    if unknown:
        raise SystemExit(
            f"Edits reference {len(unknown)} identifier(s) not in the input list: "
            + ", ".join(unknown)
        )

    connection = duckdb.connect(database=":memory:")
    connection.execute("SET TimeZone = 'UTC'")
    records = collect(connection, args.new_dir, ids)
    release = dataset_release(
        connection, args.new_dir / ARRESTS_FILE, args.new_dir / DETENTION_FILE
    )
    connection.close()

    dataset_tag = args.dataset_tag or release.tag
    summary = f"dataset-{dataset_tag} data-up-to={release.data_up_to}"
    output, manifest = render(records, summary, edits)

    if args.out:
        args.out.parent.mkdir(parents=True, exist_ok=True)
        args.out.write_text(output, encoding="utf-8")
        print(f"{len(records)} identifiers written to {args.out}")
    else:
        sys.stdout.write(output)

    if args.manifest:
        args.manifest.parent.mkdir(parents=True, exist_ok=True)
        payload = {
            "schema": 1,
            "dataset": dataset_tag,
            "fingerprint": release.fingerprint,
            "coverage": release.coverage,
            "identifiers": manifest,
        }
        args.manifest.write_text(
            json.dumps(payload, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
        )
        print(f"manifest written to {args.manifest}")

    edited_count = sum(
        1 for fields in manifest.values() if any(f["edited"] for f in fields.values())
    )
    if edited_count:
        print(f"{edited_count} identifier(s) carry manual edits")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
