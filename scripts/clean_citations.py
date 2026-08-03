"""Clean Firecrawl citation Markdown with deterministic, configurable rules."""

from __future__ import annotations

import argparse
import json
import re
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
    path.write_text(
        json.dumps(value, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )


def markdown_metrics(markdown: str) -> dict[str, int]:
    return {
        "character_count": len(markdown),
        "line_count": len(markdown.splitlines()) if markdown else 0,
        "heading_count": sum(
            1 for line in markdown.splitlines() if HEADING_RE.match(line)
        ),
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
        "hostname": hostname,
        "line_patterns": generic.get("line_patterns", [])
        + site.get("line_patterns", []),
        "end_patterns": generic.get("end_patterns", [])
        + site.get("end_patterns", []),
        "navigation_noise_patterns": generic.get(
            "navigation_noise_patterns", []
        )
        + site.get("navigation_noise_patterns", []),
        "footer_noise_patterns": generic.get("footer_noise_patterns", [])
        + site.get("footer_noise_patterns", []),
    }


def normalized_title(value: str) -> str:
    value = re.sub(r"[*_`~\[\]]", "", value)
    return re.sub(r"[\s\-–—|｜:：]+", "", value).casefold()


def find_start(lines: list[str], metadata_title: str) -> tuple[int, str]:
    headings: list[tuple[int, int, str, str]] = []
    for index, line in enumerate(lines):
        match = HEADING_RE.match(line)
        if match:
            headings.append((index, len(match.group(1)), match.group(2), line.strip()))

    wanted = normalized_title(metadata_title)
    if wanted:
        for index, level, text, marker in headings:
            candidate = normalized_title(text)
            if level == 1 and candidate and (
                candidate in wanted or wanted in candidate
            ):
                return index, marker
    for level in (1, 2):
        for index, heading_level, _text, marker in headings:
            if heading_level == level:
                return index, marker
    return 0, "START_OF_DOCUMENT"


def find_end(
    lines: list[str], start_index: int, patterns: list[str]
) -> tuple[int, str]:
    compiled = [(pattern, re.compile(pattern, re.IGNORECASE)) for pattern in patterns]
    for index in range(start_index, len(lines)):
        for _pattern, regex in compiled:
            if regex.search(lines[index]):
                return index, lines[index].strip()
    return len(lines), "END_OF_DOCUMENT"


def removed_block(
    layer: str,
    rule: str,
    lines: list[str],
    start_line: int,
    end_line: int,
) -> dict[str, Any]:
    text = "\n".join(lines)
    return {
        "layer": layer,
        "rule": rule,
        "start_line": start_line,
        "end_line": end_line,
        "character_count": len(text),
        "preview": text[:200],
    }


def clean_selected_lines(
    lines: list[str],
    line_patterns: list[dict[str, str] | str],
    maximum_repetitions: int,
    maximum_blank_lines: int,
    source_line_offset: int,
) -> tuple[list[str], list[dict[str, Any]]]:
    compiled = [
        (
            spec.get("name", spec["pattern"]) if isinstance(spec, dict) else spec,
            compile_pattern(spec),
        )
        for spec in line_patterns
    ]
    kept: list[str] = []
    removals: list[dict[str, Any]] = []
    occurrence_count: dict[str, int] = {}
    blank_count = 0

    for local_index, line in enumerate(lines):
        source_line = source_line_offset + local_index + 1
        matched_rule = next(
            (name for name, regex in compiled if regex.match(line)), None
        )
        if matched_rule:
            removals.append(
                removed_block(
                    "A", matched_rule, [line], source_line, source_line
                )
            )
            continue

        if not line.strip():
            blank_count += 1
            if blank_count > maximum_blank_lines:
                continue
            kept.append("")
            continue
        blank_count = 0

        key = re.sub(r"\s+", " ", line.strip())
        occurrence_count[key] = occurrence_count.get(key, 0) + 1
        if occurrence_count[key] > maximum_repetitions:
            removals.append(
                removed_block(
                    "A",
                    "over_repeated_line",
                    [line],
                    source_line,
                    source_line,
                )
            )
            continue
        kept.append(line.rstrip())

    while kept and not kept[-1]:
        kept.pop()
    return kept, removals


def contains_pattern(markdown: str, patterns: list[str]) -> bool:
    return any(re.search(pattern, markdown, re.IGNORECASE | re.MULTILINE) for pattern in patterns)


def empty_result(resource: dict[str, Any], rules_version: str) -> dict[str, Any]:
    firecrawl = resource.get("firecrawl")
    if not isinstance(firecrawl, dict):
        firecrawl = {}
    metadata = firecrawl.get("metadata")
    if not isinstance(metadata, dict):
        metadata = {}
    raw = firecrawl.get("markdown", "")
    if not isinstance(raw, str):
        raw = ""
    title = firecrawl.get("title") or metadata.get("title") or ""
    return {
        "case_id": resource.get("case_id", ""),
        "citation_id": resource.get("citation_id", ""),
        "source_url": resource.get("source_url", ""),
        "status": "failed",
        "source": {
            "title": title,
            "language": metadata.get("language", ""),
            "status_code": metadata.get("statusCode"),
            "content_type": metadata.get("contentType", ""),
        },
        "raw_metrics": markdown_metrics(raw),
        "cleaned": {
            "title": "",
            "markdown": "",
            "start_marker": "",
            "end_marker": "",
            "cleaning_method": "rule_based",
            "rules_version": rules_version,
        },
        "clean_metrics": {
            **markdown_metrics(""),
            "removed_character_count": len(raw),
            "removed_ratio": 1.0 if raw else 0.0,
        },
        "quality": {
            "has_title": False,
            "has_main_content": False,
            "minimum_length_passed": False,
            "possible_navigation_noise": False,
            "possible_footer_noise": False,
            "manual_review_required": True,
        },
        "removed_blocks": [],
        "error": None,
    }


def clean_resource(
    resource: dict[str, Any], rules: dict[str, Any]
) -> dict[str, Any]:
    rules_version = rules.get("rules_version", "v1.0")
    result = empty_result(resource, rules_version)
    try:
        if resource.get("status") != "success":
            raise ValueError("Source citation status is not success")
        firecrawl = resource.get("firecrawl")
        if not isinstance(firecrawl, dict):
            raise TypeError("firecrawl must be an object")
        raw = firecrawl.get("markdown")
        if not isinstance(raw, str) or not raw.strip():
            raise ValueError("firecrawl.markdown is empty")
        metadata = firecrawl.get("metadata")
        if not isinstance(metadata, dict):
            metadata = {}

        source_url = str(resource.get("source_url", ""))
        active = combined_site_rules(rules, source_url)
        lines = raw.replace("\r\n", "\n").replace("\r", "\n").split("\n")
        title = str(firecrawl.get("title") or metadata.get("title") or "")
        start_index, start_marker = find_start(lines, title)
        end_index, end_marker = find_end(
            lines, start_index, active["end_patterns"]
        )

        removals: list[dict[str, Any]] = []
        if start_index:
            removals.append(
                removed_block(
                    "B", "before_main_content", lines[:start_index], 1, start_index
                )
            )
        if end_index < len(lines):
            removals.append(
                removed_block(
                    "B",
                    "footer_boundary",
                    lines[end_index:],
                    end_index + 1,
                    len(lines),
                )
            )

        cleaned_lines, line_removals = clean_selected_lines(
            lines[start_index:end_index],
            active["line_patterns"],
            int(rules.get("maximum_repeated_line_occurrences", 2)),
            int(rules.get("maximum_consecutive_blank_lines", 1)),
            start_index,
        )
        removals.extend(line_removals)
        markdown = "\n".join(cleaned_lines).strip()
        raw_metrics = markdown_metrics(raw)
        clean_metrics = markdown_metrics(markdown)
        removed_count = raw_metrics["character_count"] - clean_metrics["character_count"]
        ratio = (
            removed_count / raw_metrics["character_count"]
            if raw_metrics["character_count"]
            else 0.0
        )
        ratio = round(ratio, 6)
        h1_or_h2_found = any(
            (match := HEADING_RE.match(line))
            and len(match.group(1)) in (1, 2)
            for line in cleaned_lines
        )
        minimum_passed = clean_metrics["character_count"] >= int(
            rules.get("minimum_clean_character_count", 500)
        )
        status_code = metadata.get("statusCode")
        manual_review = any(
            (
                not minimum_passed,
                ratio > float(
                    rules.get("maximum_removed_ratio_without_review", 0.85)
                ),
                raw_metrics["heading_count"] > 3
                and clean_metrics["heading_count"] == 0,
                not h1_or_h2_found,
                status_code != 200,
                clean_metrics["character_count"] > raw_metrics["character_count"],
            )
        )
        first_heading = next(
            (
                match.group(2)
                for line in cleaned_lines
                if (match := HEADING_RE.match(line))
            ),
            "",
        )

        result.update(
            {
                "status": "success",
                "raw_metrics": raw_metrics,
                "cleaned": {
                    "title": first_heading or title,
                    "markdown": markdown,
                    "start_marker": start_marker,
                    "end_marker": end_marker,
                    "cleaning_method": "rule_based",
                    "rules_version": rules_version,
                },
                "clean_metrics": {
                    **clean_metrics,
                    "removed_character_count": removed_count,
                    "removed_ratio": ratio,
                },
                "quality": {
                    "has_title": bool((first_heading or title).strip()),
                    "has_main_content": bool(markdown.strip()),
                    "minimum_length_passed": minimum_passed,
                    "possible_navigation_noise": contains_pattern(
                        markdown, active["navigation_noise_patterns"]
                    ),
                    "possible_footer_noise": contains_pattern(
                        markdown, active["footer_noise_patterns"]
                    ),
                    "manual_review_required": manual_review,
                },
                "removed_blocks": removals,
                "error": None,
            }
        )
    except Exception as exc:
        result["error"] = {
            "type": type(exc).__name__,
            "message": str(exc),
        }
    return result


def clean_case(
    input_dir: Path,
    output_dir: Path,
    rules_path: Path,
    citation_ids: list[str],
) -> dict[str, Any]:
    rules = read_json(rules_path)
    output_dir.mkdir(parents=True, exist_ok=True)
    manifest: dict[str, Any] = {
        "case_id": input_dir.name,
        "rules_version": rules.get("rules_version", "v1.0"),
        "expected_count": len(citation_ids),
        "success_count": 0,
        "failed_count": 0,
        "manual_review_count": 0,
        "citations": [],
    }

    for citation_id in citation_ids:
        try:
            resource = read_json(input_dir / f"{citation_id}.json")
            result = clean_resource(resource, rules)
        except Exception as exc:
            resource = {
                "case_id": input_dir.name,
                "citation_id": citation_id,
                "source_url": "",
            }
            result = empty_result(
                resource, rules.get("rules_version", "v1.0")
            )
            result["error"] = {
                "type": type(exc).__name__,
                "message": str(exc),
            }
        write_json(output_dir / f"{citation_id}.clean.json", result)
        succeeded = result["status"] == "success"
        manifest["success_count" if succeeded else "failed_count"] += 1
        review = result["quality"]["manual_review_required"]
        manifest["manual_review_count"] += int(review)
        manifest["citations"].append(
            {
                "citation_id": citation_id,
                "status": result["status"],
                "removed_ratio": result["clean_metrics"]["removed_ratio"],
                "manual_review_required": review,
            }
        )
        write_json(output_dir / "cleaning_manifest.json", manifest)
    return manifest


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--input-dir",
        type=Path,
        default=Path("data/citation_resources/case_001"),
    )
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=Path("data/cleaned_citations/case_001"),
    )
    parser.add_argument(
        "--rules",
        type=Path,
        default=Path("config/cleaning_rules.json"),
    )
    parser.add_argument(
        "--citation-ids",
        nargs="+",
        default=["citation_001", "citation_002"],
    )
    args = parser.parse_args()
    manifest = clean_case(
        args.input_dir, args.output_dir, args.rules, args.citation_ids
    )
    print(
        f"{manifest['case_id']}: {manifest['success_count']} succeeded, "
        f"{manifest['failed_count']} failed, "
        f"{manifest['manual_review_count']} require manual review."
    )
    return 0 if manifest["failed_count"] == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
