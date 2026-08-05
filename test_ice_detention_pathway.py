from datetime import UTC, datetime, timedelta, timezone

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
              release reason, program, aor, row_original) plus optional
              trailing fields: stint_ID, duplicate_drop_row,
              detainee_classification, case_status, case_threat_level,
              final_order_yes_no, final_order_date, departed_date,
              final_charge.
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
    arrest_select = (
        f"SELECT * FROM (VALUES {arrest_values}) AS rows("
        "unique_identifier, apprehension_date_time, apprehension_date, "
        "apprehension_site_landmark, apprehension_state_filled_in, "
        "apprehension_aor)"
        if arrests
        else (
            "SELECT CAST(NULL AS VARCHAR) AS unique_identifier, "
            "CAST(NULL AS TIMESTAMPTZ) AS apprehension_date_time, "
            "CAST(NULL AS DATE) AS apprehension_date, "
            "CAST(NULL AS VARCHAR) AS apprehension_site_landmark, "
            "CAST(NULL AS VARCHAR) AS apprehension_state_filled_in, "
            "CAST(NULL AS VARCHAR) AS apprehension_aor WHERE FALSE"
        )
    )
    connection.execute(
        f"""
        COPY ({arrest_select})
        TO {sql_literal(str(arrests_file))} (FORMAT PARQUET)
        """
    )

    def stint_sql(stint):
        (
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
        ) = stint[:10]
        stint_id = stint[10] if len(stint) > 10 else None
        duplicate = stint[11] if len(stint) > 11 else False
        classification = stint[12] if len(stint) > 12 else None
        case_status = stint[13] if len(stint) > 13 else None
        threat_level = stint[14] if len(stint) > 14 else None
        final_order = stint[15] if len(stint) > 15 else None
        final_order_date = stint[16] if len(stint) > 16 else None
        departed = stint[17] if len(stint) > 17 else None
        final_charge = stint[18] if len(stint) > 18 else None
        return (
            f"({sql_literal(identifier)}, {sql_literal(stay_id)}, {book_in}, "
            f"{sql_literal(facility)}, {sql_literal(code)}, "
            f"{book_out if book_out else 'CAST(NULL AS TIMESTAMPTZ)'}, "
            f"{sql_literal(reason) if reason else 'NULL'}, "
            f"{sql_literal(program) if program else 'NULL'}, "
            f"{sql_literal(aor) if aor else 'NULL'}, {row_original}, "
            f"{sql_literal(stint_id) if stint_id else 'NULL'}, "
            f"{'TRUE' if duplicate else 'FALSE'}, "
            f"{sql_literal(classification) if classification else 'NULL'}, "
            f"{sql_literal(case_status) if case_status else 'NULL'}, "
            f"{sql_literal(threat_level) if threat_level else 'NULL'}, "
            f"{sql_literal(final_order) if final_order else 'NULL'}, "
            f"{sql_literal(final_order_date) if final_order_date else 'NULL'}, "
            f"{sql_literal(departed) if departed else 'NULL'}, "
            f"{sql_literal(final_charge) if final_charge else 'NULL'})"
        )

    stint_values = ",\n".join(stint_sql(stint) for stint in stints)
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
                row_original,
                stint_ID,
                duplicate_drop_row,
                detainee_classification,
                case_status,
                case_threat_level,
                final_order_yes_no,
                final_order_date,
                departed_date,
                final_charge
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
            datetime(2024, 1, 2, 10, 0, tzinfo=UTC),
            "Center A",
            datetime(2024, 1, 3, 11, 0, tzinfo=UTC),
        ),
        (
            datetime(2024, 2, 4, 12, 0, tzinfo=UTC),
            "Center B",
            None,
        ),
    ]
    result = format_timeline(rows)
    assert "Center A" in result
    assert " -> " in result
    assert "Center B" in result
    assert "[Book-out: UNKNOWN - CURRENTLY HELD (?)][Facility: Center B]" in result


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
    assert timeline.startswith("2023-12-31 18:00:00 UTC, Arrest Place ->")
    assert (
        "[Book-in: 2024-01-01 08:00:00 UTC]"
        "[Book-out: 2024-01-03 08:00:00 UTC]"
        "[Facility: Old Center:OLD]"
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
        "[first stint — final_program: Border Patrol; "
        "book_in_aor: Houston Area of Responsibility]"
    ) in timeline
    assert "=== RELEASED (Paroled); NOT IN ICE CUSTODY FOR 396 days ===" in timeline
    assert "[STAY 2 of 2] 2025-12-30 10:36:35 UTC, Federal Plaza ->" in timeline

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
    assert timeline.startswith("NO ARREST RECORD IN THIS DATASET")
    assert "[first stint — final_program: Border Patrol" in timeline
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
        datetime(2025, 1, 2, 12, 0, tzinfo=UTC),
        None,
        "Arrest Place",
    )
    rows = [
        (
            datetime(2025, 1, 1, 12, 0, tzinfo=UTC),
            "Center",
            datetime(2025, 1, 3, 12, 0, tzinfo=UTC),
        )
    ]

    result = format_full_timeline(arrest, rows)

    assert result.startswith(
        "(DISCREPANCY: arrest date is after first detention book-in) "
    )


def test_sub_day_arrest_inversion_is_not_flagged() -> None:
    """Paperwork filed hours after booking is not impossible chronology."""
    arrest = ArrestEvent(
        datetime(2025, 1, 1, 14, 0, tzinfo=UTC), None, "Arrest Place"
    )
    rows = [
        (
            datetime(2025, 1, 1, 12, 0, tzinfo=UTC),
            "Center",
            datetime(2025, 1, 3, 12, 0, tzinfo=UTC),
        )
    ]

    assert "DISCREPANCY" not in format_full_timeline(arrest, rows)


def test_day_scale_arrest_inversion_is_still_flagged() -> None:
    arrest = ArrestEvent(
        datetime(2025, 1, 3, 12, 0, tzinfo=UTC), None, "Arrest Place"
    )
    rows = [
        (
            datetime(2025, 1, 1, 12, 0, tzinfo=UTC),
            "Center",
            datetime(2025, 1, 5, 12, 0, tzinfo=UTC),
        )
    ]

    assert format_full_timeline(arrest, rows).startswith(
        "(DISCREPANCY: arrest date is after first detention book-in) "
    )


def test_sub_day_overlap_between_stints_is_not_flagged() -> None:
    rows = [
        (
            datetime(2025, 1, 1, 12, 0, tzinfo=UTC),
            "Center A",
            datetime(2025, 1, 3, 12, 0, tzinfo=UTC),
        ),
        (
            datetime(2025, 1, 3, 6, 0, tzinfo=UTC),
            "Center B",
            datetime(2025, 1, 4, 12, 0, tzinfo=UTC),
        ),
    ]

    assert "DISCREPANCY" not in format_timeline(rows)


def test_day_scale_overlap_between_stints_is_flagged() -> None:
    rows = [
        (
            datetime(2025, 1, 1, 12, 0, tzinfo=UTC),
            "Center A",
            datetime(2025, 1, 5, 12, 0, tzinfo=UTC),
        ),
        (
            datetime(2025, 1, 3, 12, 0, tzinfo=UTC),
            "Center B",
            datetime(2025, 1, 6, 12, 0, tzinfo=UTC),
        ),
    ]

    assert "detention begins before previous book-out" in format_timeline(rows)


def test_sub_day_book_out_inversion_is_not_flagged() -> None:
    rows = [
        (
            datetime(2025, 1, 2, 12, 0, tzinfo=UTC),
            "Center",
            datetime(2025, 1, 2, 9, 0, tzinfo=UTC),
        )
    ]

    assert "DISCREPANCY" not in format_timeline(rows)


def test_format_timeline_marks_impossible_detention_dates() -> None:
    rows = [
        (
            datetime(2025, 1, 2, 12, 0, tzinfo=UTC),
            "Center",
            datetime(2025, 1, 1, 12, 0, tzinfo=UTC),
        )
    ]

    assert format_timeline(rows).startswith(
        "(DISCREPANCY: book-out is before book-in)"
    )


def test_stale_arrest_does_not_claim_a_later_stay(tmp_path) -> None:
    """A leftover older arrest must not be dumped on a later stay (C4)."""
    files = build_dataset(
        tmp_path,
        arrests=[
            ("person-a", "TIMESTAMPTZ '2024-02-08 09:19:00+00'", "Earlier Arrest"),
            ("person-a", "TIMESTAMPTZ '2024-02-08 11:42:00+00'", "Same-Day Arrest"),
        ],
        stints=[
            (
                "person-a",
                "person-a_2024-02-08",
                "TIMESTAMPTZ '2024-02-08 14:30:00+00'",
                "Old Center",
                "OLD",
                "TIMESTAMPTZ '2024-04-17 11:45:00+00'",
                "Released",
                None,
                None,
                1,
            ),
            (
                "person-a",
                "person-a_2025-06-04",
                "TIMESTAMPTZ '2025-06-04 12:41:00+00'",
                "Recent Center",
                "RECENT",
                "TIMESTAMPTZ '2025-08-08 10:00:00+00'",
                None,
                None,
                None,
                2,
            ),
        ],
    )

    pathway = fetch_pathway("person-a", *files)

    assert pathway.stays[0].arrest.location == "Same-Day Arrest"
    assert pathway.stays[1].arrest is None
    assert [arrest.location for arrest in pathway.arrests_without_stay] == [
        "Earlier Arrest"
    ]

    timeline = format_pathway(pathway)
    assert "[ARREST WITH NO RECORDED DETENTION] 2024-02-08 09:19:00 UTC" in timeline
    assert "NO ARREST RECORD IN THIS DATASET" in timeline


def test_first_stay_claims_an_old_lone_arrest(tmp_path) -> None:
    """A single arrest that precedes its only stay still pairs (C4 boundary)."""
    files = build_dataset(
        tmp_path,
        arrests=[
            ("person-b", "TIMESTAMPTZ '2024-01-01 08:00:00+00'", "Old Arrest"),
        ],
        stints=[
            (
                "person-b",
                "person-b_2024-01-10",
                "TIMESTAMPTZ '2024-01-10 12:00:00+00'",
                "Old Center",
                "OLD",
                "TIMESTAMPTZ '2024-01-20 12:00:00+00'",
                None,
                None,
                None,
                1,
            ),
        ],
    )

    pathway = fetch_pathway("person-b", *files)

    assert pathway.stays[0].arrest.location == "Old Arrest"
    assert pathway.arrests_without_stay == []


def test_same_timestamp_duplicate_arrest_does_not_claim_second_stay(
    tmp_path,
) -> None:
    """Two records of one event at the same instant open only the first stay."""
    files = build_dataset(
        tmp_path,
        arrests=[
            ("person-e", "TIMESTAMPTZ '2024-02-08 11:42:00+00'", "Arrest Record A"),
            ("person-e", "TIMESTAMPTZ '2024-02-08 11:42:00+00'", "Arrest Record B"),
        ],
        stints=[
            (
                "person-e",
                "person-e_2024-02-08",
                "TIMESTAMPTZ '2024-02-08 14:30:00+00'",
                "Old Center",
                "OLD",
                "TIMESTAMPTZ '2024-04-17 11:45:00+00'",
                "Released",
                None,
                None,
                1,
            ),
            (
                "person-e",
                "person-e_2025-06-04",
                "TIMESTAMPTZ '2025-06-04 12:41:00+00'",
                "Recent Center",
                "RECENT",
                None,
                None,
                None,
                None,
                2,
            ),
        ],
    )

    pathway = fetch_pathway("person-e", *files)

    assert pathway.stays[0].arrest is not None
    assert pathway.stays[1].arrest is None
    assert len(pathway.arrests_without_stay) == 1


def test_stay_suffix_scopes_rendering_to_that_stay(tmp_path) -> None:
    """A suffixed stay_ID renders only that stay, the others as context (B3)."""
    files = build_dataset(
        tmp_path,
        arrests=[],
        stints=[
            (
                "person-c",
                "person-c_2024-09-16",
                "TIMESTAMPTZ '2024-09-16 17:56:00+00'",
                "Old Center",
                "OLD",
                "TIMESTAMPTZ '2024-11-29 10:44:00+00'",
                "Paroled",
                "Border Patrol",
                "Houston Area of Responsibility",
                1,
                "person-c_2024-09-16 17:56:00_OLD",
            ),
            (
                "person-c",
                "person-c_2025-12-30",
                "TIMESTAMPTZ '2025-12-30 11:17:00+00'",
                "Recent Center",
                "RECENT",
                None,
                None,
                "Non-Detained Docket Control",
                "New York City Area of Responsibility",
                2,
                "person-c_2025-12-30 11:17:00_RECENT",
            ),
        ],
    )

    pathway = fetch_pathway("person-c_2025-12-30", *files)

    assert pathway.focus_stay_id == "person-c_2025-12-30"
    timeline = format_pathway(pathway)
    assert "[STAY 2 of 2]" in timeline
    assert "[CONTEXT — another stay for this person:" in timeline
    assert "[Book-in: 2025-12-30 11:17:00 UTC]" in timeline
    assert "[Book-in: 2024-09-16 17:56:00 UTC]" not in timeline


def test_stint_suffix_resolves_to_its_own_stay(tmp_path) -> None:
    """A stint_ID suffix scopes to the stay that owns the stint (B3)."""
    files = build_dataset(
        tmp_path,
        arrests=[],
        stints=[
            (
                "person-c",
                "person-c_2024-09-16",
                "TIMESTAMPTZ '2024-09-16 17:56:00+00'",
                "Old Center",
                "OLD",
                "TIMESTAMPTZ '2024-11-29 10:44:00+00'",
                "Paroled",
                "Border Patrol",
                "Houston Area of Responsibility",
                1,
                "person-c_2024-09-16 17:56:00_OLD",
            ),
            (
                "person-c",
                "person-c_2025-12-30",
                "TIMESTAMPTZ '2025-12-30 11:17:00+00'",
                "Recent Center",
                "RECENT",
                None,
                None,
                "Non-Detained Docket Control",
                "New York City Area of Responsibility",
                2,
                "person-c_2025-12-30 11:17:00_RECENT",
            ),
        ],
    )

    pathway = fetch_pathway("person-c_2025-12-30 11:17:00_RECENT", *files)

    assert pathway.focus_stay_id == "person-c_2025-12-30"
    timeline = format_pathway(pathway)
    assert "[STAY 2 of 2]" in timeline
    assert "[CONTEXT — another stay for this person:" in timeline


def test_stay_summary_and_variance_fields(tmp_path) -> None:
    """Stay-level fields come from the last stint; variance lists all values."""
    files = build_dataset(
        tmp_path,
        arrests=[],
        stints=[
            (
                "person-d",
                "person-d_2025-10-18",
                "TIMESTAMPTZ '2025-10-18 11:10:00+00'",
                "Old Center",
                "OLD",
                "TIMESTAMPTZ '2025-10-20 12:00:00+00'",
                "Transferred",
                "ERO Criminal Alien Program",
                "San Antonio Area of Responsibility",
                1,
                "person-d_2025-10-18 11:10:00_OLD",
                False,
                "Low",
                "1-Not in Removal",
                "1",
                "NO",
                None,
                None,
                "ALIEN PRESENT WITHOUT ADMISSION OR PAROLE",
            ),
            (
                "person-d",
                "person-d_2025-10-18",
                "TIMESTAMPTZ '2025-10-20 13:00:00+00'",
                "Recent Center",
                "RECENT",
                None,
                None,
                "Non-Detained Docket Control",
                "El Paso Area of Responsibility",
                2,
                "person-d_2025-10-20 13:00:00_RECENT",
                False,
                "High",
                "8-Excluded/Removed",
                "2",
                "YES",
                "2025-11-01",
                "2025-11-02",
                "FRAUD OR MISUSE OF VISA",
            ),
        ],
    )

    pathway = fetch_pathway("person-d", *files)
    stay = pathway.stays[0]

    assert stay.stint_ids == (
        "person-d_2025-10-18 11:10:00_OLD",
        "person-d_2025-10-20 13:00:00_RECENT",
    )
    assert stay.program_variance == (
        "ERO Criminal Alien Program; Non-Detained Docket Control"
    )
    assert stay.aor_variance == (
        "San Antonio Area of Responsibility; El Paso Area of Responsibility"
    )
    assert stay.summary.classification == "High"
    assert stay.summary.case_status == "8-Excluded/Removed"
    assert stay.summary.threat_level == "2"
    assert stay.summary.final_order == "YES"
    assert stay.summary.final_order_date == "2025-11-01"
    assert stay.summary.departed == "2025-11-02"
    assert stay.summary.final_charge == "FRAUD OR MISUSE OF VISA"

    timeline = format_pathway(pathway)
    assert (
        "[first stint — final_program: ERO Criminal Alien Program; "
        "book_in_aor: San Antonio Area of Responsibility]"
    ) in timeline
    assert (
        "[stint fields — final_program: Non-Detained Docket Control; "
        "book_in_aor: El Paso Area of Responsibility]"
    ) in timeline
    assert "[last stint — classification: High" in timeline
    assert "case_status: 8-Excluded/Removed" in timeline
    assert "charge: FRAUD OR MISUSE OF VISA" in timeline
