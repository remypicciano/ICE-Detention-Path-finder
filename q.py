"""Run one SQL statement against the local Parquet files and print a table.

Usage:
    python q.py "SELECT ... WHERE unique_identifier = ?" IDENTIFIER

Nothing is imported, written, or persisted. DuckDB opens an in-memory database
and reads the Parquet files in place; the database disappears when the process
exits. Pass values as ? parameters rather than pasting them into the SQL.
"""

import sys

import duckdb

if len(sys.argv) < 2:
    raise SystemExit('usage: python q.py "SQL" [parameter ...]')

connection = duckdb.connect(database=":memory:")
connection.execute("SET TimeZone = 'UTC'")
result = connection.execute(sys.argv[1], sys.argv[2:])
names = [description[0] for description in result.description]
rows = [["" if value is None else str(value) for value in row] for row in result.fetchall()]

widths = [
    max([len(name)] + [len(row[index]) for row in rows])
    for index, name in enumerate(names)
]
print("  ".join(name.ljust(widths[index]) for index, name in enumerate(names)))
print("  ".join("-" * width for width in widths))
for row in rows:
    print("  ".join(row[index].ljust(widths[index]) for index in range(len(names))))
print(f"\n({len(rows)} row{'s' if len(rows) != 1 else ''})")
