"""Tests for the release update checker.

Every GitHub response is a local fake, so the suite never touches the network
(see CONTRIBUTING). The two seams are used deliberately: `urllib.request.urlopen`
is stubbed when exercising the HTTP path itself, and `fetch_latest_release` is
stubbed when exercising everything downstream of it.
"""

from __future__ import annotations

import io
import json
import urllib.error

import pytest

import ice_detention_pathway
import update_check
from update_check import (
    LatestRelease,
    UpdateCheckError,
    UpdateStatus,
    asset_for_machine,
    check_for_update,
    fetch_latest_release,
    format_report,
    parse_version,
    update_is_available,
)

PAGE_URL = "https://github.com/remypicciano/ICE-Detention-Path-finder/releases/tag/v2.1.0"
DEFAULT_ASSETS = (
    "ICEDetentionPathway-v2.1.0-macOS-ARM64.zip",
    "ICEDetentionPathway-v2.1.0-macOS-X64.zip",
    "ICEDetentionPathway-v2.1.0-Windows.zip",
    "ICEDetentionPathway-v2.1.0-Linux-x86_64.tar.gz",
    "ICEDetentionPathway-v2.1.0-Linux-aarch64.tar.gz",
)


def release_payload(
    tag: str = "v2.1.0", assets: tuple[str, ...] = DEFAULT_ASSETS
) -> dict:
    return {
        "tag_name": tag,
        "html_url": PAGE_URL,
        "published_at": "2026-10-01T00:00:00Z",
        "assets": [{"name": name} for name in assets],
    }


class FakeResponse(io.BytesIO):
    def __init__(self, payload: dict) -> None:
        super().__init__(json.dumps(payload).encode("utf-8"))

    def __enter__(self):
        return self

    def __exit__(self, *args):
        self.close()
        return False


def stub_urlopen(monkeypatch, payload: dict) -> None:
    def fake_urlopen(request, context=None, timeout=None):
        return FakeResponse(payload)

    monkeypatch.setattr(update_check.urllib.request, "urlopen", fake_urlopen)


def stub_urlopen_error(monkeypatch, error: Exception) -> None:
    def failing_urlopen(request, context=None, timeout=None):
        raise error

    monkeypatch.setattr(update_check.urllib.request, "urlopen", failing_urlopen)


def stub_latest(
    monkeypatch,
    version: str = "2.1.0",
    assets: tuple[str, ...] = DEFAULT_ASSETS,
    page_url: str = PAGE_URL,
) -> None:
    release = LatestRelease(
        version=version,
        page_url=page_url,
        published_at="2026-10-01T00:00:00Z",
        assets=assets,
    )
    monkeypatch.setattr(update_check, "fetch_latest_release", lambda timeout=0: release)


def test_fetch_parses_the_latest_release(monkeypatch) -> None:
    stub_urlopen(monkeypatch, release_payload())

    release = fetch_latest_release()

    assert release.version == "2.1.0"
    assert release.page_url == PAGE_URL
    assert release.published_at == "2026-10-01T00:00:00Z"
    assert release.assets == DEFAULT_ASSETS


def test_fetch_rejects_a_response_without_a_tag(monkeypatch) -> None:
    stub_urlopen(monkeypatch, {"html_url": PAGE_URL})

    with pytest.raises(UpdateCheckError, match="named no release tag"):
        fetch_latest_release()


def test_fetch_reports_a_missing_release(monkeypatch) -> None:
    error = urllib.error.HTTPError(
        "https://api.github.com/repos/x/y/releases/latest",
        404,
        "Not Found",
        {},
        io.BytesIO(b""),
    )
    stub_urlopen_error(monkeypatch, error)

    with pytest.raises(UpdateCheckError, match="no published release"):
        fetch_latest_release()


def test_fetch_reports_the_rate_limit(monkeypatch) -> None:
    error = urllib.error.HTTPError(
        "https://api.github.com/repos/x/y/releases/latest",
        403,
        "Forbidden",
        {"X-RateLimit-Remaining": "0"},
        io.BytesIO(b""),
    )
    stub_urlopen_error(monkeypatch, error)

    with pytest.raises(UpdateCheckError, match="rate limit"):
        fetch_latest_release()


def test_fetch_reports_a_network_failure(monkeypatch) -> None:
    stub_urlopen_error(monkeypatch, urllib.error.URLError("connection refused"))

    with pytest.raises(UpdateCheckError, match="Could not reach GitHub"):
        fetch_latest_release()


def test_fetch_reports_an_unreadable_body(monkeypatch) -> None:
    def garbage_urlopen(request, context=None, timeout=None):
        return io.BytesIO(b"<html>not json</html>")

    monkeypatch.setattr(update_check.urllib.request, "urlopen", garbage_urlopen)

    with pytest.raises(UpdateCheckError, match="could not be read"):
        fetch_latest_release()


def test_update_is_available_compares_numerically() -> None:
    assert update_is_available("2.0.0", "2.1.0") is True
    assert update_is_available("2.1.0", "2.1.0") is False
    # Lexicographic comparison would call 2.9.0 newer than 2.10.0.
    assert update_is_available("2.10.0", "2.9.0") is False
    assert update_is_available("2.9.0", "2.10.0") is True
    # Fewer parts mean implicit zeros.
    assert update_is_available("2.1", "2.1.0") is False
    assert update_is_available("2.1.0", "2.1") is False


def test_unparsable_versions_are_rejected_not_guessed() -> None:
    with pytest.raises(UpdateCheckError, match="not a dotted-integer"):
        parse_version("release-two")
    with pytest.raises(UpdateCheckError, match="not a dotted-integer"):
        update_is_available("2.0.0", "latest")
    # A leading v is part of the tag convention, not garbage.
    assert parse_version("v2.1.0") == (2, 1, 0)


def test_check_finds_a_newer_release(monkeypatch) -> None:
    stub_latest(monkeypatch)

    status = check_for_update("2.0.0", system="Darwin", machine="arm64")

    assert status.update_available is True
    assert status.latest.version == "2.1.0"
    assert status.asset_name == "ICEDetentionPathway-v2.1.0-macOS-ARM64.zip"


def test_check_reports_current_release(monkeypatch) -> None:
    stub_latest(monkeypatch, version="2.0.0")

    status = check_for_update("2.0.0", system="Darwin", machine="arm64")

    assert status.update_available is False
    assert status.asset_name is None


def test_asset_name_matches_each_build_target() -> None:
    release = LatestRelease(
        version="2.1.0",
        page_url=PAGE_URL,
        published_at="2026-10-01T00:00:00Z",
        assets=DEFAULT_ASSETS,
    )
    cases = [
        ("Darwin", "arm64", "ICEDetentionPathway-v2.1.0-macOS-ARM64.zip"),
        ("Darwin", "aarch64", "ICEDetentionPathway-v2.1.0-macOS-ARM64.zip"),
        ("Darwin", "x86_64", "ICEDetentionPathway-v2.1.0-macOS-X64.zip"),
        ("Linux", "x86_64", "ICEDetentionPathway-v2.1.0-Linux-x86_64.tar.gz"),
        ("Linux", "aarch64", "ICEDetentionPathway-v2.1.0-Linux-aarch64.tar.gz"),
        ("Windows", "AMD64", "ICEDetentionPathway-v2.1.0-Windows.zip"),
        ("FreeBSD", "amd64", None),
    ]
    for system, machine, expected in cases:
        assert (
            asset_for_machine(release, system=system, machine=machine) == expected
        ), (system, machine)


def test_asset_match_never_crosses_architectures() -> None:
    """A differently-versioned or differently-arch asset is not a match."""
    release = LatestRelease(
        version="2.1.0",
        page_url=PAGE_URL,
        published_at="2026-10-01T00:00:00Z",
        assets=("ICEDetentionPathway-v2.1.0.1-Windows.zip",),
    )
    assert asset_for_machine(release, system="Windows") is None

    empty = LatestRelease(
        version="2.1.0",
        page_url=PAGE_URL,
        published_at="",
        assets=(),
    )
    assert asset_for_machine(empty, system="Windows") is None


def report_status(**kwargs) -> UpdateStatus:
    defaults: dict = {
        "current": "2.0.0",
        "latest": LatestRelease(
            version="2.1.0",
            page_url=PAGE_URL,
            published_at="2026-10-01T00:00:00Z",
            assets=DEFAULT_ASSETS,
        ),
        "update_available": True,
        "asset_name": "ICEDetentionPathway-v2.1.0-macOS-ARM64.zip",
    }
    defaults.update(kwargs)
    return UpdateStatus(**defaults)


def test_format_report_for_an_available_update() -> None:
    text = format_report(report_status())

    assert "UPDATE AVAILABLE: 2.1.0 (you have 2.0.0)" in text
    assert "Download for this machine: ICEDetentionPathway-v2.1.0-macOS-ARM64.zip" in text
    assert PAGE_URL in text
    assert "2026-10-01T00:00:00Z" in text


def test_format_report_when_up_to_date() -> None:
    status = report_status(
        current="2.1.0",
        update_available=False,
        asset_name=None,
    )
    text = format_report(status)

    assert "UP TO DATE: 2.1.0 is the latest release." in text
    assert "UPDATE AVAILABLE" not in text
    # The real release tag is v2.0 while the code says 2.0.0; the same
    # release spelled two ways must not read as "you are ahead".
    spelled_differently = report_status(
        current="2.0.0",
        latest=LatestRelease(
            version="2.0",
            page_url=PAGE_URL,
            published_at="",
            assets=(),
        ),
        update_available=False,
        asset_name=None,
    )
    assert "UP TO DATE: 2.0.0 is the latest release." in format_report(
        spelled_differently
    )


def test_format_report_when_local_build_is_ahead_of_releases() -> None:
    status = report_status(
        current="2.2.0",
        update_available=False,
        asset_name=None,
    )
    text = format_report(status)

    assert "you have 2.2.0; the newest published release is 2.1.0" in text


def test_cli_check_update_prints_the_report(monkeypatch, capsys) -> None:
    # A tag beyond any plausible release keeps this test valid after each
    # real version bump: the report must compare against APP_VERSION, not
    # against a hardcoded copy of it.
    stub_latest(monkeypatch, version="99.0.0")

    # The flag must never fall through to the interactive identifier prompt.
    def no_prompt(*args, **kwargs):
        raise AssertionError("--check-update must not prompt for an identifier")

    monkeypatch.setattr("builtins.input", no_prompt)

    exit_code = ice_detention_pathway.main(["--check-update"])

    captured = capsys.readouterr()
    assert exit_code == 0
    assert "UPDATE AVAILABLE: 99.0.0 (you have " in captured.out
    assert f"{ice_detention_pathway.APP_VERSION})" in captured.out
    assert "Release page:" in captured.out
    assert captured.err == ""


def test_cli_check_update_failure_exits_nonzero(monkeypatch, capsys) -> None:
    def failing_fetch(timeout=0):
        raise UpdateCheckError("Could not reach GitHub: connection refused")

    monkeypatch.setattr(update_check, "fetch_latest_release", failing_fetch)

    exit_code = ice_detention_pathway.main(["--check-update"])

    captured = capsys.readouterr()
    assert exit_code == 1
    assert "Update check failed: Could not reach GitHub" in captured.err
    assert captured.out == ""


def test_cli_app_version_is_a_dotted_integer() -> None:
    """The single source the checker compares with must stay comparable."""
    assert parse_version(ice_detention_pathway.APP_VERSION)


def test_importing_the_cli_module_pulls_no_network_stack() -> None:
    """update_check must stay stdlib-only at import time.

    A plain lookup imports the CLI module, which now imports update_check;
    loading pyarrow (via fetch_data) for every lookup would be dead weight.
    """
    import subprocess
    import sys

    code = (
        "import sys; import ice_detention_pathway; "
        "raise SystemExit(1 if 'fetch_data' in sys.modules else 0)"
    )
    assert subprocess.run([sys.executable, "-c", code], check=False).returncode == 0
