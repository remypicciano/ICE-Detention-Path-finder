from datetime import datetime, timedelta, timezone

import duckdb
import pytest

from ice_detention_pathway import (
    ArrestEvent,
    LookupError,
    clean_location,
    fetch_pathway,
    format_full_timeline,
    format_pathway,
    format_timeline,
    format_timestamp,
    normalize_identifier,
    override_arrest_location,
)


def sql_literal(value: str) -> str:
    """Quote a value for inclusion in a SQL statement."""
    return "'" + value.replace("'", "''") + "'"


def build_dataset(tmp_path, arrests, stints):
    """Write minimal arrests, detention, and facilities Parquet fixtures.

    arrests: (identifier, apprehension timestamp SQL, location)
    stints:  (identifier, stay_ID, book-in SQL, facility, code, book-out SQL,
              release reason, program, aor, row_original)
    """
    arrests_file = tmp_path / "arrests.parquet"
    detention_file = tmp_path / "detention.parquet"
    facilities_file = tmp_path / "facilities.parquet"
    connection = duckdb.connect()

    arrest_values = ",\n".join(
        f"({sql_literal(identifier)}, {moment}, CAST({moment} AS DATE), "
        f"{sql_literal(location)}, CAST(NULL AS VARCHAR), CAST(NULL AS VARCHAR))"
        for identifier, moment, location in arrests
    )
    connection.execute(
        f"""
        COPY (
            SELECT * FROM (VALUES {arrest_values}) AS rows(
                unique_identifier,
                apprehension_date_time,
                apprehension_date,
                apprehension_site_landmark,
                apprehension_state_filled_in,
                apprehension_aor
            )
        ) TO {sql_literal(str(arrests_file))} (FORMAT PARQUET)
        """
    )

    stint_values = ",\n".join(
        f"({sql_literal(identifier)}, {sql_literal(stay_id)}, {book_in}, "
        f"{sql_literal(facility)}, {sql_literal(code)}, "
        f"{book_out if book_out else 'CAST(NULL AS TIMESTAMPTZ)'}, "
        f"{sql_literal(reason) if reason else 'NULL'}, "
        f"{sql_literal(program) if program else 'NULL'}, "
        f"{sql_literal(aor) if aor else 'NULL'}, {row_original})"
        for (
            identifier,
            stay_id,
            book_in,
            facility,
            code,
            book_out,
            reason,
            program,
            aor,
            row_original,
        ) in stints
    )
    connection.execute(
        f"""
        COPY (
            SELECT * FROM (VALUES {stint_values}) AS rows(
                unique_identifier,
                stay_ID,
                book_in_date_time,
                detention_facility,
                detention_facility_code,
                book_out_date_time,
                detention_release_reason,
                final_program,
                book_in_aor,
                row_original
            )
        ) TO {sql_literal(str(detention_file))} (FORMAT PARQUET)
        """
    )

    connection.execute(
        f"""
        COPY (
            SELECT * FROM (VALUES
                ('OLD', 'Old Center'),
                ('RECENT', 'Recent Center')
            ) AS rows(detention_facility_code, name)
        ) TO {sql_literal(str(facilities_file))} (FORMAT PARQUET)
        """
    )
    connection.close()
    return arrests_file, detention_file, facilities_file


def test_normalize_identifier_accepts_base_and_suffixed_values() -> None:
    assert normalize_identifier("abc123") == "abc123"
    assert normalize_identifier(" abc123_2024-01-02_3 ") == "abc123"


def test_normalize_identifier_rejects_empty_input() -> None:
    with pytest.raises(LookupError):
        normalize_identifier("  ")


def test_override_arrest_location_is_optional_and_presentation_only() -> None:
    arrest = ArrestEvent(None, None, "Original Place")

    assert override_arrest_location(arrest, "  More Precise Place  ").location == (
        "More Precise Place"
    )
    assert override_arrest_location(arrest, "  ") is arrest
    assert arrest.location == "Original Place"


def test_format_timestamp_converts_to_utc() -> None:
    eastern = timezone(timedelta(hours=-5))
    value = datetime(2024, 1, 2, 10, 30, 45, tzinfo=eastern)
    assert format_timestamp(value, "book-in") == "2024-01-02 15:30:45 UTC"


def test_format_timeline_keeps_each_row_as_a_segment() -> None:
    rows = [
        (
            datetime(2024, 1, 2, 10, 0, tzinfo=timezone.utc),
            "Center A",
            datetime(2024, 1, 3, 11, 0, tzinfo=timezone.utc),
        ),
        (
            datetime(2024, 2, 4, 12, 0, tzinfo=timezone.utc),
            "Center B",
            None,
        ),
    ]
    result = format_timeline(rows)
    assert "Center A" in result
    assert " -> " in result
    assert "Center B" in result
    assert "[Book-out: UNKNOWN - CURRENTLY HELD (?)], Center B" in result


def test_clean_location_removes_line_breaks() -> None:
    assert clean_location(" Center\n A ") == "Center A"
    assert clean_location(None) == "UNKNOWN DETENTION CENTER"


def test_single_stay_orders_oldest_to_most_recent_with_locations(tmp_path) -> None:
    files = build_dataset(
        tmp_path,
        arrests=[
            ("person-1", "TIMESTAMPTZ '2023-12-31 18:00:00+00'", "Arrest Place")
        ],
        stints=[
            (
                "person-1",
                "person-1_2024-01-01",
                "TIMESTAMPTZ '2025-02-01 09:00:00+00'",
                "Unmapped Recent Name",
                "RECENT",
                "TIMESTAMPTZ '2025-02-02 09:00:00+00'",
                None,
                None,
                None,
                2,
            ),
            (
                "person-1",
                "person-1_2024-01-01",
                "TIMESTAMPTZ '2024-01-01 08:00:00+00'",
                "Unmapped Old Name",
                "OLD",
                "TIMESTAMPTZ '2024-01-03 08:00:00+00'",
                None,
                None,
                None,
                1,
            ),
        ],
    )

    pathway = fetch_pathway("person-1", *files)
    stay = pathway.stays[0]

    assert len(pathway.stays) == 1
    assert stay.rows[0][1] == "Old Center:OLD"
    assert stay.rows[1][1] == "Recent Center:RECENT"

    timeline = format_pathway(pathway)
    assert timeline.startswith("2023-12-31 18:00:00 UTC, Arrest Place->")
    assert (
        "[Book-in: 2024-01-01 08:00:00 UTC]"
        "[Book-out: 2024-01-03 08:00:00 UTC], "
        "Old Center:OLD"
    ) in timeline
    assert timeline.index("Old Center:OLD") < timeline.index("Recent Center:RECENT")
    assert "[STAY" not in timeline


def test_separate_stays_are_not_merged_into_one_pathway(tmp_path) -> None:
    """An earlier unrelated stay must not be chained onto a later arrest."""
    files = build_dataset(
        tmp_path,
        arrests=[
            ("person-2", "TIMESTAMPTZ '2025-12-30 10:36:35+00'", "Federal Plaza")
        ],
        stints=[
            (
                "person-2",
                "person-2_2024-09-16",
                "TIMESTAMPTZ '2024-09-16 17:56:00+00'",
                "Old Facility",
                "OLD",
                "TIMESTAMPTZ '2024-11-29 10:44:00+00'",
                "Paroled",
                "Border Patrol",
                "Houston Area of Responsibility",
                1,
            ),
            (
                "person-2",
                "person-2_2025-12-30",
                "TIMESTAMPTZ '2025-12-30 11:17:00+00'",
                "Recent Facility",
                "RECENT",
                None,
                None,
                "Non-Detained Docket Control",
                "New York City Area of Responsibility",
                2,
            ),
        ],
    )

    pathway = fetch_pathway("person-2", *files)
    timeline = format_pathway(pathway)

    assert len(pathway.stays) == 2
    assert "DISCREPANCY" not in timeline
    assert "[STAY 1 of 2] NO ARREST RECORD IN THIS DATASET" in timeline
    assert (
        "(first stint — final_program: Border Patrol; "
        "book_in_aor: Houston Area of Responsibility)"
    ) in timeline
    assert "=== RELEASED (Paroled); NOT IN ICE CUSTODY FOR 396 days ===" in timeline
    assert "[STAY 2 of 2] 2025-12-30 10:36:35 UTC, Federal Plaza->" in timeline

    # The arrest belongs to the later stay only.
    assert pathway.stays[0].arrest is None
    assert pathway.stays[1].arrest is not None


def test_multiple_arrests_pair_with_their_own_stays(tmp_path) -> None:
    """Each stay takes the nearest preceding arrest, not the earliest one."""
    files = build_dataset(
        tmp_path,
        arrests=[
            ("person-3", "TIMESTAMPTZ '2024-07-09 10:12:00+00'", "First Arrest"),
            ("person-3", "TIMESTAMPTZ '2025-01-06 13:39:00+00'", "Second Arrest"),
        ],
        stints=[
            (
                "person-3",
                "person-3_2025-01-06",
                "TIMESTAMPTZ '2025-01-06 15:42:00+00'",
                "Recent Facility",
                "RECENT",
                "TIMESTAMPTZ '2025-01-20 14:00:00+00'",
                "Removed",
                None,
                None,
                1,
            ),
        ],
    )

    pathway = fetch_pathway("person-3", *files)

    assert len(pathway.stays) == 1
    assert pathway.stays[0].arrest.location == "Second Arrest"
    assert [arrest.location for arrest in pathway.arrests_without_stay] == [
        "First Arrest"
    ]

    timeline = format_pathway(pathway)
    assert "[ARREST WITH NO RECORDED DETENTION] 2024-07-09" in timeline
    assert "NO DETENTION RECORD IN THIS DATASET" in timeline


def test_detention_without_any_arrest_row_is_still_reported(tmp_path) -> None:
    """People who entered ICE custody without an ICE arrest must be findable."""
    files = build_dataset(
        tmp_path,
        arrests=[
            ("someone-else", "TIMESTAMPTZ '2024-01-01 00:00:00+00'", "Elsewhere")
        ],
        stints=[
            (
                "person-4",
                "person-4_2024-09-16",
                "TIMESTAMPTZ '2024-09-16 17:56:00+00'",
                "Old Facility",
                "OLD",
                "TIMESTAMPTZ '2024-11-29 10:44:00+00'",
                "Paroled",
                "Border Patrol",
                "Houston Area of Responsibility",
                1,
            ),
        ],
    )

    pathway = fetch_pathway("person-4", *files)
    timeline = format_pathway(pathway)

    assert len(pathway.stays) == 1
    assert pathway.stays[0].arrest is None
    assert timeline.startswith("NO ARREST RECORD IN THIS DATASET (first stint — ")
    assert "Old Center:OLD" in timeline


def test_unknown_identifier_still_fails(tmp_path) -> None:
    files = build_dataset(
        tmp_path,
        arrests=[("person-5", "TIMESTAMPTZ '2024-01-01 00:00:00+00'", "Place")],
        stints=[
            (
                "person-5",
                "person-5_2024-01-01",
                "TIMESTAMPTZ '2024-01-01 01:00:00+00'",
                "Facility",
                "OLD",
                None,
                None,
                None,
                None,
                1,
            ),
        ],
    )

    with pytest.raises(LookupError):
        fetch_pathway("nobody", *files)


def test_format_full_timeline_marks_impossible_arrest_chronology() -> None:
    arrest = ArrestEvent(
        datetime(2025, 1, 2, 12, 0, tzinfo=timezone.utc),
        None,
        "Arrest Place",
    )
    rows = [
        (
            datetime(2025, 1, 1, 12, 0, tzinfo=timezone.utc),
            "Center",
            datetime(2025, 1, 3, 12, 0, tzinfo=timezone.utc),
        )
    ]

    result = format_full_timeline(arrest, rows)

    assert result.startswith(
        "(DISCREPANCY: arrest date is after first detention book-in) "
    )


def test_sub_day_arrest_inversion_is_not_flagged() -> None:
    """Paperwork filed hours after booking is not impossible chronology."""
    arrest = ArrestEvent(
        datetime(2025, 1, 1, 14, 0, tzinfo=timezone.utc), None, "Arrest Place"
    )
    rows = [
        (
            datetime(2025, 1, 1, 12, 0, tzinfo=timezone.utc),
            "Center",
            datetime(2025, 1, 3, 12, 0, tzinfo=timezone.utc),
        )
    ]

    assert "DISCREPANCY" not in format_full_timeline(arrest, rows)


def test_day_scale_arrest_inversion_is_still_flagged() -> None:
    arrest = ArrestEvent(
        datetime(2025, 1, 3, 12, 0, tzinfo=timezone.utc), None, "Arrest Place"
    )
    rows = [
        (
            datetime(2025, 1, 1, 12, 0, tzinfo=timezone.utc),
            "Center",
            datetime(2025, 1, 5, 12, 0, tzinfo=timezone.utc),
        )
    ]

    assert format_full_timeline(arrest, rows).startswith(
        "(DISCREPANCY: arrest date is after first detention book-in) "
    )


def test_sub_day_overlap_between_stints_is_not_flagged() -> None:
    rows = [
        (
            datetime(2025, 1, 1, 12, 0, tzinfo=timezone.utc),
            "Center A",
            datetime(2025, 1, 3, 12, 0, tzinfo=timezone.utc),
        ),
        (
            datetime(2025, 1, 3, 6, 0, tzinfo=timezone.utc),
            "Center B",
            datetime(2025, 1, 4, 12, 0, tzinfo=timezone.utc),
        ),
    ]

    assert "DISCREPANCY" not in format_timeline(rows)


def test_day_scale_overlap_between_stints_is_flagged() -> None:
    rows = [
        (
            datetime(2025, 1, 1, 12, 0, tzinfo=timezone.utc),
            "Center A",
            datetime(2025, 1, 5, 12, 0, tzinfo=timezone.utc),
        ),
        (
            datetime(2025, 1, 3, 12, 0, tzinfo=timezone.utc),
            "Center B",
            datetime(2025, 1, 6, 12, 0, tzinfo=timezone.utc),
        ),
    ]

    assert "detention begins before previous book-out" in format_timeline(rows)


def test_sub_day_book_out_inversion_is_not_flagged() -> None:
    rows = [
        (
            datetime(2025, 1, 2, 12, 0, tzinfo=timezone.utc),
            "Center",
            datetime(2025, 1, 2, 9, 0, tzinfo=timezone.utc),
        )
    ]

    assert "DISCREPANCY" not in format_timeline(rows)


def test_format_timeline_marks_impossible_detention_dates() -> None:
    rows = [
        (
            datetime(2025, 1, 2, 12, 0, tzinfo=timezone.utc),
            "Center",
            datetime(2025, 1, 1, 12, 0, tzinfo=timezone.utc),
        )
    ]

    assert format_timeline(rows).startswith(
        "(DISCREPANCY: book-out is before book-in)"
    )
