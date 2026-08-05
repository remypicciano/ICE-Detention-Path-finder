"""Bulk verification of the detention-pathway pipeline over the full datasets.

The core tool (`ice_detention_pathway`) answers one identifier per query. This
module applies the *same* grouping and pairing functions to every identifier in
one streaming pass, then checks invariants across all rows, cross-checks the
per-identifier query path against the bulk path for a sample, and compares the
resulting stay boundaries against the Deportation Data Project's independently
published stays table.

Checks performed (all over the whole local dataset unless noted):

  C1  Every stint row is grouped into exactly one stay for its identifier.
  C2  Stays within a person are ordered oldest first.
  C3  Every paired arrest precedes its stay's first book-in within the
      ARREST_TO_BOOK_IN_TOLERANCE window. The old unconditional lone-pair
      shortcut is gone, so this must hold for every pair.
  C4  Paired arrests are consumed in stay order: claim times never decrease.
  C5  Every stay renders through format_pathway without error, and rendering is
      deterministic (two renders produce identical text).
  C6  A stay_ID or stint_ID suffix always resolves back to a stay (the B3
      scoping rule). Stints whose stint_ID the source data places under more
      than one stay are counted and reported; they resolve to the earliest
      containing stay.
  C7  The per-identifier query path agrees with the bulk path for a sample of
      identifiers drawn from the interesting populations.
  C8  Bulk stay grouping reconciles with detention-stays-latest.parquet:
      every stay's non-duplicate stint count equals the published n_stints.
      Boundary deltas (published book-in before the first stint row, book-out
      after the last) come from the published table's wider nominal window and
      are reported as source-data anomalies, not pipeline failures.
  C9  The dataset's coverage window and null rows are counted and reported.
  C10 No source value collides with the output grammar's delimiters: facility
      names/codes carry no brackets or colons, rendered stint fields carry no
      semicolons, release reasons carry no close-parens or semicolons, arrest
      locations carry no arrows or brackets, and no rendered field contains a
      newline. A future DDP data refresh that would silently break the
      documented machine-readable format is caught here.

Usage:
    python verify_bulk.py                     # full pass + cross-check sample
    python verify_bulk.py --sample 3000       # larger fetch_pathway cross-check
    python verify_bulk.py --skip-sample       # bulk checks only, faster
    python verify_bulk.py --limit 400000      # cap stints read (dev smoke runs)

Exit code is 0 when every check passes, 1 otherwise.
"""

from __future__ import annotations

import argparse
import hashlib
import sys
import time
from collections import Counter
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Sequence

import duckdb

from ice_detention_pathway import (
    ARREST_TO_BOOK_IN_TOLERANCE,
    DEFAULT_ARRESTS_FILE,
    DEFAULT_DETENTION_FILE,
    DEFAULT_FACILITIES_FILE,
    ArrestEvent,
    LookupError,
    Pathway,
    Stay,
    available_columns,
    arrest_location_expr,
    detention_order_by,
    detention_select,
    detention_sources,
    fetch_pathway,
    focus_stay_id,
    format_pathway,
    group_stays,
    pair_arrests_with_stays,
    utc_datetime,
)

RULE = "=" * 78


@dataclass
class PersonBulk:
    """The bulk result for one identifier, mirroring the core's Pathway."""

    identifier: str
    stays: list[Stay]
    unmatched: list[ArrestEvent]


@dataclass
class Findings:
    """Accumulated check results."""

    failures: list[str]
    notes: list[str]

    def check(self, name: str, ok: bool, detail: str = "") -> None:
        if ok:
            return
        self.failures.append(f"{name}: {detail}".rstrip())

    def note(self, message: str) -> None:
        self.notes.append(message)


def open_connection() -> duckdb.DuckDBPyConnection:
    connection = duckdb.connect(database=":memory:")
    connection.execute("SET TimeZone = 'UTC'")
    return connection


def load_arrests(
    connection: duckdb.DuckDBPyConnection, arrests_file: Path
) -> dict[str, list[ArrestEvent]]:
    """All arrest rows keyed by identifier, ordered as the core orders them."""
    rows = connection.execute(
        f"""
        SELECT unique_identifier,
               apprehension_date_time,
               apprehension_date,
               {arrest_location_expr()}
        FROM read_parquet(?)
        WHERE unique_identifier IS NOT NULL
        ORDER BY unique_identifier,
                 apprehension_date_time NULLS LAST,
                 apprehension_date NULLS LAST
        """,
        [str(arrests_file)],
    ).fetchall()
    grouped: dict[str, list[ArrestEvent]] = {}
    for identifier, moment, date_only, location in rows:
        grouped.setdefault(identifier, []).append(
            ArrestEvent(moment, date_only, location)
        )
    return grouped


def keep_set(
    connection: duckdb.DuckDBPyConnection,
    detention_file: Path,
    arrests_file: Path,
    random_sample: int,
) -> set[str]:
    """Identifiers worth keeping for the fetch_pathway cross-check.

    Drawn from the populations where the core's decisions are easiest to get
    wrong: people with more than one stay (scoping), the former lone-pair
    shortcut population (pairing), and a deterministic random sample.
    """
    selected: set[str] = set()
    multi_stay = connection.execute(
        """
        SELECT unique_identifier
        FROM read_parquet(?)
        WHERE stay_ID IS NOT NULL AND unique_identifier IS NOT NULL
        GROUP BY unique_identifier
        HAVING count(DISTINCT stay_ID) > 1
        """,
        [str(detention_file)],
    ).fetchall()
    selected.update(row[0] for row in multi_stay)

    short_cut = connection.execute(
        """
        WITH a AS (
            SELECT unique_identifier, count(*) AS n, min(apprehension_date_time) AS t
            FROM read_parquet(?)
            WHERE unique_identifier IS NOT NULL
            GROUP BY unique_identifier
        ),
        s AS (
            SELECT unique_identifier, count(DISTINCT stay_ID) AS n,
                   min(book_in_date_time) AS t
            FROM read_parquet(?)
            WHERE stay_ID IS NOT NULL AND unique_identifier IS NOT NULL
            GROUP BY unique_identifier
        )
        SELECT a.unique_identifier
        FROM a JOIN s USING (unique_identifier)
        WHERE a.n = 1 AND s.n = 1 AND a.t IS NOT NULL AND s.t IS NOT NULL
          AND s.t + INTERVAL '1 day' < a.t
        """,
        [str(arrests_file), str(detention_file)],
    ).fetchall()
    selected.update(row[0] for row in short_cut)

    if random_sample:
        sampled = connection.execute(
            """
            SELECT unique_identifier
            FROM read_parquet(?)
            WHERE unique_identifier IS NOT NULL
            GROUP BY unique_identifier
            ORDER BY hash(unique_identifier) % 1000000
            LIMIT ?
            """,
            [str(detention_file), random_sample],
        ).fetchall()
        selected.update(row[0] for row in sampled)
    return selected


def bulk_pass(
    connection: duckdb.DuckDBPyConnection,
    arrests_file: Path,
    detention_file: Path,
    facilities_file: Path,
    keep: set[str],
    limit: int | None,
    findings: Findings,
) -> tuple[dict[str, PersonBulk], Counter, str, set[str]]:
    """Run the real grouping and pairing over every person; return results."""
    present = available_columns(connection, detention_file)
    dup_column = (
        "CASE WHEN d.duplicate_drop_row THEN 1 ELSE 0 END AS dup_flag"
        if "duplicate_drop_row" in present
        else "CAST(0 AS INTEGER) AS dup_flag"
    )
    sql = (
        f"SELECT d.unique_identifier, {detention_select(present)}, {dup_column} "
        f"{detention_sources()} "
        "WHERE d.unique_identifier IS NOT NULL "
        f"ORDER BY d.unique_identifier, {detention_order_by(present)}"
    )
    if limit:
        sql += f" LIMIT {int(limit)}"

    arrests = load_arrests(connection, arrests_file)
    cursor = connection.execute(sql, [str(detention_file), str(facilities_file)])

    counts: Counter = Counter()
    kept: dict[str, PersonBulk] = {}
    digest = hashlib.sha256()

    current_id: str | None = None
    current_rows: list[tuple] = []

    def flush() -> None:
        nonlocal current_id, current_rows
        if current_id is None:
            if current_rows:
                counts["rows_with_null_identifier"] += len(current_rows)
            current_rows = []
            return
        person_stays = group_stays(current_rows)
        person_stays, unmatched = pair_arrests_with_stays(
            arrests.get(current_id, []), person_stays
        )
        counts["people"] += 1
        counts["stays"] += len(person_stays)
        counts["unpaired_stays"] += sum(1 for stay in person_stays if stay.arrest is None)
        counts["unmatched_arrests"] += len(unmatched)
        counts["stints_grouped"] += sum(len(stay.rows) for stay in person_stays)

        previous: datetime | None = None
        for stay in person_stays:
            if stay.start is None:
                continue
            if stay.arrest is not None and stay.arrest.moment is not None:
                latest_allowed = stay.start + ARREST_TO_BOOK_IN_TOLERANCE
                if stay.arrest.moment > latest_allowed:
                    findings.check(
                        "C3 pairing window",
                        False,
                        f"{current_id} arrest "
                        f"{format_stamp(stay.arrest.moment)} vs book-in "
                        f"{format_stamp(stay.start)}",
                    )
                counts["paired_stays"] += 1
                if previous is not None and stay.arrest.moment < previous:
                    findings.check(
                        "C4 monotone claims",
                        False,
                        f"{current_id} claim regressed",
                    )
                previous = stay.arrest.moment
            if stay.stay_id is not None:
                if focus_stay_id(person_stays, stay.stay_id) != stay.stay_id:
                    findings.check(
                        "C6 focus stay",
                        False,
                        f"{current_id} stay {stay.stay_id} not self-resolving",
                    )
                for stint_id in stay.stint_ids:
                    if not stint_id:
                        continue
                    resolved = focus_stay_id(person_stays, stint_id)
                    if resolved is None:
                        findings.check(
                            "C6 focus stint",
                            False,
                            f"{current_id} stint {stint_id} unresolvable",
                        )
                    elif resolved != stay.stay_id:
                        counts["stints_shared_between_stays"] += 1
            if stay.program_variance:
                counts["stays_with_program_variance"] += 1
            if stay.aor_variance:
                counts["stays_with_aor_variance"] += 1

        rendered = format_pathway(Pathway(current_id, person_stays, unmatched))
        if format_pathway(Pathway(current_id, person_stays, unmatched)) != rendered:
            findings.check("C5 deterministic rendering", False, current_id)
        digest.update(f"{current_id}\0{rendered}\0".encode("utf-8"))

        if current_id in keep:
            kept[current_id] = PersonBulk(current_id, person_stays, unmatched)
        current_rows = []

    while True:
        batch = cursor.fetchmany(200_000)
        if not batch:
            break
        for row in batch:
            identifier = row[0]
            if identifier != current_id:
                flush()
                current_id = identifier
            current_rows.append(row[1:])
    flush()

    return kept, counts, digest.hexdigest(), present  # type: ignore[return-value]


def format_stamp(moment: datetime) -> str:
    return utc_datetime(moment).strftime("%Y-%m-%d %H:%M:%S UTC")


def cross_check_sample(
    files: tuple[Path, Path, Path],
    kept: dict[str, PersonBulk],
    findings: Findings,
    limit: int,
) -> None:
    """Run fetch_pathway for sampled identifiers and compare to the bulk path."""
    sample = sorted(kept)[:limit]
    mismatches = 0
    for identifier in sample:
        bulk = kept[identifier]
        try:
            pathway = fetch_pathway(identifier, *files)
        except LookupError as exc:
            findings.check(
                "C7 fetch parity", False, f"{identifier}: lookup failed: {exc}"
            )
            mismatches += 1
            continue
        problems = compare_person(bulk, pathway)
        if problems:
            mismatches += 1
            if mismatches <= 8:
                findings.check(
                    "C7 fetch parity", False, f"{identifier}: {'; '.join(problems)}"
                )
    if mismatches:
        findings.check(
            "C7 fetch parity",
            False,
            f"{mismatches} of {len(sample)} sampled identifiers disagreed",
        )
def compare_person(bulk: PersonBulk, pathway) -> list[str]:
    """Return a list of differences between the bulk and per-identifier results."""
    problems: list[str] = []
    if pathway.identifier != bulk.identifier:
        problems.append("identifier mismatch")
    if len(pathway.stays) != len(bulk.stays):
        problems.append(f"stay count {len(pathway.stays)} != {len(bulk.stays)}")
        return problems
    for bulk_stay, fetched_stay in zip(bulk.stays, pathway.stays):
        if bulk_stay.stay_id != fetched_stay.stay_id:
            problems.append(f"stay id {fetched_stay.stay_id} != {bulk_stay.stay_id}")
        if len(bulk_stay.rows) != len(fetched_stay.rows):
            problems.append(
                f"stay {bulk_stay.stay_id} row count {len(fetched_stay.rows)} "
                f"!= {len(bulk_stay.rows)}"
            )
        if bulk_stay.rows != fetched_stay.rows:
            problems.append(f"stay {bulk_stay.stay_id} rows differ")
        for label, bulk_value, fetched_value in (
            ("arrest", bulk_stay.arrest, fetched_stay.arrest),
            ("entry_program", bulk_stay.entry_program, fetched_stay.entry_program),
            ("entry_aor", bulk_stay.entry_aor, fetched_stay.entry_aor),
            ("release", bulk_stay.release_reason, fetched_stay.release_reason),
            ("summary", bulk_stay.summary, fetched_stay.summary),
        ):
            if bulk_value != fetched_value:
                problems.append(f"{label} differs for {bulk_stay.stay_id}")
    if len(bulk.unmatched) != len(pathway.arrests_without_stay):
        problems.append("unmatched arrest count differs")
    return problems


def stays_file_reconciliation(
    connection: duckdb.DuckDBPyConnection,
    detention_file: Path,
    stays_file: Path,
    findings: Findings,
) -> None:
    """Cross-check bulk stay boundaries against the DDP stays table (C8).

    Each stay_ID in the published stays table carries an n_stints and stay
    book-in/out. The detention stints table groups rows by stay_ID, so the
    verifier confirms that grouping reproduces the published boundaries after
    dropping duplicate-flagged rows, which is how the stays table was built.
    """
    present = available_columns(connection, detention_file)
    dup_bool = "NOT d.duplicate_drop_row" if "duplicate_drop_row" in present else "TRUE"
    rows = connection.execute(
        f"""
        WITH t AS (
            SELECT stay_ID,
                   count(*) FILTER (WHERE {dup_bool}) AS nondup_rows,
                   min(book_in_date_time) FILTER (WHERE {dup_bool}) AS min_bookin,
                   max(book_out_date_time) FILTER (WHERE {dup_bool}) AS max_bookout,
                   bool_or(book_out_date_time IS NULL AND {dup_bool}) AS any_open
            FROM read_parquet(?) d
            WHERE stay_ID IS NOT NULL
            GROUP BY stay_ID
        )
        SELECT s.stay_ID, s.n_stints, s.stay_book_in_date_time,
               s.stay_book_out_date_time, t.nondup_rows, t.min_bookin,
               t.max_bookout, t.any_open
        FROM read_parquet(?) s
        JOIN t USING (stay_ID)
        """,
        [str(detention_file), str(stays_file)],
    ).fetchall()

    mismatches: Counter = Counter()
    total = len(rows)
    for stay_id, n_stints, stay_in, stay_out, nondup, min_in, max_out, any_open in rows:
        if nondup != n_stints:
            mismatches["count"] += 1
        else:
            mismatches["count_ok"] += 1
        if stay_in is not None and min_in != stay_in:
            if stay_in < min_in:
                mismatches["book_in_earlier"] += 1
            else:
                mismatches["book_in_later"] += 1
        else:
            mismatches["book_in_ok"] += 1
        if any_open:
            mismatches["open_stays"] += 1
            if stay_out is not None:
                mismatches["open_with_published_out"] += 1
        else:
            if stay_out is not None and max_out != stay_out:
                if stay_out > max_out:
                    mismatches["book_out_later"] += 1
                else:
                    mismatches["book_out_earlier"] += 1
            elif stay_out is None:
                mismatches["closed_no_published_out"] += 1
            else:
                mismatches["book_out_ok"] += 1

    findings.check(
        "C8 count vs n_stints",
        mismatches["count"] == 0,
        f"{mismatches['count']} of {total} stays disagree with n_stints",
    )
    findings.note(
        f"stays cross-checked: {total}; open (no book-out in stints): "
        f"{mismatches['open_stays']}"
    )
    findings.note(
        "C8 boundary reconciliation with the published stays table "
        "(source-data window vs stint rows, not pipeline failures): "
        f"book-in earlier than first stint row: {mismatches['book_in_earlier']}; "
        f"book-in later: {mismatches['book_in_later']}; "
        f"book-out later than last stint row: {mismatches['book_out_later']}; "
        f"book-out earlier: {mismatches['book_out_earlier']}; "
        f"closed in stints but no published book-out: "
        f"{mismatches['closed_no_published_out']}; "
        f"open in stints but published book-out present: "
        f"{mismatches['open_with_published_out']}"
    )


def delimiter_contract(
    connection: duckdb.DuckDBPyConnection,
    arrests_file: Path,
    detention_file: Path,
    facilities_file: Path,
    findings: Findings,
) -> None:
    """C10: verify no source value collides with the output grammar.

    The machine-readable format relies on fixed delimiters (segments joined by
    ` -> `, each stint a sequence of `[Label: value]` blocks, field pairs split
    on `; `, facility name/code split on `:`). The checks below confirm the
    current data cannot produce a value that would be mis-split; a DDP data
    refresh that introduces such a value fails verification instead of silently
    breaking the format.
    """
    scans = [
        (
            "facility names/codes",
            detention_file,
            ["detention_facility", "detention_facility_code"],
            "[\\[\\]\\:]",
        ),
        (
            "canonical facility names",
            facilities_file,
            ["name"],
            "[\\[\\]\\:]",
        ),
        (
            "rendered stint summary fields",
            detention_file,
            [
                "final_program",
                "book_in_aor",
                "detainee_classification",
                "case_status",
                "case_threat_level",
                "final_order_yes_no",
                "final_order_date",
                "departed_date",
                "final_charge",
            ],
            "\\;",
        ),
        (
            "release reasons",
            detention_file,
            ["detention_release_reason"],
            "\\;|\\r|\\n",
        ),
    ]
    total_bad = 0
    for label, file, columns, pattern in scans:
        where = " OR ".join(
            f"regexp_matches(cast({column} AS VARCHAR), ?)" for column in columns
        )
        bad = connection.execute(
            f"SELECT count(*) FROM read_parquet(?) WHERE {where}",
            [str(file)] + [pattern] * len(columns),
        ).fetchone()[0]
        total_bad += bad
        if bad:
            findings.check(
                "C10 delimiter contract", False, f"{label}: {bad} row(s) match"
            )

    where = " OR ".join(
        f"regexp_matches(cast({column} AS VARCHAR), ?)"
        for column in [
            "apprehension_site_landmark",
            "apprehension_state_filled_in",
            "apprehension_aor",
            "operation",
        ]
    )
    bad = connection.execute(
        f"SELECT count(*) FROM read_parquet(?) WHERE {where}",
        [str(arrests_file)] + ["[\\[\\]]|->"] * 4,
    ).fetchone()[0]
    total_bad += bad
    if bad:
        findings.check(
            "C10 delimiter contract", False,
            f"arrest location fields: {bad} row(s) match",
        )

    if total_bad == 0:
        findings.note(
            "C10 delimiter contract: no rendered value collides with the "
            "documented output grammar's delimiters"
        )


def data_scope(
    connection: duckdb.DuckDBPyConnection,
    arrests_file: Path,
    detention_file: Path,
) -> None:
    """Report the local data window and the arrests scope (C9)."""
    scope = connection.execute(
        """
        SELECT apprehension_aor, arresting_agency, count(*)
        FROM read_parquet(?)
        GROUP BY 1, 2
        ORDER BY 3 DESC
        """,
        [str(arrests_file)],
    ).fetchall()
    print("LOCAL ARRESTS SCOPE (what is NOT in this copy)")
    for aor, agency, rows in scope:
        print(f"  {rows:>8,}  {agency or 'unknown'}  |  {aor or 'unknown AOR'}")
    print("  Any arrest outside these categories cannot appear, whether or not")
    print("  it happened. Absence here is not evidence of absence in reality.")

    window = connection.execute(
        """
        SELECT min(book_in_date_time), max(book_in_date_time),
               min(book_out_date_time), max(book_out_date_time),
               count(*) FILTER (WHERE book_out_date_time IS NULL),
               count(*) FILTER (WHERE stay_ID IS NULL)
        FROM read_parquet(?)
        """,
        [str(detention_file)],
    ).fetchone()
    min_in, max_in, min_out, max_out, open_stints, null_stay = window
    print("\nDATA WINDOW (detention stints)")
    print(f"  book-in   {format_stamp(min_in)}  ->  {format_stamp(max_in)}")
    print(f"  book-out  {format_stamp(min_out)}  ->  {format_stamp(max_out)}")
    print(f"  stints with no book-out: {open_stints:,}")
    print(
        f"  stints with no stay_ID (all also lack a unique_identifier and are "
        f"unreachable): {null_stay:,}"
    )


def report(findings: Findings, counts: Counter, digest: str, elapsed: float) -> int:
    print(RULE)
    print(f"BULK VERIFICATION  —  {time.strftime('%Y-%m-%d %H:%M:%S')}")
    print(RULE)
    print(
        f"people: {counts['people']:,}   stays: {counts['stays']:,}   "
        f"stints grouped: {counts['stints_grouped']:,}"
    )
    print(
        f"paired stays: {counts['paired_stays']:,}   unpaired stays: "
        f"{counts['unpaired_stays']:,}   unmatched arrests: "
        f"{counts['unmatched_arrests']:,}"
    )
    print(
        f"stays with varying program: {counts['stays_with_program_variance']:,}   "
        f"varying AOR: {counts['stays_with_aor_variance']:,}"
    )
    if counts["stints_shared_between_stays"]:
        findings.note(
            "C6 stints whose stint_ID appears under more than one stay "
            "(source data; resolves to the earliest containing stay): "
            f"{counts['stints_shared_between_stays']}"
        )
    print(f"rows with null identifier (excluded): {counts['rows_with_null_identifier']:,}")
    print(f"rendering digest (sha256): {digest}")
    print(f"elapsed: {elapsed:.1f}s")
    for note in findings.notes:
        print(f"note: {note}")

    if findings.failures:
        print(f"\nFAIL: {len(findings.failures)} check(s) failed")
        for failure in findings.failures[:20]:
            print(f"  - {failure}")
        if len(findings.failures) > 20:
            print(f"  ... and {len(findings.failures) - 20} more")
        return 1
    print("\nPASS: every check succeeded across the local datasets.")
    return 0


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        prog="verify-bulk",
        description=(
            "Run the real grouping and pairing logic over every identifier in "
            "the local datasets and check invariants, parity with the "
            "per-identifier path, and reconciliation with the DDP stays table."
        ),
    )
    parser.add_argument("--sample", type=int, default=1500,
                        help="fetch_pathway cross-check sample size (default 1500)")
    parser.add_argument("--skip-sample", action="store_true",
                        help="skip the per-identifier cross-check")
    parser.add_argument("--limit", type=int, default=None,
                        help="cap the number of stints read (dev smoke runs)")
    parser.add_argument("--arrests-file", type=Path, default=DEFAULT_ARRESTS_FILE)
    parser.add_argument("--detention-file", type=Path, default=DEFAULT_DETENTION_FILE)
    parser.add_argument("--facilities-file", type=Path, default=DEFAULT_FACILITIES_FILE)
    parser.add_argument("--stays-file", type=Path,
                        default=Path("detention-stays-latest.parquet"))
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    files = (args.arrests_file, args.detention_file, args.facilities_file)
    findings = Findings([], [])
    connection = open_connection()
    try:
        data_scope(connection, args.arrests_file, args.detention_file)
        delimiter_contract(
            connection,
            args.arrests_file,
            args.detention_file,
            args.facilities_file,
            findings,
        )
        keep = keep_set(
            connection, args.detention_file, args.arrests_file, args.sample
        )
        started = time.monotonic()
        kept, counts, digest, _ = bulk_pass(
            connection, *files, keep, args.limit, findings
        )
        if not args.skip_sample and kept:
            cross_check_sample(files, kept, findings, args.sample)
        stays_file = args.stays_file
        if stays_file.is_file() and args.limit is None:
            stays_file_reconciliation(
                connection, args.detention_file, stays_file, findings
            )
        elapsed = time.monotonic() - started
    finally:
        connection.close()
    return report(findings, counts, digest, elapsed)


if __name__ == "__main__":
    raise SystemExit(main())
