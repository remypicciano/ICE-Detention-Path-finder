"""Look up a single arrest identifier and print its detention timeline."""

from __future__ import annotations

import argparse
import sys
from dataclasses import dataclass
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Sequence

import duckdb


DEFAULT_ARRESTS_FILE = Path("arrests-latest.parquet")
DEFAULT_DETENTION_FILE = Path("detention-stints-latest.parquet")


class LookupError(Exception):
    """Raised when a lookup cannot produce a reliable detention timeline."""


@dataclass(frozen=True)
class ArrestEvent:
    date_time: datetime | None
    date_only: date | None
    location: str | None


def normalize_identifier(value: str) -> str:
    """Return the base identifier before an optional underscore suffix."""
    identifier = value.strip().split("_", maxsplit=1)[0]
    if not identifier:
        raise LookupError("The identifier cannot be empty.")
    return identifier


def format_timestamp(value: datetime | None, event_label: str) -> str:
    """Format a timestamp in UTC without relying on the machine timezone."""
    if value is None:
        if event_label == "book-out":
            return "UNKNOWN - CURRENTLY HELD (?) (book-out)"
        return f"UNKNOWN UTC ({event_label})"
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    else:
        value = value.astimezone(timezone.utc)
    return f"{value:%Y-%m-%d %H:%M:%S} UTC ({event_label})"


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


def validate_file(path: Path, label: str) -> None:
    if not path.is_file():
        raise LookupError(f"The {label} Parquet file was not found: {path} -- make sure it's in the same folder as this program!")


def fetch_timeline(
    identifier_input: str,
    arrests_file: Path = DEFAULT_ARRESTS_FILE,
    detention_file: Path = DEFAULT_DETENTION_FILE,
) -> tuple[
    str,
    ArrestEvent,
    list[tuple[datetime | None, str | None, datetime | None]],
]:
    """Validate one arrest row and return all matching detention rows."""
    identifier = normalize_identifier(identifier_input)
    validate_file(arrests_file, "arrests")
    validate_file(detention_file, "detention")

    connection = duckdb.connect(database=":memory:")
    connection.execute("SET TimeZone = 'UTC'")
    try:
        arrest_rows = connection.execute(
            """
            SELECT apprehension_date_time,
                   apprehension_date,
                   coalesce(
                       nullif(trim(apprehension_site_landmark), ''),
                       nullif(trim(apprehension_state_filled_in), ''),
                       nullif(trim(apprehension_aor), '')
                   ) AS arrest_location
            FROM read_parquet(?)
            WHERE unique_identifier = ?
            """,
            [str(arrests_file), identifier],
        ).fetchall()

        if not arrest_rows:
            raise LookupError(
                "Identifier not found in the current NYC-AOR arrests dataset. "
                "It may be invalid or may have been removed by the NYC-only filter."
            )
        if len(arrest_rows) != 1:
            raise LookupError(
                f"Expected exactly one arrest row, but found {len(arrest_rows)}. "
                "No detention lookup was performed."
            )

        arrest = ArrestEvent(*arrest_rows[0])

        rows = connection.execute(
            """
            SELECT book_in_date_time, detention_facility, book_out_date_time
            FROM read_parquet(?)
            WHERE unique_identifier = ?
            ORDER BY book_in_date_time NULLS LAST,
                     book_out_date_time NULLS LAST,
                     detention_facility NULLS LAST,
                     row_original NULLS LAST
            """,
            [str(detention_file), identifier],
        ).fetchall()
    finally:
        connection.close()

    if not rows:
        raise LookupError("The arrest row matched, but no detention rows were found.")
    return identifier, arrest, rows


def format_timeline(
    rows: Sequence[tuple[datetime | None, str | None, datetime | None]],
) -> str:
    segments = []
    previous_book_out: datetime | None = None
    for book_in, location, book_out in rows:
        warnings = []
        if book_in is not None and book_out is not None:
            if utc_datetime(book_out) < utc_datetime(book_in):
                warnings.append("book-out is before book-in")
        if book_in is not None and previous_book_out is not None:
            if utc_datetime(book_in) < utc_datetime(previous_book_out):
                warnings.append("detention begins before previous book-out")

        segment = ", ".join(
            (
                format_timestamp(book_in, "book-in"),
                clean_location(location),
                format_timestamp(book_out, "book-out"),
            )
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
    arrest: ArrestEvent,
    rows: Sequence[tuple[datetime | None, str | None, datetime | None]],
) -> str:
    warnings = []
    first_book_in = next((row[0] for row in rows if row[0] is not None), None)
    if first_book_in is not None:
        if arrest.date_time is not None:
            if utc_datetime(arrest.date_time) > utc_datetime(first_book_in):
                warnings.append("arrest date is after first detention book-in")
        elif arrest.date_only is not None:
            if arrest.date_only > utc_datetime(first_book_in).date():
                warnings.append("arrest date is after first detention book-in")

    if arrest.date_time is not None:
        arrest_date = format_timestamp(arrest.date_time, "arrest")
    elif arrest.date_only is not None:
        arrest_date = f"{arrest.date_only:%Y-%m-%d} (arrest)"
    else:
        arrest_date = "UNKNOWN (arrest)"

    arrest_segment = f"{arrest_date}, {clean_arrest_location(arrest.location)}"
    if warnings:
        arrest_segment = (
            f"(DISCREPANCY: {'; '.join(warnings)}) {arrest_segment}"
        )
    return f"{arrest_segment} -> {format_timeline(rows)}"


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Require one arrest row for an identifier, then print every matching "
            "detention row as a chronological timeline."
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
    args = parser.parse_args()
    if args.identifier is None:
        args.identifier = input("Enter the unique identifier to search for: ").strip()
    return args


def main() -> int:
    args = parse_args()
    try:
        identifier, arrest, rows = fetch_timeline(
            args.identifier,
            arrests_file=args.arrests_file,
            detention_file=args.detention_file,
        )
    except (LookupError, duckdb.Error) as exc:
        print(f"Lookup failed: {exc}", file=sys.stderr)
        return 1

    print(f"Identifier: {identifier}")
    print(f"Detention rows: {len(rows)}")
    print(format_full_timeline(arrest, rows))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
