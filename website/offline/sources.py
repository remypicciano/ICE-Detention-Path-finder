"""Fetch the current Parquet files listed in the root `data-sources.json`.

The GUI app and the website build use the same download URLs and the same
validator in `fetch_data.py`. Missing files are fetched; present files are
refreshed when they age past `max_age_days`. Use `refresh=True` to force a
fresh download of all three required files.
"""

from __future__ import annotations

import time
from pathlib import Path

from fetch_data import DownloadError, download_all, load_sources
from website.offline import datasets

DEFAULT_MAX_AGE_DAYS = 7
_SECONDS_PER_DAY = 86_400


def _stale(path: Path, max_age_days: int) -> bool:
    """True when a required file is absent or older than the freshness window."""
    if not path.is_file():
        return True
    if max_age_days <= 0:
        return False
    return time.time() - path.stat().st_mtime > max_age_days * _SECONDS_PER_DAY


def ensure_current(
    project_dir: Path,
    refresh: bool = False,
    max_age_days: int = DEFAULT_MAX_AGE_DAYS,
) -> list[str]:
    """Download required datasets if missing or stale; return files fetched."""
    sources = load_sources(project_dir)
    missing_urls = [name for name in datasets.DATASET_FILES if name not in sources]
    if missing_urls:
        raise DownloadError(
            "data-sources.json has no URL for: " + ", ".join(missing_urls)
        )

    if refresh:
        selected = list(datasets.DATASET_FILES)
    else:
        selected = [
            name
            for name in datasets.DATASET_FILES
            if _stale(project_dir / name, max_age_days)
        ]
    if not selected:
        return []

    print(
        "Refreshing datasets from data-sources.json: " + ", ".join(selected),
        flush=True,
    )
    return [result.filename for result in download_all(project_dir, selected)]
