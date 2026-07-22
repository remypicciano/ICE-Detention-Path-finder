"""Inspect local Parquet files without loading an entire dataset into memory."""

from __future__ import annotations

import argparse
from pathlib import Path
from typing import Sequence

import pyarrow as pa
import pyarrow.parquet as pq


DEFAULT_PREVIEW_ROWS = 5
MAX_PREVIEW_ROWS = 100
DEFAULT_PREVIEW_COLUMNS = 8
MAX_CELL_WIDTH = 36


def parse_column_positions(value: str) -> list[int]:
    """Parse a comma-separated list of one-based column positions."""
    try:
        positions = [int(item.strip()) for item in value.split(",")]
    except ValueError as exc:
        raise argparse.ArgumentTypeError(
            "column positions must be comma-separated integers"
        ) from exc

    if not positions or any(position < 1 for position in positions):
        raise argparse.ArgumentTypeError("column positions must be at least 1")
    if len(set(positions)) != len(positions):
        raise argparse.ArgumentTypeError("column positions must not repeat")
    return positions


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Show Parquet metadata, numbered schema fields, and a small row preview. "
            "Only the preview columns are read."
        )
    )
    parser.add_argument(
        "files",
        nargs="*",
        type=Path,
        help="Parquet files to inspect (default: every *.parquet in this directory)",
    )
    parser.add_argument(
        "--rows",
        type=int,
        default=DEFAULT_PREVIEW_ROWS,
        help=f"preview rows per file, from 0 to {MAX_PREVIEW_ROWS} (default: 5)",
    )
    parser.add_argument(
        "--columns",
        type=parse_column_positions,
        help=(
            "comma-separated one-based positions to preview, such as 1,14,24 "
            f"(default: first {DEFAULT_PREVIEW_COLUMNS})"
        ),
    )
    args = parser.parse_args()
    if not 0 <= args.rows <= MAX_PREVIEW_ROWS:
        parser.error(f"--rows must be between 0 and {MAX_PREVIEW_ROWS}")
    return args


def discover_files(requested: Sequence[Path]) -> list[Path]:
    files = list(requested) if requested else sorted(Path.cwd().glob("*.parquet"))
    if not files:
        raise SystemExit("No Parquet files found.")

    invalid = [path for path in files if not path.is_file()]
    if invalid:
        raise SystemExit(f"Not a readable file: {invalid[0]}")
    return files


def selected_fields(
    schema: pa.Schema, requested_positions: Sequence[int] | None
) -> list[pa.Field]:
    if requested_positions is None:
        return list(schema)[:DEFAULT_PREVIEW_COLUMNS]

    invalid = [position for position in requested_positions if position > len(schema)]
    if invalid:
        raise ValueError(
            f"column position {invalid[0]} is outside this file's 1-{len(schema)} range"
        )
    return [schema.field(position - 1) for position in requested_positions]


def display_value(value: object) -> str:
    text = "<NULL>" if value is None else str(value).replace("\n", "\\n")
    if len(text) > MAX_CELL_WIDTH:
        return text[: MAX_CELL_WIDTH - 1] + "…"
    return text


def print_preview(parquet_file: pq.ParquetFile, fields: Sequence[pa.Field], rows: int) -> None:
    if rows == 0 or not fields:
        return

    names = [field.name for field in fields]
    batches = parquet_file.iter_batches(batch_size=rows, columns=names)
    batch = next(batches, None)
    if batch is None:
        print("\nPreview: file contains no rows")
        return

    print(f"\nPreview ({len(batch)} row(s); tab-separated; long values truncated):")
    print("\t".join(names))
    for row in batch.to_pylist():
        print("\t".join(display_value(row[name]) for name in names))


def inspect_file(
    path: Path, rows: int, requested_positions: Sequence[int] | None
) -> None:
    parquet_file = pq.ParquetFile(path)
    metadata = parquet_file.metadata
    schema = parquet_file.schema_arrow

    print("=" * 80)
    print(f"File: {path}")
    print(f"Size: {path.stat().st_size:,} bytes")
    print(f"Rows: {metadata.num_rows:,}")
    print(f"Row groups: {metadata.num_row_groups:,}")
    print(f"Columns: {metadata.num_columns:,}")
    print("\nSchema (one-based positions):")
    for position, field in enumerate(schema, start=1):
        nullability = "nullable" if field.nullable else "required"
        print(f"  {position:>3}. {field.name} [{field.type}; {nullability}]")

    fields = selected_fields(schema, requested_positions)
    print_preview(parquet_file, fields, rows)


def main() -> int:
    args = parse_args()
    files = discover_files(args.files)

    failed = False
    for path in files:
        try:
            inspect_file(path, args.rows, args.columns)
        except (OSError, ValueError, pa.ArrowException) as exc:
            failed = True
            print(f"Could not inspect {path}: {exc}")
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
