"""Build Firecrawl citation resources for one case without LLM processing."""

from __future__ import annotations

import argparse
import email.utils
import json
import os
import sys
import time
import urllib.error
import urllib.request
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


FIRECRAWL_SCRAPE_URL = "https://api.firecrawl.dev/v2/scrape"
MAX_RATE_LIMIT_RETRIES = 3
DEFAULT_RATE_LIMIT_RETRY_SECONDS = 15.0


def load_env(path: Path) -> None:
    """Load simple KEY=VALUE entries without overwriting process variables."""
    if not path.exists():
        return
    for raw_line in path.read_text(encoding="utf-8-sig").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip()
        value = value.strip()
        if (
            len(value) >= 2
            and value[0] == value[-1]
            and value[0] in {"'", '"'}
        ):
            value = value[1:-1]
        if key:
            os.environ.setdefault(key, value)


def write_json(path: Path, value: Any) -> None:
    path.write_text(
        json.dumps(value, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )


def retry_delay_seconds(exc: urllib.error.HTTPError, body: str) -> float:
    """Return Firecrawl's suggested retry delay, or the safe default."""
    retry_after = exc.headers.get("Retry-After") if exc.headers else None
    if retry_after:
        try:
            return max(0.0, float(retry_after))
        except ValueError:
            try:
                retry_at = email.utils.parsedate_to_datetime(retry_after)
                if retry_at.tzinfo is None:
                    retry_at = retry_at.replace(tzinfo=timezone.utc)
                return max(
                    0.0,
                    (retry_at - datetime.now(timezone.utc)).total_seconds(),
                )
            except (TypeError, ValueError, OverflowError):
                pass

    try:
        detail = json.loads(body)
    except json.JSONDecodeError:
        detail = None
    if isinstance(detail, dict):
        for key in ("retryAfter", "retry_after", "retryAfterSeconds"):
            value = detail.get(key)
            if isinstance(value, (int, float)) and not isinstance(value, bool):
                return max(0.0, float(value))
            if isinstance(value, str):
                try:
                    return max(0.0, float(value))
                except ValueError:
                    pass
    return DEFAULT_RATE_LIMIT_RETRY_SECONDS


def scrape_url(url: str, api_key: str, timeout_seconds: int) -> dict[str, Any]:
    """Call Firecrawl v2 Scrape, retrying HTTP 429 responses."""
    payload = json.dumps(
        {
            "url": url,
            "formats": ["markdown"],
            "onlyMainContent": True,
        }
    ).encode("utf-8")
    request = urllib.request.Request(
        FIRECRAWL_SCRAPE_URL,
        data=payload,
        method="POST",
        headers={
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
            "User-Agent": "rag-tool-citation-resource-builder/1.0",
        },
    )
    for retry_number in range(MAX_RATE_LIMIT_RETRIES + 1):
        try:
            with urllib.request.urlopen(
                request, timeout=timeout_seconds
            ) as response:
                body = response.read().decode("utf-8")
            break
        except urllib.error.HTTPError as exc:
            body = exc.read().decode("utf-8", errors="replace")
            if exc.code == 429 and retry_number < MAX_RATE_LIMIT_RETRIES:
                delay = retry_delay_seconds(exc, body)
                print(
                    "Firecrawl rate limit reached; "
                    f"retrying in {delay:g} seconds "
                    f"({retry_number + 1}/{MAX_RATE_LIMIT_RETRIES}).",
                    file=sys.stderr,
                )
                time.sleep(delay)
                continue
            try:
                detail = json.loads(body)
            except json.JSONDecodeError:
                detail = body
            raise RuntimeError(f"Firecrawl HTTP {exc.code}: {detail}") from exc
        except urllib.error.URLError as exc:
            raise RuntimeError(
                f"Firecrawl request failed: {exc.reason}"
            ) from exc

    try:
        result = json.loads(body)
    except json.JSONDecodeError as exc:
        raise RuntimeError("Firecrawl returned non-JSON content") from exc
    if not isinstance(result, dict) or result.get("success") is not True:
        raise RuntimeError(f"Firecrawl reported failure: {result}")
    if not isinstance(result.get("data"), dict):
        raise RuntimeError("Firecrawl response is missing a data object")
    return result


def build_firecrawl_payload(response: dict[str, Any]) -> dict[str, Any]:
    """Preserve every field in Firecrawl data and expose the title explicitly."""
    firecrawl = dict(response["data"])
    metadata = firecrawl.get("metadata")
    if not isinstance(metadata, dict):
        metadata = {}
        firecrawl["metadata"] = metadata
    firecrawl.setdefault("markdown", "")
    firecrawl.setdefault("title", metadata.get("title", ""))

    # Preserve response-level fields such as creditsUsed or warning as received.
    envelope = {key: value for key, value in response.items() if key != "data"}
    if envelope:
        firecrawl["_response"] = envelope
    return firecrawl


def crawl_case(
    case_path: Path,
    output_root: Path,
    api_key: str,
    timeout_seconds: int = 120,
    limit: int | None = None,
    sleep_between_urls: float = 2.0,
) -> dict[str, Any]:
    case = json.loads(case_path.read_text(encoding="utf-8"))
    case_id = case.get("case_id")
    prompt = case.get("prompt")
    cited_urls = case.get("cited_urls")
    if not isinstance(case_id, str) or not case_id:
        raise ValueError("Case must contain a non-empty case_id")
    if not isinstance(prompt, str) or not prompt:
        raise ValueError("Case must contain a non-empty prompt")
    if not isinstance(cited_urls, list):
        raise TypeError("Case cited_urls must be a list")
    if limit is not None:
        if limit < 1:
            raise ValueError("limit must be at least 1")
        cited_urls = cited_urls[:limit]
    if sleep_between_urls < 0:
        raise ValueError("sleep_between_urls cannot be negative")

    output_dir = output_root / case_id
    output_dir.mkdir(parents=True, exist_ok=True)
    manifest: dict[str, Any] = {
        "case_id": case_id,
        "prompt": prompt,
        "expected_url_count": len(cited_urls),
        "success_count": 0,
        "failed_count": 0,
        "resources": [],
    }
    write_json(output_dir / "manifest.json", manifest)

    for position, source_url in enumerate(cited_urls, start=1):
        citation_id = f"citation_{position:03d}"
        resource: dict[str, Any] = {
            "case_id": case_id,
            "citation_id": citation_id,
            "source_url": source_url,
            "status": "failed",
            "firecrawl": {
                "title": "",
                "markdown": "",
                "metadata": {},
            },
            "structured": None,
            "error": None,
        }
        try:
            response = scrape_url(source_url, api_key, timeout_seconds)
            resource["firecrawl"] = build_firecrawl_payload(response)
            resource["status"] = "success"
            manifest["success_count"] += 1
        except Exception as exc:  # Continue so one URL cannot abort the Case.
            resource["error"] = {
                "type": type(exc).__name__,
                "message": str(exc),
            }
            manifest["failed_count"] += 1

        write_json(output_dir / f"{citation_id}.json", resource)
        manifest["resources"].append(
            {
                "citation_id": citation_id,
                "source_url": source_url,
                "status": resource["status"],
            }
        )
        # Persist progress after every URL so interrupted runs remain inspectable.
        write_json(output_dir / "manifest.json", manifest)
        if position < len(cited_urls) and sleep_between_urls:
            time.sleep(sleep_between_urls)

    return manifest


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--case",
        type=Path,
        default=Path("data/cases/case_001.json"),
    )
    parser.add_argument(
        "--output-root",
        type=Path,
        default=Path("data/citation_resources"),
    )
    parser.add_argument("--env-file", type=Path, default=Path(".env"))
    parser.add_argument("--timeout", type=int, default=120)
    parser.add_argument(
        "--limit",
        type=int,
        default=None,
        help="Process only the first N cited URLs (for staged testing)",
    )
    parser.add_argument(
        "--sleep-between-urls",
        type=float,
        default=2.0,
        help="Seconds to wait between cited URLs (default: 2)",
    )
    args = parser.parse_args()

    load_env(args.env_file)
    api_key = os.environ.get("FIRECRAWL_API_KEY", "").strip()
    if not api_key:
        print(
            f"Error: FIRECRAWL_API_KEY is missing. Add it to {args.env_file}.",
            file=sys.stderr,
        )
        return 2

    manifest = crawl_case(
        args.case,
        args.output_root,
        api_key,
        args.timeout,
        args.limit,
        args.sleep_between_urls,
    )
    print(
        f"{manifest['case_id']}: {manifest['success_count']} succeeded, "
        f"{manifest['failed_count']} failed."
    )
    return 0 if manifest["failed_count"] == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
