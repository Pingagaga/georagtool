"""Clean citations from Case Packages with deterministic, conservative rules."""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path
from typing import Any
from urllib.parse import urlparse


HEADING_RE = re.compile(r"^\s{0,3}(#{1,6})\s+(.+?)\s*$")
LINK_RE = re.compile(r"(?<!!)\[[^\]]+\]\([^)]+\)")


def read_json(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8-sig"))
    if not isinstance(value, dict):
        raise TypeError(f"{path} must contain a JSON object")
    return value


def write_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def project_path(path: Path) -> str:
    return path.as_posix()


def markdown_metrics(markdown: str) -> dict[str, int]:
    return {
        "character_count": len(markdown),
        "line_count": len(markdown.splitlines()) if markdown else 0,
        "heading_count": sum(1 for line in markdown.splitlines() if HEADING_RE.match(line)),
        "link_count": len(LINK_RE.findall(markdown)),
    }


def compile_pattern(spec: dict[str, str] | str) -> re.Pattern[str]:
    if isinstance(spec, str):
        return re.compile(spec, re.IGNORECASE)
    flags = re.IGNORECASE if "IGNORECASE" in spec.get("flags", "") else 0
    return re.compile(spec["pattern"], flags)


def combined_site_rules(rules: dict[str, Any], source_url: str) -> dict[str, Any]:
    generic = rules.get("generic", {})
    hostname = (urlparse(source_url).hostname or "").lower()
    site = rules.get("sites", {}).get(hostname, {})
    return {
        "line_patterns": generic.get("line_patterns", []) + site.get("line_patterns", []),
        "navigation_noise_patterns": generic.get("navigation_noise_patterns", [])
        + site.get("navigation_noise_patterns", []),
        "footer_noise_patterns": generic.get("footer_noise_patterns", [])
        + site.get("footer_noise_patterns", []),
    }


def removed_line(rule: str, line: str, source_line: int) -> dict[str, Any]:
    return {
        "layer": "line",
        "rule": rule,
        "start_line": source_line,
        "end_line": source_line,
        "character_count": len(line),
        "preview": line[:200],
    }


def normalize_markdown(
    markdown: str,
    line_patterns: list[dict[str, str] | str],
    maximum_blank_lines: int,
) -> tuple[str, list[dict[str, Any]]]:
    """Apply only low-risk, line-local normalization to Markdown."""
    compiled = [
        (spec.get("name", spec["pattern"]) if isinstance(spec, dict) else spec, compile_pattern(spec))
        for spec in line_patterns
    ]
    lines = markdown.replace("\r\n", "\n").replace("\r", "\n").split("\n")
    kept: list[str] = []
    removals: list[dict[str, Any]] = []
    blank_count = 0
    for source_line, original_line in enumerate(lines, start=1):
        line = original_line.rstrip()
        matched_rule = next((name for name, regex in compiled if regex.fullmatch(line)), None)
        if matched_rule:
            removals.append(removed_line(matched_rule, line, source_line))
            continue
        if not line:
            blank_count += 1
            if blank_count > maximum_blank_lines:
                continue
            kept.append("")
            continue
        blank_count = 0
        kept.append(line)
    while kept and not kept[-1]:
        kept.pop()
    return "\n".join(kept), removals


def contains_pattern(markdown: str, patterns: list[str]) -> bool:
    return any(re.search(pattern, markdown, re.IGNORECASE | re.MULTILINE) for pattern in patterns)


def first_heading(markdown: str) -> str:
    for line in markdown.splitlines():
        match = HEADING_RE.match(line)
        if match:
            return match.group(2).strip()
    return ""


def base_result(
    citation: dict[str, Any], rules_version: str, package_path: Path, case_id: str
) -> dict[str, Any]:
    required = ("citation_id", "position", "source_url", "title", "status", "capture_method")
    missing = [field for field in required if field not in citation]
    if missing:
        raise ValueError(f"Citation is missing required fields: {', '.join(missing)}")
    return {
        "schema_version": "1.0",
        "case_id": case_id,
        "citation_id": citation["citation_id"],
        "position": citation["position"],
        "source_url": citation["source_url"],
        "title": citation["title"],
        "status": citation["status"],
        "capture_method": citation["capture_method"],
        "cleaning_status": "failed",
        "provenance": {
            "source_package": project_path(package_path),
            "case_id": case_id,
            "citation_id": citation["citation_id"],
        },
        "cleaned": {
            "title": citation["title"],
            "markdown": "",
            "cleaning_method": "rule_based",
            "rules_version": rules_version,
        },
        "raw_metrics": markdown_metrics(""),
        "clean_metrics": {
            **markdown_metrics(""),
            "removed_character_count": 0,
            "removed_ratio": 0.0,
        },
        "quality": {
            "has_title": bool(str(citation["title"]).strip()),
            "has_main_content": False,
            "minimum_length_passed": False,
            "possible_navigation_noise": False,
            "possible_footer_noise": False,
            "manual_review_required": False,
        },
        "removed_blocks": [],
        "error": None,
    }


def clean_citation(
    citation: dict[str, Any], rules: dict[str, Any], package_path: Path, case_id: str
) -> dict[str, Any]:
    rules_version = str(rules.get("rules_version", "unknown"))
    result = base_result(citation, rules_version, package_path, case_id)
    if citation["status"] == "unsupported":
        result["cleaning_status"] = "skipped_unsupported"
        result["cleaned"]["cleaning_method"] = "not_applicable"
        return result
    try:
        if citation["status"] != "success":
            raise ValueError(f"Citation status is not cleanable: {citation['status']!r}")
        raw = citation.get("markdown")
        if not isinstance(raw, str) or not raw.strip():
            raise ValueError("Citation markdown is empty")
        active = combined_site_rules(rules, str(citation["source_url"]))
        cleaned, removals = normalize_markdown(
            raw,
            active["line_patterns"],
            int(rules.get("maximum_consecutive_blank_lines", 1)),
        )
        raw_metrics = markdown_metrics(raw)
        clean_metrics = markdown_metrics(cleaned)
        removed_count = max(0, raw_metrics["character_count"] - clean_metrics["character_count"])
        removed_ratio = (
            round(removed_count / raw_metrics["character_count"], 6)
            if raw_metrics["character_count"]
            else 0.0
        )
        title = first_heading(cleaned) or str(citation["title"])
        minimum_passed = clean_metrics["character_count"] >= int(
            rules.get("minimum_clean_character_count", 500)
        )
        navigation_noise = contains_pattern(cleaned, active["navigation_noise_patterns"])
        footer_noise = contains_pattern(cleaned, active["footer_noise_patterns"])
        result.update(
            {
                "cleaning_status": "cleaned",
                "cleaned": {
                    "title": title,
                    "markdown": cleaned,
                    "cleaning_method": "rule_based",
                    "rules_version": rules_version,
                },
                "raw_metrics": raw_metrics,
                "clean_metrics": {
                    **clean_metrics,
                    "removed_character_count": removed_count,
                    "removed_ratio": removed_ratio,
                },
                "quality": {
                    "has_title": bool(title.strip()),
                    "has_main_content": bool(cleaned.strip()),
                    "minimum_length_passed": minimum_passed,
                    "possible_navigation_noise": navigation_noise,
                    "possible_footer_noise": footer_noise,
                    "manual_review_required": not minimum_passed or navigation_noise or footer_noise,
                },
                "removed_blocks": removals,
                "error": None,
            }
        )
    except Exception as exc:
        result["cleaning_status"] = "failed"
        result["quality"]["manual_review_required"] = True
        result["error"] = {"type": type(exc).__name__, "message": str(exc)}
    return result


def failed_result(
    citation: dict[str, Any], rules: dict[str, Any], package_path: Path, case_id: str, exc: Exception
) -> dict[str, Any]:
    safe = {
        "citation_id": citation.get("citation_id", ""),
        "position": citation.get("position"),
        "source_url": citation.get("source_url", ""),
        "title": citation.get("title", ""),
        "status": citation.get("status", ""),
        "capture_method": citation.get("capture_method", ""),
    }
    result = base_result(safe, str(rules.get("rules_version", "unknown")), package_path, case_id)
    result["quality"]["manual_review_required"] = True
    result["error"] = {"type": type(exc).__name__, "message": str(exc)}
    return result


def build_clean_case(
    package_path: Path, rules: dict[str, Any]
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    package = read_json(package_path)
    case = package.get("case")
    citations = package.get("citations")
    if not isinstance(case, dict) or not isinstance(case.get("case_id"), str):
        raise ValueError(f"{package_path} has no valid case.case_id")
    if not isinstance(citations, list):
        raise TypeError(f"{package_path} citations must be a list")
    case_id = case["case_id"]
    results: list[dict[str, Any]] = []
    for value in citations:
        citation = value if isinstance(value, dict) else {}
        try:
            result = clean_citation(citation, rules, package_path, case_id)
        except Exception as exc:
            result = failed_result(citation, rules, package_path, case_id, exc)
        results.append(result)
    entries = [
        {
            "citation_id": result["citation_id"],
            "position": result["position"],
            "status": result["status"],
            "capture_method": result["capture_method"],
            "cleaning_status": result["cleaning_status"],
            "manual_review_required": result["quality"]["manual_review_required"],
            "output_file": f"{result['citation_id']}.clean.json",
        }
        for result in results
    ]
    manifest = {
        "schema_version": "1.0",
        "case_id": case_id,
        "source_package": project_path(package_path),
        "rules_version": str(rules.get("rules_version", "unknown")),
        "expected_citation_count": len(citations),
        "output_citation_count": len(results),
        "cleaned_count": sum(r["cleaning_status"] == "cleaned" for r in results),
        "skipped_unsupported_count": sum(
            r["cleaning_status"] == "skipped_unsupported" for r in results
        ),
        "cleaning_failed_count": sum(r["cleaning_status"] == "failed" for r in results),
        "manual_review_count": sum(r["quality"]["manual_review_required"] for r in results),
        "citation_count_matches": len(citations) == len(results),
        "citations": entries,
    }
    return results, manifest


def discover_packages(input_dir: Path, case_id: str | None) -> list[Path]:
    paths = sorted(input_dir.glob("case_*.package.json"))
    if case_id:
        paths = [path for path in paths if path.name == f"{case_id}.package.json"]
    return paths


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input-dir", type=Path, default=Path("data/case_packages"))
    parser.add_argument("--output-root", type=Path, default=Path("data/cleaned_citations"))
    parser.add_argument("--rules", type=Path, default=Path("config/cleaning_rules.json"))
    parser.add_argument("--case", dest="case_id")
    parser.add_argument("--dry-run", action="store_true")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    packages = discover_packages(args.input_dir, args.case_id)
    if not packages:
        print("Error: no matching Case Packages found.", file=sys.stderr)
        return 2
    rules = read_json(args.rules)
    package_failures = 0
    total_citations = total_cleaned = total_unsupported = total_failed = 0
    for package_path in packages:
        try:
            results, manifest = build_clean_case(package_path, rules)
        except Exception as exc:
            package_failures += 1
            print(f"{package_path.name}: package failed ({exc})", file=sys.stderr)
            continue
        total_citations += manifest["output_citation_count"]
        total_cleaned += manifest["cleaned_count"]
        total_unsupported += manifest["skipped_unsupported_count"]
        total_failed += manifest["cleaning_failed_count"]
        if not args.dry_run:
            output_dir = args.output_root / manifest["case_id"]
            for result in results:
                write_json(output_dir / f"{result['citation_id']}.clean.json", result)
            write_json(output_dir / "cleaning_manifest.json", manifest)
        action = "dry-run" if args.dry_run else "wrote"
        print(
            f"{manifest['case_id']}: citations={manifest['output_citation_count']}, "
            f"cleaned={manifest['cleaned_count']}, "
            f"unsupported={manifest['skipped_unsupported_count']}, "
            f"failed={manifest['cleaning_failed_count']}, action={action}"
        )
    print(
        f"Processed {len(packages)} package(s): citations={total_citations}, "
        f"cleaned={total_cleaned}, unsupported={total_unsupported}, "
        f"failed={total_failed}, package_failures={package_failures}, dry_run={args.dry_run}"
    )
    return 0 if not package_failures and total_failed == 0 and total_citations else 1


if __name__ == "__main__":
    raise SystemExit(main())
