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
import json
import sys
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from typing import Any

import duckdb

import update_check

DEFAULT_ARRESTS_FILE = Path("arrests-latest.parquet")
DEFAULT_DETENTION_FILE = Path("detention-stints-latest.parquet")
DEFAULT_FACILITIES_FILE = Path("facilities-latest.parquet")

# The single in-code source of the application version. `pyproject.toml`,
# `version_info.txt`, and the PyInstaller spec mirror it per
# docs/release-process.md, and `update_check` compares it against the latest
# GitHub release tag.
APP_VERSION = "2.1.1"

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

# Marks a value the reader typed rather than one the publisher supplied. The
# replaced dataset value follows as `original=...` so the edit stays reversible
# without consulting a side file.
MANUAL_EDIT_MARK = "[MANUAL_EDIT"

Overrides = Mapping[str, str]

# Every entry the renderer will accept a manual value for, in the order the
# renderer meets them. Anything else is refused, so a typo in an override name
# cannot silently produce output that looks untouched.
EDITABLE_FIELDS: tuple[str, ...] = (
    "location",
    "landmark",
    "final_program",
    "book_in_aor",
    "classification",
    "case_status",
    "threat_level",
    "final_order",
    "final_order_date",
    "departed",
    "charge",
)

DetentionRow = tuple["datetime | None", "str | None", "datetime | None"]

# Which stay an entry applies to: None means every stay, an int means that one.
Scope = int | None


def override_key(scope: Scope, field: str) -> str:
    """Address one entry: a bare name applies to every stay, `stay-N/…` to one.

    A person may be detained more than once, and a correction that belongs to
    the second detention must not be printed onto the first. Scoping keeps a
    single field name meaningful without inventing per-stay field names.
    """
    return field if scope is None else f"stay-{scope}/{field}"


def edit_scope(key: str) -> tuple[Scope, str]:
    """Split an entry key back into (stay it applies to, field name)."""
    prefix, separator, field = key.partition("/")
    if not separator or not prefix.startswith("stay-"):
        return None, key
    number = prefix.removeprefix("stay-")
    if not number.isdigit() or field not in EDITABLE_FIELDS:
        return None, key
    return int(number), field


def edited_value(
    overrides: Overrides | None,
    key: str,
    dataset_value: str | None,
    scope: Scope = None,
) -> str:
    """Show a manual value when one was entered, otherwise the dataset value.

    An entry scoped to a stay wins over the same field entered for all stays,
    so a reader can set a default once and correct the one stay that differs.

    An edited block stays reversible by reading: the value that was replaced is
    named inline, so nothing has to be remembered outside the text itself.
    """
    if overrides is None:
        return dataset_value or ""
    manual = " ".join(overrides.get(override_key(scope, key), "").split())
    if not manual and scope is not None:
        manual = " ".join(overrides.get(key, "").split())
    if not manual:
        return dataset_value or ""
    return f"{manual} {MANUAL_EDIT_MARK} original={dataset_value or '(empty)'}]"


class LookupError(Exception):
    """Raised when a lookup cannot produce a reliable detention timeline."""


@dataclass(frozen=True)
class ArrestEvent:
    date_time: datetime | None
    date_only: date | None
    location: str | None
    landmark: str | None = None
    state_source: str | None = None
    landmark_field: str = "event_landmark"
    location_original: str | None = None

    @property
    def moment(self) -> datetime | None:
        """Return the arrest instant in UTC, or None if only a date is known."""
        if self.date_time is None:
            return None
        return utc_datetime(self.date_time)

    @property
    def provenance(self) -> list[str]:
        """Return (label, value) pairs naming the arrest's raw source fields."""
        return arrest_provenance(self)


def arrest_provenance(
    arrest: ArrestEvent,
    overrides: Overrides | None = None,
    scope: Scope = None,
) -> list[str]:
    """Return `label: value` pairs naming the arrest's raw source fields.

    The label is the column the value actually came from, which differs
    between releases, because the value is quoted verbatim. Quoting a
    coverage statement like `DALLAS COUNTY GENERAL AREA` under its own name
    is honest; reporting it as the location was not.

    A value containing the `;` pair separator is emitted as one pair per
    segment, reusing the convention the field-list grammar already defines
    for a repeated label. The current release contains three landmark values
    that need this (`PRISON CAP PROGRAM; FEDERAL BOP PRISON CASES`); without
    it a parser would read the second half as a separate field.

    A manual landmark replaces the publisher's own wording and carries the
    `[MANUAL_EDIT original=...]` marker naming what it took the place of.
    """
    pairs = []
    landmark_value = edited_value(overrides, "landmark", arrest.landmark, scope)
    for label, value in (
        (arrest.landmark_field, landmark_value),
        ("apprehension_state_fill_source", arrest.state_source),
    ):
        if not value:
            continue
        for segment in value.split(";") if ";" in value else [value]:
            segment = segment.strip()
            if segment:
                pairs.append(f"{label}: {segment}")
    return pairs


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

    def pairs(self) -> list[tuple[str, str | None]]:
        """Return (label, value) for every last-stint field, empty or not.

        Empty values are returned too, so a manual entry can fill a field the
        release did not publish instead of being dropped for having no original.
        """
        return [
            ("classification", self.classification),
            ("case_status", self.case_status),
            ("threat_level", self.threat_level),
            ("final_order", self.final_order),
            ("final_order_date", self.final_order_date),
            ("departed", self.departed),
            ("charge", self.final_charge),
        ]

    @property
    def present(self) -> list[str]:
        """Return (label, value) pairs for fields that carry a value."""
        return [f"{label}: {value}" for label, value in self.pairs() if value]


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
    missing_fields: tuple[str, ...] = ()

    @property
    def row_count(self) -> int:
        return sum(len(stay.rows) for stay in self.stays)


def override_arrest_location(
    arrest: ArrestEvent, manual_location: str
) -> ArrestEvent:
    """Return an arrest event with an optional presentation-only location.

    The value the publisher supplied is kept on ``location_original`` so the
    renderer can show what was replaced. A second override keeps the first
    original, so repeated edits never lose the dataset value.
    """
    cleaned_location = " ".join(manual_location.split())
    if not cleaned_location:
        return arrest
    return ArrestEvent(
        arrest.date_time,
        arrest.date_only,
        cleaned_location,
        arrest.landmark,
        arrest.state_source,
        arrest.landmark_field,
        arrest.location_original or clean_arrest_location(arrest.location),
    )


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
        pathway.identifier,
        stays,
        unmatched,
        focus_stay_id=pathway.focus_stay_id,
        missing_fields=pathway.missing_fields,
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
    value = (
        value.replace(tzinfo=UTC)
        if value.tzinfo is None
        else value.astimezone(UTC)
    )
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
        return value.replace(tzinfo=UTC)
    return value.astimezone(UTC)


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


def optional_column(name: str, present: set[str], alias: str = "d") -> str:
    """Select a column when the file has it, otherwise select NULL.

    Pass an empty alias for a query that reads a single unaliased source.
    """
    prefix = f"{alias}." if alias else ""
    return f"{prefix}{name}" if name in present else "NULL"


def first_present_column(candidates: Sequence[str], present: set[str]) -> str | None:
    """Return the first candidate column the file actually has.

    Releases rename and drop columns. Naming the whole lineage lets one build
    read every release instead of only the one it was written against.
    """
    for name in candidates:
        if name in present:
            return name
    return None


def release_stamp(value: object) -> str:
    """`2025-10-07 22:20:00` for a timestamp, dropping any tz offset suffix."""
    if value is None:
        return ""
    text = str(value)
    for suffix in ("+00:00:00", "+00:00", " UTC"):
        if text.endswith(suffix):
            return text[: -len(suffix)]
    return text


def release_date(value: object) -> str:
    """`2025-10-07` for a timestamp, empty when the file has no value."""
    if value is None:
        return ""
    return str(value)[:10]


@dataclass(frozen=True)
class ReleaseInfo:
    """What can be stated about the release a lookup was read from."""

    tag: str
    data_up_to: str
    fingerprint: str
    coverage: str

    @property
    def summary(self) -> str:
        """The watermark: which generation, and how far the data runs.

        Deliberately one short line. Row counts, per-file spans and the local
        retrieval date stay on `fingerprint` and `coverage` for the manifest,
        where they are evidence rather than reading text.
        """
        return f"dataset-{self.tag} data-up-to={self.data_up_to}"


def _aggregate_row(
    connection: duckdb.DuckDBPyConnection, sql: str, params: Sequence[str]
) -> tuple[Any, ...]:
    """One aggregate row, or `(None, None, 0)` when there is nothing to report.

    An empty file still deserves a watermark rather than a crash: the release
    exists, it simply holds no rows yet.
    """
    row = connection.execute(sql, list(params)).fetchone()
    return row if row is not None else (None, None, 0)


def dataset_release(
    connection: duckdb.DuckDBPyConnection,
    arrests_path: Path,
    detention_path: Path,
) -> ReleaseInfo:
    """Read off the release a pair of files belongs to.

    The tag separates the two published generations: the current one renamed
    `apprehension_site_landmark` to `event_landmark`, the earlier one still
    carries the old name. `data_up_to` is the newest record anywhere in the
    data, which is the date readers ask for first. The fingerprint carries row
    counts, the newest timestamp in each file, and the local retrieval date, so
    a block of text can still be attributed to a release after it is separated
    from its source. The coverage states the span each file actually holds.
    """
    present = available_columns(connection, arrests_path)
    tag = "NEW" if "event_landmark" in present else "OLD"
    stint_start, stint_end, stints = _aggregate_row(
        connection,
        "SELECT min(book_in_date_time), max(book_in_date_time), count(*) "
        "FROM read_parquet(?)",
        [str(detention_path)],
    )
    arrest_start, arrest_end, arrests = _aggregate_row(
        connection,
        "SELECT min(apprehension_date_time), max(apprehension_date_time), count(*) "
        "FROM read_parquet(?)",
        [str(arrests_path)],
    )
    retrieved = datetime.fromtimestamp(detention_path.stat().st_mtime, tz=UTC)
    fingerprint = (
        f"stints={stints}@{release_stamp(stint_end)} "
        f"arrests={arrests}@{release_stamp(arrest_end)} "
        f"retrieved={retrieved.date().isoformat()}"
    )
    coverage = (
        f"stints={release_date(stint_start)}..{release_date(stint_end)} "
        f"arrests={release_date(arrest_start)}..{release_date(arrest_end)}"
    )
    data_up_to = max(
        release_date(stint_end), release_date(arrest_end)
    )
    return ReleaseInfo(tag, data_up_to, fingerprint, coverage)


def release_watermark(arrests_path: Path, detention_path: Path) -> str:
    """`dataset-NEW data-up-to=2026-08-06`, or "" when the files cannot say.

    Best effort by design: a watermark problem must never fail a lookup the
    data itself can answer, so an unreadable release reports nothing rather
    than nothing loudly.
    """
    connection = duckdb.connect(database=":memory:")
    try:
        connection.execute("SET TimeZone = 'UTC'")
        return dataset_release(connection, arrests_path, detention_path).summary
    except (duckdb.Error, OSError):
        return ""
    finally:
        connection.close()


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


def landmark_placeholder_test(column: str) -> str:
    """SQL that is true when a landmark records an area, not a place.

    ICE's landmark field is frequently a coverage statement rather than an
    address: `DALLAS COUNTY GENERAL AREA`, `MTG GENERAL AREA, NON-SPECIFIC`.
    Those values carry a third of the current arrest rows and two fifths of the
    previous release's, so passing one through as "the arrest location" asserts a
    precision the source does not have. They are excluded from the location and
    printed verbatim, under their own field name, instead.
    """
    value = f"coalesce({column}, '')"
    return (
        f"(upper({value}) LIKE '%GENERAL AREA%' "
        f"OR upper({value}) LIKE '%NON-SPECIFIC%' "
        f"OR upper({value}) LIKE '%NONSPECIFIC%')"
    )


# The arrest city field arrived in the 2026-09 release as two columns: a raw
# hand-entered value and the data publisher's own normalized form. The raw
# column carries 18,512 distinct spellings of what are far fewer real places —
# `CONROE` and `Conroe` alone are 29,680 rows — and a small number of counties
# and boroughs entered in the city slot. The normalized column is the one to
# read, so it takes precedence; the raw value is never silently substituted.
ARREST_CITY_LINEAGE = ("apprehension_city_common_case", "apprehension_city_original")

# `event_landmark` replaced `apprehension_site_landmark` in the 2026-09 release.
ARREST_LANDMARK_LINEAGE = ("event_landmark", "apprehension_site_landmark")

# Fields the renderer would quote if the dataset carried them. When a release
# drops one, the output omits it; `missing_source_fields` names the omission so a
# reader is never left believing the source was silent rather than absent.
EXPECTED_DETENTION_FIELDS = (
    "book_in_aor",
    "detainee_classification",
    "detention_release_reason",
    "final_program",
    "case_status",
    "case_threat_level",
    "final_order_yes_no",
    "final_order_date",
    "departed_date",
    "final_charge",
)


def missing_source_fields(present: set[str]) -> list[str]:
    """Return the renderer-relevant fields this dataset copy does not carry.

    Absence of a record is not evidence of absence in reality, and neither is
    absence of a field. When a release stops publishing a field, every pathway
    silently loses it; naming the loss keeps the gap visible to whoever reads the
    result.
    """
    return [name for name in EXPECTED_DETENTION_FIELDS if name not in present]


def arrest_location_expr(present: set[str], alias: str = "arrest_location") -> str:
    """The arrest's location, ordered by decreasing precision and trust.

    The city is preferred because it is the publisher's normalized value, and
    the state follows it. A landmark is used only when there is no city to
    report, and only when it names a place rather than a coverage area — a real
    address outranks a bare state. The AOR is the last resort because it is an
    organizational region, not a location.

    Pass an empty alias to get the bare expression, for a caller that needs to
    compose it rather than select it as a named column.
    """
    city = first_present_column(ARREST_CITY_LINEAGE, present)
    landmark = first_present_column(ARREST_LANDMARK_LINEAGE, present)
    city_part = (
        f"nullif(trim({city}), '')" if city else "NULL"
    )
    state_part = (
        "nullif(trim(apprehension_state_filled_in), '')"
        if "apprehension_state_filled_in" in present
        else "NULL"
    )
    landmark_part = (
        f"CASE WHEN {landmark_placeholder_test(landmark)} THEN NULL "
        f"ELSE nullif(trim({landmark}), '') END"
        if landmark
        else "NULL"
    )
    aor_part = (
        "nullif(trim(apprehension_aor), '')"
        if "apprehension_aor" in present
        else "NULL"
    )
    return (
        "coalesce("
        f"CASE WHEN {city_part} IS NOT NULL "
        f"THEN concat_ws(', ', {city_part}, {state_part}) END, "
        f"{landmark_part}, "
        f"{state_part}, "
        f"{aor_part}"
        ")" + (f" AS {alias}" if alias else "")
    )


def arrest_provenance_expr(present: set[str]) -> tuple[str, str, str]:
    """SQL selecting the arrest's raw source fields for the provenance line.

    Returns the landmark, state, and the publisher's own account of how that
    state was derived. Naming these fields rather than folding them into a
    location string is what keeps a placeholder landmark from reading as a place.
    """
    landmark = first_present_column(ARREST_LANDMARK_LINEAGE, present)
    return (
        f"nullif(trim({landmark}), '')" if landmark else "NULL",
        (
            "nullif(trim(apprehension_state_filled_in), '')"
            if "apprehension_state_filled_in" in present
            else "NULL"
        ),
        (
            "nullif(trim(apprehension_state_fill_source), '')"
            if "apprehension_state_fill_source" in present
            else "NULL"
        ),
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
        arrest_present = available_columns(connection, arrests_file)
        landmark_expr, state_expr, state_source_expr = arrest_provenance_expr(
            arrest_present
        )
        landmark_field = (
            first_present_column(ARREST_LANDMARK_LINEAGE, arrest_present)
            or "event_landmark"
        )
        arrest_rows = connection.execute(
            f"""
            SELECT apprehension_date_time,
                   apprehension_date,
                   {arrest_location_expr(arrest_present)},
                   {landmark_expr},
                   {state_source_expr}
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

    arrests = [
        ArrestEvent(
            row[0], row[1], row[2], row[3], row[4], landmark_field
        )
        for row in arrest_rows
    ]

    if not arrest_rows and not stint_rows:
        raise LookupError(
            "Identifier not found in the arrests or detention datasets. It may "
            "be invalid or may have been excluded from the locally filtered data."
        )

    stays = group_stays(stint_rows)
    stays, unmatched = pair_arrests_with_stays(arrests, stays)
    focus = focus_stay_id(stays, identifier_input.strip()) if suffix else None
    return Pathway(
        identifier,
        stays,
        unmatched,
        focus_stay_id=focus,
        missing_fields=tuple(missing_source_fields(present)),
    )


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


def summary_from_row(row: Sequence[Any]) -> StaySummary:
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


def group_stays(stint_rows: Sequence[tuple[Any, ...]]) -> list[Stay]:
    """Group stint rows into stays by stay_ID, oldest stay first."""
    order: list[str | None] = []
    grouped: dict[str | None, list[tuple[Any, ...]]] = {}
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
        chosen = max(candidates, key=lambda arrest: arrest.moment or datetime.min)
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
        if (
            book_in is not None
            and book_out is not None
            and exceeds_gap(utc_datetime(book_out), utc_datetime(book_in))
        ):
            warnings.append("book-out is before book-in")
        if (
            book_in is not None
            and previous_book_out is not None
            and exceeds_gap(utc_datetime(book_in), utc_datetime(previous_book_out))
        ):
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

        if (
            book_out is not None
            and (
                previous_book_out is None
                or utc_datetime(book_out) > utc_datetime(previous_book_out)
            )
        ):
            previous_book_out = book_out
    return " -> ".join(segments)


def format_full_timeline(
    arrest: ArrestEvent | None,
    rows: Sequence[DetentionRow],
    overrides: Overrides | None = None,
    scope: Scope = None,
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

    dataset_location = clean_arrest_location(arrest.location)
    shown_location = dataset_location
    if arrest.location_original and arrest.location_original != dataset_location:
        shown_location = (
            f"{dataset_location} {MANUAL_EDIT_MARK}"
            f" original={arrest.location_original}]"
        )
    if overrides is not None:
        candidate = edited_value(
            overrides, "location", dataset_location, scope
        )
        # `edited_value` names what it replaced, so it differs from the dataset
        # value exactly when an override applied — leaving a display-level
        # `location_original` marker intact when none did.
        if candidate != dataset_location:
            shown_location = candidate
    arrest_segment = f"{arrest_date}, {shown_location}"
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


def format_arrest_fields(
    stay: Stay, overrides: Overrides | None = None, scope: Scope = None
) -> str:
    """Render the arrest's raw source fields, when it has one.

    The location in the timeline is the publisher's normalized city and state.
    This line carries the verbatim landmark beside it, so a coverage statement
    such as `DALLAS COUNTY GENERAL AREA` is visible as the source's own wording
    rather than passed off as a street address.
    """
    if stay.arrest is None:
        return ""
    pairs = arrest_provenance(stay.arrest, overrides, scope)
    return "[arrest — " + "; ".join(pairs) + "]" if pairs else ""


def format_stay_fields(
    stay: Stay, overrides: Overrides | None = None, scope: Scope = None
) -> str:
    """Render the named stint fields for a stay, when the data carries them.

    Every label names a field in the source data rather than narrating an event.
    `final_program` is the program of record for the case; it does not state
    which agency made an apprehension, and the source data has no field that
    does. The first stint's values are always named so a program such as ERO or
    Border Patrol is visible even when an arrest record exists; if later stints
    disagree, the differing values are listed so a reader is not told only the
    opening one. The last stint's record state is quoted verbatim.

    The `[stint fields — ...]` line stays on source values even when an entry
    is overridden, so the variance a reader would otherwise miss is not edited
    away along with the first stint's value.
    """
    lines = []
    first = []
    program = edited_value(
        overrides, "final_program", stay.entry_program, scope
    )
    if program:
        first.append(f"final_program: {program}")
    aor = edited_value(overrides, "book_in_aor", stay.entry_aor, scope)
    if aor:
        first.append(f"book_in_aor: {aor}")
    if first:
        lines.append("[first stint — " + "; ".join(first) + "]")

    differing = []
    for value in differing_values(stay.entry_program, stay.all_programs):
        differing.append(f"final_program: {value}")
    for value in differing_values(stay.entry_aor, stay.all_aors):
        differing.append(f"book_in_aor: {value}")
    if differing:
        lines.append("[stint fields — " + "; ".join(differing) + "]")

    if stay.summary is not None:
        summary_pairs = [
            f"{key}: {shown}"
            for key, value in stay.summary.pairs()
            if (shown := edited_value(overrides, key, value, scope))
        ]
        if summary_pairs:
            lines.append("[last stint — " + "; ".join(summary_pairs) + "]")
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


def format_pathway(pathway: Pathway, overrides: Overrides | None = None) -> str:
    """Render every stay, keeping separate detentions visibly separate.

    When the lookup was scoped by a `stay_ID` or `stint_ID` suffix, that stay is
    rendered in full and the person's other stays are collapsed to one-line
    context so the answer matches the question asked.

    `overrides` carries manual entries keyed by `EDITABLE_FIELDS`, optionally
    scoped to one stay as `stay-N/<field>` so a correction aimed at a person's
    second detention never appears in their first. Each entry replaces the
    source value in the rendered text and names what it replaced, so an edited
    result is always distinguishable from a recorded one.
    """
    if not pathway.stays:
        return "\n".join(
            format_full_timeline(arrest, [], overrides)
            for arrest in pathway.arrests_without_stay
        )

    total = len(pathway.stays)
    parts: list[str] = []
    if pathway.focus_stay_id is not None:
        for number, stay in enumerate(pathway.stays, start=1):
            if stay.stay_id == pathway.focus_stay_id:
                parts.append(
                    f"[STAY {number} of {total}] "
                    + format_full_timeline(stay.arrest, stay.rows, overrides, number)
                )
                parts.extend(stay_detail_lines(stay, overrides, number))
            else:
                parts.append(format_context_stay(stay))
    else:
        previous: Stay | None = None
        for number, stay in enumerate(pathway.stays, start=1):
            if previous is not None:
                parts.append(format_gap(previous, stay))
            rendered = format_full_timeline(stay.arrest, stay.rows, overrides, number)
            # A lone stay needs no label; numbering only helps when stays must be
            # told apart.
            parts.append(
                rendered if total == 1 else f"[STAY {number} of {total}] {rendered}"
            )
            parts.extend(stay_detail_lines(stay, overrides, number))
            previous = stay

    for arrest in pathway.arrests_without_stay:
        parts.append(
            "[ARREST WITH NO RECORDED DETENTION] "
            + format_full_timeline(arrest, [], overrides)
        )
    return "\n".join(parts)


def stay_detail_lines(
    stay: Stay, overrides: Overrides | None = None, scope: Scope = None
) -> list[str]:
    """The field lines that follow one stay's timeline, in documented order."""
    return [
        line
        for line in (
            format_arrest_fields(stay, overrides, scope),
            format_stay_fields(stay, overrides, scope),
        )
        if line
    ]


def iso_utc(value: datetime | None) -> str | None:
    """Serialize a datetime as a UTC ISO-8601 string, or None."""
    if value is None:
        return None
    return utc_datetime(value).strftime("%Y-%m-%dT%H:%M:%SZ")


def _summary_value(value: Any) -> str | None:
    """Coerce a raw summary field into a JSON-safe string."""
    if value is None:
        return None
    if isinstance(value, datetime):
        return iso_utc(value)
    if isinstance(value, date):
        return value.isoformat()
    return str(value)


def _arrest_event_to_dict(arrest: ArrestEvent) -> dict[str, Any]:
    return {
        "date_time": iso_utc(arrest.moment),
        "date_only": arrest.date_only.isoformat() if arrest.date_only else None,
        "location": clean_arrest_location(arrest.location),
        "landmark": arrest.landmark,
        "apprehension_state_fill_source": arrest.state_source,
    }


def pathway_to_dict(pathway: Pathway) -> dict[str, Any]:
    """Serialize a Pathway as a plain dict ready for ``json.dumps``.

    The text renderer is a reading surface; this is the machine surface. Every
    datetime is normalized to UTC and emitted as an ISO-8601 string so the
    export stands alone without the source Parquet files.
    """
    stays = []
    for stay in pathway.stays:
        stays.append(
            {
                "stay_id": stay.stay_id,
                "arrest": (
                    _arrest_event_to_dict(stay.arrest)
                    if stay.arrest is not None
                    else None
                ),
                "entry_program": stay.entry_program,
                "entry_aor": stay.entry_aor,
                "release_reason": stay.release_reason,
                "stint_ids": list(stay.stint_ids),
                "program_variance": stay.program_variance,
                "aor_variance": stay.aor_variance,
                "start": iso_utc(stay.start),
                "end": iso_utc(stay.end),
                "summary": (
                    {
                        label: value
                        for label, value in (
                            ("classification", _summary_value(stay.summary.classification)),
                            ("case_status", _summary_value(stay.summary.case_status)),
                            ("threat_level", _summary_value(stay.summary.threat_level)),
                            ("final_order", _summary_value(stay.summary.final_order)),
                            ("final_order_date", _summary_value(stay.summary.final_order_date)),
                            ("departed", _summary_value(stay.summary.departed)),
                            ("final_charge", _summary_value(stay.summary.final_charge)),
                        )
                        if value
                    }
                    if stay.summary is not None
                    else None
                ),
                "rows": [
                    {
                        "book_in": iso_utc(row[0]),
                        "facility": (
                            None
                            if not row[1] or not row[1].strip()
                            else " ".join(row[1].split())
                        ),
                        "book_out": iso_utc(row[2]),
                    }
                    for row in stay.rows
                ],
            }
        )
    return {
        "schema_version": 2,
        "exported_at": datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "identifier": pathway.identifier,
        "focus_stay_id": pathway.focus_stay_id,
        "source_fields_absent": list(pathway.missing_fields),
        "totals": {
            "stays": len(pathway.stays),
            "detention_rows": pathway.row_count,
            "arrests_without_stay": len(pathway.arrests_without_stay),
        },
        "stays": stays,
        "arrests_without_stay": [
            _arrest_event_to_dict(arrest) for arrest in pathway.arrests_without_stay
        ],
    }


def export_pathway_json(pathway: Pathway, path: Path) -> None:
    """Write a Pathway as pretty JSON with a trailing newline."""
    payload = json.dumps(pathway_to_dict(pathway), indent=2)
    path.write_text(payload + "\n", encoding="utf-8")


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
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
    parser.add_argument(
        "--export-json",
        type=Path,
        help="also write the pathway as structured JSON to this path",
    )
    parser.add_argument(
        "--check-update",
        action="store_true",
        help=(
            "check GitHub Releases for a newer version of this application "
            "and exit without looking up an identifier"
        ),
    )
    args = parser.parse_args(argv)
    if args.identifier is None and not args.check_update:
        args.identifier = input("Enter the unique identifier to search for: ").strip()
    return args


def main(argv: Sequence[str] | None = None) -> int:
    args = parse_args(argv)
    if args.check_update:
        # The update check is the one lookup-adjacent command that may touch
        # the network, and only because the person asked for it by flag.
        try:
            status = update_check.check_for_update(APP_VERSION)
        except update_check.UpdateCheckError as exc:
            print(f"Update check failed: {exc}", file=sys.stderr)
            return 1
        print(update_check.format_report(status))
        return 0
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

    watermark = release_watermark(args.arrests_file, args.detention_file)
    if watermark:
        print(watermark)
    print(f"Identifier: {pathway.identifier}")
    if pathway.focus_stay_id is not None:
        print(f"Scoped to stay: {pathway.focus_stay_id}")
    print(f"Stays: {len(pathway.stays)}   Detention rows: {pathway.row_count}")
    if pathway.arrests_without_stay:
        print(f"Arrests with no recorded detention: {len(pathway.arrests_without_stay)}")
    if pathway.missing_fields:
        print(
            "Not in this dataset release: "
            + ", ".join(pathway.missing_fields)
            + "  (upstream stopped publishing these; values are absent, not unknown)"
        )
    print(format_pathway(pathway))
    if args.export_json is not None:
        try:
            export_pathway_json(pathway, args.export_json)
        except OSError as exc:
            print(f"Failed to write JSON export: {exc}", file=sys.stderr)
            return 1
        print(f"Exported JSON: {args.export_json}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
