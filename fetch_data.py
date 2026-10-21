"""Download the Deportation Data Project Parquet files this project reads.

Downloads stream to a temporary file beside the target, are checked for
readability and required columns, and only then replace the existing file with
an atomic `os.replace`. An interrupted or corrupt download therefore never
destroys data that already works.

This is the dataset half of the project's network use — the other is
`update_check.py`, the optional release check — and both run only when a
person explicitly asks for them. Lookups never make network requests.

Source URLs live in `data-sources.json` beside the application so they can be
corrected without rebuilding. The same file is shipped inside the application
and released next to it, so a copy that has been moved or deleted still finds
its URLs. See `data-sources.example.json`.
"""

from __future__ import annotations

import argparse
import errno
import io
import json
import os
import ssl
import sys
import urllib.error
import urllib.request
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from pathlib import Path

import pyarrow.parquet as pq

from ice_detention_pathway import APP_VERSION, missing_source_fields

CONFIG_FILENAME = "data-sources.json"
EXAMPLE_CONFIG_FILENAME = "data-sources.example.json"
DOWNLOAD_SUFFIX = ".download.tmp"
USER_AGENT = (
    f"ICE-Detention-Pathway/{APP_VERSION} (+https://github.com/remypicciano)"
)
CHUNK_SIZE = 1 << 20

# A download is only accepted if it is readable Parquet containing the columns
# the lookup depends on. This catches an HTML error page saved under a .parquet
# name, a truncated transfer, and a URL that points at the wrong dataset.
#
# These are the columns the lookup cannot work without, which is deliberately a
# short list. The publisher reshapes its files between releases — the 2026-09
# arrivals file renamed `apprehension_site_landmark` to `event_landmark` and
# added normalized city columns — so the fields the renderer merely *reads* are
# discovered at query time rather than demanded at download time. Those are
# reported by `report_absent_fields` instead of treated as a broken download.
REQUIRED_COLUMNS: dict[str, tuple[str, ...]] = {
    "arrests-latest.parquet": ("unique_identifier", "apprehension_date_time"),
    "detention-stints-latest.parquet": (
        "unique_identifier",
        "book_in_date_time",
        "book_out_date_time",
    ),
    "facilities-latest.parquet": ("detention_facility_code", "name"),
    "detention-stays-latest.parquet": ("stay_ID", "stay_book_in_date_time"),
    "joined-arrests-detention-stays-latest.parquet": (
        "unique_identifier",
        "stay_ID",
        "has_detention_stay",
    ),
}

# Which optional renderer fields belong to which dataset, so an update can say
# what it stopped publishing.
ABSENT_FIELD_DATASETS = {
    "arrests-latest.parquet": (
        "apprehension_site_landmark",
        "apprehension_city_original",
        "apprehension_city_common_case",
        "apprehension_state_filled_in",
        "apprehension_state_fill_source",
        "arresting_agency",
        "operation",
    ),
    "detention-stints-latest.parquet": None,
}


class DownloadError(Exception):
    """Raised when a dataset cannot be downloaded or fails validation."""


@dataclass(frozen=True)
class DownloadResult:
    filename: str
    bytes_written: int
    rows: int
    replaced_existing: bool
    absent_fields: tuple[str, ...] = ()


ProgressCallback = Callable[[str, int, int | None], None]


def certificate_context() -> ssl.SSLContext:
    """Return an SSL context that also works inside a frozen application.

    A PyInstaller bundle does not inherit the interpreter's certificate
    bundle, so HTTPS fails at runtime on macOS and Windows even though it works
    when run from source. Prefer certifi when it is available.
    """
    try:
        import certifi

        return ssl.create_default_context(cafile=certifi.where())
    except ImportError:
        return ssl.create_default_context()


def config_path(project_dir: Path) -> Path:
    return project_dir / CONFIG_FILENAME


def bundled_config_path() -> Path | None:
    """Locate the copy of the config that ships with the application.

    A packaged build has no source tree next to it, so the URLs have to travel
    inside the bundle for the download feature to work at all. Returns None
    when no copy is available rather than raising: the caller decides what a
    missing config means.
    """
    candidates: list[Path] = []
    frozen_bundle = getattr(sys, "_MEIPASS", None)
    if frozen_bundle:
        candidates.append(Path(frozen_bundle) / CONFIG_FILENAME)
    if getattr(sys, "frozen", False):
        candidates.append(Path(sys.executable).resolve().parent / CONFIG_FILENAME)
    candidates.append(Path(__file__).resolve().parent / CONFIG_FILENAME)
    return next((candidate for candidate in candidates if candidate.is_file()), None)


def load_sources(project_dir: Path) -> dict[str, str]:
    """Read the filename-to-URL mapping, rejecting anything unusable.

    A config beside the application wins, so a wrong URL can be corrected
    without rebuilding; otherwise the copy shipped with the application is
    used.
    """
    path = config_path(project_dir)
    if not path.is_file():
        shipped = bundled_config_path()
        if shipped is None:
            raise DownloadError(
                f"No {CONFIG_FILENAME} found in {project_dir}, and this build "
                f"ships no copy of its own. Copy {EXAMPLE_CONFIG_FILENAME} to "
                f"{CONFIG_FILENAME} and put the current download URLs in it."
            )
        path = shipped
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise DownloadError(f"Could not read {path}: {exc}") from exc

    sources = raw.get("sources", raw)
    if not isinstance(sources, dict) or not sources:
        raise DownloadError(f"{path} contains no 'sources' mapping.")

    cleaned: dict[str, str] = {}
    for filename, url in sources.items():
        if not isinstance(url, str) or not url.strip():
            continue
        if not url.startswith("https://"):
            raise DownloadError(
                f"Refusing a non-HTTPS URL for {filename}: {url}. Downloads "
                "must be encrypted so the file cannot be altered in transit."
            )
        if "/" in filename or filename.startswith("."):
            raise DownloadError(f"Unsafe destination filename in {path}: {filename}")
        cleaned[filename] = url.strip()

    if not cleaned:
        raise DownloadError(
            f"{path} has no usable URLs. Replace the placeholder values with "
            "the current Deportation Data Project download links."
        )
    return cleaned


def parquet_columns(path: Path, filename: str) -> set[str]:
    """Return the column names of a Parquet file."""
    try:
        payload = path.read_bytes()
    except OSError as exc:
        raise DownloadError(f"Could not read {filename}: {exc}") from exc
    try:
        # Validate from an in-memory buffer: pyarrow never opens the on-disk
        # temp file, so on Windows no lingering handle can block its removal.
        parquet_file = pq.ParquetFile(io.BytesIO(payload))
        return {field.name for field in parquet_file.schema_arrow}
    except Exception as exc:  # pyarrow raises several unrelated types
        raise DownloadError(
            f"{filename} did not download as readable Parquet. The URL may "
            f"point at a web page rather than the file itself. ({exc})"
        ) from None


def validate_parquet(path: Path, filename: str) -> tuple[int, set[str]]:
    """Confirm a file is readable Parquet with the needed columns.

    Returns the row count and the full column set, so the caller can also report
    which optional fields this release no longer publishes.
    """
    columns = parquet_columns(path, filename)
    try:
        payload = path.read_bytes()
    except OSError as exc:
        raise DownloadError(f"Could not read {filename}: {exc}") from exc
    try:
        rows = pq.ParquetFile(io.BytesIO(payload)).metadata.num_rows
    except Exception as exc:  # pyarrow raises several unrelated types
        raise DownloadError(
            f"{filename} did not download as readable Parquet. The URL may "
            f"point at a web page rather than the file itself. ({exc})"
        ) from None

    missing = [
        column for column in REQUIRED_COLUMNS.get(filename, ()) if column not in columns
    ]
    if missing:
        raise DownloadError(
            f"{filename} is missing expected column(s): {', '.join(missing)}. "
            "The URL may point at a different dataset."
        )
    if rows == 0:
        raise DownloadError(f"{filename} downloaded with zero rows.")
    return int(rows), columns


def download_one(
    filename: str,
    url: str,
    project_dir: Path,
    progress: ProgressCallback | None = None,
) -> DownloadResult:
    """Download one dataset and replace the local copy only once it validates."""
    destination = project_dir / filename
    temporary = project_dir / (filename + DOWNLOAD_SUFFIX)
    request = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    written = 0

    try:
        with urllib.request.urlopen(
            request, context=certificate_context(), timeout=60
        ) as response:
            length_header = response.headers.get("Content-Length")
            total = int(length_header) if length_header else None
            with temporary.open("wb") as handle:
                while True:
                    chunk = response.read(CHUNK_SIZE)
                    if not chunk:
                        break
                    handle.write(chunk)
                    written += len(chunk)
                    if progress is not None:
                        progress(filename, written, total)
    except (urllib.error.URLError, OSError, ValueError) as exc:
        temporary.unlink(missing_ok=True)
        if isinstance(exc, OSError) and exc.errno == errno.EROFS:
            raise DownloadError(
                f"Could not write {filename} to {project_dir}: the folder is "
                "read-only. Move the app to a folder you can write to, or set "
                "ICE_PATHWAY_DATA to one."
            ) from exc
        raise DownloadError(f"Could not download {filename}: {exc}") from exc

    try:
        rows, columns = validate_parquet(temporary, filename)
    except DownloadError:
        temporary.unlink(missing_ok=True)
        raise

    absent = report_absent_fields(filename, columns)
    replaced = destination.is_file()
    os.replace(temporary, destination)
    return DownloadResult(filename, written, rows, replaced, absent)


def report_absent_fields(filename: str, columns: set[str]) -> tuple[str, ...]:
    """Name the renderer fields this release does not publish.

    A field the publisher has dropped is absent, not unknown. Reporting it at
    download time means the loss is visible at the moment of the update rather
    than discovered later as a silent gap in a person's pathway.
    """
    if filename == "detention-stints-latest.parquet":
        return tuple(missing_source_fields(columns))
    watched = ABSENT_FIELD_DATASETS.get(filename)
    if watched is None:
        return ()
    return tuple(field for field in watched if field not in columns)


def download_all(
    project_dir: Path,
    filenames: Sequence[str] | None = None,
    progress: ProgressCallback | None = None,
) -> list[DownloadResult]:
    """Download every configured dataset, or only the ones named."""
    sources = load_sources(project_dir)
    if filenames:
        unknown = [name for name in filenames if name not in sources]
        if unknown:
            raise DownloadError(
                f"Not configured in {CONFIG_FILENAME}: {', '.join(unknown)}"
            )
        selected = {name: sources[name] for name in filenames}
    else:
        selected = sources

    results = []
    try:
        project_dir.mkdir(parents=True, exist_ok=True)
    except OSError as exc:
        raise DownloadError(
            f"Could not prepare the folder {project_dir}: {exc}"
        ) from exc
    for filename, url in selected.items():
        results.append(download_one(filename, url, project_dir, progress))
    return results


def print_progress(filename: str, written: int, total: int | None) -> None:
    if total:
        percent = 100.0 * written / total
        line = f"  {filename}: {written / 1e6:,.1f} MB of {total / 1e6:,.1f} MB ({percent:.0f}%)"
    else:
        line = f"  {filename}: {written / 1e6:,.1f} MB"
    print(f"\r{line:<70}", end="", file=sys.stderr, flush=True)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        prog="fetch-data",
        description=(
            "Download the Deportation Data Project Parquet files. Existing "
            "files are replaced only after the download validates."
        ),
    )
    parser.add_argument(
        "files",
        nargs="*",
        help="filenames to download (default: every entry in data-sources.json)",
    )
    parser.add_argument(
        "--project-dir",
        type=Path,
        default=Path.cwd(),
        help="directory holding data-sources.json and the datasets",
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    try:
        results = download_all(args.project_dir, args.files, print_progress)
    except DownloadError as exc:
        print(f"\nDownload failed: {exc}", file=sys.stderr)
        return 1

    print("\r" + " " * 70, end="\r", file=sys.stderr)
    for result in results:
        action = "replaced" if result.replaced_existing else "created"
        print(
            f"{result.filename}: {action}, {result.rows:,} rows, "
            f"{result.bytes_written / 1e6:,.1f} MB"
        )
        if result.absent_fields:
            print(
                f"  no longer publishes: {', '.join(result.absent_fields)}"
            )
            print(
                "  These will be omitted from every pathway. The publisher no"
                " longer"
            )
            print(
                "  supplies them, so nothing in the output stands in for them."
            )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
