"""Build one JSON case per row from an Ahrefs UTF-16 TSV export."""

from __future__ import annotations

import argparse
import csv
import json
import logging
import re
from pathlib import Path
from typing import Any


LOGGER = logging.getLogger("build_cases")
URL_PREFIXES = ("http://", "https://")
CASE_FILE_RE = re.compile(r"case_\d+\.json$")


def parse_volume(value: str | None) -> int | float | None:
    """Return a numeric volume when possible, otherwise null."""
    if value is None:
        return None
    cleaned = value.strip().replace(",", "")
    if not cleaned:
        return None
    try:
        number = float(cleaned)
    except ValueError:
        return None
    return int(number) if number.is_integer() else number


def parse_urls(
    value: str | None,
    *,
    source_row: int,
    field: str,
    report: dict[str, Any],
) -> list[str]:
    """Split, trim, de-duplicate and validate URLs within a single field."""
    urls: list[str] = []
    seen: set[str] = set()

    for raw_url in (value or "").splitlines():
        url = raw_url.strip()
        if not url:
            continue
        if url in seen:
            report["duplicate_urls_removed"] += 1
            continue
        seen.add(url)
        urls.append(url)
        if not url.startswith(URL_PREFIXES):
            issue = {"source_row": source_row, "field": field, "url": url}
            report["invalid_urls"].append(issue)
            warning = (
                f"Row {source_row}: invalid URL in {field!r}; "
                f"expected http:// or https://: {url!r}"
            )
            report["warnings"].append(warning)
            LOGGER.warning(warning)

    return urls


def read_ahrefs_rows(input_path: Path) -> list[dict[str, str]]:
    """Read an Ahrefs export using its required UTF-16 TSV format."""
    with input_path.open("r", encoding="utf-16", newline="") as input_file:
        return list(csv.DictReader(input_file, delimiter="\t"))


def build_cases(input_path: Path, output_dir: Path) -> dict[str, Any]:
    rows = read_ahrefs_rows(input_path)
    output_dir.mkdir(parents=True, exist_ok=True)

    report: dict[str, Any] = {
        "input_rows": len(rows),
        "output_cases": 0,
        "total_cited_urls": 0,
        "duplicate_urls_removed": 0,
        "invalid_urls": [],
        "empty_prompt_rows": [],
        "warnings": [],
    }
    cases: list[dict[str, Any]] = []

    for source_row, row in enumerate(rows, start=1):
        prompt = (row.get("Keyword") or "").strip()
        if not prompt:
            report["empty_prompt_rows"].append(source_row)
            warning = f"Row {source_row}: empty prompt; row skipped"
            report["warnings"].append(warning)
            LOGGER.warning(warning)
            continue

        case_id = f"case_{len(cases) + 1:03d}"
        cited_urls = parse_urls(
            row.get("Cited pages"),
            source_row=source_row,
            field="Cited pages",
            report=report,
        )
        found_urls = parse_urls(
            row.get("Found but not cited"),
            source_row=source_row,
            field="Found but not cited",
            report=report,
        )
        case = {
            "case_id": case_id,
            "country": (row.get("Country") or "").strip(),
            "prompt": prompt,
            "response": (row.get("Response") or "").strip(),
            "model": (row.get("Model") or "").strip(),
            "volume": parse_volume(row.get("Volume")),
            "fanout_queries": [],
            "cited_urls": cited_urls,
            "found_but_not_cited_urls": found_urls,
            "updated": (row.get("Updated") or "").strip(),
            "source_row": source_row,
        }
        cases.append(case)

    case_ids = [case["case_id"] for case in cases]
    if len(case_ids) != len(set(case_ids)):
        raise ValueError("Duplicate case_id generated")
    if any(not isinstance(case["cited_urls"], list) for case in cases):
        raise TypeError("Every cited_urls value must be a list")

    # Remove only stale generated case files; reports and unrelated files remain.
    for path in output_dir.iterdir():
        if path.is_file() and CASE_FILE_RE.fullmatch(path.name):
            path.unlink()

    index_cases = []
    for case in cases:
        filename = f"{case['case_id']}.json"
        (output_dir / filename).write_text(
            json.dumps(case, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        index_cases.append(
            {
                "case_id": case["case_id"],
                "prompt": case["prompt"],
                "cited_url_count": len(case["cited_urls"]),
                "file": filename,
            }
        )

    total_cited_urls = sum(len(case["cited_urls"]) for case in cases)
    index = {
        "total_cases": len(cases),
        "total_cited_urls": total_cited_urls,
        "cases": index_cases,
    }
    report["output_cases"] = len(cases)
    report["total_cited_urls"] = total_cited_urls

    (output_dir / "cases_index.json").write_text(
        json.dumps(index, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    (output_dir / "build_report.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    return {"index": index, "report": report}


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--input",
        type=Path,
        default=Path("data/raw/ahrefs.csv"),
        help="Ahrefs UTF-16 TSV input",
    )
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=Path("data/cases"),
        help="Directory for case JSON files",
    )
    args = parser.parse_args()
    logging.basicConfig(level=logging.WARNING, format="%(levelname)s: %(message)s")
    result = build_cases(args.input, args.output_dir)
    index = result["index"]
    print(
        f"Built {index['total_cases']} cases with "
        f"{index['total_cited_urls']} cited URLs."
    )


if __name__ == "__main__":
    main()
