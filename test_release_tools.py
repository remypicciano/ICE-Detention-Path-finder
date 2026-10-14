"""Tests for the release-crossing tools.

`emit_moves.py` renders identifiers in the delimited MOVES format and marks
every block with the release it came from. `link_releases.py` maps identifiers
from an older export onto the re-salted identifiers of the current release.

Both tools read files by the publisher's own names, so the fixtures are written
into a directory as `arrests-latest.parquet`, `detention-stints-latest.parquet`
and `facilities-latest.parquet` — the same layout a downloaded release has.
"""

from __future__ import annotations

import json
import sys
from datetime import UTC, datetime
from pathlib import Path

import pyarrow as pa
import pyarrow.parquet as pq
import pytest

import emit_moves
import link_releases
from ice_detention_pathway import release_watermark
from ice_history import build_bulk_text

ARRESTS = "arrests-latest.parquet"
DETENTION = "detention-stints-latest.parquet"
FACILITIES = "facilities-latest.parquet"


def _ts(value: str | None) -> datetime | None:
    if value is None:
        return None
    return datetime.fromisoformat(value).replace(tzinfo=UTC)


def write_release(
    directory: Path,
    *,
    arrests: list[dict],
    stints: list[dict],
    generation: str = "NEW",
) -> Path:
    """Write a release directory shaped like the publisher's Parquet files.

    `generation` selects the column the tag is derived from: the current
    release publishes `event_landmark`, the earlier one
    `apprehension_site_landmark`.
    """
    directory.mkdir(parents=True, exist_ok=True)
    landmark_col = "event_landmark" if generation == "NEW" else "apprehension_site_landmark"
    # The current release publishes the publisher's normalized city and
    # corrected state beside the hand-typed originals; the earlier one does
    # not. Tests exercise precedence by supplying `city_common` / `state_filled`
    # explicitly; otherwise the normalized columns mirror the raw values.
    normalized_fields = (
        [
            pa.field("apprehension_city_common_case", pa.string()),
            pa.field("apprehension_state_filled_in", pa.string()),
        ]
        if generation == "NEW"
        else []
    )

    arrest_schema = pa.schema(
        [
            pa.field("unique_identifier", pa.string()),
            pa.field("apprehension_date_time", pa.timestamp("us", tz="UTC")),
            pa.field("apprehension_date", pa.date32()),
            pa.field("apprehension_city_original", pa.string()),
            *normalized_fields,
            pa.field("apprehension_state_original", pa.string()),
            pa.field(landmark_col, pa.string()),
            pa.field("age_at_apprehension_approximate", pa.float64()),
            pa.field("birth_year", pa.int32()),
        ]
    )
    def normalized(row: dict) -> dict:
        """The publisher-normalized columns for one row (NEW generation only)."""
        if generation != "NEW":
            return {}
        return {
            "apprehension_city_common_case": row.get("city_common", row.get("city")),
            "apprehension_state_filled_in": row.get(
                "state_filled", row.get("state")
            ),
        }

    arrest_rows = [
        {
            "unique_identifier": row["uid"],
            "apprehension_date_time": _ts(row.get("when")),
            "apprehension_date": None,
            "apprehension_city_original": row.get("city"),
            **normalized(row),
            "apprehension_state_original": row.get("state"),
            landmark_col: row.get("landmark"),
            "age_at_apprehension_approximate": row.get("age"),
            "birth_year": row.get("birth_year"),
        }
        for row in arrests
    ]
    pq.write_table(
        pa.Table.from_pylist(arrest_rows, schema=arrest_schema),
        directory / ARRESTS,
    )

    stint_schema = pa.schema(
        [
            ("unique_identifier", pa.string()),
            ("stay_ID", pa.string()),
            ("stint_ID", pa.string()),
            ("book_in_date_time", pa.timestamp("us", tz="UTC")),
            ("book_out_date_time", pa.timestamp("us", tz="UTC")),
            ("detention_facility", pa.string()),
            ("detention_facility_code", pa.string()),
            ("birth_year", pa.int32()),
            ("gender", pa.string()),
            ("age_at_stint_book_in_approximate", pa.int32()),
            ("federal_court_district_of_confinement", pa.string()),
            ("detention_release_reason", pa.string()),
            ("final_program", pa.string()),
            ("duplicate_drop_row", pa.bool_()),
            ("row_original", pa.int32()),
        ]
    )
    stint_rows = [
        {
            "unique_identifier": row["uid"],
            "stay_ID": row["stay_id"],
            "stint_ID": row.get("stint_id"),
            "book_in_date_time": _ts(row["book_in"]),
            "book_out_date_time": _ts(row.get("book_out")),
            "detention_facility": row["facility"],
            "detention_facility_code": row["code"],
            "birth_year": row.get("birth_year"),
            "gender": row.get("gender"),
            "age_at_stint_book_in_approximate": row.get("age"),
            "federal_court_district_of_confinement": row.get("district"),
            "detention_release_reason": row.get("release_reason"),
            "final_program": row.get("program"),
            "duplicate_drop_row": False,
            "row_original": 1,
        }
        for row in stints
    ]
    pq.write_table(
        pa.Table.from_pylist(stint_rows, schema=stint_schema),
        directory / DETENTION,
    )

    facility_schema = pa.schema(
        [
            ("detention_facility_code", pa.string()),
            ("name", pa.string()),
            ("federal_court_district_of_confinement", pa.string()),
        ]
    )
    codes = sorted({row["code"] for row in stints})
    pq.write_table(
        pa.Table.from_pylist(
            [
                {
                    "detention_facility_code": code,
                    "name": f"Facility {code}",
                    "federal_court_district_of_confinement": "District of Test",
                }
                for code in codes
            ],
            schema=facility_schema,
        ),
        directory / FACILITIES,
    )
    return directory


def write_inputs(path: Path, payloads: list[dict]) -> Path:
    path.write_text(json.dumps(payloads), encoding="utf-8")
    return path


# A path that survives between releases: one closed stint, one still open.
OLD_PATH = [
    dict(
        uid="old-uid-1",
        stay_id="old-uid-1_2024-01-01 10:00:00",
        book_in="2024-01-01T10:00:00",
        book_out="2024-01-05T10:00:00",
        facility="Facility CEN_A",
        code="CEN_A",
        birth_year=1987,
        gender="M",
    ),
    dict(
        uid="old-uid-1",
        stay_id="old-uid-1_2024-01-01 10:00:00",
        book_in="2024-02-01T10:00:00",
        book_out=None,
        facility="Facility CEN_B",
        code="CEN_B",
        birth_year=1987,
        gender="M",
    ),
]


def path_for(uid: str, extra: list[dict] | None = None) -> list[dict]:
    """The canonical two-stint path, re-labelled for `uid`.

    The old export's identifiers never appear in a new release, so a fixture
    standing in for the new release has to carry the same path under a
    different identifier — exactly the situation the linker exists to solve.
    """
    rows = []
    for stint in OLD_PATH:
        row = dict(stint, uid=uid)
        row["stay_id"] = stint["stay_id"].replace("old-uid-1", uid)
        rows.append(row)
    for stint in extra or []:
        rows.append(dict(stint, uid=uid))
    return rows


def test_watermark_names_the_generation_and_how_far_the_data_runs(tmp_path) -> None:
    write_release(
        tmp_path,
        arrests=[dict(uid="a", when="2024-01-02T03:04:00", birth_year=1987)],
        stints=[dict(OLD_PATH[0], uid="a"), dict(OLD_PATH[1], uid="a")],
        generation="NEW",
    )
    summary = release_watermark(tmp_path / ARRESTS, tmp_path / DETENTION)

    assert summary.startswith("dataset-NEW data-up-to=")
    assert summary.endswith("2024-02-01")


def test_watermark_is_OLD_for_the_previous_generation(tmp_path) -> None:
    write_release(
        tmp_path,
        arrests=[dict(uid="a", when="2024-01-02T03:04:00")],
        stints=[dict(OLD_PATH[0], uid="a")],
        generation="OLD",
    )
    summary = release_watermark(tmp_path / ARRESTS, tmp_path / DETENTION)

    assert summary.startswith("dataset-OLD ")


def run_emit(tmp_path: Path, ids: list[dict], release_dir: Path) -> str:
    """Run `emit_moves.py` the way a user does: argv in, output file out."""
    inputs = write_inputs(tmp_path / "ids.json", ids)
    out = tmp_path / "moves.txt"
    old_argv = sys.argv
    try:
        sys.argv = [
            "emit_moves.py",
            str(inputs),
            "--new-dir",
            str(release_dir),
            "-o",
            str(out),
        ]
        assert emit_moves.main() == 0
    finally:
        sys.argv = old_argv
    return out.read_text(encoding="utf-8")


def test_emit_marks_the_block_with_its_dataset_and_uses_the_arrow_delimiter(
    tmp_path,
) -> None:
    release = write_release(
        tmp_path / "release",
        arrests=[
            dict(
                uid="uid-new-1",
                when="2024-01-02T03:04:00",
                city="Chicago",
                state="ILLINOIS",
                landmark="CHICAGO GENERAL AREA",
                age=30.0,
                birth_year=1987,
            )
        ],
        stints=[
            dict(
                OLD_PATH[0],
                uid="uid-new-1",
                district="District of Test",
                age=30,
            )
        ],
    )
    inputs = write_inputs(tmp_path / "ids.json", [{"identifier": "uid-new-1", "label": "A"}])
    out = tmp_path / "moves.txt"
    manifest = tmp_path / "moves.manifest.json"
    old_argv = sys.argv
    try:
        sys.argv = [
            "emit_moves.py",
            str(inputs),
            "--new-dir",
            str(release),
            "-o",
            str(out),
            "--manifest",
            str(manifest),
        ]
        assert emit_moves.main() == 0
    finally:
        sys.argv = old_argv

    lines = out.read_text(encoding="utf-8").splitlines()
    assert lines[0] == "dataset-NEW data-up-to=2024-01-02"
    moves = next(line for line in lines if line.startswith("MOVES: "))
    assert " -> [Book-in: 2024-01-01 10:00:00 UTC][Book-out: 2024-01-05 10:00:00 UTC]" in moves
    assert moves.endswith("Facility CEN_A:CEN_A")
    # One block: header fields, the MOVES line, then the identifier itself.
    assert lines[-1] == "uid-new-1"

    payload = json.loads(manifest.read_text(encoding="utf-8"))
    assert payload["dataset"] == "NEW"
    assert "retrieved=" in payload["fingerprint"]


def test_blocks_are_separated_by_the_double_slash(tmp_path) -> None:
    release = write_release(
        tmp_path / "release",
        arrests=[],
        stints=[
            *path_for("uid-1"),
            *path_for("uid-2"),
        ],
    )
    text = run_emit(
        tmp_path, [{"identifier": "uid-1"}, {"identifier": "uid-2"}], release
    )

    blocks = text.strip().split("\n//\n")
    assert len(blocks) == 2
    assert blocks[0].splitlines()[-1] == "uid-1"
    assert blocks[1].splitlines()[-1] == "uid-2"
    assert all(block.startswith("dataset-NEW data-up-to=") for block in blocks)


def test_emit_dob_is_the_birth_year_even_without_an_arrest_record(tmp_path) -> None:
    release = write_release(
        tmp_path / "release",
        arrests=[],  # nobody in this pathway has an arrest row
        stints=[
            dict(
                OLD_PATH[0],
                uid="uid-no-arrest",
                district="District of Test",
                age=37,
            )
        ],
    )
    text = run_emit(tmp_path, [{"identifier": "uid-no-arrest"}], release)

    # The birth year comes from the detention rows; the court district is a
    # different fact and must never leak into the dob line.
    assert "dob -1987\n" in text
    assert "dob -District of Test" not in text
    assert "district-District of Test" in text
    assert "(NO ARREST RECORD IN THIS DATASET)" in text


def test_emit_location_excludes_placeholder_landmarks(tmp_path) -> None:
    """AUDIT A0.3: `... GENERAL AREA` is a coverage statement, not a place.

    With no recorded city the MOVES line must fall through to the state
    rather than present the placeholder as the arrest's location.
    """
    release = write_release(
        tmp_path / "release",
        arrests=[
            dict(
                uid="uid-ph",
                when="2024-01-02T03:04:00",
                city=None,
                state="TEXAS",
                landmark="DALLAS COUNTY GENERAL AREA",
                birth_year=1987,
            )
        ],
        stints=[dict(OLD_PATH[0], uid="uid-ph")],
    )
    text = run_emit(tmp_path, [{"identifier": "uid-ph"}], release)

    moves = next(line for line in text.splitlines() if line.startswith("MOVES: "))
    assert "DALLAS COUNTY GENERAL AREA" not in moves
    assert "TEXAS" in moves


def test_emit_location_prefers_the_normalized_city_and_state(tmp_path) -> None:
    """AUDIT A0.4: the raw hand-typed columns are never silently substituted."""
    release = write_release(
        tmp_path / "release",
        arrests=[
            dict(
                uid="uid-norm",
                when="2024-01-02T03:04:00",
                city="CONROE",
                city_common="Conroe",
                state="texs",
                state_filled="TEXAS",
                birth_year=1987,
            )
        ],
        stints=[dict(OLD_PATH[0], uid="uid-norm")],
    )
    text = run_emit(tmp_path, [{"identifier": "uid-norm"}], release)

    moves = next(line for line in text.splitlines() if line.startswith("MOVES: "))
    assert "Conroe, TEXAS" in moves
    assert "CONROE" not in moves
    assert "texs" not in moves


def test_emit_refuses_identifiers_the_release_does_not_hold(tmp_path) -> None:
    release = write_release(
        tmp_path / "release",
        arrests=[],
        stints=[dict(OLD_PATH[0], uid="someone-else")],
    )
    old_argv = sys.argv
    try:
        sys.argv = [
            "emit_moves.py",
            str(write_inputs(tmp_path / "ids.json", [{"identifier": "absent"}])),
            "--new-dir",
            str(release),
            "-o",
            str(tmp_path / "moves.txt"),
        ]
        with pytest.raises(SystemExit) as excinfo:
            emit_moves.main()
    finally:
        sys.argv = old_argv
    assert "Not present in this release" in str(excinfo.value)


def link_main(args: list[str]) -> int:
    return link_releases.main(args)


def test_old_identifier_resolves_to_the_new_re_salt(tmp_path) -> None:
    old_dir = write_release(
        tmp_path / "old", arrests=[], stints=OLD_PATH, generation="OLD"
    )
    new_dir = write_release(
        tmp_path / "new",
        arrests=[],
        stints=[
            *path_for(
                "new-uid-1",
                [
                    # Appended since the old export: the match is `extended`.
                    dict(
                        OLD_PATH[0],
                        stay_id="new-uid-1_2025-03-01 10:00:00",
                        book_in="2025-03-01T10:00:00",
                        book_out="2025-03-05T10:00:00",
                    )
                ],
            ),
            # Same path, different person: demographics must reject it.
            dict(path_for("impostor")[0], birth_year=1991),
        ],
    )
    crosswalk = tmp_path / "crosswalk.json"

    status = link_main(
        [
            str(write_inputs(tmp_path / "ids.json", ["old-uid-1"])),
            "--old-dir",
            str(old_dir),
            "--new-dir",
            str(new_dir),
            "--out-crosswalk",
            str(crosswalk),
        ]
    )

    assert status == 0
    links = json.loads(crosswalk.read_text(encoding="utf-8"))["links"]
    assert links == [
        {
            "old_identifier": "old-uid-1",
            "new_identifier": "new-uid-1",
            "status": "extended",
            "stints_appended": 1,
        }
    ]


def test_closed_and_extended_is_reported_as_such(tmp_path) -> None:
    """A final stint that gained its book-out changed the path.

    `stints_appended` stays 0 here, so a crosswalk that re-derived the match
    shape from it would call this link `exact` — the strongest claim it can
    make — while the path on record actually differs.
    """
    old_dir = write_release(
        tmp_path / "old", arrests=[], stints=OLD_PATH, generation="OLD"
    )
    new_stints = path_for("new-uid-1")
    new_stints[1]["book_out"] = "2024-03-05T10:00:00"
    new_dir = write_release(tmp_path / "new", arrests=[], stints=new_stints)
    crosswalk = tmp_path / "crosswalk.json"

    status = link_main(
        [
            str(write_inputs(tmp_path / "ids.json", ["old-uid-1"])),
            "--old-dir",
            str(old_dir),
            "--new-dir",
            str(new_dir),
            "--out-crosswalk",
            str(crosswalk),
        ]
    )

    assert status == 0
    links = json.loads(crosswalk.read_text(encoding="utf-8"))["links"]
    assert links == [
        {
            "old_identifier": "old-uid-1",
            "new_identifier": "new-uid-1",
            "status": "closed_and_extended",
            "stints_appended": 0,
        }
    ]


def test_ambiguous_candidate_sets_are_reported_never_guessed(tmp_path) -> None:
    old_dir = write_release(
        tmp_path / "old", arrests=[], stints=OLD_PATH, generation="OLD"
    )
    new_dir = write_release(
        tmp_path / "new",
        arrests=[],
        stints=[
            *path_for("twin-a"),
            *path_for("twin-b"),
        ],
    )
    crosswalk = tmp_path / "crosswalk.json"
    report = tmp_path / "report.txt"

    status = link_main(
        [
            str(write_inputs(tmp_path / "ids.json", ["old-uid-1"])),
            "--old-dir",
            str(old_dir),
            "--new-dir",
            str(new_dir),
            "--out-crosswalk",
            str(crosswalk),
            "--out-text",
            str(report),
        ]
    )

    assert status == 2
    links = json.loads(crosswalk.read_text(encoding="utf-8"))["links"]
    assert links[0]["status"] == "ambiguous"
    assert links[0]["new_identifier"] is None
    assert "cannot be identified without guessing" in report.read_text(encoding="utf-8")


def test_text_report_explains_a_miss_instead_of_printing_error_none(tmp_path) -> None:
    old_dir = write_release(
        tmp_path / "old", arrests=[], stints=OLD_PATH, generation="OLD"
    )
    new_dir = write_release(
        tmp_path / "new",
        arrests=[],
        stints=[dict(path_for("someone-else")[0], birth_year=1970)],
    )
    report = tmp_path / "report.txt"

    link_main(
        [
            str(write_inputs(tmp_path / "ids.json", ["old-uid-1"])),
            "--old-dir",
            str(old_dir),
            "--new-dir",
            str(new_dir),
            "--out-text",
            str(report),
        ]
    )

    text = report.read_text(encoding="utf-8")
    assert "ERROR: None" not in text
    assert "No person in the new release shares this detention path" in text


def test_bulk_text_falls_back_to_the_detail_field() -> None:
    rendered = build_bulk_text(
        [
            {
                "identifier": "gone",
                "label": None,
                "status": "not_found",
                "detail": "the person is absent from this release",
            }
        ]
    )

    assert "ERROR: the person is absent from this release" in rendered


def test_emit_reads_identifiers_from_saved_output(tmp_path) -> None:
    saved = tmp_path / "saved.txt"
    saved.write_text(
        "dataset-NEW data-up-to=2026-08-06\n"
        "Identifier: aaaa000000000000000000000000000000000001\n"
        "//\n"
        "Identifier: aaaa000000000000000000000000000000000002\n",
        encoding="utf-8",
    )

    assert emit_moves.read_ids(saved) == [
        "aaaa000000000000000000000000000000000001",
        "aaaa000000000000000000000000000000000002",
    ]


def test_saved_pathway_output_can_be_read_as_linker_input(tmp_path) -> None:
    saved = tmp_path / "outputs.txt"
    saved.write_text(
        "Identifier: old-uid-1\nsome pathway\n",
        encoding="utf-8",
    )

    assert link_releases.read_inputs(saved) == [("old-uid-1", None)]
