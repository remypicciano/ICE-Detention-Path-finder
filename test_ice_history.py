import pytest

from ice_history import (
    HistoryEntry,
    append_history,
    append_history_many,
    build_bulk_json,
    build_bulk_text,
    load_history,
    parse_identifiers_payload,
    recent_history,
)


def test_history_round_trip_and_recent_order(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("ICE_PATHWAY_DATA", str(tmp_path))
    append_history(HistoryEntry("a", "2026-08-01T00:00:00Z", n_stays=1, n_rows=2))
    append_history(HistoryEntry("b", "2026-08-02T00:00:00Z", n_stays=2, n_rows=4))

    assert load_history() == [
        {"identifier": "a", "searched_at": "2026-08-01T00:00:00Z", "n_stays": 1, "n_rows": 2, "label": None},
        {"identifier": "b", "searched_at": "2026-08-02T00:00:00Z", "n_stays": 2, "n_rows": 4, "label": None},
    ]
    assert [entry["identifier"] for entry in recent_history()] == ["b", "a"]


def test_append_history_replaces_same_identifier(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("ICE_PATHWAY_DATA", str(tmp_path))
    append_history(HistoryEntry("a", "2026-08-01T00:00:00Z", n_stays=1, n_rows=2))
    append_history(HistoryEntry("a", "2026-08-03T00:00:00Z", n_stays=3, n_rows=6))

    entries = load_history()
    assert len(entries) == 1
    assert entries[0]["searched_at"] == "2026-08-03T00:00:00Z"


def test_append_history_many_deduplicates_and_merges(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("ICE_PATHWAY_DATA", str(tmp_path))
    append_history(HistoryEntry("a", "2026-08-01T00:00:00Z", n_stays=1, n_rows=2))
    append_history_many(
        [
            HistoryEntry("a", "2026-08-04T00:00:00Z", n_stays=2, n_rows=4),
            HistoryEntry("c", "2026-08-05T00:00:00Z", n_stays=1, n_rows=1),
        ]
    )

    entries = load_history()
    assert {entry["identifier"] for entry in entries} == {"a", "c"}
    by_identifier = {entry["identifier"]: entry for entry in entries}
    assert by_identifier["a"]["searched_at"] == "2026-08-04T00:00:00Z"


def test_recent_history_respects_limit(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("ICE_PATHWAY_DATA", str(tmp_path))
    for index in range(20):
        append_history(HistoryEntry(f"id-{index}", f"2026-08-01T00:00:0{index % 10}Z"))
    recent = recent_history(limit=3)
    assert [entry["identifier"] for entry in recent] == ["id-19", "id-18", "id-17"]


def test_parse_identifiers_payload_accepts_plain_array() -> None:
    assert parse_identifiers_payload('["a", "b"]') == [("a", None), ("b", None)]


def test_parse_identifiers_payload_accepts_identifiers_object() -> None:
    payload = '{"identifiers": ["a", {"identifier": "b", "label": "Case B"}]}'
    assert parse_identifiers_payload(payload) == [("a", None), ("b", "Case B")]


def test_parse_identifiers_payload_rejects_bad_shapes() -> None:
    with pytest.raises(ValueError):
        parse_identifiers_payload("not json")
    with pytest.raises(ValueError):
        parse_identifiers_payload('{"ids": ["a"]}')
    with pytest.raises(ValueError):
        parse_identifiers_payload('["a", 42]')
    with pytest.raises(ValueError):
        parse_identifiers_payload("[]")


def test_build_bulk_json_strips_display_text_and_counts() -> None:
    results = [
        {
            "identifier": "a",
            "label": None,
            "status": "ok",
            "pathway": {"stays": []},
            "text": "rendered timeline",
        },
        {
            "identifier": "b",
            "label": "Case B",
            "status": "error",
            "pathway": None,
            "error": "no records",
        },
    ]
    payload = build_bulk_json(results)
    assert payload["schema_version"] == 1
    assert payload["total"] == 2
    assert payload["found"] == 1
    assert payload["failed"] == 1
    assert "text" not in payload["results"][0]
    assert payload["results"][1]["error"] == "no records"


def test_build_bulk_text_renders_headers_summary_and_errors() -> None:
    results = [
        {"identifier": "a", "label": None, "status": "ok", "text": "pathway-a"},
        {"identifier": "b", "label": "Case B", "status": "error", "error": "no records"},
    ]
    report = build_bulk_text(results)
    assert "===== a =====" in report
    assert "pathway-a" in report
    assert "===== b (Case B) =====" in report
    assert "ERROR: no records" in report
    assert "Found: 1    Failed: 1    Total: 2" in report
