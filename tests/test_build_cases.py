import csv
import json
from pathlib import Path

import pytest

from scripts.build_cases import build_cases


FIELDS = [
    "Country",
    "Keyword",
    "Tags",
    "Volume",
    "Response",
    "Model",
    "Mentions",
    "Cited pages",
    "Found but not cited",
    "Updated",
]


def write_utf16_tsv(path: Path, rows: list[dict[str, str]]) -> None:
    with path.open("w", encoding="utf-16", newline="") as output:
        writer = csv.DictWriter(output, fieldnames=FIELDS, delimiter="\t")
        writer.writeheader()
        writer.writerows(rows)


@pytest.fixture
def built(tmp_path: Path):
    input_path = tmp_path / "ahrefs.csv"
    output_dir = tmp_path / "cases"
    shared_url = "https://example.com/shared?q=keep"
    rows = [
        {
            "Country": "tw",
            "Keyword": "第一個問題",
            "Volume": "1,200",
            "Response": "回答一",
            "Model": "model-a",
            "Cited pages": (
                f"  https://example.com/a  \n\n{shared_url}\n"
                "https://example.com/a\n   "
            ),
            "Found but not cited": " https://example.com/found ",
            "Updated": "2026-07-01",
        },
        {
            "Country": "tw",
            "Keyword": "第二個問題",
            "Volume": "",
            "Response": "回答二",
            "Model": "model-b",
            "Cited pages": shared_url,
            "Found but not cited": "",
            "Updated": "2026-07-02",
        },
        {
            "Country": "us",
            "Keyword": "Third prompt",
            "Cited pages": "",
        },
    ]
    write_utf16_tsv(input_path, rows)
    result = build_cases(input_path, output_dir)
    cases = [
        json.loads((output_dir / f"case_{number:03d}.json").read_text("utf-8"))
        for number in range(1, 4)
    ]
    return result, cases, output_dir, shared_url


def test_reads_utf16_tab_delimited_and_maps_fields(built):
    _, cases, _, _ = built
    assert cases[0]["country"] == "tw"
    assert cases[0]["prompt"] == "第一個問題"
    assert cases[0]["response"] == "回答一"
    assert cases[0]["volume"] == 1200


def test_multiple_urls_remain_in_one_case_and_blanks_are_removed(built):
    _, cases, _, shared_url = built
    assert cases[0]["cited_urls"] == ["https://example.com/a", shared_url]


def test_duplicate_url_is_removed_within_case(built):
    result, _, _, _ = built
    assert result["report"]["duplicate_urls_removed"] == 1


def test_same_url_is_not_removed_across_cases(built):
    _, cases, _, shared_url = built
    assert shared_url in cases[0]["cited_urls"]
    assert cases[1]["cited_urls"] == [shared_url]


def test_empty_cited_pages_becomes_empty_list(built):
    _, cases, _, _ = built
    assert cases[2]["cited_urls"] == []


def test_case_ids_follow_input_row_order(built):
    _, cases, _, _ = built
    assert [case["case_id"] for case in cases] == [
        "case_001",
        "case_002",
        "case_003",
    ]
    assert [case["source_row"] for case in cases] == [1, 2, 3]


def test_index_and_report_are_written(built):
    result, _, output_dir, _ = built
    index = json.loads((output_dir / "cases_index.json").read_text("utf-8"))
    report = json.loads((output_dir / "build_report.json").read_text("utf-8"))
    assert index == result["index"]
    assert report == result["report"]
    assert index["total_cases"] == 3
    assert index["total_cited_urls"] == 3


def test_invalid_url_is_retained_and_reported(tmp_path: Path):
    input_path = tmp_path / "ahrefs.csv"
    output_dir = tmp_path / "cases"
    write_utf16_tsv(
        input_path,
        [{"Keyword": "Prompt", "Cited pages": "not-a-url"}],
    )
    result = build_cases(input_path, output_dir)
    case = json.loads((output_dir / "case_001.json").read_text("utf-8"))
    assert case["cited_urls"] == ["not-a-url"]
    assert result["report"]["invalid_urls"][0]["url"] == "not-a-url"
    assert result["report"]["warnings"]
