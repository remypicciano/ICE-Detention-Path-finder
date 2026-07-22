"""Retain NYC arrests and every detention stint belonging to those people."""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

import duckdb


NYC_AOR = "New York City Area of Responsibility"
ARRESTS_FILENAME = "arrests-latest.parquet"
DETENTION_FILENAME = "detention-stints-latest.parquet"
JOINED_FILENAME = "joined-arrests-detention-stays-latest.parquet"
REQUIRED_FILENAMES = (ARRESTS_FILENAME, DETENTION_FILENAME, JOINED_FILENAME)


class NYCFilterError(Exception):
    """Raised when the NYC arrest-cohort replacement cannot complete safely."""


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


def retain_nyc_arrest_cohort(project_dir: Path) -> list[FilterResult]:
    """Keep NYC arrests plus all stints for their identifiers, then replace files."""
    arrests_file = project_dir / ARRESTS_FILENAME
    detention_file = project_dir / DETENTION_FILENAME
    joined_file = project_dir / JOINED_FILENAME
    tasks = (arrests_file, detention_file, joined_file)
    missing = [source.name for source in tasks if not source.is_file()]
    if missing:
        raise NYCFilterError("Missing required file(s): " + ", ".join(missing))

    arrests_sql = sql_literal(str(arrests_file))
    aor_sql = sql_literal(NYC_AOR)
    predicates = {
        ARRESTS_FILENAME: f"apprehension_aor = {aor_sql}",
        DETENTION_FILENAME: f"""
            unique_identifier IN (
                SELECT unique_identifier
                FROM read_parquet({arrests_sql})
                WHERE apprehension_aor = {aor_sql}
                  AND unique_identifier IS NOT NULL
            )
        """,
        JOINED_FILENAME: f"apprehension_aor = {aor_sql}",
    }

    connection = duckdb.connect(database=":memory:")
    generated: list[tuple[Path, Path]] = []
    results: list[FilterResult] = []
    try:
        for source in tasks:
            predicate = predicates[source.name]
            source_sql = sql_literal(str(source))
            temporary = source.with_name(
                source.name + ".nyc-arrest-cohort.tmp.parquet"
            )
            temporary.unlink(missing_ok=True)

            original_schema = connection.execute(
                "DESCRIBE SELECT * FROM read_parquet(?)", [str(source)]
            ).fetchall()
            original_rows, expected_rows = connection.execute(
                f"""
                SELECT count(*), count(*) FILTER (WHERE {predicate})
                FROM read_parquet({source_sql})
                """
            ).fetchone()

            connection.execute(
                f"""
                COPY (
                    SELECT * FROM read_parquet({source_sql}) WHERE {predicate}
                ) TO {sql_literal(str(temporary))}
                (FORMAT PARQUET, COMPRESSION ZSTD)
                """
            )
            generated.append((source, temporary))

            filtered_schema = connection.execute(
                "DESCRIBE SELECT * FROM read_parquet(?)", [str(temporary)]
            ).fetchall()
            temporary_sql = sql_literal(str(temporary))
            retained_rows, invalid_rows = connection.execute(
                f"""
                SELECT count(*),
                       count(*) FILTER (WHERE ({predicate}) IS NOT TRUE)
                FROM read_parquet({temporary_sql})
                """
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
