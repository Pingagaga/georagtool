"""Build reproducible Case Package JSON files from cases and citations."""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


PACKAGE_VERSION = "1.0"
CASE_FILE_RE = re.compile(r"case_\d+\.json$")
CASE_FIELDS = (
    "case_id",
    "country",
    "prompt",
    "response",
    "model",
    "volume",
    "fanout_queries",
    "found_but_not_cited_urls",
    "updated",
    "source_row",
)


def read_json(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8-sig"))
    if not isinstance(value, dict):
        raise TypeError(f"{path} must contain a JSON object")
    return value


def atomic_write_json(path: Path, value: Any) -> None:
    """Write formatted UTF-8 JSON and atomically replace the destination."""
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary_name: str | None = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="w",
            encoding="utf-8",
            newline="\n",
            dir=path.parent,
            prefix=f".{path.name}.",
            suffix=".tmp",
            delete=False,
        ) as temporary_file:
            temporary_name = temporary_file.name
            json.dump(value, temporary_file, ensure_ascii=False, indent=2)
            temporary_file.write("\n")
            temporary_file.flush()
            os.fsync(temporary_file.fileno())
        os.replace(temporary_name, path)
    except BaseException:
        if temporary_name is not None:
            try:
                Path(temporary_name).unlink(missing_ok=True)
            except OSError:
                pass
        raise


def iso_now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def project_path(path: Path) -> str:
    return path.as_posix()


def empty_citation(
    citation_id: str, position: int, source_url: str, status: str
) -> dict[str, Any]:
    return {
        "citation_id": citation_id,
        "position": position,
        "source_url": source_url,
        "status": status,
        "capture_method": "none",
        "title": "",
        "markdown": "",
        "metadata": {},
        "structured": None,
        "error": None,
    }


def manual_capture(path: Path) -> tuple[str, str, dict[str, Any]]:
    """Return title, verbatim Markdown, and parsed template metadata."""
    markdown = path.read_text(encoding="utf-8-sig")
    title = ""
    metadata: dict[str, Any] = {
        "source": "manual",
        "manual_source_file": project_path(path),
    }
    for line in markdown.splitlines():
        if not title and line.startswith("# "):
            title = line[2:].strip()
        if line.startswith("> ") and ":" in line:
            key, value = line[2:].split(":", 1)
            metadata[key.strip().lower().replace(" ", "_")] = value.strip()
        elif line.startswith("- ") and ":" in line:
            key, value = line[2:].split(":", 1)
            metadata[key.strip().lower().replace(" ", "_")] = value.strip()
    return title, markdown, metadata


def determine_capture_method(record: dict[str, Any]) -> str:
    capture = record.get("capture")
    if isinstance(capture, dict) and capture.get("method"):
        return str(capture["method"])

    status = record.get("status")
    firecrawl = record.get("firecrawl")
    firecrawl = firecrawl if isinstance(firecrawl, dict) else {}
    metadata = firecrawl.get("metadata")
    metadata = metadata if isinstance(metadata, dict) else {}
    if status == "success" and metadata.get("source") == "manual":
        return "manual"
    if status == "success" and firecrawl.get("markdown"):
        return "firecrawl"
    if status in {"unsupported", "failed"}:
        return "none"
    return "none"


def citation_from_record(
    record: dict[str, Any], citation_id: str, position: int, source_url: str
) -> dict[str, Any]:
    citation = empty_citation(
        citation_id, position, source_url, str(record.get("status") or "failed")
    )
    firecrawl = record.get("firecrawl")
    firecrawl = firecrawl if isinstance(firecrawl, dict) else {}
    metadata = firecrawl.get("metadata")
    citation.update(
        {
            "capture_method": determine_capture_method(record),
            "title": str(firecrawl.get("title") or ""),
            "markdown": str(firecrawl.get("markdown") or ""),
            "metadata": metadata if isinstance(metadata, dict) else {},
            "structured": record.get("structured"),
            "error": record.get("error"),
        }
    )
    return citation


def build_package(
    case_path: Path,
    citation_root: Path,
    manual_root: Path,
    *,
    built_at: str | None = None,
) -> tuple[dict[str, Any], dict[str, Any]]:
    case = read_json(case_path)
    case_id = case.get("case_id")
    if not isinstance(case_id, str) or not case_id:
        raise ValueError(f"{case_path} has no valid case_id")
    cited_urls = case.get("cited_urls")
    if not isinstance(cited_urls, list) or not all(
        isinstance(url, str) for url in cited_urls
    ):
        raise TypeError(f"{case_path} cited_urls must be a list of strings")

    citation_dir = citation_root / case_id
    manifest_path = citation_dir / "manifest.json"
    warnings: list[str] = []
    found_json_count = 0
    missing_file_count = 0
    source_urls_match = True
    citations: list[dict[str, Any]] = []

    if not manifest_path.exists():
        warnings.append(f"Manifest file missing: {project_path(manifest_path)}")
    else:
        try:
            manifest = read_json(manifest_path)
            if manifest.get("case_id") != case_id:
                warnings.append(
                    "Manifest case_id mismatch: "
                    f"expected {case_id!r}, found {manifest.get('case_id')!r}"
                )
            if manifest.get("expected_url_count") != len(cited_urls):
                warnings.append(
                    "Manifest expected_url_count mismatch: "
                    f"expected {len(cited_urls)}, "
                    f"found {manifest.get('expected_url_count')!r}"
                )
        except (OSError, ValueError, TypeError, json.JSONDecodeError) as exc:
            warnings.append(f"Cannot read manifest: {type(exc).__name__}: {exc}")

    for position, canonical_url in enumerate(cited_urls, start=1):
        citation_id = f"citation_{position:03d}"
        citation_path = citation_dir / f"{citation_id}.json"
        manual_path = manual_root / f"{case_id}_{citation_id}.md"

        record: dict[str, Any] | None = None
        if citation_path.exists():
            found_json_count += 1
            try:
                record = read_json(citation_path)
            except (OSError, ValueError, TypeError, json.JSONDecodeError) as exc:
                warnings.append(
                    f"{citation_id}: cannot read Citation JSON: "
                    f"{type(exc).__name__}: {exc}"
                )
        else:
            missing_file_count += 1
            warnings.append(
                f"{citation_id}: Citation JSON missing: "
                f"{project_path(citation_path)}"
            )

        if record is None:
            citation = empty_citation(
                citation_id, position, canonical_url, "missing"
            )
            citation["error"] = {
                "type": "MissingCitationFile",
                "message": f"Citation JSON not found: {project_path(citation_path)}",
            }
        else:
            record_url = record.get("source_url")
            if record_url != canonical_url:
                source_urls_match = False
                warnings.append(
                    f"{citation_id}: source_url mismatch; canonical Case URL "
                    f"{canonical_url!r}, Citation JSON URL {record_url!r}"
                )
            if record.get("citation_id") != citation_id:
                warnings.append(
                    f"{citation_id}: citation_id mismatch in JSON; "
                    f"found {record.get('citation_id')!r}"
                )
            citation = citation_from_record(
                record, citation_id, position, canonical_url
            )

        if manual_path.exists():
            try:
                title, markdown, metadata = manual_capture(manual_path)
                citation.update(
                    {
                        "status": "success",
                        "capture_method": "manual",
                        "title": title,
                        "markdown": markdown,
                        "metadata": metadata,
                        "structured": None,
                        "error": None,
                    }
                )
            except OSError as exc:
                warnings.append(
                    f"{citation_id}: cannot read manual capture: "
                    f"{type(exc).__name__}: {exc}"
                )

        citations.append(citation)

    summary = {
        "expected_citation_count": len(citations),
        "available_citation_count": sum(
            c["status"] == "success" and bool(c["markdown"]) for c in citations
        ),
        "firecrawl_count": sum(
            c["capture_method"] == "firecrawl" for c in citations
        ),
        "manual_count": sum(c["capture_method"] == "manual" for c in citations),
        "unsupported_count": sum(c["status"] == "unsupported" for c in citations),
        "failed_count": sum(c["status"] == "failed" for c in citations),
        "missing_file_count": missing_file_count,
        "package_complete": False,
    }
    summary["package_complete"] = missing_file_count == 0 and all(
        c["status"] in {"success", "unsupported"} for c in citations
    )

    package = {
        "package_version": PACKAGE_VERSION,
        "case": {field: case.get(field) for field in CASE_FIELDS},
        "citations": citations,
        "summary": summary,
        "provenance": {
            "case_source_file": project_path(case_path),
            "citation_source_directory": project_path(citation_dir),
            "manifest_source_file": project_path(manifest_path),
            "built_at": built_at or iso_now(),
        },
        "warnings": warnings,
    }
    diagnostics = {
        "case_id": case_id,
        "expected_citation_count": len(cited_urls),
        "citation_json_count": found_json_count,
        "success_count": sum(c["status"] == "success" for c in citations),
        "source_urls_match": source_urls_match,
        "warnings": warnings,
    }
    return package, diagnostics


def package_index(packages: list[dict[str, Any]]) -> dict[str, Any]:
    entries = []
    for package in packages:
        case = package["case"]
        summary = package["summary"]
        entries.append(
            {
                "case_id": case["case_id"],
                "prompt": case["prompt"],
                "file": f"{case['case_id']}.package.json",
                "expected_citation_count": summary["expected_citation_count"],
                "available_citation_count": summary["available_citation_count"],
                "manual_count": summary["manual_count"],
                "unsupported_count": summary["unsupported_count"],
                "failed_count": summary["failed_count"],
                "missing_file_count": summary["missing_file_count"],
                "package_complete": summary["package_complete"],
            }
        )
    return {
        "package_version": PACKAGE_VERSION,
        "total_cases": len(packages),
        "total_expected_citations": sum(
            p["summary"]["expected_citation_count"] for p in packages
        ),
        "total_available_citations": sum(
            p["summary"]["available_citation_count"] for p in packages
        ),
        "total_firecrawl": sum(p["summary"]["firecrawl_count"] for p in packages),
        "total_manual": sum(p["summary"]["manual_count"] for p in packages),
        "total_unsupported": sum(
            p["summary"]["unsupported_count"] for p in packages
        ),
        "total_failed": sum(p["summary"]["failed_count"] for p in packages),
        "total_missing": sum(
            p["summary"]["missing_file_count"] for p in packages
        ),
        "complete_package_count": sum(
            p["summary"]["package_complete"] for p in packages
        ),
        "partial_package_count": sum(
            not p["summary"]["package_complete"] for p in packages
        ),
        "packages": entries,
    }


def discover_cases(cases_dir: Path, case_id: str | None) -> list[Path]:
    paths = sorted(
        path
        for path in cases_dir.glob("case_*.json")
        if CASE_FILE_RE.fullmatch(path.name)
    )
    if case_id:
        paths = [path for path in paths if path.stem == case_id]
        if not paths:
            raise FileNotFoundError(f"Case not found: {case_id}")
    return paths


def print_diagnostics(package: dict[str, Any], diagnostics: dict[str, Any]) -> None:
    summary = package["summary"]
    print(f"Case ID: {diagnostics['case_id']}")
    print(f"Expected citations: {diagnostics['expected_citation_count']}")
    print(f"Citation JSON files found: {diagnostics['citation_json_count']}")
    print(
        "Counts: "
        f"success={diagnostics['success_count']}, "
        f"manual={summary['manual_count']}, "
        f"unsupported={summary['unsupported_count']}, "
        f"failed={summary['failed_count']}, "
        f"missing={summary['missing_file_count']}"
    )
    print(
        "Source URLs match: "
        + ("yes" if diagnostics["source_urls_match"] else "no")
    )
    if diagnostics["warnings"]:
        print("Warnings:")
        for warning in diagnostics["warnings"]:
            print(f"  - {warning}")
    else:
        print("Warnings: none")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--cases-dir", type=Path, default=Path("data/cases"))
    parser.add_argument(
        "--citation-root", type=Path, default=Path("data/citation_resources")
    )
    parser.add_argument(
        "--manual-root", type=Path, default=Path("data/manual_inputs")
    )
    parser.add_argument(
        "--output-dir", type=Path, default=Path("data/case_packages")
    )
    parser.add_argument("--case", dest="case_id")
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--overwrite", action="store_true")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        case_paths = discover_cases(args.cases_dir, args.case_id)
    except FileNotFoundError as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 2
    if not case_paths:
        print(f"Error: no Case JSON files found in {args.cases_dir}", file=sys.stderr)
        return 2

    built_at = iso_now()
    packages: list[dict[str, Any]] = []
    writes = skips = 0
    for case_path in case_paths:
        package, diagnostics = build_package(
            case_path,
            args.citation_root,
            args.manual_root,
            built_at=built_at,
        )
        packages.append(package)
        print_diagnostics(package, diagnostics)
        destination = args.output_dir / f"{package['case']['case_id']}.package.json"
        if args.dry_run:
            print(f"Action: dry-run; would write {destination}")
        elif destination.exists() and not args.overwrite:
            skips += 1
            print(f"Action: skipped existing {destination}; use --overwrite")
        else:
            atomic_write_json(destination, package)
            writes += 1
            print(f"Action: wrote {destination}")

    # A single-case build intentionally leaves the all-cases index untouched.
    if not args.case_id:
        index_path = args.output_dir / "packages_index.json"
        index = package_index(packages)
        if args.dry_run:
            print(f"Index: dry-run; would write {index_path}")
        elif index_path.exists() and not args.overwrite:
            print(f"Index: skipped existing {index_path}; use --overwrite")
        else:
            atomic_write_json(index_path, index)
            print(f"Index: wrote {index_path}")

    print(
        f"Processed {len(packages)} case(s): wrote={writes}, "
        f"skipped={skips}, dry_run={args.dry_run}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
