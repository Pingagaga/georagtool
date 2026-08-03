import json
from pathlib import Path

from scripts.clean_citations import clean_case, clean_resource


RULES = {
    "rules_version": "v1.0",
    "minimum_clean_character_count": 20,
    "maximum_removed_ratio_without_review": 0.85,
    "maximum_consecutive_blank_lines": 1,
    "maximum_repeated_line_occurrences": 2,
    "generic": {
        "line_patterns": [
            {
                "name": "boolean",
                "pattern": r"^\s*(?:true|false)\s*$",
                "flags": "IGNORECASE",
            },
            {"name": "identifier", "pattern": r"^\s*\d{12,}\s*$"},
            {"name": "empty_image", "pattern": r"^\s*!\[\]\(\s*\)\s*$"},
        ],
        "end_patterns": [],
        "navigation_noise_patterns": [],
        "footer_noise_patterns": ["support widget"],
    },
    "sites": {
        "support.example.com": {
            "end_patterns": [r"^## Help us improve$"],
        }
    },
}


def resource(markdown: str, status_code: int = 200) -> dict:
    return {
        "case_id": "case_001",
        "citation_id": "citation_001",
        "source_url": "https://support.example.com/article",
        "status": "success",
        "firecrawl": {
            "title": "Article title - Help",
            "markdown": markdown,
            "metadata": {
                "title": "Article title - Help",
                "language": "en",
                "statusCode": status_code,
                "contentType": "text/html",
            },
        },
    }


def test_title_h1_boundary_and_footer_boundary_preserve_body_markdown():
    raw = (
        "[navigation](https://example.com)\n\n# Article title\n\n"
        "Price is $12.50 on 2026-07-30.\n\n"
        "### Related links\n- [Body link](https://example.com/body)\n\n"
        "## Help us improve\nsupport widget"
    )
    result = clean_resource(resource(raw), RULES)
    assert result["cleaned"]["start_marker"] == "# Article title"
    assert result["cleaned"]["end_marker"] == "## Help us improve"
    assert "$12.50" in result["cleaned"]["markdown"]
    assert "### Related links" in result["cleaned"]["markdown"]
    assert "navigation" not in result["cleaned"]["markdown"]
    assert "support widget" not in result["cleaned"]["markdown"]


def test_layer_a_removes_only_standalone_noise_and_limits_repetition():
    raw = (
        "# Article title\nfalse\n123456789012\n![]()\n"
        "true facts remain\n50%\nrepeat\nrepeat\nrepeat\n"
        "Enough body content remains here."
    )
    result = clean_resource(resource(raw), RULES)
    cleaned = result["cleaned"]["markdown"]
    assert "\nfalse\n" not in f"\n{cleaned}\n"
    assert "123456789012" not in cleaned
    assert "![]()" not in cleaned
    assert "true facts remain" in cleaned
    assert "50%" in cleaned
    assert cleaned.count("repeat") == 2


def test_h2_fallback_and_manual_review_for_non_200():
    raw = "navigation\n## Main section\nThis is sufficiently long main content."
    result = clean_resource(resource(raw, status_code=503), RULES)
    assert result["cleaned"]["start_marker"] == "## Main section"
    assert result["quality"]["manual_review_required"] is True


def test_missing_heading_requires_manual_review():
    result = clean_resource(
        resource("Plain content that is long enough to pass the minimum."),
        RULES,
    )
    assert result["cleaned"]["start_marker"] == "START_OF_DOCUMENT"
    assert result["quality"]["manual_review_required"] is True


def test_one_failure_does_not_abort_case(tmp_path: Path):
    input_dir = tmp_path / "case_001"
    output_dir = tmp_path / "cleaned" / "case_001"
    input_dir.mkdir()
    (input_dir / "citation_001.json").write_text(
        json.dumps(resource("# Article title\nValid content long enough.")),
        encoding="utf-8",
    )
    rules_path = tmp_path / "rules.json"
    rules_path.write_text(json.dumps(RULES), encoding="utf-8")

    manifest = clean_case(
        input_dir,
        output_dir,
        rules_path,
        ["citation_001", "citation_002"],
    )
    assert manifest["expected_count"] == 2
    assert manifest["success_count"] == 1
    assert manifest["failed_count"] == 1
    assert (output_dir / "citation_002.clean.json").exists()
