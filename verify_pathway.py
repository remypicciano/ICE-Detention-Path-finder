"""Show the evidence behind one pathway so a reader can check it independently.

Every fact printed here carries the source file, sheet, and row number it came
from, so a skeptical reader can open the original FOIA spreadsheet and confirm
it.

Grouping into stays and pairing arrests with stays are delegated to
`ice_detention_pathway`, so this receipt cannot disagree with the pathway it is
meant to verify. Only provenance lookup and presentation live here.
"""

from __future__ import annotations

import argparse
import hashlib
import html
import sys
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Sequence

import duckdb

from ice_detention_pathway import (
    DEFAULT_ARRESTS_FILE,
    DEFAULT_DETENTION_FILE,
    DEFAULT_FACILITIES_FILE,
    DISCREPANCY_MINIMUM_GAP,
    ArrestEvent,
    LookupError,
    Stay,
    available_columns,
    clean_arrest_location,
    duration_text,
    exceeds_gap,
    format_timestamp,
    normalize_identifier,
    optional_column,
    pair_arrests_with_stays,
    utc_datetime,
    validate_file,
)


BAR_WIDTH = 56
RULE = "=" * 78


@dataclass(frozen=True)
class Stint:
    """One recorded facility placement, with the source row it came from."""

    stay_id: str | None
    stint_id: str | None
    book_in: datetime | None
    book_out: datetime | None
    facility: str
    facility_code: str | None
    city: str | None
    state: str | None
    release_reason: str | None
    program: str | None
    book_in_aor: str | None
    file_original: str | None
    sheet_original: str | None
    row_original: int | None

    @property
    def place(self) -> str:
        where = ", ".join(part for part in (self.city, self.state) if part)
        return f"{self.facility} ({where})" if where else self.facility

    @property
    def citation(self) -> str:
        return citation_text(self.file_original, self.sheet_original, self.row_original)


@dataclass(frozen=True)
class ArrestRecord:
    """One arrest row, with the source row it came from."""

    event: ArrestEvent
    aor: str | None
    method: str | None
    arresting_agency: str | None
    program: str | None
    file_original: str | None
    sheet_original: str | None
    row_original: int | None

    @property
    def citation(self) -> str:
        return citation_text(self.file_original, self.sheet_original, self.row_original)


@dataclass(frozen=True)
class StayRecord:
    """A group of stints sharing one stay_ID, with the arrest that opened it."""

    stay_id: str | None
    stints: list[Stint]
    arrest: ArrestRecord | None

    @property
    def start(self) -> datetime | None:
        moments = [utc_datetime(s.book_in) for s in self.stints if s.book_in]
        return min(moments) if moments else None

    @property
    def end(self) -> datetime | None:
        if any(s.book_out is None for s in self.stints):
            return None
        moments = [utc_datetime(s.book_out) for s in self.stints if s.book_out]
        return max(moments) if moments else None


def citation_text(file_name: str | None, sheet: str | None, row: int | None) -> str:
    if not file_name:
        return "no source citation recorded"
    parts = [file_name]
    if sheet:
        parts.append(f"sheet {sheet}")
    if row is not None:
        parts.append(f"row {row}")
    return " / ".join(parts)


def file_digest(path: Path) -> str:
    """Return a short SHA-256 prefix so a reader can confirm the same file."""
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1 << 20), b""):
            digest.update(block)
    return digest.hexdigest()[:16]


def fetch_evidence(
    identifier_input: str,
    arrests_file: Path,
    detention_file: Path,
    facilities_file: Path,
) -> tuple[str, list[StayRecord], list[ArrestRecord], list[tuple[str, str, int]]]:
    """Return every stay with its source rows, plus the local dataset's scope."""
    identifier = normalize_identifier(identifier_input)
    validate_file(arrests_file, "arrests")
    validate_file(detention_file, "detention")
    validate_file(facilities_file, "facilities")

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
                   ) AS arrest_location,
                   apprehension_aor,
                   apprehension_method,
                   arresting_agency,
                   final_program,
                   file_original,
                   sheet_original,
                   row_original
            FROM read_parquet(?)
            WHERE unique_identifier = ?
            ORDER BY apprehension_date_time NULLS LAST
            """,
            [str(arrests_file), identifier],
        ).fetchall()

        present = available_columns(connection, detention_file)
        stint_rows = connection.execute(
            f"""
            SELECT {optional_column('stay_ID', present)} AS stay_id,
                   {optional_column('stint_ID', present)} AS stint_id,
                   d.book_in_date_time,
                   d.book_out_date_time,
                   coalesce(
                       nullif(trim(f.name), ''),
                       nullif(trim(d.detention_facility), ''),
                       'UNKNOWN DETENTION CENTER'
                   ) AS facility,
                   d.detention_facility_code,
                   {optional_column('city', present)} AS city,
                   {optional_column('state', present)} AS state,
                   {optional_column('detention_release_reason', present)} AS reason,
                   {optional_column('final_program', present)} AS program,
                   {optional_column('book_in_aor', present)} AS book_in_aor,
                   d.file_original,
                   d.sheet_original,
                   d.row_original
            FROM read_parquet(?) d
            LEFT JOIN (
                SELECT detention_facility_code, max(name) AS name
                FROM read_parquet(?)
                WHERE detention_facility_code IS NOT NULL
                GROUP BY detention_facility_code
            ) f USING (detention_facility_code)
            WHERE d.unique_identifier = ?
            ORDER BY d.book_in_date_time NULLS LAST,
                     d.book_out_date_time NULLS LAST,
                     d.row_original NULLS LAST
            """,
            [str(detention_file), str(facilities_file), identifier],
        ).fetchall()

        scope = connection.execute(
            """
            SELECT apprehension_aor, arresting_agency, count(*) AS rows
            FROM read_parquet(?)
            GROUP BY 1, 2
            ORDER BY rows DESC
            """,
            [str(arrests_file)],
        ).fetchall()
    finally:
        connection.close()

    if not arrest_rows and not stint_rows:
        raise LookupError(
            "Identifier not found in the arrests or detention datasets. It may "
            "be invalid or may have been excluded from the locally filtered data."
        )

    arrests = [
        ArrestRecord(
            event=ArrestEvent(row[0], row[1], row[2]),
            aor=row[3],
            method=row[4],
            arresting_agency=row[5],
            program=row[6],
            file_original=row[7],
            sheet_original=row[8],
            row_original=row[9],
        )
        for row in arrest_rows
    ]
    stints = [Stint(*row) for row in stint_rows]
    stays, unmatched = assemble_stays(stints, arrests)
    return identifier, stays, unmatched, scope


def assemble_stays(
    stints: Sequence[Stint], arrests: Sequence[ArrestRecord]
) -> tuple[list[StayRecord], list[ArrestRecord]]:
    """Group stints and pair arrests using the core module's own logic.

    The receipt must reach the same conclusion as the pathway it verifies, so
    the pairing decision is made by `pair_arrests_with_stays` rather than being
    reimplemented here.
    """
    order: list[str | None] = []
    grouped: dict[str | None, list[Stint]] = {}
    for stint in stints:
        if stint.stay_id not in grouped:
            grouped[stint.stay_id] = []
            order.append(stint.stay_id)
        grouped[stint.stay_id].append(stint)

    groups = [grouped[stay_id] for stay_id in order]
    groups.sort(
        key=lambda members: (
            all(s.book_in is None for s in members),
            min(
                (utc_datetime(s.book_in) for s in members if s.book_in),
                default=datetime.max.replace(tzinfo=timezone.utc),
            ),
        )
    )

    core_stays = [
        Stay(
            stay_id=members[0].stay_id,
            arrest=None,
            rows=[(s.book_in, s.facility, s.book_out) for s in members],
            entry_program=members[0].program,
            entry_aor=members[0].book_in_aor,
            release_reason=members[-1].release_reason,
        )
        for members in groups
    ]
    paired, _ = pair_arrests_with_stays(
        [arrest.event for arrest in arrests], core_stays
    )

    by_event = {id(arrest.event): arrest for arrest in arrests}
    stays: list[StayRecord] = []
    claimed: set[int] = set()
    for members, core_stay in zip(groups, paired):
        record = None
        if core_stay.arrest is not None:
            record = by_event[id(core_stay.arrest)]
            claimed.add(id(core_stay.arrest))
        stays.append(StayRecord(members[0].stay_id, members, record))

    unmatched = [arrest for arrest in arrests if id(arrest.event) not in claimed]
    return stays, unmatched


def check_consistency(stays: Sequence[StayRecord]) -> list[tuple[str, str, str]]:
    """Return (scope, verdict, explanation) for each chronology check."""
    findings: list[tuple[str, str, str]] = []
    all_stints = [stint for stay in stays for stint in stay.stints]

    earliest = min(
        (utc_datetime(s.book_in) for s in all_stints if s.book_in), default=None
    )
    arrests = [stay.arrest for stay in stays if stay.arrest is not None]
    moment = arrests[0].event.moment if arrests else None

    if moment is not None and earliest is not None:
        if moment > earliest:
            findings.append(
                (
                    "identifier-wide",
                    "FLAGS",
                    f"first arrest {format_timestamp(moment, 'arrest')} is after the "
                    f"earliest book-in of any stay "
                    f"({format_timestamp(earliest, 'book-in')}) — this comparison "
                    "spans unrelated stays and is shown only for contrast",
                )
            )
        else:
            findings.append(
                ("identifier-wide", "clean", "first arrest precedes every book-in")
            )

    for number, stay in enumerate(stays, start=1):
        if stay.arrest is None:
            findings.append(
                (
                    f"stay {number}",
                    "n/a",
                    "no arrest record in this dataset corresponds to this stay",
                )
            )
            continue
        arrest_moment = stay.arrest.event.moment
        if arrest_moment is None or stay.start is None:
            continue
        if exceeds_gap(stay.start, arrest_moment):
            findings.append(
                (
                    f"stay {number}",
                    "FLAGS",
                    f"arrest is {duration_text(stay.start, arrest_moment)} after this "
                    "stay's own first book-in",
                )
            )
        elif arrest_moment > stay.start:
            findings.append(
                (
                    f"stay {number}",
                    "clean",
                    f"arrest is {duration_text(stay.start, arrest_moment)} after "
                    f"book-in — under the {DISCREPANCY_MINIMUM_GAP.days}-day "
                    "threshold, treated as filing order",
                )
            )
        else:
            findings.append(
                (
                    f"stay {number}",
                    "clean",
                    f"arrest precedes its stay's first book-in by "
                    f"{duration_text(arrest_moment, stay.start)}",
                )
            )

    for stint in all_stints:
        if stint.book_in and stint.book_out:
            if exceeds_gap(utc_datetime(stint.book_out), utc_datetime(stint.book_in)):
                findings.append(
                    (
                        "single stint",
                        "FLAGS",
                        f"{stint.facility}: book-out precedes book-in ({stint.citation})",
                    )
                )

    for number, stay in enumerate(stays, start=1):
        previous: datetime | None = None
        for stint in stay.stints:
            if stint.book_in and previous:
                if exceeds_gap(utc_datetime(stint.book_in), previous):
                    findings.append(
                        (
                            f"stay {number}",
                            "FLAGS",
                            f"{stint.facility} begins before the previous book-out "
                            f"({stint.citation})",
                        )
                    )
            if stint.book_out:
                book_out = utc_datetime(stint.book_out)
                previous = book_out if previous is None else max(previous, book_out)
    return findings


def scaled_bar(
    start: datetime | None, end: datetime | None, window_start: datetime, span: float
) -> str:
    """Render one bar positioned on a shared time axis."""
    if start is None:
        return "?" * 3
    offset = int(BAR_WIDTH * (start - window_start).total_seconds() / span)
    offset = max(0, min(BAR_WIDTH - 1, offset))
    if end is None:
        return " " * offset + "#" * (BAR_WIDTH - offset) + ">"
    width = int(BAR_WIDTH * (utc_datetime(end) - start).total_seconds() / span)
    width = max(1, min(BAR_WIDTH - offset, width))
    return " " * offset + "#" * width


def time_window(stays: Sequence[StayRecord]) -> tuple[datetime, datetime]:
    starts = [stay.start for stay in stays if stay.start]
    ends = [stay.end for stay in stays if stay.end]
    latest_in = max(
        (utc_datetime(s.book_in) for stay in stays for s in stay.stints if s.book_in),
        default=None,
    )
    window_start = min(starts) if starts else datetime.now(timezone.utc)
    candidates = [moment for moment in (*ends, latest_in) if moment]
    window_end = max(candidates) if candidates else window_start + timedelta(days=1)
    if window_end <= window_start:
        window_end = window_start + timedelta(days=1)
    return window_start, window_end


def print_report(
    identifier: str,
    stays: Sequence[StayRecord],
    unmatched: Sequence[ArrestRecord],
    scope: Sequence[tuple[str, str, int]],
    files: Sequence[tuple[str, Path]],
) -> None:
    print(RULE)
    print(f"PROVENANCE RECEIPT  —  {identifier}")
    print(RULE)

    print("\n1. FILES USED (SHA-256 prefix; a reader with the same files gets these)")
    for label, path in files:
        print(f"   {label:<11} {path.name}")
        print(f"   {'':<11} sha256 {file_digest(path)}  {path.stat().st_size:,} bytes")

    print("\n2. SCOPE OF THE LOCAL ARRESTS DATA (what is NOT in this copy)")
    for aor, agency, rows in scope:
        print(f"   {rows:>8,}  {agency or 'unknown agency'}  |  {aor or 'unknown AOR'}")
    print("   Any arrest outside these categories cannot appear, whether or not")
    print("   it happened. Absence here is not evidence of absence in reality.")

    print(f"\n3. CUSTODY STAYS ({len(stays)} distinct stay_ID)")
    previous: StayRecord | None = None
    for number, stay in enumerate(stays, start=1):
        if previous is not None and previous.end and stay.start:
            print(
                f"\n   ---- not in custody for {duration_text(previous.end, stay.start)} ----"
            )
        print(f"\n   STAY {number}")
        print(f"   stay_ID   {stay.stay_id or 'none recorded'}")
        entry = stay.stints[0]
        print(
            f"   stint     final_program: {entry.program or 'none'}; "
            f"book_in_aor: {entry.book_in_aor or 'none'}"
        )
        if stay.arrest is None:
            print("   arrest    NO ARREST RECORD IN THIS DATASET")
        else:
            arrest = stay.arrest
            print(f"   arrest    {format_timestamp(arrest.event.date_time, 'arrest')}")
            print(f"             {clean_arrest_location(arrest.event.location)}")
            print(
                f"             agency: {arrest.arresting_agency or 'unknown'}; "
                f"method: {arrest.method or 'unknown'}"
            )
            print(f"             SOURCE {arrest.citation}")
        for stint in stay.stints:
            print(
                f"     {format_timestamp(stint.book_in, 'book-in')}"
                f"  ->  {format_timestamp(stint.book_out, 'book-out')}"
                f"   [{duration_text(stint.book_in, stint.book_out)}]"
            )
            print(f"       {stint.place}  code {stint.facility_code or 'none'}")
            print(f"       released: {stint.release_reason or 'no release recorded'}")
            print(f"       SOURCE  {stint.citation}")
        previous = stay

    if unmatched:
        print(f"\n   ARRESTS WITH NO RECORDED DETENTION ({len(unmatched)})")
        for arrest in unmatched:
            print(f"     {format_timestamp(arrest.event.date_time, 'arrest')}")
            print(f"       {clean_arrest_location(arrest.event.location)}")
            print(f"       SOURCE  {arrest.citation}")

    if stays:
        window_start, window_end = time_window(stays)
        span = max((window_end - window_start).total_seconds(), 1.0)
        print("\n4. TIMELINE (one shared axis; '>' means no book-out recorded)")
        print(f"   {window_start:%Y-%m-%d} {'':<{BAR_WIDTH - 21}} {window_end:%Y-%m-%d}")
        for number, stay in enumerate(stays, start=1):
            for stint in stay.stints:
                bar = scaled_bar(
                    utc_datetime(stint.book_in) if stint.book_in else None,
                    stint.book_out,
                    window_start,
                    span,
                )
                print(f"   |{bar:<{BAR_WIDTH + 1}}| S{number} {stint.facility}")
        for stay in stays:
            if stay.arrest is None or stay.arrest.event.moment is None:
                continue
            offset = int(
                BAR_WIDTH
                * (stay.arrest.event.moment - window_start).total_seconds()
                / span
            )
            offset = max(0, min(BAR_WIDTH - 1, offset))
            print(f"   |{' ' * offset}^{'':<{BAR_WIDTH - offset}}| arrest")

    print(
        f"\n5. CHRONOLOGY CHECKS (flagged only past {DISCREPANCY_MINIMUM_GAP.days} day)"
    )
    for check_scope, verdict, explanation in check_consistency(stays):
        print(f"   [{verdict:^5}] {check_scope:<16} {explanation}")

    print("\n6. HOW TO CHECK THIS YOURSELF")
    print("   Each SOURCE line names the ICE spreadsheet, sheet, and row the value")
    print("   came from. Request or download that file, open that row, and compare.")
    print("   Everything above is copied from those rows; nothing is inferred except")
    print("   the grouping into stays, which uses the stay_ID recorded in the data.")
    print()


def render_html(
    identifier: str,
    stays: Sequence[StayRecord],
    unmatched: Sequence[ArrestRecord],
    files: Sequence[tuple[str, Path]],
) -> str:
    """Render a self-contained page: a timeline plus the source row for each bar."""
    window_start, window_end = time_window(stays)
    span = max((window_end - window_start).total_seconds(), 1.0)

    def position(moment: datetime) -> float:
        return 100.0 * (moment - window_start).total_seconds() / span

    bars = []
    for number, stay in enumerate(stays, start=1):
        for stint in stay.stints:
            if stint.book_in is None:
                continue
            start = utc_datetime(stint.book_in)
            left = position(start)
            if stint.book_out is None:
                width = 100.0 - left
                open_ended = True
            else:
                width = max(0.6, position(utc_datetime(stint.book_out)) - left)
                open_ended = False
            tone = "e1" if number % 2 else "e2"
            bars.append(
                f'<div class="row"><div class="track">'
                f'<div class="bar {tone}{" open" if open_ended else ""}" '
                f'style="left:{left:.3f}%;width:{width:.3f}%"></div></div>'
                f'<div class="label"><strong>{html.escape(stint.facility)}</strong>'
                f'<span>{html.escape(duration_text(stint.book_in, stint.book_out))}'
                f' &middot; stay {number}</span></div></div>'
            )

    markers = "".join(
        f'<div class="marker" style="left:{position(stay.arrest.event.moment):.3f}%">'
        f'<span>arrest</span></div>'
        for stay in stays
        if stay.arrest is not None and stay.arrest.event.moment is not None
    )

    rows = []
    for number, stay in enumerate(stays, start=1):
        if stay.arrest is not None:
            rows.append(
                "<tr>"
                f"<td>{number}</td>"
                f"<td><strong>Arrest</strong> — "
                f"{html.escape(clean_arrest_location(stay.arrest.event.location))}</td>"
                f"<td>{html.escape(format_timestamp(stay.arrest.event.date_time, 'arrest'))}</td>"
                f"<td>—</td><td>{html.escape(stay.arrest.method or '—')}</td>"
                f"<td class=\"cite\">{html.escape(stay.arrest.citation)}</td>"
                "</tr>"
            )
        for stint in stay.stints:
            rows.append(
                "<tr>"
                f"<td>{number}</td>"
                f"<td>{html.escape(stint.place)}</td>"
                f"<td>{html.escape(format_timestamp(stint.book_in, 'book-in'))}</td>"
                f"<td>{html.escape(format_timestamp(stint.book_out, 'book-out'))}</td>"
                f"<td>{html.escape(stint.release_reason or '—')}</td>"
                f"<td class=\"cite\">{html.escape(stint.citation)}</td>"
                "</tr>"
            )

    stay_notes = []
    for number, stay in enumerate(stays, start=1):
        entry = stay.stints[0]
        tag = (
            "paired with the arrest record above"
            if stay.arrest is not None
            else "no arrest record in this dataset corresponds to this stay"
        )
        stay_notes.append(
            f"<li><strong>Stay {number}</strong> — first stint records "
            f"<code>final_program</code>: {html.escape(entry.program or 'none')}; "
            f"<code>book_in_aor</code>: {html.escape(entry.book_in_aor or 'none')}. "
            f"{html.escape(tag)}.</li>"
        )
    for arrest in unmatched:
        stay_notes.append(
            "<li><strong>Arrest with no recorded detention</strong> — "
            f"{html.escape(format_timestamp(arrest.event.date_time, 'arrest'))}, "
            f"{html.escape(clean_arrest_location(arrest.event.location))}.</li>"
        )

    file_rows = "".join(
        f"<tr><td>{html.escape(label)}</td><td>{html.escape(path.name)}</td>"
        f"<td class=\"cite\">{file_digest(path)}</td></tr>"
        for label, path in files
    )

    return f"""<title>Detention pathway evidence — {html.escape(identifier[:12])}…</title>
<style>
  :root {{
    --bg: #ffffff; --fg: #16181d; --muted: #5b6270; --line: #e2e5ea;
    --panel: #f6f7f9; --accent: #b4232c; --e1: #7a8699; --e2: #2f6f9f;
  }}
  @media (prefers-color-scheme: dark) {{
    :root {{
      --bg: #14161a; --fg: #e8eaee; --muted: #9aa3b2; --line: #2a2e36;
      --panel: #1b1e24; --accent: #ff6b6b; --e1: #6b7688; --e2: #5aa9dd;
    }}
  }}
  :root[data-theme="light"] {{
    --bg: #ffffff; --fg: #16181d; --muted: #5b6270; --line: #e2e5ea;
    --panel: #f6f7f9; --accent: #b4232c; --e1: #7a8699; --e2: #2f6f9f;
  }}
  :root[data-theme="dark"] {{
    --bg: #14161a; --fg: #e8eaee; --muted: #9aa3b2; --line: #2a2e36;
    --panel: #1b1e24; --accent: #ff6b6b; --e1: #6b7688; --e2: #5aa9dd;
  }}
  body {{
    background: var(--bg); color: var(--fg); margin: 0 auto; padding: 2.5rem 1.25rem 4rem;
    max-width: 60rem; line-height: 1.55;
    font-family: ui-sans-serif, -apple-system, "Segoe UI", system-ui, sans-serif;
  }}
  h1 {{ font-size: 1.5rem; margin: 0 0 .3rem; letter-spacing: -.01em; }}
  h2 {{ font-size: .8rem; text-transform: uppercase; letter-spacing: .09em;
       color: var(--muted); margin: 2.5rem 0 .9rem; font-weight: 600; }}
  .id {{ font-family: ui-monospace, SFMono-Regular, Menlo, monospace;
        font-size: .82rem; color: var(--muted); word-break: break-all; }}
  .chart {{ position: relative; border: 1px solid var(--line); border-radius: 8px;
           padding: 1.1rem 1rem .6rem; background: var(--panel); }}
  .axis {{ display: flex; justify-content: space-between; font-size: .72rem;
          color: var(--muted); margin-bottom: .7rem;
          font-family: ui-monospace, Menlo, monospace; }}
  .row {{ display: flex; align-items: center; gap: .8rem; margin-bottom: .45rem; }}
  .track {{ position: relative; flex: 1 1 58%; height: 15px;
           border-radius: 3px; background: rgba(128,138,155,.16); }}
  .bar {{ position: absolute; top: 0; height: 15px; border-radius: 3px; min-width: 3px; }}
  .bar.e1 {{ background: var(--e1); }}
  .bar.e2 {{ background: var(--e2); }}
  .bar.open {{ background: repeating-linear-gradient(135deg,
        var(--accent) 0 7px, rgba(180,35,44,.45) 7px 14px); }}
  .label {{ flex: 0 0 40%; font-size: .8rem; display: flex; flex-direction: column; }}
  .label span {{ color: var(--muted); font-size: .72rem; }}
  .marker {{ position: absolute; top: 2.6rem; bottom: .5rem; width: 2px;
            background: var(--accent); }}
  .marker span {{ position: absolute; top: -1.15rem; left: -1px; white-space: nowrap;
                 font-size: .7rem; color: var(--accent); font-weight: 600; }}
  .scroll {{ overflow-x: auto; }}
  table {{ border-collapse: collapse; width: 100%; font-size: .8rem; min-width: 42rem; }}
  th {{ text-align: left; font-weight: 600; color: var(--muted); font-size: .72rem;
       text-transform: uppercase; letter-spacing: .05em;
       border-bottom: 1px solid var(--line); padding: .4rem .6rem .4rem 0; }}
  td {{ padding: .55rem .6rem .55rem 0; border-bottom: 1px solid var(--line);
       vertical-align: top; }}
  .cite {{ font-family: ui-monospace, Menlo, monospace; font-size: .72rem;
          color: var(--muted); }}
  ul {{ padding-left: 1.1rem; }} li {{ margin-bottom: .4rem; font-size: .88rem; }}
  .note {{ border-left: 3px solid var(--accent); padding: .1rem 0 .1rem .9rem;
          color: var(--muted); font-size: .85rem; }}
</style>

<h1>Recorded detention pathway</h1>
<p class="id">{html.escape(identifier)}</p>

<h2>Timeline</h2>
<div class="chart">
  <div class="axis"><span>{window_start:%Y-%m-%d}</span><span>{window_end:%Y-%m-%d}</span></div>
  {''.join(bars)}
  {markers}
</div>

<h2>Custody stays</h2>
<ul>{''.join(stay_notes)}</ul>

<h2>Every row, and where it came from</h2>
<div class="scroll">
<table>
  <tr><th>Stay</th><th>Facility / event</th><th>Book-in (UTC)</th><th>Book-out (UTC)</th>
      <th>Release reason</th><th>Source row</th></tr>
  {''.join(rows)}
</table>
</div>

<h2>Source files</h2>
<div class="scroll">
<table>
  <tr><th>Role</th><th>File</th><th>SHA-256 prefix</th></tr>
  {file_rows}
</table>
</div>

<h2>How to check this</h2>
<p class="note">Every value above is copied from the source row named beside it.
Nothing is inferred except the grouping into stays, which uses the
<code>stay_ID</code> recorded in the data itself. A missing book-out means the
source has no later release or transfer recorded — not that the person is
confirmed to be held. <code>final_program</code> is the program of record for a
case; it does not state which agency made an apprehension, and the source data
has no field that does. Data comes from ICE via FOIA request, processed by the
Deportation Data Project; it has known limitations and may be revised.</p>
"""


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        prog="verify-pathway",
        description=(
            "Print the source rows behind one detention pathway so a reader can "
            "verify it against the original ICE spreadsheets."
        ),
    )
    parser.add_argument("identifier", help="unique_identifier, stay_ID, or stint_ID")
    parser.add_argument(
        "--html",
        type=Path,
        help="also write a self-contained HTML timeline to this path",
    )
    parser.add_argument("--arrests-file", type=Path, default=DEFAULT_ARRESTS_FILE)
    parser.add_argument("--detention-file", type=Path, default=DEFAULT_DETENTION_FILE)
    parser.add_argument("--facilities-file", type=Path, default=DEFAULT_FACILITIES_FILE)
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    try:
        identifier, stays, unmatched, scope = fetch_evidence(
            args.identifier,
            args.arrests_file,
            args.detention_file,
            args.facilities_file,
        )
    except (LookupError, duckdb.Error) as exc:
        print(f"Verification failed: {exc}", file=sys.stderr)
        return 1

    files = [
        ("arrests", args.arrests_file),
        ("detention", args.detention_file),
        ("facilities", args.facilities_file),
    ]

    print_report(identifier, stays, unmatched, scope, files)

    if args.html:
        args.html.write_text(
            render_html(identifier, stays, unmatched, files), encoding="utf-8"
        )
        print(f"HTML timeline written to {args.html}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
