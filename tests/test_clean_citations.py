import json
from pathlib import Path

from scripts.clean_citations import (
    build_clean_case,
    clean_citation,
    discover_packages,
)


RULES = {
    "rules_version": "v1.1",
    "minimum_clean_character_count": 10,
    "maximum_consecutive_blank_lines": 1,
    "generic": {
        "line_patterns": [
            {
                "name": "empty_image_without_url",
                "pattern": r'^\s*!\[[^\]]*\]\(\s*(?:[\"\'][^\"\']*[\"\'])?\s*\)\s*$',
            }
        ],
        "navigation_noise_patterns": [r"^navigation$"],
        "footer_noise_patterns": [r"^END PATTERN$"],
    },
    "sites": {},
}


def citation(
    citation_id: str = "citation_001",
    position: int = 1,
    *,
    status: str = "success",
    capture_method: str = "firecrawl",
    markdown: str = "# Title\n\nBody long enough",
) -> dict:
    return {
        "citation_id": citation_id,
        "position": position,
        "source_url": f"https://example.test/{position}",
        "status": status,
        "capture_method": capture_method,
        "title": "Source title",
        "markdown": markdown,
        "metadata": {},
        "structured": None,
        "error": None,
    }


def package(path: Path, citations: list[object], case_id: str = "case_001") -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps({"case": {"case_id": case_id}, "citations": citations}),
        encoding="utf-8",
    )
    return path


def test_firecrawl_success_preserves_identity_and_provenance(tmp_path: Path):
    source = citation(markdown="# Heading\r\n\r\nBody   \r\n\r\n\r\n![]()")
    package_path = tmp_path / "case_001.package.json"
    result = clean_citation(source, RULES, package_path, "case_001")

    assert result["cleaning_status"] == "cleaned"
    assert result["status"] == "success"
    assert result["capture_method"] == "firecrawl"
    assert result["position"] == 1
    assert result["cleaned"]["markdown"] == "# Heading\n\nBody"
    assert result["provenance"] == {
        "source_package": package_path.as_posix(),
        "case_id": "case_001",
        "citation_id": "citation_001",
    }
    assert "original_markdown" not in result


def test_manual_success_uses_same_cleaning_and_keeps_capture_method(tmp_path: Path):
    source = citation(capture_method="manual", markdown="# Manual\n\nManual body   \n![]()")
    result = clean_citation(source, RULES, tmp_path / "case.package.json", "case_001")

    assert result["cleaning_status"] == "cleaned"
    assert result["capture_method"] == "manual"
    assert result["cleaned"]["markdown"] == "# Manual\n\nManual body"


def test_unsupported_is_passthrough_and_not_cleaned(tmp_path: Path):
    source = citation(
        status="unsupported",
        capture_method="none",
        markdown="# Must not be cleaned\n![]()",
    )
    result = clean_citation(source, RULES, tmp_path / "case.package.json", "case_001")

    assert result["status"] == "unsupported"
    assert result["capture_method"] == "none"
    assert result["cleaning_status"] == "skipped_unsupported"
    assert result["cleaned"]["markdown"] == ""
    assert result["removed_blocks"] == []
    assert result["error"] is None


def test_structural_and_evidence_content_is_preserved(tmp_path: Path):
    markdown = (
        "Lead paragraph before heading\n\n"
        "# Heading\n\n"
        "| ID | Value |\n| --- | --- |\n| 1 | same |\n| 1 | same |\n| 1 | same |\n\n"
        "- repeated item\n- repeated item\n- repeated item\n\n"
        "## FAQ\n\n### Question?\nAnswer.\n\n"
        "END PATTERN\nImportant正文 after end marker\n"
        "12345678901234567890\ntrue\nfalse"
    )
    result = clean_citation(
        citation(markdown=markdown), RULES, tmp_path / "case.package.json", "case_001"
    )
    cleaned = result["cleaned"]["markdown"]

    assert cleaned.startswith("Lead paragraph before heading")
    assert cleaned.count("| 1 | same |") == 3
    assert cleaned.count("- repeated item") == 3
    assert "## FAQ" in cleaned and "### Question?" in cleaned
    assert "Important正文 after end marker" in cleaned
    assert "12345678901234567890" in cleaned
    assert "\ntrue\nfalse" in cleaned
    assert result["quality"]["possible_footer_noise"] is True


def test_order_position_and_count_are_preserved_in_manifest(tmp_path: Path):
    package_path = package(
        tmp_path / "case_001.package.json",
        [
            citation("citation_001", 1),
            citation("citation_002", 2, capture_method="manual"),
            citation("citation_003", 3, status="unsupported", capture_method="none"),
        ],
    )
    results, manifest = build_clean_case(package_path, RULES)

    assert [r["citation_id"] for r in results] == [
        "citation_001",
        "citation_002",
        "citation_003",
    ]
    assert [r["position"] for r in results] == [1, 2, 3]
    assert [r["capture_method"] for r in results] == ["firecrawl", "manual", "none"]
    assert manifest["expected_citation_count"] == 3
    assert manifest["output_citation_count"] == 3
    assert manifest["citation_count_matches"] is True
    assert manifest["cleaned_count"] == 2
    assert manifest["skipped_unsupported_count"] == 1
    assert manifest["cleaning_failed_count"] == 0


def test_one_citation_exception_does_not_stop_case_or_batch(tmp_path: Path):
    first = package(
        tmp_path / "case_001.package.json",
        [citation("citation_001", 1), {"citation_id": "broken"}, citation("citation_003", 3)],
    )
    second = package(
        tmp_path / "case_002.package.json",
        [citation("citation_001", 1)],
        case_id="case_002",
    )

    results, manifest = build_clean_case(first, RULES)
    assert len(results) == 3
    assert results[1]["cleaning_status"] == "failed"
    assert results[2]["cleaning_status"] == "cleaned"
    assert manifest["cleaning_failed_count"] == 1
    assert manifest["output_citation_count"] == 3
    assert discover_packages(tmp_path, None) == [first, second]


def test_case_filter_discovers_only_requested_package(tmp_path: Path):
    first = package(tmp_path / "case_001.package.json", [citation()])
    package(tmp_path / "case_002.package.json", [citation()], case_id="case_002")
    assert discover_packages(tmp_path, "case_001") == [first]
