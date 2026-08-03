"""Batch Firecrawl citation resources for all discovered cases.

Resume is intentionally case-based: an incomplete case is rebuilt by the
existing crawl_case() function; individual successful URLs are not resumed.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

try:
    from crawl_case import crawl_case, load_env, write_json
except ImportError:  # Allow importing this module as scripts.crawl_all_cases.
    from scripts.crawl_case import crawl_case, load_env, write_json


EXPECTED_TOTAL_CASES = 14
EXPECTED_TOTAL_RELATIONSHIPS = 87


def iso_now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def read_case(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise TypeError("Case JSON root must be an object")
    case_id = value.get("case_id")
    cited_urls = value.get("cited_urls")
    if not isinstance(case_id, str) or not case_id:
        raise ValueError("Case must contain a non-empty case_id")
    if not isinstance(cited_urls, list):
        raise TypeError("Case cited_urls must be a list")
    return value


def read_manifest(path: Path) -> tuple[dict[str, Any] | None, str | None]:
    if not path.exists():
        return None, None
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(value, dict):
            raise TypeError("manifest root must be an object")
        return value, None
    except (OSError, ValueError, TypeError, json.JSONDecodeError) as exc:
        return None, f"{type(exc).__name__}: {exc}"


def manifest_counts(manifest: dict[str, Any] | None) -> tuple[int, int]:
    if manifest is None:
        return 0, 0
    success = manifest.get("success_count", 0)
    failed = manifest.get("failed_count", 0)
    if not isinstance(success, int) or isinstance(success, bool) or success < 0:
        success = 0
    if not isinstance(failed, int) or isinstance(failed, bool) or failed < 0:
        failed = 0
    return success, failed


def case_status(
    manifest: dict[str, Any] | None,
    expected_url_count: int,
    manifest_error: str | None = None,
) -> str:
    if manifest_error:
        return "incomplete"
    if manifest is None:
        return "not_started"
    success, failed = manifest_counts(manifest)
    manifest_expected = manifest.get("expected_url_count")
    if (
        manifest_expected == expected_url_count
        and success == expected_url_count
        and failed == 0
    ):
        return "completed"
    return "incomplete"


def outcome_status(success: int, failed: int, expected: int) -> str:
    if success == expected and failed == 0:
        return "completed"
    if success > 0:
        return "partial"
    return "failed"


def base_report(started_at: str) -> dict[str, Any]:
    return {
        "started_at": started_at,
        "completed_at": "",
        "mode": "dry_run",
        "total_cases_discovered": 0,
        "total_citation_relationships": 0,
        "selected_case_count": 0,
        "completed_before_run": 0,
        "skipped_completed": 0,
        "processed_case_count": 0,
        "success_case_count": 0,
        "partial_case_count": 0,
        "failed_case_count": 0,
        "total_url_success_count": 0,
        "total_url_failed_count": 0,
        "cases": [],
        "errors": [],
    }


def save_report(report_path: Path, report: dict[str, Any]) -> None:
    report_path.parent.mkdir(parents=True, exist_ok=True)
    report["completed_at"] = iso_now()
    write_json(report_path, report)


def error_record(case_id: str | None, path: Path | None, exc: object) -> dict[str, Any]:
    record: dict[str, Any] = {"message": str(exc)}
    if case_id:
        record["case_id"] = case_id
    if path:
        record["path"] = str(path)
    if isinstance(exc, BaseException):
        record["type"] = type(exc).__name__
    return record


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--cases-dir", type=Path, default=Path("data/cases"))
    parser.add_argument(
        "--output-root", type=Path, default=Path("data/citation_resources")
    )
    parser.add_argument(
        "--report", type=Path, default=Path("reports/crawl_batch_report.json")
    )
    parser.add_argument("--env-file", type=Path, default=Path(".env"))
    parser.add_argument("--timeout", type=int, default=120)
    parser.add_argument("--case", dest="case_id")
    parser.add_argument("--limit-cases", type=int)
    parser.add_argument("--url-limit", type=int)
    parser.add_argument("--retry-incomplete", action="store_true")
    parser.add_argument("--dry-run", action="store_true")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    started_at = iso_now()
    report = base_report(started_at)
    report["mode"] = "dry_run" if args.dry_run else "crawl"

    if args.limit_cases is not None and args.limit_cases < 1:
        print("Error: --limit-cases must be at least 1.", file=sys.stderr)
        return 2
    if args.url_limit is not None and args.url_limit < 1:
        print("Error: --url-limit must be at least 1.", file=sys.stderr)
        return 2
    if not args.cases_dir.is_dir():
        message = f"Cases directory not found: {args.cases_dir}"
        report["errors"].append(error_record(None, args.cases_dir, message))
        save_report(args.report, report)
        print(f"Error: {message}", file=sys.stderr)
        return 2

    paths = list(args.cases_dir.glob("case_*.json"))
    if not paths:
        message = f"No case_*.json files found in {args.cases_dir}"
        report["errors"].append(error_record(None, args.cases_dir, message))
        save_report(args.report, report)
        print(f"Error: {message}", file=sys.stderr)
        return 2

    report["total_cases_discovered"] = len(paths)
    cases: list[dict[str, Any]] = []
    invalid_entries: list[dict[str, Any]] = []
    for path in paths:
        try:
            case = read_case(path)
            cases.append({"path": path, "data": case})
        except Exception as exc:
            case_id = path.stem
            report["errors"].append(error_record(case_id, path, exc))
            invalid_entries.append(
                {
                    "case_id": case_id,
                    "expected_url_count": 0,
                    "status_before": "invalid_case",
                    "action": "dry_run" if args.dry_run else "skipped",
                    "success_count": 0,
                    "failed_count": 0,
                    "status_after": "invalid_case",
                }
            )
            print(f"{case_id}: invalid Case JSON ({exc})", file=sys.stderr)

    cases.sort(key=lambda item: item["data"]["case_id"])
    report["total_citation_relationships"] = sum(
        len(item["data"]["cited_urls"]) for item in cases
    )

    selected = cases
    if args.case_id:
        selected = [
            item for item in selected if item["data"]["case_id"] == args.case_id
        ]
        if not selected:
            message = f"Case not found or invalid: {args.case_id}"
            report["errors"].append(error_record(args.case_id, None, message))
            report["cases"].extend(invalid_entries)
            save_report(args.report, report)
            print(f"Error: {message}", file=sys.stderr)
            return 2
    if args.limit_cases is not None:
        selected = selected[: args.limit_cases]

    prepared: list[dict[str, Any]] = []
    for item in selected:
        case_id = item["data"]["case_id"]
        full_count = len(item["data"]["cited_urls"])
        expected = min(full_count, args.url_limit) if args.url_limit else full_count
        manifest_path = args.output_root / case_id / "manifest.json"
        manifest, manifest_error = read_manifest(manifest_path)
        status = case_status(manifest, expected, manifest_error)
        success, failed = manifest_counts(manifest)
        prepared.append(
            {
                **item,
                "expected": expected,
                "manifest": manifest,
                "manifest_error": manifest_error,
                "status": status,
                "success": success,
                "failed": failed,
            }
        )
        if manifest_error:
            report["errors"].append(
                error_record(case_id, manifest_path, f"Invalid manifest: {manifest_error}")
            )

    report["selected_case_count"] = len(prepared)
    report["completed_before_run"] = sum(
        item["status"] == "completed" for item in prepared
    )

    integrity_ok = (
        report["total_cases_discovered"] == EXPECTED_TOTAL_CASES
        and report["total_citation_relationships"] == EXPECTED_TOTAL_RELATIONSHIPS
        and not invalid_entries
    )
    if not integrity_ok:
        message = (
            "Dataset totals do not match the required baseline: "
            f"cases={report['total_cases_discovered']} (expected {EXPECTED_TOTAL_CASES}), "
            "citation_relationships="
            f"{report['total_citation_relationships']} "
            f"(expected {EXPECTED_TOTAL_RELATIONSHIPS})."
        )
        report["errors"].append(error_record(None, None, message))
        print(f"Error: {message}", file=sys.stderr)

    if not args.dry_run:
        if not integrity_ok:
            report["cases"].extend(invalid_entries)
            save_report(args.report, report)
            print("Formal crawl was not started.", file=sys.stderr)
            return 2
        load_env(args.env_file)
        api_key = os.environ.get("FIRECRAWL_API_KEY", "").strip()
        if not api_key:
            message = (
                f"FIRECRAWL_API_KEY is missing. Add it to {args.env_file}."
            )
            report["errors"].append(error_record(None, args.env_file, message))
            save_report(args.report, report)
            print(f"Error: {message}", file=sys.stderr)
            return 2
    else:
        api_key = ""

    for item in prepared:
        case_id = item["data"]["case_id"]
        entry = {
            "case_id": case_id,
            "expected_url_count": item["expected"],
            "status_before": item["status"],
            "action": "dry_run",
            "success_count": item["success"],
            "failed_count": item["failed"],
            "status_after": item["status"],
        }
        if args.dry_run:
            report["cases"].append(entry)
            continue
        if item["status"] == "completed":
            entry["action"] = "skipped"
            report["skipped_completed"] += 1
            report["cases"].append(entry)
            print(f"{case_id}: skipped (completed)")
            continue

        entry["action"] = "processed"
        report["processed_case_count"] += 1
        try:
            manifest = crawl_case(
                item["path"],
                args.output_root,
                api_key,
                args.timeout,
                args.url_limit,
            )
            success, failed = manifest_counts(manifest)
            status_after = outcome_status(success, failed, item["expected"])
            entry.update(
                {
                    "success_count": success,
                    "failed_count": failed,
                    "status_after": status_after,
                }
            )
            report["total_url_success_count"] += success
            report["total_url_failed_count"] += failed
            if status_after == "completed":
                report["success_case_count"] += 1
            elif status_after == "partial":
                report["partial_case_count"] += 1
            else:
                report["failed_case_count"] += 1
            print(f"{case_id}: {success} succeeded, {failed} failed")
        except Exception as exc:
            entry["status_after"] = "failed"
            report["failed_case_count"] += 1
            report["errors"].append(error_record(case_id, item["path"], exc))
            print(f"{case_id}: unexpected error ({exc})", file=sys.stderr)
        report["cases"].append(entry)

    report["cases"].extend(invalid_entries)
    save_report(args.report, report)

    print(f"total_cases: {report['total_cases_discovered']}")
    print(
        "total_citation_relationships: "
        f"{report['total_citation_relationships']}"
    )
    for item in prepared:
        print(f"{item['data']['case_id']}: {item['expected']} URLs")
    if args.dry_run:
        completed = sum(item["status"] == "completed" for item in prepared)
        not_started = sum(item["status"] == "not_started" for item in prepared)
        incomplete = sum(item["status"] == "incomplete" for item in prepared)
        print(f"completed_cases: {completed}")
        print(f"not_started_cases: {not_started}")
        print(f"incomplete_cases: {incomplete + len(invalid_entries)}")
    print(f"batch_report: {args.report}")
    return 0 if integrity_ok and not report["failed_case_count"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
