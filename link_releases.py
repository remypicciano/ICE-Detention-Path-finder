"""Re-identify people across DDP data releases and emit an updated bulk export.

The publisher re-salts `unique_identifier` on every rebuild, so an identifier
saved from an older export does not resolve against current data. The person is
still there; only the label changed. This module rebuilds the old-to-new mapping
from evidence in the data itself, then produces the same bulk export the desktop
Bulk Search produces — but for the new identifiers, with whatever stints have
been appended since.

## How a match is established

A person is identified by their **path fingerprint**: the ordered chain of
`(facility_code, book_in, book_out)` across their stints. This is far stronger
than any single stint — mass book-ins put many unrelated people into the same
holding room on the same minute, so one stint typically matches dozens of
candidates — but it is not sufficient on its own either. Byte-identical paths
belonging to genuinely different people are common, so a path match is only
accepted when `birth_year` and `gender` also agree. Those two are stable within
a person (a handful of the million identifiers have any internal conflict), so
agreement is meaningful evidence while path alone is not.

Two match shapes are distinguished:

- **exact** — the path is unchanged; the new release holds the same stints.
- **extended** — the old path is a strict *prefix* of the new one, meaning stints
  were appended after the old snapshot. This is the case exact matching misses
  entirely, and it is where re-arrestment shows up.

## What this will not do

A candidate set with more than one member is reported as **ambiguous** with no
pathway emitted. Roughly 8% of people cannot be re-identified at all, and the
publisher publishes no crosswalk. Those cases are listed rather than guessed,
because attaching the wrong person's detention history is worse than reporting
none: a wrong pathway is indistinguishable from a true one to whoever reads it.

Usage:

    python link_releases.py OLD_IDS.json --new-dir NEW_DIR --out-json linked.json

    # or feed the saved text outputs of an earlier export
    python link_releases.py old_outputs.txt --old-dir OLD_DIR --new-dir NEW_DIR
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from collections.abc import Iterable, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import duckdb

from ice_detention_pathway import (
    Pathway,
    fetch_pathway,
    format_pathway,
    pathway_to_dict,
)
from ice_history import build_bulk_json, build_bulk_text, parse_identifiers_payload

LINK_SCHEMA_VERSION = 1

ARRESTS_FILE = "arrests-latest.parquet"
DETENTION_FILE = "detention-stints-latest.parquet"
FACILITIES_FILE = "facilities-latest.parquet"

IDENTIFIER_LINE = re.compile(r"^Identifier:\s*(\S+)\s*$", re.MULTILINE)
TIMELINE_STEP = re.compile(
    r"\[Book-in:\s*(\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}) UTC\]"
    r"\[Book-out:\s*(\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}) UTC\]"
    r"\[Facility:\s*(?P<name>.*?):(?P<code>[A-Z0-9]+)\]"
)

MATCH_EXACT = "exact"
MATCH_EXTENDED = "extended"
MATCH_CLOSED_AND_EXTENDED = "closed_and_extended"
MATCH_AMBIGUOUS = "ambiguous"
MATCH_NOT_FOUND = "not_found"
MATCH_NO_SOURCE = "no_old_data"
MATCH_UNVALIDATED = "unvalidated"


class LinkError(Exception):
    """Raised when a release pair cannot be read at all."""


@dataclass(frozen=True)
class Candidate:
    """One new-release person that could be the same as an old one."""

    new_id: str
    grew_by: int
    match: str = MATCH_EXACT


@dataclass
class Link:
    """The verdict for one old identifier."""

    old_id: str
    label: str | None = None
    status: str = MATCH_NOT_FOUND
    new_id: str | None = None
    grew_by: int = 0
    candidates: list[str] = field(default_factory=list)
    note: str = ""
    pathway: Pathway | None = None


def segment_expression(alias: str) -> str:
    """SQL building one stint's fingerprint segment."""
    return (
        f"{alias}.detention_facility_code || '|' || "
        f"strftime({alias}.book_in_date_time, '%Y-%m-%dT%H:%M:%S') || '|' || "
        f"coalesce(strftime({alias}.book_out_date_time, '%Y-%m-%dT%H:%M:%S'), '?')"
    )


def _people_table(
    connection: duckdb.DuckDBPyConnection, table: str, detention_file: Path
) -> None:
    """Materialize per-person path fingerprints and demographics for one release."""
    order = (
        "ORDER BY book_in_date_time NULLS LAST, book_out_date_time NULLS LAST, "
        "detention_facility_code"
    )
    connection.execute(
        f"""
        CREATE TABLE {table} AS
        SELECT unique_identifier AS uid,
               any_value(birth_year) AS birth_y,
               any_value(gender) AS gender,
               list({segment_expression('d')} {order}) AS path
        FROM read_parquet(?) d
        WHERE unique_identifier IS NOT NULL
        GROUP BY 1
        """,
        [str(detention_file)],
    )


@dataclass
class ReleasePair:
    """Two releases loaded for matching."""

    connection: duckdb.DuckDBPyConnection
    old_file: Path | None
    new_file: Path

    @classmethod
    def open(
        cls, old_file: Path | None, new_file: Path
    ) -> ReleasePair:
        if not new_file.is_file():
            raise LinkError(f"New detention file not found: {new_file}")
        if old_file is not None and not old_file.is_file():
            raise LinkError(f"Old detention file not found: {old_file}")
        connection = duckdb.connect(database=":memory:")
        connection.execute("SET TimeZone = 'UTC'")
        _people_table(connection, "new_people", new_file)
        if old_file is not None:
            _people_table(connection, "old_people", old_file)
        return cls(connection, old_file, new_file)

    def close(self) -> None:
        self.connection.close()

    def old_profiles(self, ids: Sequence[str]) -> dict[str, tuple[Any, str, list[str]]]:
        """Return `(birth_year, gender, path)` for the given old identifiers."""
        if self.old_file is None or not ids:
            return {}
        placeholders = ", ".join("?" for _ in ids)
        rows = self.connection.execute(
            f"""
            SELECT uid, birth_y, gender, path FROM old_people
            WHERE uid IN ({placeholders})
            """,
            list(ids),
        ).fetchall()
        return {row[0]: (row[1], row[2], row[3]) for row in rows}

    def candidates_for(
        self, birth_year: Any, gender: Any, path: Sequence[str]
    ) -> list[Candidate]:
        """Every new-release person plausibly the same person, agreeing demographically.

        Three shapes are accepted, all keyed on the ordered fingerprint chain:

        ``exact``               the new path is identical;
        ``extended``            the new path starts with the old path and adds stints;
        ``closed_and_extended`` the old path's final stint was still open, so a later
                               release may now carry its book-out. The stints before it
                               must still match exactly and the final stint must keep
                               the same facility and book-in, which anchors the chain.

        Returns an empty list when demographics are unavailable, because a path
        match cannot be validated without them.
        """
        if birth_year is None or gender is None or not path:
            return []
        head = list(path[:-1])
        last_facility, last_book_in, last_book_out = path[-1].split("|")
        tail_was_open = last_book_out == "?"
        rows = self.connection.execute(
            """
            SELECT uid, len(path),
                   path = ? AS is_exact,
                   list_slice(path, 1, ?) = ? AS is_prefix
            FROM new_people
            WHERE path[1] = ?
              AND birth_y = ? AND gender = ?
              AND (
                    list_slice(path, 1, ?) = ?
                 OR (?
                     AND len(path) >= ?
                     AND list_slice(path, 1, ?) = ?
                     AND starts_with(path[?], ? || '|' || ? || '|')
                  )
              )
            """,
            [
                list(path),
                len(path),
                list(path),
                path[0],
                birth_year,
                gender,
                len(path),
                list(path),
                tail_was_open,
                len(path),
                len(path) - 1,
                head,
                len(path),
                last_facility,
                last_book_in,
            ],
        ).fetchall()
        candidates: list[Candidate] = []
        for uid, length, is_exact, is_prefix in rows:
            if is_exact:
                match = MATCH_EXACT
            elif is_prefix:
                match = MATCH_EXTENDED
            else:
                match = MATCH_CLOSED_AND_EXTENDED
            candidates.append(
                Candidate(
                    new_id=uid, grew_by=int(length) - len(path), match=match
                )
            )
        return candidates


def classify(old_id: str, candidates: Sequence[Candidate]) -> tuple[str, str | None, int]:
    """Turn a candidate set into a verdict. Never guesses between candidates."""
    if not candidates:
        return MATCH_NOT_FOUND, None, 0
    if len(candidates) > 1:
        return MATCH_AMBIGUOUS, None, 0
    only = candidates[0]
    return only.match, only.new_id, only.grew_by


def link_people(
    pair: ReleasePair, old_ids: Sequence[str]
) -> list[Link]:
    """Resolve old identifiers to new ones using old-release demographics."""
    profiles = pair.old_profiles(old_ids)
    links: list[Link] = []
    for old_id in old_ids:
        profile = profiles.get(old_id)
        if pair.old_file is None:
            links.append(
                Link(
                    old_id=old_id,
                    status=MATCH_UNVALIDATED,
                    note="No old dataset supplied, so a path match could not be "
                    "validated against birth year and gender. Pass --old-dir.",
                )
            )
            continue
        if profile is None:
            links.append(
                Link(
                    old_id=old_id,
                    status=MATCH_NOT_FOUND,
                    note="Identifier absent from the old detention file.",
                )
            )
            continue
        birth_year, gender, path = profile
        if not path:
            links.append(
                Link(old_id=old_id, status=MATCH_NO_SOURCE, note="No stints recorded.")
            )
            continue
        if birth_year is None or gender is None:
            links.append(
                Link(
                    old_id=old_id,
                    status=MATCH_NOT_FOUND,
                    note=(
                        "The old release records no birth year or gender for this "
                        "person, so a path match cannot be validated."
                    ),
                )
            )
            continue
        candidates = pair.candidates_for(birth_year, gender, path)
        status, new_id, grew = classify(old_id, candidates)
        note = ""
        if status == MATCH_AMBIGUOUS:
            note = (
                f"{len(candidates)} new-release people share this path and "
                "birth year, so the person cannot be identified without guessing."
            )
        elif status == MATCH_NOT_FOUND:
            note = (
                "No person in the new release shares this detention path with the "
                "same birth year and gender: the person is either absent from this "
                "release or their recorded stints changed shape."
            )
        links.append(
            Link(
                old_id=old_id,
                status=status,
                new_id=new_id,
                grew_by=grew,
                candidates=[] if status != MATCH_AMBIGUOUS else [c.new_id for c in candidates],
                note=note,
            )
        )
    return links


def identifiers_from_outputs(text: str) -> list[str]:
    """Pull identifiers out of saved pathway text.

    The rendered output starts with `Identifier: <id>`, so a saved export can be
    re-linked without also keeping the original query list.
    """
    found: list[str] = []
    for match in IDENTIFIER_LINE.finditer(text):
        value = match.group(1)
        if value not in found:
            found.append(value)
    return found


def read_inputs(path: Path) -> list[tuple[str, str | None]]:
    """Read a JSON identifier list or a file of saved pathway outputs."""
    raw = path.read_text(encoding="utf-8", errors="replace")
    stripped = raw.lstrip()
    if stripped.startswith(("[", "{")):
        return parse_identifiers_payload(raw)
    ids = identifiers_from_outputs(raw)
    if not ids:
        raise LinkError(
            f"{path} contains no `Identifier:` lines. Expected saved pathway "
            "output, or a JSON array of identifiers."
        )
    return [(identifier, None) for identifier in ids]


def attach_pathways(
    links: Iterable[Link],
    new_dir: Path,
) -> list[dict[str, Any]]:
    """Fetch each confirmed new identifier's pathway and build bulk results.

    Unconfirmed links are carried through with their failure reason so the
    export accounts for every input rather than silently dropping the hard cases.
    """
    arrests_file = new_dir / ARRESTS_FILE
    detention_file = new_dir / DETENTION_FILE
    facilities_file = new_dir / FACILITIES_FILE
    results: list[dict[str, Any]] = []

    for link in links:
        if link.new_id is None:
            results.append(
                {
                    "identifier": link.old_id,
                    "label": link.label,
                    "status": link.status,
                    "new_identifier": None,
                    "stints_appended": 0,
                    "pathway": None,
                    "text": None,
                    "detail": link.note or link.status,
                    "candidates": link.candidates,
                }
            )
            continue
        try:
            pathway = fetch_pathway(
                link.new_id,
                arrests_file=arrests_file,
                detention_file=detention_file,
                facilities_file=facilities_file,
            )
        except LookupError:
            results.append(
                {
                    "identifier": link.old_id,
                    "label": link.label,
                    "status": MATCH_NOT_FOUND,
                    "new_identifier": link.new_id,
                    "stints_appended": 0,
                    "pathway": None,
                    "text": None,
                    "detail": "Matched, but the new identifier resolved to no rows.",
                    "candidates": [],
                }
            )
            continue
        link.pathway = pathway
        pathway_dict = pathway_to_dict(pathway)
        results.append(
            {
                "identifier": link.old_id,
                "label": link.label,
                "status": "ok",
                "link_confidence": "validated",
                "new_identifier": link.new_id,
                "match_type": MATCH_EXACT if link.grew_by == 0 else MATCH_EXTENDED,
                "stints_appended": link.grew_by,
                "pathway": pathway_dict,
                "text": format_pathway(pathway),
                "detail": (
                    f"{MATCH_EXACT if link.grew_by == 0 else MATCH_EXTENDED}; "
                    f"{pathway_dict['totals']['stays']} stays, "
                    f"{pathway_dict['totals']['detention_rows']} rows, "
                    f"+{link.grew_by} stint(s) since the old export"
                ),
                "candidates": [],
            }
        )
    return results


def link_report(results: Sequence[dict[str, Any]]) -> dict[str, Any]:
    """Counts per verdict, for the JSON bundle header."""
    counts: dict[str, int] = {}
    for result in results:
        counts[result["status"]] = counts.get(result["status"], 0) + 1
    confirmed = sum(
        1
        for result in results
        if result["status"] == "ok" and result.get("link_confidence") == "validated"
    )
    return {
        "total": len(results),
        "confirmed": confirmed,
        "unconfirmed": len(results) - confirmed,
        "by_status": dict(sorted(counts.items())),
        "method": (
            "Path fingerprint (ordered facility + book-in + book-out chain) matched "
            "as a prefix against the new release, requiring birth_year and gender "
            "agreement. Ambiguous candidate sets are reported, never guessed."
        ),
    }


def _resolve(path: Path | None) -> Path | None:
    return path.expanduser() if path else None


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        prog="link-releases",
        description=(
            "Re-identify people across DDP data releases and export their current "
            "pathways. Identifiers are re-salted each release; this rebuilds the "
            "mapping from the detention path itself."
        ),
    )
    parser.add_argument(
        "inputs",
        type=Path,
        help=(
            "JSON array of old identifiers, or a text file of saved pathway "
            "outputs (identifiers are read from their `Identifier:` lines)."
        ),
    )
    parser.add_argument(
        "--old-dir",
        type=Path,
        default=None,
        help=(
            "Directory holding the OLD release's Parquet files. Required for a "
            "validated match: it supplies birth year and gender to check against."
        ),
    )
    parser.add_argument(
        "--new-dir", type=Path, required=True, help="Directory holding the NEW release's files."
    )
    parser.add_argument("--out-json", type=Path, default=None, help="Write the JSON bundle here.")
    parser.add_argument("--out-text", type=Path, default=None, help="Write the text report here.")
    parser.add_argument(
        "--out-crosswalk",
        type=Path,
        default=None,
        help="Write just the old->new identifier mapping as JSON.",
    )
    return parser.parse_args(argv)


def main(argv: Sequence[str] | None = None) -> int:
    args = parse_args(argv)
    try:
        items = read_inputs(args.inputs)
    except (OSError, LinkError, ValueError) as exc:
        print(f"Could not read inputs: {exc}", file=sys.stderr)
        return 1

    old_file = None
    if args.old_dir:
        old_file = Path(_resolve(args.old_dir) or args.old_dir) / DETENTION_FILE

    try:
        pair = ReleasePair.open(old_file, Path(args.new_dir) / DETENTION_FILE)
    except LinkError as exc:
        print(f"Could not open releases: {exc}", file=sys.stderr)
        return 1

    try:
        labels = {identifier: label for identifier, label in items}
        links = link_people(pair, [identifier for identifier, _ in items])
        for link in links:
            link.label = labels.get(link.old_id)
        results = attach_pathways(links, Path(args.new_dir))
    finally:
        pair.close()

    report = link_report(results)
    bundle = {
        "schema_version": LINK_SCHEMA_VERSION,
        "link_report": report,
        **build_bulk_json(results),
    }

    print(f"old identifiers:      {report['total']}")
    print(f"confirmed:            {report['confirmed']}")
    print(f"unconfirmed:          {report['unconfirmed']}")
    for status, count in report["by_status"].items():
        print(f"  {status:<12} {count:>7,}")

    if args.out_crosswalk:
        crosswalk = [
            {
                "old_identifier": result["identifier"],
                "new_identifier": result.get("new_identifier"),
                # A crosswalk's whole point is how sure the mapping is, so a
                # confirmed link reports its match shape rather than a bare
                # "ok": `exact` (path unchanged) or `extended` (stints added).
                "status": (
                    str(result.get("match_type") or "ok")
                    if result["status"] == "ok"
                    else result["status"]
                ),
                "stints_appended": result.get("stints_appended", 0),
            }
            for result in results
        ]
        args.out_crosswalk.write_text(
            json.dumps({"schema_version": LINK_SCHEMA_VERSION, "links": crosswalk}, indent=2)
            + "\n",
            encoding="utf-8",
        )
        print(f"crosswalk written:    {args.out_crosswalk}")

    if args.out_json:
        args.out_json.write_text(json.dumps(bundle, indent=2) + "\n", encoding="utf-8")
        print(f"JSON bundle written:  {args.out_json}")
    if args.out_text:
        args.out_text.write_text(build_bulk_text(results), encoding="utf-8")
        print(f"text report written:  {args.out_text}")
    if not args.out_json and not args.out_text and not args.out_crosswalk:
        print(json.dumps(bundle, indent=2))

    return 0 if report["unconfirmed"] == 0 else 2


if __name__ == "__main__":
    raise SystemExit(main())
