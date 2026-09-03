import json
from pathlib import Path

from scripts.build_case_packages import (
    atomic_write_json,
    build_package,
    determine_capture_method,
    package_index,
)


def write_json(path: Path, value: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value), encoding="utf-8")


def test_capture_method_priority() -> None:
    assert determine_capture_method(
        {"status": "unsupported", "capture": {"method": "manual"}}
    ) == "manual"
    assert determine_capture_method(
        {
            "status": "success",
            "firecrawl": {"metadata": {"source": "manual"}},
        }
    ) == "manual"
    assert determine_capture_method(
        {"status": "success", "firecrawl": {"markdown": "body"}}
    ) == "firecrawl"
    assert determine_capture_method({"status": "unsupported"}) == "none"
    assert determine_capture_method({"status": "failed"}) == "none"


def test_build_package_preserves_order_manual_unsupported_and_missing(
    tmp_path: Path,
) -> None:
    cases = tmp_path / "data/cases"
    citations = tmp_path / "data/citation_resources"
    manuals = tmp_path / "data/manual_inputs"
    case_path = cases / "case_001.json"
    urls = ["https://one.test", "https://two.test", "https://three.test"]
    write_json(
        case_path,
        {
            "case_id": "case_001",
            "prompt": "prompt",
            "cited_urls": urls,
        },
    )
    write_json(
        citations / "case_001/manifest.json",
        {"case_id": "case_001", "expected_url_count": 3},
    )
    original_markdown = "verbatim\nmarkdown"
    write_json(
        citations / "case_001/citation_001.json",
        {
            "citation_id": "citation_001",
            "source_url": urls[0],
            "status": "success",
            "firecrawl": {
                "title": "One",
                "markdown": original_markdown,
                "metadata": {"x": 1},
            },
            "structured": None,
            "error": None,
        },
    )
    write_json(
        citations / "case_001/citation_002.json",
        {
            "citation_id": "wrong_id",
            "source_url": "https://wrong.test",
            "status": "unsupported",
            "firecrawl": {},
            "error": {"type": "UnsupportedContent", "message": "no body"},
        },
    )
    manuals.mkdir(parents=True)
    manual_body = "# Manual title\n\n> URL: https://two.test\n\n## Content\n\nBody\n"
    (manuals / "case_001_citation_002.md").write_text(
        manual_body, encoding="utf-8"
    )

    package, diagnostics = build_package(
        case_path, citations, manuals, built_at="2026-01-01T00:00:00Z"
    )

    assert [c["source_url"] for c in package["citations"]] == urls
    assert [c["position"] for c in package["citations"]] == [1, 2, 3]
    assert package["citations"][0]["markdown"] == original_markdown
    assert package["citations"][1]["status"] == "success"
    assert package["citations"][1]["capture_method"] == "manual"
    assert package["citations"][1]["markdown"] == manual_body
    assert package["citations"][2]["status"] == "missing"
    assert package["summary"] == {
        "expected_citation_count": 3,
        "available_citation_count": 2,
        "firecrawl_count": 1,
        "manual_count": 1,
        "unsupported_count": 0,
        "failed_count": 0,
        "missing_file_count": 1,
        "package_complete": False,
    }
    assert diagnostics["citation_json_count"] == 2
    assert diagnostics["source_urls_match"] is False
    assert any("source_url mismatch" in w for w in package["warnings"])
    assert any("Citation JSON missing" in w for w in package["warnings"])


def test_unsupported_is_complete_and_not_failed(tmp_path: Path) -> None:
    case_path = tmp_path / "data/cases/case_001.json"
    citation_root = tmp_path / "data/citation_resources"
    write_json(
        case_path,
        {
            "case_id": "case_001",
            "prompt": "p",
            "cited_urls": ["https://unsupported.test"],
        },
    )
    write_json(
        citation_root / "case_001/citation_001.json",
        {
            "citation_id": "citation_001",
            "source_url": "https://unsupported.test",
            "status": "unsupported",
            "firecrawl": {},
            "error": {"type": "UnsupportedContent", "message": "unsupported"},
        },
    )
    package, _ = build_package(
        case_path, citation_root, tmp_path / "manual", built_at="now"
    )
    assert package["summary"]["unsupported_count"] == 1
    assert package["summary"]["failed_count"] == 0
    assert package["summary"]["package_complete"] is True


def test_package_index_and_atomic_write(tmp_path: Path) -> None:
    package = {
        "case": {"case_id": "case_001", "prompt": "p"},
        "summary": {
            "expected_citation_count": 1,
            "available_citation_count": 1,
            "firecrawl_count": 0,
            "manual_count": 1,
            "unsupported_count": 0,
            "failed_count": 0,
            "missing_file_count": 0,
            "package_complete": True,
        },
    }
    index = package_index([package])
    assert index["total_cases"] == 1
    assert index["total_expected_citations"] == 1
    assert index["complete_package_count"] == 1
    destination = tmp_path / "nested/package.json"
    atomic_write_json(destination, index)
    assert json.loads(destination.read_text(encoding="utf-8")) == index
    assert not list(destination.parent.glob("*.tmp"))
