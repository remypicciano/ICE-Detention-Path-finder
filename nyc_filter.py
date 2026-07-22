"""Destructively retain only NYC Area of Responsibility rows in project data."""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

import duckdb


NYC_AOR = "New York City Area of Responsibility"
FILE_FILTERS = (
    ("arrests-latest.parquet", "apprehension_aor"),
    ("detention-stints-latest.parquet", "book_in_aor"),
    ("joined-arrests-detention-stays-latest.parquet", "apprehension_aor"),
)


class NYCFilterError(Exception):
    """Raised when the NYC-only replacement cannot be completed safely."""


@dataclass(frozen=True)
class FilterResult:
    filename: str
    original_rows: int
    retained_rows: int

    @property
    def removed_rows(self) -> int:
        return self.original_rows - self.retained_rows


def sql_literal(value: str) -> str:
    return "'" + value.replace("'", "''") + "'"


def retain_nyc_aor_rows(project_dir: Path) -> list[FilterResult]:
    """Validate filtered copies of all datasets, then replace every original."""
    tasks = [
        (project_dir / filename, column) for filename, column in FILE_FILTERS
    ]
    missing = [source.name for source, _column in tasks if not source.is_file()]
    if missing:
        raise NYCFilterError("Missing required file(s): " + ", ".join(missing))

    connection = duckdb.connect(database=":memory:")
    generated: list[tuple[Path, Path]] = []
    results: list[FilterResult] = []
    try:
        for source, column in tasks:
            temporary = source.with_name(source.name + ".nyc-filtered.tmp.parquet")
            temporary.unlink(missing_ok=True)

            original_schema = connection.execute(
                "DESCRIBE SELECT * FROM read_parquet(?)", [str(source)]
            ).fetchall()
            original_rows, expected_rows = connection.execute(
                f"""
                SELECT count(*), count(*) FILTER (WHERE {column} = ?)
                FROM read_parquet(?)
                """,
                [NYC_AOR, str(source)],
            ).fetchone()

            connection.execute(
                f"""
                COPY (
                    SELECT * FROM read_parquet(?) WHERE {column} = ?
                ) TO {sql_literal(str(temporary))}
                (FORMAT PARQUET, COMPRESSION ZSTD)
                """,
                [str(source), NYC_AOR],
            )
            generated.append((source, temporary))

            filtered_schema = connection.execute(
                "DESCRIBE SELECT * FROM read_parquet(?)", [str(temporary)]
            ).fetchall()
            retained_rows, invalid_rows = connection.execute(
                f"""
                SELECT count(*),
                       count(*) FILTER (WHERE {column} IS DISTINCT FROM ?)
                FROM read_parquet(?)
                """,
                [NYC_AOR, str(temporary)],
            ).fetchone()

            if [row[:2] for row in original_schema] != [
                row[:2] for row in filtered_schema
            ]:
                raise NYCFilterError(f"Schema validation failed for {source.name}.")
            if retained_rows != expected_rows or invalid_rows:
                raise NYCFilterError(f"Row validation failed for {source.name}.")

            results.append(
                FilterResult(source.name, original_rows, retained_rows)
            )

        for source, temporary in generated:
            os.replace(temporary, source)
        return results
    finally:
        connection.close()
        for _source, temporary in generated:
            temporary.unlink(missing_ok=True)

