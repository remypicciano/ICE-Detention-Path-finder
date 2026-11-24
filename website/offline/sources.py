"""Fetch the current Parquet files listed in the root `data-sources.json`.

The GUI app and the website build use the same download URLs and the same
validator in `fetch_data.py`. A file is fetched when it is missing, older than
`max_age_days`, or when its URL in `data-sources.json` changed since the last
build. Use `refresh=True` to force a fresh download of all three required
files.
"""

from __future__ import annotations

import json
import time
from pathlib import Path

from fetch_data import DownloadError, download_all, load_sources
from website.offline import datasets

DEFAULT_MAX_AGE_DAYS = 7
_SECONDS_PER_DAY = 86_400
STATE_NAME = "source-urls.json"


def _stale(path: Path, max_age_days: int) -> bool:
    """True when a required file is absent or older than the freshness window."""
    if not path.is_file():
        return True
    if max_age_days <= 0:
        return False
    return time.time() - path.stat().st_mtime > max_age_days * _SECONDS_PER_DAY


def _state_path(out_dir: Path | None) -> Path | None:
    """State sits beside the build directory, never inside the uploaded shards."""
    return out_dir.parent / STATE_NAME if out_dir is not None else None


def _previous_urls(state_path: Path | None) -> dict[str, str]:
    if state_path is None or not state_path.is_file():
        return {}
    try:
        value = json.loads(state_path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    if not isinstance(value, dict):
        return {}
    return {key: item for key, item in value.items() if isinstance(item, str)}


def _save_urls(state_path: Path | None, sources: dict[str, str]) -> None:
    if state_path is None:
        return
    state_path.parent.mkdir(parents=True, exist_ok=True)
    selected = {name: sources[name] for name in datasets.DATASET_FILES}
    state_path.write_text(
        json.dumps(selected, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
    )


def ensure_current(
    project_dir: Path,
    refresh: bool = False,
    max_age_days: int = DEFAULT_MAX_AGE_DAYS,
    out_dir: Path | None = None,
) -> list[str]:
    """Download changed, missing, or stale datasets; return files fetched."""
    sources = load_sources(project_dir)
    missing_urls = [name for name in datasets.DATASET_FILES if name not in sources]
    if missing_urls:
        raise DownloadError(
            "data-sources.json has no URL for: " + ", ".join(missing_urls)
        )

    state_path = _state_path(out_dir)
    previous = _previous_urls(state_path)
    changed = [
        name
        for name in datasets.DATASET_FILES
        if name in previous and previous[name] != sources[name]
    ]

    if refresh:
        selected = list(datasets.DATASET_FILES)
    else:
        selected = list(dict.fromkeys([
            *changed,
            *[
                name
                for name in datasets.DATASET_FILES
                if _stale(project_dir / name, max_age_days)
            ],
        ]))

    if selected:
        print(
            "Refreshing datasets from data-sources.json: " + ", ".join(selected),
            flush=True,
        )
        fetched = [result.filename for result in download_all(project_dir, selected)]
    else:
        fetched = []
    _save_urls(state_path, sources)
    return fetched
