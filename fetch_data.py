"""Download the Deportation Data Project Parquet files this project reads.

Downloads stream to a temporary file beside the target, are checked for
readability and required columns, and only then replace the existing file with
an atomic `os.replace`. An interrupted or corrupt download therefore never
destroys data that already works.

This is the only part of the project that touches the network, and it runs only
when a person explicitly asks for it. Lookups never make network requests.

Source URLs live in `data-sources.json` beside the application so they can be
corrected without rebuilding. See `data-sources.example.json`.
"""

from __future__ import annotations

import argparse
import gc
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

CONFIG_FILENAME = "data-sources.json"
EXAMPLE_CONFIG_FILENAME = "data-sources.example.json"
DOWNLOAD_SUFFIX = ".download.tmp"
USER_AGENT = "ICE-Detention-Pathway/3.1.0 (+https://github.com/remypicciano)"
CHUNK_SIZE = 1 << 20

# A download is only accepted if it is readable Parquet containing the columns
# the lookup depends on. This catches an HTML error page saved under a .parquet
# name, a truncated transfer, and a URL that points at the wrong dataset.
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


class DownloadError(Exception):
    """Raised when a dataset cannot be downloaded or fails validation."""


@dataclass(frozen=True)
class DownloadResult:
    filename: str
    bytes_written: int
    rows: int
    replaced_existing: bool


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


def load_sources(project_dir: Path) -> dict[str, str]:
    """Read the filename-to-URL mapping, rejecting anything unusable."""
    path = config_path(project_dir)
    if not path.is_file():
        raise DownloadError(
            f"No {CONFIG_FILENAME} found in {project_dir}. Copy "
            f"{EXAMPLE_CONFIG_FILENAME} to {CONFIG_FILENAME} and put the "
            "current download URLs in it."
        )
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


def validate_parquet(path: Path, filename: str) -> int:
    """Confirm a downloaded file is readable Parquet with the needed columns."""
    parquet_file: pq.ParquetFile | None = None
    try:
        parquet_file = pq.ParquetFile(path)
        columns = {field.name for field in parquet_file.schema_arrow}
        rows = parquet_file.metadata.num_rows
    except Exception as exc:  # pyarrow raises several unrelated types
        if parquet_file is not None:
            parquet_file.close()
        # Drop every reference to the failed pyarrow object before raising:
        # its memory-mapped file would otherwise keep the temp file locked on
        # Windows and block the caller's unlink.
        del parquet_file, exc
        raise DownloadError(
            f"{filename} did not download as readable Parquet. The URL may "
            f"point at a web page rather than the file itself."
        ) from None

    missing = [
        column for column in REQUIRED_COLUMNS.get(filename, ()) if column not in columns
    ]
    parquet_file.close()
    if missing:
        raise DownloadError(
            f"{filename} is missing expected column(s): {', '.join(missing)}. "
            "The URL may point at a different dataset."
        )
    if rows == 0:
        raise DownloadError(f"{filename} downloaded with zero rows.")
    return int(rows)


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
        unlink_later(temporary)
        raise DownloadError(f"Could not download {filename}: {exc}") from exc

    try:
        rows = validate_parquet(temporary, filename)
    except DownloadError:
        unlink_later(temporary)
        raise

    replaced = destination.is_file()
    os.replace(temporary, destination)
    return DownloadResult(filename, written, rows, replaced)


def unlink_later(path: Path) -> None:
    """Delete a temp file, retrying so a still-open handle can be released."""
    for _ in range(3):
        try:
            path.unlink(missing_ok=True)
            return
        except PermissionError:
            gc.collect()
    path.unlink(missing_ok=True)


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
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
