#!/usr/bin/env python3
"""Upload an offline build_index.py output directory to Cloudflare R2.

Two auth modes:

  --auth oauth  Use the signed-in `cf` CLI token (default for this repo).
                Needs CLOUDFLARE_ACCOUNT_ID or --account-id.
  --auth s3     Use R2 S3 credentials from R2_ACCOUNT_ID,
                R2_ACCESS_KEY_ID, and R2_SECRET_ACCESS_KEY.

The bucket name is passed with --bucket. Build once and upload the same
directory to the staging bucket as well.
"""

from __future__ import annotations

import argparse
import json
import os
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from typing import Any

from website.offline.build_index import DEFAULT_OUT

API_ROOT = "https://api.cloudflare.com/client/v4"


def content_type(key: str) -> str:
    if key.endswith(".txt"):
        return "text/plain; charset=utf-8"
    return "application/json"


def cf_config_path(explicit: str | None) -> Path | None:
    candidates = []
    if explicit:
        candidates.append(Path(explicit).expanduser())
    if os.environ.get("CLOUDFLARE_CONFIG"):
        candidates.append(Path(os.environ["CLOUDFLARE_CONFIG"]).expanduser())
    candidates.extend(
        [
            Path.home() / "Library/Preferences/cloudflare/config/default.json",
            Path.home() / ".config/cloudflare/config/default.json",
        ]
    )
    return next((path for path in candidates if path.is_file()), None)


def oauth_credentials(explicit_config: str | None) -> tuple[str, str]:
    token = os.environ.get("CLOUDFLARE_API_TOKEN", "")
    if not token:
        path = cf_config_path(explicit_config)
        if path is None:
            raise SystemExit(
                "No cf CLI credentials found. Run `cf auth login` or set CLOUDFLARE_API_TOKEN."
            )
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise SystemExit(f"Cannot read cf config {path}: {exc}") from exc
        value = payload.get("oauth_token")
        token = value if isinstance(value, str) else ""
    if not token:
        raise SystemExit("cf config has no oauth_token.")

    account = os.environ.get("CLOUDFLARE_ACCOUNT_ID", "")
    if not account:
        path = cf_config_path(explicit_config)
        if path is not None:
            try:
                payload = json.loads(path.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                payload = {}
            scopes = payload.get("scopes", [])
            if isinstance(scopes, list):
                for scope in scopes:
                    if isinstance(scope, dict) and scope.get("id"):
                        account = str(scope["id"])
                        break
    return token, account


def s3_client() -> Any:  # type: ignore[name-defined]
    try:
        import boto3
    except ImportError as exc:  # pragma: no cover - deployment helper
        raise SystemExit(
            "boto3 is required for --auth s3: "
            ".venv/bin/python -m pip install -r website/requirements-build.txt"
        ) from exc
    account = os.environ.get("R2_ACCOUNT_ID", "")
    key_id = os.environ.get("R2_ACCESS_KEY_ID", "")
    secret = os.environ.get("R2_SECRET_ACCESS_KEY", "")
    missing = [
        name
        for name, value in (
            ("R2_ACCOUNT_ID", account),
            ("R2_ACCESS_KEY_ID", key_id),
            ("R2_SECRET_ACCESS_KEY", secret),
        )
        if not value
    ]
    if missing:
        raise SystemExit(f"Missing R2 credentials: {', '.join(missing)}")
    return boto3.client(
        "s3",
        endpoint_url=f"https://{account}.r2.cloudflarestorage.com",
        aws_access_key_id=key_id,
        aws_secret_access_key=secret,
        region_name="auto",
    )


class RateLimiter:
    """Keep Cloudflare API writes under its per-token request limit."""

    def __init__(self, per_second: float) -> None:
        self.interval = 1.0 / max(per_second, 0.1)
        self.lock = threading.Lock()
        self.next_at = 0.0

    def wait(self) -> None:
        with self.lock:
            now = time.monotonic()
            delay = self.next_at - now
            if delay > 0:
                time.sleep(delay)
                now = time.monotonic()
            self.next_at = max(now, self.next_at) + self.interval


def retry_after_seconds(exc: urllib.error.HTTPError) -> float | None:
    raw = exc.headers.get("Retry-After") if exc.headers else None
    if raw:
        try:
            return max(0.0, float(raw))
        except ValueError:
            pass
    return None


def api_get_json(url: str, token: str) -> dict[str, Any]:
    """GET a Cloudflare API payload with retry/backoff for transient errors."""
    for attempt in range(10):
        request = urllib.request.Request(
            url,
            method="GET",
            headers={"Authorization": f"Bearer {token}"},
        )
        try:
            with urllib.request.urlopen(request, timeout=60) as response:
                return json.load(response)
        except urllib.error.HTTPError as exc:
            if exc.code == 429 and attempt < 9:
                delay = retry_after_seconds(exc)
                time.sleep(delay if delay is not None else min(60.0, 2 ** min(attempt, 6)))
                continue
            if exc.code in (500, 502, 503, 504) and attempt < 9:
                time.sleep(min(30.0, 2 ** min(attempt, 5)))
                continue
            detail = exc.read().decode("utf-8", "replace")[:300]
            raise SystemExit(f"Cloudflare API GET failed: HTTP {exc.code} {detail}") from exc
        except (urllib.error.URLError, TimeoutError, OSError) as exc:
            if attempt < 9:
                time.sleep(min(30.0, 2 ** min(attempt, 5)))
                continue
            raise SystemExit(f"Cloudflare API GET failed: {exc}") from exc
    raise SystemExit("Cloudflare API GET failed after retries.")


def list_object_keys(bucket: str, token: str, account: str) -> set[str]:
    """List every key already present in an R2 bucket."""
    if not account:
        raise SystemExit("Missing account ID: set CLOUDFLARE_ACCOUNT_ID or pass --account-id.")
    base = f"{API_ROOT}/accounts/{account}/r2/buckets/{bucket}/objects"
    keys: set[str] = set()
    cursor: str | None = None
    while True:
        params: dict[str, str | int] = {"per_page": 1000}
        if cursor:
            params["cursor"] = cursor
        payload = api_get_json(f"{base}?{urllib.parse.urlencode(params)}", token)
        if not payload.get("success"):
            errors = payload.get("errors") or []
            raise SystemExit(f"Cannot list objects in {bucket}: {errors}")
        for item in payload.get("result") or []:
            if isinstance(item, dict) and isinstance(item.get("key"), str):
                keys.add(item["key"])
        info = payload.get("result_info") or {}
        if not info.get("is_truncated"):
            break
        next_cursor = info.get("cursor")
        if not next_cursor:
            break
        cursor = str(next_cursor)
    return keys


def upload_oauth(
    files: list[Path],
    build_dir: Path,
    bucket: str,
    token: str,
    account: str,
    workers: int,
    rate: float,
) -> None:
    if not account:
        raise SystemExit("Missing account ID: set CLOUDFLARE_ACCOUNT_ID or pass --account-id.")
    limiter = RateLimiter(rate)

    def put(path: Path) -> str | None:
        key = path.relative_to(build_dir).as_posix()
        url = (
            f"{API_ROOT}/accounts/{account}/r2/buckets/{bucket}/objects/"
            f"{urllib.parse.quote(key, safe='/')}"
        )
        body = path.read_bytes()
        for attempt in range(10):
            limiter.wait()
            request = urllib.request.Request(
                url,
                data=body,
                method="PUT",
                headers={
                    "Authorization": f"Bearer {token}",
                    "Content-Type": content_type(key),
                },
            )
            try:
                with urllib.request.urlopen(request, timeout=120) as response:
                    if response.status in (200, 201):
                        return None
                    return f"{key}: HTTP {response.status}"
            except urllib.error.HTTPError as exc:
                if exc.code == 429 and attempt < 9:
                    delay = retry_after_seconds(exc)
                    time.sleep(delay if delay is not None else min(60.0, 2 ** min(attempt, 6)))
                    continue
                if exc.code in (500, 502, 503, 504) and attempt < 9:
                    time.sleep(min(30.0, 2 ** min(attempt, 5)))
                    continue
                detail = exc.read().decode("utf-8", "replace")[:300]
                return f"{key}: HTTP {exc.code} {detail}"
            except (urllib.error.URLError, TimeoutError, OSError) as exc:
                if attempt < 9:
                    time.sleep(min(30.0, 2 ** min(attempt, 5)))
                    continue
                return f"{key}: {exc}"
        return f"{key}: upload failed"

    errors: list[str] = []
    with ThreadPoolExecutor(max_workers=workers) as pool:
        futures = {pool.submit(put, path): path for path in files}
        for number, future in enumerate(as_completed(futures), start=1):
            error = future.result()
            if error:
                errors.append(error)
            if number % 1000 == 0 or number == len(files):
                print(f"  uploaded {number:,}/{len(files):,}", flush=True)
    if errors:
        for error in errors[:20]:
            print(error)
        raise SystemExit(f"{len(errors):,} object(s) failed to upload.")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--build-dir", type=Path, default=DEFAULT_OUT)
    parser.add_argument("--bucket", required=True)
    parser.add_argument("--auth", choices=("oauth", "s3"), default="oauth")
    parser.add_argument("--account-id", default="", help="Cloudflare account ID for --auth oauth")
    parser.add_argument("--cf-config", default="", help="cf CLI config path for --auth oauth")
    parser.add_argument("--workers", type=int, default=4)
    parser.add_argument(
        "--rate",
        type=float,
        default=3.5,
        help="Max Cloudflare API PUT requests per second (default 3.5).",
    )
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument(
        "--skip-existing",
        action="store_true",
        help="With --auth oauth, list the bucket and upload only keys that are missing.",
    )
    args = parser.parse_args()
    if args.skip_existing and args.auth != "oauth":
        raise SystemExit("--skip-existing currently supports --auth oauth only.")
    if not args.build_dir.is_dir():
        raise SystemExit(f"No build directory at {args.build_dir}; run build_index.py first.")

    files = sorted(path for path in args.build_dir.rglob("*") if path.is_file())
    if not files:
        raise SystemExit("Build directory is empty.")

    if args.dry_run:
        for path in files:
            print(path.relative_to(args.build_dir))
        print(f"Dry run: {len(files)} objects for bucket {args.bucket}")
        return

    if args.auth == "oauth":
        token, account = oauth_credentials(args.cf_config or None)
        if args.account_id:
            account = args.account_id
        if args.skip_existing:
            existing = list_object_keys(args.bucket, token, account)
            total = len(files)
            files = [
                path
                for path in files
                if path.relative_to(args.build_dir).as_posix() not in existing
            ]
            print(
                f"skipping {total - len(files):,} existing objects; {len(files):,} left",
                flush=True,
            )
            if not files:
                print(f"All objects already present in {args.bucket}.")
                return
        upload_oauth(
            files, args.build_dir, args.bucket, token, account, args.workers, args.rate
        )
    else:
        client = s3_client()
        for number, path in enumerate(files, start=1):
            key = path.relative_to(args.build_dir).as_posix()
            # No ContentEncoding: the Worker decompresses .gz explicitly.
            client.upload_file(
                str(path),
                args.bucket,
                key,
                ExtraArgs={"ContentType": content_type(key)},
            )
            if number % 1000 == 0:
                print(f"  uploaded {number:,}/{len(files):,}", flush=True)
    print(f"Uploaded {len(files):,} objects to {args.bucket}")


if __name__ == "__main__":
    main()
