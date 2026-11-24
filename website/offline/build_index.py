#!/usr/bin/env python3
"""Build the precomputed lookup index read by the Cloudflare Worker.

The free Workers plan cannot run Python or DuckDB. This local step first
refreshes the current Parquet files from the URLs in `data-sources.json`, then
streams every arrest and detention row once through the unchanged desktop
engine. Each shard is a gzipped JSON object mapping identifier, stay_ID, or
stint_ID to a pathway document or an alias. Upload with `upload_index.py`.
"""

from __future__ import annotations

import argparse
import gzip
import hashlib
import json
import shutil
from collections import OrderedDict
from collections.abc import Iterator
from datetime import UTC, date, datetime
from pathlib import Path
from typing import Any

import duckdb

from fetch_data import DownloadError
from ice_detention_pathway import (
    ARREST_LANDMARK_LINEAGE,
    ArrestEvent,
    Pathway,
    _summary_value,
    arrest_location_expr,
    arrest_provenance_expr,
    available_columns,
    detention_order_by,
    detention_select,
    detention_sources,
    first_present_column,
    format_pathway,
    group_stays,
    missing_source_fields,
    pair_arrests_with_stays,
    pathway_to_dict,
)
from website.offline import datasets, sources

DEFAULT_OUT = Path(__file__).resolve().parents[2] / "website" / ".build" / "r2"
BATCH = 20_000
MAX_OPEN_SHARDS = 256


def as_datetime(value: Any) -> Any:
    """DuckDB sometimes returns ISO strings for timestamp columns."""
    if not isinstance(value, str):
        return value
    text = value.strip()
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    return datetime.fromisoformat(text)


def as_date(value: Any) -> Any:
    """Normalize DuckDB date strings to date objects for ArrestEvent."""
    if isinstance(value, datetime):
        return value.date()
    if not isinstance(value, str):
        return value
    return date.fromisoformat(value[:10])


class ShardSet:
    """Write shard JSON incrementally without holding every shard in memory."""

    def __init__(self, directory: Path, max_open: int = MAX_OPEN_SHARDS) -> None:
        self.directory = directory
        self.directory.mkdir(parents=True, exist_ok=True)
        self.max_open = max_open
        self._handles: OrderedDict[str, Any] = OrderedDict()
        self._started: set[str] = set()
        self._counts: dict[str, int] = {}
        self._keys: set[str] = set()
        self.count = 0

    def _handle(self, shard: str) -> Any:
        handle = self._handles.get(shard)
        if handle is not None:
            self._handles.move_to_end(shard)
            return handle
        path = self.directory / f"{shard}.json"
        if shard in self._started:
            handle = path.open("a", encoding="utf-8")
        else:
            handle = path.open("w", encoding="utf-8")
            handle.write('{"v":1,"entries":{')
            self._started.add(shard)
        self._handles[shard] = handle
        if len(self._handles) > self.max_open:
            _old, evicted = self._handles.popitem(last=False)
            evicted.close()
        return handle

    def add(self, key: str, value: dict[str, Any]) -> None:
        if not key or key in self._keys:
            return
        self._keys.add(key)
        # 3 hex chars => 4,096 shards. Small enough for fast Worker gzip
        # decompression, few enough for the Cloudflare API upload rate limit.
        shard = hashlib.sha256(key.encode("utf-8")).hexdigest()[:3]
        handle = self._handle(shard)
        first = self._counts.get(shard, 0) == 0
        handle.write(
            ("" if first else ",")
            + json.dumps(key, ensure_ascii=False)
            + ":"
            + json.dumps(value, separators=(",", ":"), ensure_ascii=False)
        )
        self._counts[shard] = self._counts.get(shard, 0) + 1
        self.count += 1

    def finish(self) -> int:
        for _shard, handle in self._handles.items():
            handle.close()
        self._handles.clear()
        compressed = 0
        for path in sorted(self.directory.glob("*.json")):
            with path.open("r", encoding="utf-8") as source, gzip.open(
                path.with_suffix(".json.gz"), "wt", encoding="utf-8", compresslevel=6
            ) as target:
                shutil.copyfileobj(source, target)
                target.write("}}")
            # The prefix opened each shard; gzip needs the closing braces.
            path.unlink()
            compressed += 1
        return compressed


def _arrest_rows(
    connection: duckdb.DuckDBPyConnection, release: dict[str, Any], present: set[str]
) -> Iterator[tuple[str, tuple[Any, ...], str]]:
    landmark_expr, _state_expr, state_source_expr = arrest_provenance_expr(present)
    landmark_field = first_present_column(ARREST_LANDMARK_LINEAGE, present) or "event_landmark"
    sql = f"""
        SELECT unique_identifier,
               apprehension_date_time,
               apprehension_date,
               {arrest_location_expr(present)},
               {landmark_expr},
               {state_source_expr}
        FROM read_parquet(?)
        WHERE unique_identifier IS NOT NULL
        ORDER BY unique_identifier,
                 apprehension_date_time NULLS LAST,
                 apprehension_date NULLS LAST
    """
    cursor = connection.execute(sql, [str(release["arrests"])])
    while batch := cursor.fetchmany(BATCH):
        for row in batch:
            values = (
                as_datetime(row[1]),
                as_date(row[2]),
                row[3],
                row[4],
                row[5],
            )
            yield row[0], values, landmark_field


def _detention_rows(
    connection: duckdb.DuckDBPyConnection, release: dict[str, Any], present: set[str]
) -> Iterator[tuple[str, tuple[Any, ...]]]:
    sql = f"""
        SELECT d.unique_identifier, {detention_select(present)}
        {detention_sources()}
        WHERE d.unique_identifier IS NOT NULL
        ORDER BY d.unique_identifier, {detention_order_by(present)}
    """
    cursor = connection.execute(
        sql, [str(release["detention"]), str(release["facilities"])]
    )
    while batch := cursor.fetchmany(BATCH):
        for row in batch:
            values = list(row[1:])
            values[1] = as_datetime(values[1])
            values[3] = as_datetime(values[3])
            yield row[0], tuple(values)


def _merged(
    arrests: Iterator[tuple[str, tuple[Any, ...], str]],
    stints: Iterator[tuple[str, tuple[Any, ...]]],
) -> Iterator[tuple[str, list[tuple[Any, ...]], list[tuple[Any, ...]], str]]:
    """Merge two identifier-ordered streams without loading the datasets."""
    a = next(arrests, None)
    b = next(stints, None)
    while a is not None or b is not None:
        if b is None or (a is not None and a[0] < b[0]):
            identifier, row, field = a
            yield identifier, [row], [], field
            a = next(arrests, None)
        elif a is None or b[0] < a[0]:
            identifier, row = b
            yield identifier, [], [row], ""
            b = next(stints, None)
        else:
            identifier = a[0]
            field = a[2]
            arrest_rows, stint_rows = [a[1]], [b[1]]
            a = next(arrests, None)
            b = next(stints, None)
            while a is not None and a[0] == identifier:
                arrest_rows.append(a[1])
                a = next(arrests, None)
            while b is not None and b[0] == identifier:
                stint_rows.append(b[1])
                b = next(stints, None)
            yield identifier, arrest_rows, stint_rows, field


def _augment(doc: dict[str, Any], pathway: Pathway, landmark_field: str) -> dict[str, Any]:
    """Add the few renderer-only values pathway_to_dict intentionally omits."""
    doc["_text"] = format_pathway(pathway, {})
    for stay_doc, stay in zip(doc["stays"], pathway.stays, strict=True):
        stay_doc["_all_programs"] = list(stay.all_programs)
        stay_doc["_all_aors"] = list(stay.all_aors)
        # pathway_to_dict() drops empty summary values, but the renderer keeps
        # them so a manual entry can fill a field the release left blank.
        stay_doc["_summary_fields"] = (
            {
                ("final_charge" if label == "charge" else label): _summary_value(value)
                for label, value in stay.summary.pairs()
            }
            if stay.summary is not None
            else None
        )
        if stay_doc.get("arrest") is not None:
            stay_doc["arrest"]["_landmark_field"] = landmark_field
    for arrest_doc in doc["arrests_without_stay"]:
        arrest_doc["_landmark_field"] = landmark_field
    return doc


def build_release(
    release: dict[str, Any], out: Path, limit: int | None = None
) -> dict[str, Any]:
    directory = out / "releases" / release["key"] / "shards"
    if directory.exists():
        shutil.rmtree(directory)
    shards = ShardSet(directory)
    # Two cursors on one DuckDB connection share the current result set, which
    # corrupts the interleaved merge. Use one connection per stream.
    arrest_connection = duckdb.connect(database=":memory:")
    detention_connection = duckdb.connect(database=":memory:")
    try:
        for connection in (arrest_connection, detention_connection):
            connection.execute("SET TimeZone = 'UTC'")
        arrest_present = available_columns(arrest_connection, release["arrests"])
        detention_present = available_columns(detention_connection, release["detention"])
        missing = tuple(missing_source_fields(detention_present))
        arrests = _arrest_rows(arrest_connection, release, arrest_present)
        stints = _detention_rows(detention_connection, release, detention_present)
        for built, (identifier, arrest_rows, stint_rows, landmark_field) in enumerate(
            _merged(arrests, stints), start=1
        ):
            if limit is not None and built > limit:
                break
            arrest_events = [
                ArrestEvent(row[0], row[1], row[2], row[3], row[4], landmark_field)
                for row in arrest_rows
            ]
            stays = group_stays(stint_rows)
            stays, unmatched = pair_arrests_with_stays(arrest_events, stays)
            pathway = Pathway(
                identifier, stays, unmatched, missing_fields=missing
            )
            doc = _augment(pathway_to_dict(pathway), pathway, landmark_field)
            shards.add(identifier, doc)
            for stay in pathway.stays:
                focus = stay.stay_id
                if focus:
                    shards.add(focus, {"_alias": identifier, "_focus": focus})
                for stint_id in stay.stint_ids:
                    if stint_id and stint_id != focus:
                        shards.add(stint_id, {"_alias": identifier, "_focus": focus})
            if built % 25_000 == 0:
                print(f"  {release['key']}: {built:,} identifiers", flush=True)
    finally:
        arrest_connection.close()
        detention_connection.close()
    files = shards.finish()
    return {
        "key": release["key"],
        "label": release["label"],
        "tag": release["tag"],
        "watermark": datasets.release_header(release),
        "identifiers": shards.count,
        "shards": files,
    }


def build_demo(out: Path) -> None:
    result = datasets.demo()
    doc = json.loads(result["pathway_json"])
    doc["_text"] = result["text"]
    (out / "demo.json").write_text(
        json.dumps(
            {"doc": doc, "status": result["status"], "text": result["text"]},
            separators=(",", ":"),
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out", type=Path, default=DEFAULT_OUT)
    parser.add_argument("--limit", type=int, default=None, help="identifiers per release (smoke test)")
    parser.add_argument("--releases", nargs="*", default=None, help="release keys to build")
    parser.add_argument(
        "--refresh-data",
        action="store_true",
        help="download all three current Parquet files even when they are recent",
    )
    parser.add_argument(
        "--no-download",
        action="store_true",
        help="use only the Parquet files already on disk",
    )
    parser.add_argument(
        "--data-max-age-days",
        type=int,
        default=sources.DEFAULT_MAX_AGE_DAYS,
        help="refresh a present file older than this many days (default: 7)",
    )
    args = parser.parse_args()
    args.out.mkdir(parents=True, exist_ok=True)

    if not args.no_download:
        try:
            sources.ensure_current(
                datasets.data_root(),
                refresh=args.refresh_data,
                max_age_days=args.data_max_age_days,
                out_dir=args.out,
            )
        except DownloadError as exc:
            raise SystemExit(f"Dataset refresh failed: {exc}") from exc

    releases = datasets.discover_releases()
    if args.releases:
        releases = [r for r in releases if r["key"] in args.releases]
    if not releases:
        raise SystemExit("No complete dataset release found.")

    built_at = datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")
    info = []
    for release in releases:
        print(f"Building {release['key']} from {release['arrests'].parent}", flush=True)
        info.append(build_release(release, args.out, args.limit))
    build_demo(args.out)
    manifest = {
        "schema_version": 1,
        "built_at": built_at,
        "app_version": datasets.APP_VERSION,
        "default_release": info[0]["key"],
        "releases": info,
    }
    (args.out / "manifest.json").write_text(
        json.dumps(manifest, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
    )
    print(f"Built {len(info)} release(s), {sum(r['identifiers'] for r in info):,} keys → {args.out}")


if __name__ == "__main__":
    main()
