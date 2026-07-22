import duckdb
import pytest

from nyc_filter import NYC_AOR, NYCFilterError, retain_nyc_aor_rows, sql_literal


FILE_COLUMNS = (
    ("arrests-latest.parquet", "apprehension_aor"),
    ("detention-stints-latest.parquet", "book_in_aor"),
    ("joined-arrests-detention-stays-latest.parquet", "apprehension_aor"),
)


def write_test_parquet(path, column: str) -> None:
    connection = duckdb.connect()
    connection.execute(
        f"""
        COPY (
            SELECT * FROM (VALUES
                (1, ?),
                (2, 'Another Area'),
                (3, ?)
            ) AS rows(row_number, {column})
        ) TO {sql_literal(str(path))} (FORMAT PARQUET)
        """,
        [NYC_AOR, NYC_AOR],
    )
    connection.close()


def test_retain_nyc_aor_rows_filters_every_file(tmp_path) -> None:
    for filename, column in FILE_COLUMNS:
        write_test_parquet(tmp_path / filename, column)

    results = retain_nyc_aor_rows(tmp_path)

    assert len(results) == 3
    assert all(result.original_rows == 3 for result in results)
    assert all(result.retained_rows == 2 for result in results)
    connection = duckdb.connect()
    for filename, column in FILE_COLUMNS:
        values = connection.execute(
            f"SELECT DISTINCT {column} FROM read_parquet(?)",
            [str(tmp_path / filename)],
        ).fetchall()
        assert values == [(NYC_AOR,)]
    connection.close()


def test_retain_nyc_aor_rows_requires_all_files(tmp_path) -> None:
    with pytest.raises(NYCFilterError, match="Missing required file"):
        retain_nyc_aor_rows(tmp_path)
