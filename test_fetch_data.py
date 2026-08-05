"""Tests for the dataset downloader.

Downloads are exercised against real Parquet bytes through a stubbed opener, so
the suite never touches the network.
"""

from __future__ import annotations

import io
import json
from pathlib import Path

import duckdb
import pytest

import fetch_data
from fetch_data import DownloadError, download_all, download_one, load_sources


def write_parquet(path: Path, *, rows: int = 2, with_columns: bool = True) -> bytes:
    connection = duckdb.connect()
    if with_columns:
        select = (
            "SELECT 'abc' AS unique_identifier, "
            "TIMESTAMPTZ '2025-01-01 00:00:00+00' AS apprehension_date_time"
        )
    else:
        select = "SELECT 'abc' AS something_else"
    connection.execute(
        f"COPY ({select} FROM range({rows})) TO '{path}' (FORMAT PARQUET)"
    )
    connection.close()
    return path.read_bytes()


def write_joined_parquet(path: Path) -> bytes:
    connection = duckdb.connect()
    connection.execute(
        "COPY (SELECT 'abc' AS unique_identifier, 'stay1' AS stay_ID, "
        "TRUE AS has_detention_stay FROM range(2)) "
        f"TO '{path}' (FORMAT PARQUET)"
    )
    connection.close()
    return path.read_bytes()


class FakeResponse(io.BytesIO):
    def __init__(self, payload: bytes) -> None:
        super().__init__(payload)
        self.headers = {"Content-Length": str(len(payload))}

    def __enter__(self):
        return self

    def __exit__(self, *args):
        self.close()
        return False


def stub_urlopen(monkeypatch, payload: bytes) -> None:
    def fake_urlopen(request, context=None, timeout=None):
        return FakeResponse(payload)

    monkeypatch.setattr(fetch_data.urllib.request, "urlopen", fake_urlopen)


def write_config(project_dir: Path, sources: dict[str, str]) -> None:
    (project_dir / "data-sources.json").write_text(
        json.dumps({"sources": sources}), encoding="utf-8"
    )


def test_load_sources_requires_a_config(tmp_path) -> None:
    with pytest.raises(DownloadError, match="data-sources.json"):
        load_sources(tmp_path)


def test_load_sources_rejects_plain_http(tmp_path) -> None:
    write_config(tmp_path, {"arrests-latest.parquet": "http://example.test/a.parquet"})

    with pytest.raises(DownloadError, match="non-HTTPS"):
        load_sources(tmp_path)


def test_load_sources_rejects_path_traversal(tmp_path) -> None:
    write_config(tmp_path, {"../escape.parquet": "https://example.test/a.parquet"})

    with pytest.raises(DownloadError, match="Unsafe destination"):
        load_sources(tmp_path)


def test_load_sources_rejects_placeholder_only_config(tmp_path) -> None:
    write_config(tmp_path, {"arrests-latest.parquet": "   "})

    with pytest.raises(DownloadError, match="no usable URLs"):
        load_sources(tmp_path)


def test_download_writes_and_validates(tmp_path, monkeypatch) -> None:
    payload = write_parquet(tmp_path / "source.parquet")
    stub_urlopen(monkeypatch, payload)
    write_config(
        tmp_path, {"arrests-latest.parquet": "https://example.test/a.parquet"}
    )

    results = download_all(tmp_path)

    assert len(results) == 1
    assert results[0].filename == "arrests-latest.parquet"
    assert results[0].rows == 2
    assert (tmp_path / "arrests-latest.parquet").is_file()
    assert not list(tmp_path.glob("*.download.tmp"))


def test_existing_file_survives_a_corrupt_download(tmp_path, monkeypatch) -> None:
    """A bad download must never destroy data that already works."""
    destination = tmp_path / "arrests-latest.parquet"
    write_parquet(destination)
    original = destination.read_bytes()

    stub_urlopen(monkeypatch, b"<html>404 Not Found</html>")

    with pytest.raises(DownloadError, match="readable Parquet"):
        download_one(
            "arrests-latest.parquet", "https://example.test/a.parquet", tmp_path
        )

    assert destination.read_bytes() == original
    assert not list(tmp_path.glob("*.download.tmp"))


def test_existing_file_survives_a_wrong_dataset(tmp_path, monkeypatch) -> None:
    destination = tmp_path / "arrests-latest.parquet"
    write_parquet(destination)
    original = destination.read_bytes()

    payload = write_parquet(tmp_path / "other.parquet", with_columns=False)
    stub_urlopen(monkeypatch, payload)

    with pytest.raises(DownloadError, match="missing expected column"):
        download_one(
            "arrests-latest.parquet", "https://example.test/a.parquet", tmp_path
        )

    assert destination.read_bytes() == original


def test_network_failure_leaves_no_partial_file(tmp_path, monkeypatch) -> None:
    def failing_urlopen(request, context=None, timeout=None):
        raise fetch_data.urllib.error.URLError("connection refused")

    monkeypatch.setattr(fetch_data.urllib.request, "urlopen", failing_urlopen)

    with pytest.raises(DownloadError, match="Could not download"):
        download_one(
            "arrests-latest.parquet", "https://example.test/a.parquet", tmp_path
        )

    assert not list(tmp_path.glob("*"))


def test_download_reports_unknown_filenames(tmp_path) -> None:
    write_config(
        tmp_path, {"arrests-latest.parquet": "https://example.test/a.parquet"}
    )

    with pytest.raises(DownloadError, match="Not configured"):
        download_all(tmp_path, ["nope.parquet"])


def test_joined_file_is_validated_for_its_columns(tmp_path, monkeypatch) -> None:
    """The joined arrests+stays download must pass its own required columns."""
    destination = tmp_path / "joined-arrests-detention-stays-latest.parquet"
    write_joined_parquet(destination)
    original = destination.read_bytes()

    payload = write_parquet(tmp_path / "other.parquet", with_columns=False)
    stub_urlopen(monkeypatch, payload)

    with pytest.raises(DownloadError, match="missing expected column"):
        download_one(
            "joined-arrests-detention-stays-latest.parquet",
            "https://example.test/a.parquet",
            tmp_path,
        )

    assert destination.read_bytes() == original


def test_joined_file_download_accepts_matching_columns(tmp_path, monkeypatch) -> None:
    payload = write_joined_parquet(tmp_path / "source.parquet")
    stub_urlopen(monkeypatch, payload)

    result = download_one(
        "joined-arrests-detention-stays-latest.parquet",
        "https://example.test/a.parquet",
        tmp_path,
    )

    assert result.filename == "joined-arrests-detention-stays-latest.parquet"
    assert result.rows == 2
    assert (tmp_path / "joined-arrests-detention-stays-latest.parquet").is_file()
