"""Check GitHub Releases for a newer version of this application.

Lookups never touch the network. This module runs only when a person
explicitly asks — the desktop app's Check for Updates… button or the terminal's
`--check-update` flag — and it only *reads* the latest published release
metadata. Nothing is downloaded or replaced here; the release page URL is
returned so the caller can offer it for the person to open themselves.

The published tag (`v2.1.0`) is compared numerically against the running
version, and the asset whose name matches this machine is identified with the
naming convention `ICEDetentionPathway-v<version>-<os>-<arch>.<ext>` produced by
.github/workflows/build-desktop.yml. When no asset matches, the release page
itself is still usable.

The module imports only the standard library at load time, so a plain lookup
never pulls in the download stack. The HTTPS certificate context is borrowed
from `fetch_data` at call time, because a frozen PyInstaller application does
not inherit the interpreter's certificate bundle.
"""

from __future__ import annotations

import json
import platform
import urllib.error
import urllib.request
from dataclasses import dataclass

GITHUB_REPOSITORY = "remypicciano/ICE-Detention-Path-finder"
LATEST_RELEASE_URL = (
    f"https://api.github.com/repos/{GITHUB_REPOSITORY}/releases/latest"
)
RELEASES_PAGE_URL = f"https://github.com/{GITHUB_REPOSITORY}/releases"
REQUEST_TIMEOUT = 15.0


class UpdateCheckError(Exception):
    """Raised when the latest release cannot be fetched or compared."""


@dataclass(frozen=True)
class LatestRelease:
    """The published release GitHub reported as latest."""

    version: str
    page_url: str
    published_at: str
    assets: tuple[str, ...]


@dataclass(frozen=True)
class UpdateStatus:
    """The verdict of one check: is the running version not the newest one?"""

    current: str
    latest: LatestRelease
    update_available: bool
    asset_name: str | None


def parse_version(text: str) -> tuple[int, ...]:
    """Turn a release version into comparable numeric parts.

    Only plain dotted integers are accepted. A tag this cannot order is an
    error rather than an excuse to guess: reporting "you are up to date"
    about a version that was never compared would be worse than saying
    nothing.
    """
    cleaned = text.strip().removeprefix("v").removeprefix("V")
    parts = cleaned.split(".")
    if not parts or any(not part.isdigit() for part in parts):
        raise UpdateCheckError(
            f"Cannot compare versions: {text!r} is not a dotted-integer "
            "release tag."
        )
    return tuple(int(part) for part in parts)


def update_is_available(current: str, latest: str) -> bool:
    """True when the published release is newer than the running version."""
    current_parts = parse_version(current)
    latest_parts = parse_version(latest)
    width = max(len(current_parts), len(latest_parts))
    # Fewer parts mean implicit zeros (2.1 equals 2.1.0), so pad before
    # comparing position by position.
    current_parts += (0,) * (width - len(current_parts))
    latest_parts += (0,) * (width - len(latest_parts))
    return latest_parts > current_parts


def _http_error_message(exc: urllib.error.HTTPError) -> str:
    if exc.code == 404:
        return "This repository has no published release yet."
    headers = exc.headers
    if exc.code in (403, 429) and headers is not None:
        if headers.get("X-RateLimit-Remaining") == "0":
            return (
                "GitHub's API rate limit for this address is exhausted. "
                "Try again in a little while."
            )
        return f"GitHub refused the request (HTTP {exc.code})."
    return f"GitHub returned an error (HTTP {exc.code})."


def fetch_latest_release(timeout: float = REQUEST_TIMEOUT) -> LatestRelease:
    """Read the latest published release from the GitHub API."""
    # Imported here rather than at module load: fetch_data pulls in pyarrow,
    # which a plain `--check-update` does not otherwise need.
    from fetch_data import USER_AGENT, certificate_context

    request = urllib.request.Request(
        LATEST_RELEASE_URL,
        headers={
            "Accept": "application/vnd.github+json",
            "User-Agent": USER_AGENT,
            "X-GitHub-Api-Version": "2022-11-28",
        },
    )
    try:
        with urllib.request.urlopen(
            request, context=certificate_context(), timeout=timeout
        ) as response:
            payload = json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        raise UpdateCheckError(_http_error_message(exc)) from exc
    except OSError as exc:
        raise UpdateCheckError(f"Could not reach GitHub: {exc}") from exc
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise UpdateCheckError(
            "GitHub returned a response that could not be read."
        ) from exc

    tag = payload.get("tag_name") if isinstance(payload, dict) else None
    if not isinstance(tag, str) or not tag.strip():
        raise UpdateCheckError("GitHub's response named no release tag.")
    version = tag.strip().removeprefix("v")
    parse_version(version)  # reject an uncomparable tag before reporting it

    page_url = payload.get("html_url")
    if not isinstance(page_url, str) or not page_url.startswith("https://"):
        page_url = RELEASES_PAGE_URL
    published_at = payload.get("published_at")
    raw_assets = payload.get("assets")
    assets = tuple(
        item["name"]
        for item in raw_assets
        if isinstance(item, dict) and isinstance(item.get("name"), str)
    ) if isinstance(raw_assets, list) else ()
    return LatestRelease(
        version=version,
        page_url=page_url,
        published_at=published_at if isinstance(published_at, str) else "",
        assets=assets,
    )


def asset_for_machine(
    release: LatestRelease,
    *,
    system: str | None = None,
    machine: str | None = None,
) -> str | None:
    """Name the release asset built for this machine, if one was published.

    The suffixes mirror the artifact names the build workflow produces:
    macOS uses the runner architecture (`ARM64`/`X64`), Linux uses
    `uname -m` (`x86_64`/`aarch64`), and the Windows asset carries no
    architecture. An unmatched release returns `None` — the caller falls
    back to the release page instead of offering a wrong download.
    """
    system_name = (system or platform.system()).lower()
    machine_name = (machine or platform.machine() or "").lower()

    if system_name.startswith("win"):
        suffix = "Windows"
    elif system_name == "darwin":
        architecture = "ARM64" if machine_name in ("arm64", "aarch64") else "X64"
        suffix = f"macOS-{architecture}"
    elif system_name.startswith("linux"):
        if machine_name in ("arm64", "aarch64"):
            architecture = "aarch64"
        elif machine_name in ("x86_64", "amd64"):
            architecture = "x86_64"
        else:
            architecture = machine_name
        suffix = f"Linux-{architecture}"
    else:
        return None

    prefix = f"ICEDetentionPathway-v{release.version}-{suffix}"
    for name in release.assets:
        extension = name[len(prefix):] if name.startswith(prefix) else None
        if extension in (".zip", ".tar.gz"):
            return name
    return None


def check_for_update(
    current: str,
    *,
    timeout: float = REQUEST_TIMEOUT,
    system: str | None = None,
    machine: str | None = None,
) -> UpdateStatus:
    """Fetch the latest release and compare it against the running version."""
    latest = fetch_latest_release(timeout=timeout)
    available = update_is_available(current, latest.version)
    asset = asset_for_machine(latest, system=system, machine=machine) if available else None
    return UpdateStatus(
        current=current,
        latest=latest,
        update_available=available,
        asset_name=asset,
    )


def _same_version(left: str, right: str) -> bool:
    """True when two version strings denote the same release (2.0 == 2.0.0)."""
    return not update_is_available(left, right) and not update_is_available(
        right, left
    )


def format_report(status: UpdateStatus) -> str:
    """Render the check as a few plain lines for the terminal or a log."""
    if status.update_available:
        lines = [
            (
                f"UPDATE AVAILABLE: {status.latest.version} "
                f"(you have {status.current})"
            )
        ]
        if status.asset_name:
            lines.append(f"Download for this machine: {status.asset_name}")
    elif _same_version(status.current, status.latest.version):
        lines = [f"UP TO DATE: {status.current} is the latest release."]
    else:
        # A source checkout can legitimately be ahead of every published
        # release; say so instead of claiming it is the release.
        lines = [
            (
                f"UP TO DATE: you have {status.current}; the newest published "
                f"release is {status.latest.version}."
            )
        ]
    lines.append(f"Release page: {status.latest.page_url}")
    if status.latest.published_at:
        lines.append(f"Published: {status.latest.published_at}")
    return "\n".join(lines)


