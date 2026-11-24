"""Tests for the website build's dataset freshness check."""

from __future__ import annotations

import json
from pathlib import Path

from website.offline import datasets, sources


def _write_config(root: Path, urls: dict[str, str]) -> None:
    (root / "data-sources.json").write_text(
        json.dumps({"sources": urls}), encoding="utf-8"
    )


def test_changed_source_url_refreshes_only_that_file(tmp_path: Path, monkeypatch) -> None:
    out = tmp_path / "build" / "r2"
    out.mkdir(parents=True)
    for name in datasets.DATASET_FILES:
        (tmp_path / name).write_bytes(b"x")
    urls = {name: f"https://example.test/{name}" for name in datasets.DATASET_FILES}
    _write_config(tmp_path, urls)

    fetched: list[str] = []

    def fake_download_all(project_dir: Path, selected: list[str]):
        fetched.extend(selected)
        return []

    monkeypatch.setattr(sources, "download_all", fake_download_all)

    assert sources.ensure_current(tmp_path, out_dir=out) == []
    assert fetched == []

    urls[datasets.DATASET_FILES[0]] = f"https://example.test/v2/{datasets.DATASET_FILES[0]}"
    _write_config(tmp_path, urls)

    sources.ensure_current(tmp_path, out_dir=out)
    assert fetched == [datasets.DATASET_FILES[0]]

    state = json.loads((out.parent / sources.STATE_NAME).read_text(encoding="utf-8"))
    assert state[datasets.DATASET_FILES[0]].endswith("/v2/" + datasets.DATASET_FILES[0])
