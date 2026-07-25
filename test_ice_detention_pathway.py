from datetime import datetime, timedelta, timezone

import duckdb
import pytest

from ice_detention_pathway import (
    ArrestEvent,
    LookupError,
    clean_location,
    fetch_timeline,
    format_full_timeline,
    format_timeline,
    format_timestamp,
    normalize_identifier,
    override_arrest_location,
)
from nyc_filter import sql_literal


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


def test_fetch_timeline_orders_oldest_to_most_recent_with_locations(tmp_path) -> None:
    arrests_file = tmp_path / "arrests.parquet"
    detention_file = tmp_path / "detention.parquet"
    facilities_file = tmp_path / "facilities.parquet"
    connection = duckdb.connect()
    connection.execute(
        f"""
        COPY (
            SELECT 'person-1' AS unique_identifier,
                   TIMESTAMPTZ '2023-12-31 18:00:00+00' AS apprehension_date_time,
                   DATE '2023-12-31' AS apprehension_date,
                   'Arrest Place' AS apprehension_site_landmark,
                   'NEW YORK' AS apprehension_state_filled_in,
                   'New York City Area of Responsibility' AS apprehension_aor
        )
        TO {sql_literal(str(arrests_file))} (FORMAT PARQUET)
        """
    )
    connection.execute(
        f"""
        COPY (
            SELECT * FROM (VALUES
                ('person-1', TIMESTAMPTZ '2025-02-01 09:00:00+00',
                 'Unmapped Recent Name', 'RECENT',
                 TIMESTAMPTZ '2025-02-02 09:00:00+00', 2),
                ('person-1', TIMESTAMPTZ '2024-01-01 08:00:00+00',
                 'Unmapped Old Name', 'OLD',
                 TIMESTAMPTZ '2024-01-03 08:00:00+00', 1)
            ) AS rows(
                unique_identifier,
                book_in_date_time,
                detention_facility,
                detention_facility_code,
                book_out_date_time,
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

    _identifier, arrest, rows = fetch_timeline(
        "person-1",
        arrests_file=arrests_file,
        detention_file=detention_file,
        facilities_file=facilities_file,
    )

    assert rows[0][1] == "Old Center:OLD"
    assert rows[1][1] == "Recent Center:RECENT"
    timeline = format_full_timeline(arrest, rows)
    assert timeline.startswith("2023-12-31 18:00:00 UTC, Arrest Place->")
    assert (
        "[Book-in: 2024-01-01 08:00:00 UTC]"
        "[Book-out: 2024-01-03 08:00:00 UTC], "
        "Old Center:OLD"
    ) in timeline
    assert timeline.index("Old Center:OLD") < timeline.index(
        "Recent Center:RECENT"
    )


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
