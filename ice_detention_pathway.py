"""Reconstruct one anonymized person's recorded path through ICE detention.

A person may be detained more than once. The source data records each
continuous period in custody as a stay (`stay_ID`) and each facility placement
within a stay as a stint. This module groups stints into stays, pairs each stay
with the arrest that opened it when such a record exists, and renders one
pathway per stay so separate detentions are never merged into a false
chronology.

Neither an arrest record nor a detention record is required on its own. People
who entered ICE custody without an ICE arrest — a Border Patrol transfer, for
example — have detention rows and no arrest row, and are reported normally.
"""

from __future__ import annotations

import argparse
import sys
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Sequence

import duckdb


DEFAULT_ARRESTS_FILE = Path("arrests-latest.parquet")
DEFAULT_DETENTION_FILE = Path("detention-stints-latest.parquet")
DEFAULT_FACILITIES_FILE = Path("facilities-latest.parquet")

# An arrest is treated as opening a stay that begins at or after it. Book-in
# times occasionally precede the arrest by a little; this tolerance absorbs that
# without letting an arrest claim a stay from an earlier detention.
ARREST_TO_BOOK_IN_TOLERANCE = timedelta(hours=12)

# Chronology is only reported as a discrepancy once the contradiction reaches a
# full day. Sub-day inversions are overwhelmingly the order in which paperwork
# was filed rather than an impossible sequence: of 9,058 stays whose arrest is
# timestamped after their own first book-in, 7,867 are under 24 hours, and the
# distribution flattens immediately past that point. Flagging them all made the
# label appear on roughly a third of lookups, which taught readers to ignore it.
#
# The same threshold governs every chronology check so one rule explains them
# all. Nothing is dropped or altered: sub-day inversions remain visible in the
# printed timestamps and in the source rows.
DISCREPANCY_MINIMUM_GAP = timedelta(days=1)

NO_ARREST_NOTE = "NO ARREST RECORD IN THIS DATASET"

DetentionRow = tuple["datetime | None", "str | None", "datetime | None"]


class LookupError(Exception):
    """Raised when a lookup cannot produce a reliable detention timeline."""


@dataclass(frozen=True)
class ArrestEvent:
    date_time: datetime | None
    date_only: date | None
    location: str | None

    @property
    def moment(self) -> datetime | None:
        """Return the arrest instant in UTC, or None if only a date is known."""
        if self.date_time is None:
            return None
        return utc_datetime(self.date_time)


@dataclass(frozen=True)
class StaySummary:
    """Values taken from the stay's last recorded stint, named by field.

    Every value is quoted from the detention record. These are not outcomes a
    reader can assume are final: a missing `departed` means only that no later
    record exists in the source data.
    """

    classification: str | None = None
    case_status: str | None = None
    threat_level: str | None = None
    final_order: str | None = None
    final_order_date: str | None = None
    departed: str | None = None
    final_charge: str | None = None

    @property
    def present(self) -> list[str]:
        """Return (label, value) pairs for fields that carry a value."""
        pairs = []
        for label, value in (
            ("classification", self.classification),
            ("case_status", self.case_status),
            ("threat_level", self.threat_level),
            ("final_order", self.final_order),
            ("final_order_date", self.final_order_date),
            ("departed", self.departed),
            ("charge", self.final_charge),
        ):
            if value:
                pairs.append(f"{label}: {value}")
        return pairs


@dataclass(frozen=True)
class Stay:
    """One continuous period in ICE custody, with the stints inside it."""

    stay_id: str | None
    arrest: ArrestEvent | None
    rows: list[DetentionRow]
    entry_program: str | None = None
    entry_aor: str | None = None
    release_reason: str | None = None
    stint_ids: tuple[str | None, ...] = ()
    all_programs: tuple[str, ...] = ()
    all_aors: tuple[str, ...] = ()
    summary: StaySummary | None = None

    @property
    def program_variance(self) -> str | None:
        distinct = [program for program in self.all_programs if program]
        if len(distinct) > 1:
            return "; ".join(distinct)
        return None

    @property
    def aor_variance(self) -> str | None:
        distinct = [aor for aor in self.all_aors if aor]
        if len(distinct) > 1:
            return "; ".join(distinct)
        return None

    @property
    def start(self) -> datetime | None:
        moments = [utc_datetime(row[0]) for row in self.rows if row[0] is not None]
        return min(moments) if moments else None

    @property
    def end(self) -> datetime | None:
        """Return the last book-out, or None while any stint is still open."""
        if any(row[2] is None for row in self.rows):
            return None
        moments = [utc_datetime(row[2]) for row in self.rows if row[2] is not None]
        return max(moments) if moments else None

@dataclass(frozen=True)
class Pathway:
    """Everything recorded for one identifier, grouped into stays."""

    identifier: str
    stays: list[Stay]
    arrests_without_stay: list[ArrestEvent]
    focus_stay_id: str | None = None

    @property
    def row_count(self) -> int:
        return sum(len(stay.rows) for stay in self.stays)


def override_arrest_location(
    arrest: ArrestEvent, manual_location: str
) -> ArrestEvent:
    """Return an arrest event with an optional presentation-only location."""
    cleaned_location = " ".join(manual_location.split())
    if not cleaned_location:
        return arrest
    return ArrestEvent(arrest.date_time, arrest.date_only, cleaned_location)


def override_pathway_arrest_location(
    pathway: Pathway, manual_location: str
) -> Pathway:
    """Apply a presentation-only location to every arrest in a pathway."""
    if not " ".join(manual_location.split()):
        return pathway
    stays = [
        Stay(
            stay.stay_id,
            override_arrest_location(stay.arrest, manual_location)
            if stay.arrest is not None
            else None,
            stay.rows,
            stay.entry_program,
            stay.entry_aor,
            stay.release_reason,
            stay.stint_ids,
            stay.all_programs,
            stay.all_aors,
            stay.summary,
        )
        for stay in pathway.stays
    ]
    unmatched = [
        override_arrest_location(arrest, manual_location)
        for arrest in pathway.arrests_without_stay
    ]
    return Pathway(
        pathway.identifier, stays, unmatched, focus_stay_id=pathway.focus_stay_id
    )


def normalize_identifier(value: str) -> str:
    """Return the base identifier before an optional underscore suffix."""
    identifier, _ = parse_identifier(value)
    return identifier


def format_timestamp(value: datetime | None, event_label: str) -> str:
    """Format a timestamp in UTC without adding an event label."""
    if value is None:
        if event_label == "book-out":
            return "UNKNOWN - CURRENTLY HELD (?)"
        return "UNKNOWN UTC"
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    else:
        value = value.astimezone(timezone.utc)
    return f"{value:%Y-%m-%d %H:%M:%S} UTC"


def clean_location(value: str | None) -> str:
    if value is None or not value.strip():
        return "UNKNOWN DETENTION CENTER"
    return " ".join(value.split())


def clean_arrest_location(value: str | None) -> str:
    if value is None or not value.strip():
        return "UNKNOWN ARREST LOCATION"
    return " ".join(value.split())


def utc_datetime(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def exceeds_gap(earlier: datetime, later: datetime) -> bool:
    """Report whether `earlier` precedes `later` by at least a full day.

    Used by every chronology check so that a sub-day inversion — almost always
    the order paperwork was filed — is not labelled the same as an impossible
    sequence spanning months.
    """
    return later - earlier >= DISCREPANCY_MINIMUM_GAP


def duration_text(start: datetime | None, end: datetime | None) -> str:
    """Describe the span between two instants in whole days and hours."""
    if start is None or end is None:
        return "an unknown period"
    span = utc_datetime(end) - utc_datetime(start)
    if span < timedelta(0):
        return f"a negative span of {abs(span.days)} days"
    if span.days:
        return f"{span.days} days"
    hours = span.seconds // 3600
    if hours:
        return f"{hours} hours"
    return f"{max(span.seconds // 60, 1)} minutes"


def validate_file(path: Path, label: str) -> None:
    if not path.is_file():
        raise LookupError(f"The {label} Parquet file was not found: {path} -- make sure it's in the same folder as this program!")


def available_columns(connection: duckdb.DuckDBPyConnection, path: Path) -> set[str]:
    """Return the column names present in a Parquet file."""
    rows = connection.execute(
        "DESCRIBE SELECT * FROM read_parquet(?)", [str(path)]
    ).fetchall()
    return {row[0] for row in rows}


def optional_column(name: str, present: set[str]) -> str:
    """Select a column when the file has it, otherwise select NULL."""
    return f"d.{name}" if name in present else "NULL"


def facility_name_expr() -> str:
    """Canonical facility name, falling back to the raw detention-table name."""
    return (
        "coalesce("
        "nullif(trim(f.name), ''), "
        "nullif(trim(d.detention_facility), ''), "
        "'UNKNOWN DETENTION CENTER')"
    )


def facility_display_expr(duplicate_marker: str) -> str:
    """Facility name plus code, with an optional duplicate-row marker."""
    return (
        "concat("
        "CASE WHEN d.detention_facility_code IS NOT NULL THEN "
        f"concat({facility_name_expr()}, ':', d.detention_facility_code) "
        f"ELSE {facility_name_expr()} END, "
        f"{duplicate_marker})"
    )


def detention_select(present: set[str]) -> str:
    """The column list for one detention stint row.

    Shared by the per-identifier lookup and the bulk verifier so the two can
    never disagree about which columns a stint carries or how a facility is
    rendered. Indexes in the returned row match `group_stays`.
    """
    duplicate_marker = (
        "CASE WHEN d.duplicate_drop_row THEN ' [FLAGGED DUPLICATE ROW]' ELSE '' END"
        if "duplicate_drop_row" in present
        else "''"
    )
    return f"""
           {optional_column('stay_ID', present)} AS stay_id,
           d.book_in_date_time,
           {facility_display_expr(duplicate_marker)} AS facility_display,
           d.book_out_date_time,
           {optional_column('detention_release_reason', present)} AS release_reason,
           {optional_column('final_program', present)} AS entry_program,
           {optional_column('book_in_aor', present)} AS entry_aor,
           {optional_column('stint_ID', present)} AS stint_id,
           {optional_column('detainee_classification', present)} AS classification,
           {optional_column('case_status', present)} AS case_status,
           {optional_column('case_threat_level', present)} AS threat_level,
           {optional_column('final_order_yes_no', present)} AS final_order,
           {optional_column('final_order_date', present)} AS final_order_date,
           {optional_column('departed_date', present)} AS departed,
           {optional_column('final_charge', present)} AS final_charge"""


def detention_sources() -> str:
    """The FROM/JOIN that supplies detention stints and canonical facility names."""
    return """
            FROM read_parquet(?) d
            LEFT JOIN (
                SELECT detention_facility_code, max(name) AS name
                FROM read_parquet(?)
                WHERE detention_facility_code IS NOT NULL
                GROUP BY detention_facility_code
            ) f USING (detention_facility_code)"""


def detention_order_by(present: set[str]) -> str:
    row_order = "d.row_original NULLS LAST" if "row_original" in present else "facility_display"
    return (
        "d.book_in_date_time NULLS LAST, "
        "d.book_out_date_time NULLS LAST, "
        f"facility_display NULLS LAST, {row_order}"
    )


def arrest_location_expr() -> str:
    """The best available arrest location, in order of decreasing precision."""
    return (
        "coalesce("
        "nullif(trim(apprehension_site_landmark), ''), "
        "nullif(trim(apprehension_state_filled_in), ''), "
        "nullif(trim(apprehension_aor), '')"
        ") AS arrest_location"
    )


def parse_identifier(value: str) -> tuple[str, str | None]:
    """Split an identifier into its base and any `_`-suffix.

    `stay_ID` values carry a `base_YYYY-MM-DD HH:MM:SS` suffix and `stint_ID`
    values a `base_YYYY-MM-DD HH:MM:SS_code` suffix. The base alone is what the
    tables key on; the suffix identifies one stay.
    """
    stripped = value.strip()
    base, sep, suffix = stripped.partition("_")
    if not base:
        raise LookupError("The identifier cannot be empty.")
    return base, (suffix if sep else None)


def fetch_pathway(
    identifier_input: str,
    arrests_file: Path = DEFAULT_ARRESTS_FILE,
    detention_file: Path = DEFAULT_DETENTION_FILE,
    facilities_file: Path = DEFAULT_FACILITIES_FILE,
) -> Pathway:
    """Return every recorded stay for one identifier, oldest first."""
    identifier, suffix = parse_identifier(identifier_input)
    validate_file(arrests_file, "arrests")
    validate_file(detention_file, "detention")
    validate_file(facilities_file, "facilities")

    connection = duckdb.connect(database=":memory:")
    connection.execute("SET TimeZone = 'UTC'")
    try:
        arrest_rows = connection.execute(
            f"""
            SELECT apprehension_date_time,
                   apprehension_date,
                   {arrest_location_expr()}
            FROM read_parquet(?)
            WHERE unique_identifier = ?
            ORDER BY apprehension_date_time NULLS LAST,
                     apprehension_date NULLS LAST
            """,
            [str(arrests_file), identifier],
        ).fetchall()

        present = available_columns(connection, detention_file)
        stint_rows = connection.execute(
            f"""
            SELECT {detention_select(present)}
            {detention_sources()}
            WHERE d.unique_identifier = ?
            ORDER BY {detention_order_by(present)}
            """,
            [str(detention_file), str(facilities_file), identifier],
        ).fetchall()
    finally:
        connection.close()

    arrests = [ArrestEvent(row[0], row[1], row[2]) for row in arrest_rows]

    if not arrest_rows and not stint_rows:
        raise LookupError(
            "Identifier not found in the arrests or detention datasets. It may "
            "be invalid or may have been excluded from the locally filtered data."
        )

    stays = group_stays(stint_rows)
    stays, unmatched = pair_arrests_with_stays(arrests, stays)
    focus = focus_stay_id(stays, identifier_input.strip()) if suffix else None
    return Pathway(identifier, stays, unmatched, focus_stay_id=focus)


def focus_stay_id(stays: Sequence[Stay], full_value: str) -> str | None:
    """Return the stay_ID that a suffixed input names, if it exists.

    `full_value` is the identifier exactly as passed in, including any
    `_YYYY-MM-DD HH:MM:SS` stay suffix or `_..._code` stint suffix, because a
    stay_ID is `base_suffix` and only the complete value identifies one stay.
    """
    for stay in stays:
        if stay.stay_id == full_value:
            return stay.stay_id
        for stint_id in stay.stint_ids:
            if stint_id == full_value:
                return stay.stay_id
    return None


def summary_from_row(row: Sequence) -> StaySummary:
    """Collect the named fields of one stint for the stay-level summary."""
    return StaySummary(
        classification=row[8],
        case_status=row[9],
        threat_level=row[10],
        final_order=row[11],
        final_order_date=row[12],
        departed=row[13],
        final_charge=row[14],
    )


def group_stays(stint_rows: Sequence[tuple]) -> list[Stay]:
    """Group stint rows into stays by stay_ID, oldest stay first."""
    order: list[str | None] = []
    grouped: dict[str | None, list[tuple]] = {}
    for row in stint_rows:
        stay_id = row[0]
        if stay_id not in grouped:
            grouped[stay_id] = []
            order.append(stay_id)
        grouped[stay_id].append(row)

    stays = []
    for stay_id in order:
        members = grouped[stay_id]
        last = members[-1]
        stays.append(
            Stay(
                stay_id=stay_id,
                arrest=None,
                rows=[(row[1], row[2], row[3]) for row in members],
                entry_program=members[0][5],
                entry_aor=members[0][6],
                release_reason=last[4],
                stint_ids=tuple(row[7] for row in members),
                all_programs=tuple(
                    dict.fromkeys(row[5] for row in members if row[5])
                ),
                all_aors=tuple(
                    dict.fromkeys(row[6] for row in members if row[6])
                ),
                summary=summary_from_row(last),
            )
        )
    return sorted(stays, key=lambda stay: (stay.start is None, stay.start or 0))


def pair_arrests_with_stays(
    arrests: Sequence[ArrestEvent], stays: Sequence[Stay]
) -> tuple[list[Stay], list[ArrestEvent]]:
    """Attach each arrest to the stay it opened.

    Each stay takes the latest unclaimed arrest that precedes its first
    book-in, because that is the arrest that produced the booking. Choosing the
    nearest rather than the earliest matters for people arrested more than
    once: an arrest from a year earlier must not claim a recent stay.

    Arrests that open no recorded stay, and stays that no arrest explains, are
    both reported rather than being forced together.

    The same time check governs a lone arrest with a lone stay. A single arrest
    logged years after a stay's first book-in did not open that stay, and pairing
    it unconditionally made the tool print a misleading DISCREPANCY and imply a
    causal link the data does not support.

    Claims are consumed in stay order: once a stay has taken the latest arrest
    before its book-in, an *older* arrest can never open a later stay. A stale
    arrest left over after an earlier stay already claimed a newer one is left
    unmatched rather than being dumped on an unrelated later stay.
    """
    remaining = list(arrests)
    paired: dict[int, ArrestEvent] = {}
    previous_claim: datetime | None = None
    for index, stay in enumerate(stays):
        if stay.start is None:
            continue
        latest_allowed = stay.start + ARREST_TO_BOOK_IN_TOLERANCE
        candidates = [
            arrest
            for arrest in remaining
            if arrest.moment is not None
            and arrest.moment <= latest_allowed
            and (previous_claim is None or arrest.moment > previous_claim)
        ]
        if not candidates:
            continue
        chosen = max(candidates, key=lambda arrest: arrest.moment)
        paired[index] = chosen
        remaining.remove(chosen)
        previous_claim = chosen.moment

    unmatched = remaining

    updated = [
        Stay(
            stay.stay_id,
            paired.get(index),
            stay.rows,
            stay.entry_program,
            stay.entry_aor,
            stay.release_reason,
            stay.stint_ids,
            stay.all_programs,
            stay.all_aors,
            stay.summary,
        )
        for index, stay in enumerate(stays)
    ]
    return updated, unmatched


def format_timeline(rows: Sequence[DetentionRow]) -> str:
    segments = []
    previous_book_out: datetime | None = None
    for book_in, location, book_out in rows:
        warnings = []
        if book_in is not None and book_out is not None:
            if exceeds_gap(utc_datetime(book_out), utc_datetime(book_in)):
                warnings.append("book-out is before book-in")
        if book_in is not None and previous_book_out is not None:
            if exceeds_gap(utc_datetime(book_in), utc_datetime(previous_book_out)):
                warnings.append("detention begins before previous book-out")

        segment = (
            f"[Book-in: {format_timestamp(book_in, 'book-in')}]"
            f"[Book-out: {format_timestamp(book_out, 'book-out')}]"
            f"[Facility: {clean_location(location)}]"
        )
        if warnings:
            warning_text = "; ".join(warnings)
            segment = f"(DISCREPANCY: {warning_text}) {segment}"
        segments.append(segment)

        if book_out is not None:
            if previous_book_out is None or utc_datetime(book_out) > utc_datetime(
                previous_book_out
            ):
                previous_book_out = book_out
    return " -> ".join(segments)


def format_full_timeline(
    arrest: ArrestEvent | None,
    rows: Sequence[DetentionRow],
) -> str:
    """Render one stay: its opening event, then every stint in order.

    Chronology warnings compare the arrest only against the stints supplied
    here, so an unrelated earlier stay can never trigger a false discrepancy.
    """
    if arrest is None:
        return f"{NO_ARREST_NOTE} -> {format_timeline(rows)}"

    warnings = []
    first_book_in = next((row[0] for row in rows if row[0] is not None), None)
    if first_book_in is not None:
        if arrest.date_time is not None:
            if exceeds_gap(utc_datetime(first_book_in), utc_datetime(arrest.date_time)):
                warnings.append("arrest date is after first detention book-in")
        elif arrest.date_only is not None:
            book_in_date = utc_datetime(first_book_in).date()
            if arrest.date_only - book_in_date >= DISCREPANCY_MINIMUM_GAP:
                warnings.append("arrest date is after first detention book-in")

    if arrest.date_time is not None:
        arrest_date = format_timestamp(arrest.date_time, "arrest")
    elif arrest.date_only is not None:
        arrest_date = f"{arrest.date_only:%Y-%m-%d}"
    else:
        arrest_date = "UNKNOWN ARREST DATE"

    arrest_segment = f"{arrest_date}, {clean_arrest_location(arrest.location)}"
    if warnings:
        arrest_segment = (
            f"(DISCREPANCY: {'; '.join(warnings)}) {arrest_segment}"
        )
    if not rows:
        return f"{arrest_segment} -> NO DETENTION RECORD IN THIS DATASET"
    return f"{arrest_segment} -> {format_timeline(rows)}"


def format_gap(previous: Stay, current: Stay) -> str:
    """Describe the break between two stays so they read as separate."""
    if previous.end is None or current.start is None:
        return "=== SEPARATE STAY; GAP NOT MEASURABLE ==="
    span = duration_text(previous.end, current.start)
    if previous.release_reason:
        return (
            f"=== RELEASED ({' '.join(previous.release_reason.split())}); "
            f"NOT IN ICE CUSTODY FOR {span} ==="
        )
    return f"=== NOT IN ICE CUSTODY FOR {span} ==="


def format_stay_fields(stay: Stay) -> str:
    """Render the named stint fields for a stay, when the data carries them.

    Every label names a field in the source data rather than narrating an event.
    `final_program` is the program of record for the case; it does not state
    which agency made an apprehension, and the source data has no field that
    does. The first stint's values are always named so a program such as ERO or
    Border Patrol is visible even when an arrest record exists; if later stints
    disagree, the differing values are listed so a reader is not told only the
    opening one. The last stint's record state is quoted verbatim.
    """
    lines = []
    first = []
    if stay.entry_program:
        first.append(f"final_program: {stay.entry_program}")
    if stay.entry_aor:
        first.append(f"book_in_aor: {stay.entry_aor}")
    if first:
        lines.append("[first stint — " + "; ".join(first) + "]")

    differing = []
    for value in differing_values(stay.entry_program, stay.all_programs):
        differing.append(f"final_program: {value}")
    for value in differing_values(stay.entry_aor, stay.all_aors):
        differing.append(f"book_in_aor: {value}")
    if differing:
        lines.append("[stint fields — " + "; ".join(differing) + "]")

    if stay.summary is not None and stay.summary.present:
        lines.append("[last stint — " + "; ".join(stay.summary.present) + "]")
    return "\n".join(lines)


def differing_values(entry: str | None, all_values: tuple[str, ...]) -> list[str]:
    """Distinct non-empty values in `all_values` other than the first stint's."""
    seen: list[str] = []
    for value in all_values:
        if value and value != entry and value not in seen:
            seen.append(value)
    return seen


def format_context_stay(stay: Stay) -> str:
    """One line for a stay that is context to the requested stay."""
    start = format_timestamp(stay.start, "book-in") if stay.start else "UNKNOWN"
    end = format_timestamp(stay.end, "book-out") if stay.end else "UNKNOWN"
    label = clean_location(stay.rows[0][1]) if stay.rows else "UNKNOWN DETENTION CENTER"
    return f"  [CONTEXT — another stay for this person: {start} -> {end}, {label}]"


def format_pathway(pathway: Pathway) -> str:
    """Render every stay, keeping separate detentions visibly separate.

    When the lookup was scoped by a `stay_ID` or `stint_ID` suffix, that stay is
    rendered in full and the person's other stays are collapsed to one-line
    context so the answer matches the question asked.
    """
    if not pathway.stays:
        return "\n".join(
            format_full_timeline(arrest, []) for arrest in pathway.arrests_without_stay
        )

    total = len(pathway.stays)
    parts: list[str] = []
    if pathway.focus_stay_id is not None:
        for number, stay in enumerate(pathway.stays, start=1):
            if stay.stay_id == pathway.focus_stay_id:
                parts.append(
                    f"[STAY {number} of {total}] "
                    f"{format_full_timeline(stay.arrest, stay.rows)}"
                )
                fields = format_stay_fields(stay)
                if fields:
                    parts.append(fields)
            else:
                parts.append(format_context_stay(stay))
    else:
        previous: Stay | None = None
        for number, stay in enumerate(pathway.stays, start=1):
            if previous is not None:
                parts.append(format_gap(previous, stay))
            rendered = format_full_timeline(stay.arrest, stay.rows)
            # A lone stay needs no label; numbering only helps when stays must be
            # told apart.
            parts.append(
                rendered if total == 1 else f"[STAY {number} of {total}] {rendered}"
            )
            fields = format_stay_fields(stay)
            if fields:
                parts.append(fields)
            previous = stay

    for arrest in pathway.arrests_without_stay:
        parts.append(
            f"[ARREST WITH NO RECORDED DETENTION] {format_full_timeline(arrest, [])}"
        )
    return "\n".join(parts)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        prog="ice-detention-pathway",
        description=(
            "Reconstruct a chronological ICE detention pathway from a Deportation "
            "Data Project identifier."
        )
    )
    parser.add_argument(
        "identifier",
        nargs="?",
        help="unique_identifier, stay_ID, or stint_ID (suffixes after '_' are ignored)",
    )
    parser.add_argument(
        "--arrests-file",
        type=Path,
        default=DEFAULT_ARRESTS_FILE,
        help=f"arrests Parquet path (default: {DEFAULT_ARRESTS_FILE})",
    )
    parser.add_argument(
        "--detention-file",
        type=Path,
        default=DEFAULT_DETENTION_FILE,
        help=f"detention Parquet path (default: {DEFAULT_DETENTION_FILE})",
    )
    parser.add_argument(
        "--facilities-file",
        type=Path,
        default=DEFAULT_FACILITIES_FILE,
        help=f"facilities Parquet path (default: {DEFAULT_FACILITIES_FILE})",
    )
    args = parser.parse_args()
    if args.identifier is None:
        args.identifier = input("Enter the unique identifier to search for: ").strip()
    return args


def main() -> int:
    args = parse_args()
    try:
        pathway = fetch_pathway(
            args.identifier,
            arrests_file=args.arrests_file,
            detention_file=args.detention_file,
            facilities_file=args.facilities_file,
        )
    except (LookupError, duckdb.Error) as exc:
        print(f"Lookup failed: {exc}", file=sys.stderr)
        return 1

    print(f"Identifier: {pathway.identifier}")
    if pathway.focus_stay_id is not None:
        print(f"Scoped to stay: {pathway.focus_stay_id}")
    print(f"Stays: {len(pathway.stays)}   Detention rows: {pathway.row_count}")
    if pathway.arrests_without_stay:
        print(f"Arrests with no recorded detention: {len(pathway.arrests_without_stay)}")
    print(format_pathway(pathway))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
