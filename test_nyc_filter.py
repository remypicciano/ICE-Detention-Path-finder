import duckdb
import pytest

from nyc_filter import (
    NYC_AOR,
    NYCFilterError,
    retain_nyc_arrest_cohort,
    sql_literal,
)


def write_test_files(directory) -> None:
    connection = duckdb.connect()
    connection.execute(
        f"""
        COPY (
            SELECT * FROM (VALUES
                ('nyc-person', {sql_literal(NYC_AOR)}),
                ('second-nyc-person', {sql_literal(NYC_AOR)}),
                ('other-person', 'Another Area')
            ) AS rows(unique_identifier, apprehension_aor)
        ) TO {sql_literal(str(directory / 'arrests-latest.parquet'))}
        (FORMAT PARQUET)
        """
    )
    connection.execute(
        f"""
        COPY (
            SELECT * FROM (VALUES
                ('nyc-person', {sql_literal(NYC_AOR)}, 'NYC Center'),
                ('nyc-person', 'Another Area', 'Out-of-NYC Center'),
                ('other-person', {sql_literal(NYC_AOR)}, 'Unrelated Center'),
                ('unknown-person', 'Another Area', 'Unknown Center')
            ) AS rows(unique_identifier, book_in_aor, detention_facility)
        ) TO {sql_literal(str(directory / 'detention-stints-latest.parquet'))}
        (FORMAT PARQUET)
        """
    )
    connection.execute(
        f"""
        COPY (
            SELECT * FROM (VALUES
                ('nyc-person', {sql_literal(NYC_AOR)}),
                ('second-nyc-person', {sql_literal(NYC_AOR)}),
                ('other-person', 'Another Area')
            ) AS rows(unique_identifier, apprehension_aor)
        ) TO {sql_literal(str(directory / 'joined-arrests-detention-stays-latest.parquet'))}
        (FORMAT PARQUET)
        """
    )
    connection.close()


def test_filter_keeps_every_stint_for_nyc_arrest_identifiers(tmp_path) -> None:
    write_test_files(tmp_path)

    results = retain_nyc_arrest_cohort(tmp_path)

    assert len(results) == 3
    assert [result.retained_rows for result in results] == [2, 2, 2]

    connection = duckdb.connect()
    arrest_aors = connection.execute(
        "SELECT DISTINCT apprehension_aor FROM read_parquet(?)",
        [str(tmp_path / "arrests-latest.parquet")],
    ).fetchall()
    detention_rows = connection.execute(
        """
        SELECT unique_identifier, book_in_aor, detention_facility
        FROM read_parquet(?)
        ORDER BY detention_facility
        """,
        [str(tmp_path / "detention-stints-latest.parquet")],
    ).fetchall()
    joined_aors = connection.execute(
        "SELECT DISTINCT apprehension_aor FROM read_parquet(?)",
        [str(tmp_path / "joined-arrests-detention-stays-latest.parquet")],
    ).fetchall()
    connection.close()

    assert arrest_aors == [(NYC_AOR,)]
    assert joined_aors == [(NYC_AOR,)]
    assert detention_rows == [
        ("nyc-person", NYC_AOR, "NYC Center"),
        ("nyc-person", "Another Area", "Out-of-NYC Center"),
    ]


def test_filter_requires_all_files(tmp_path) -> None:
    with pytest.raises(NYCFilterError, match="Missing required file"):
        retain_nyc_arrest_cohort(tmp_path)
