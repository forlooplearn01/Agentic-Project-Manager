"""Numbered ASSUMPTIONS / DEPENDENCIES lists, checked against the real input files."""
import glob
from collections import Counter
from pathlib import Path

import pytest

from apps.utils.sow_sections import parse_raid_sections, title_and_description

DATASETS = Path(__file__).resolve().parents[2] / "datasets"


def _counts(path):
    return dict(Counter(i["item_type"] for i in parse_raid_sections(Path(path).read_text())))


def test_the_synthetic_sow_yields_nine_assumptions_and_six_dependencies():
    items = parse_raid_sections((DATASETS / "simulation-ai-x-vmware" / "sow.txt").read_text())
    assert dict(Counter(i["item_type"] for i in items)) == {"assumption": 9, "dependency": 6}
    assert [i["number"] for i in items if i["item_type"] == "dependency"] == [f"8.{n}" for n in range(1, 7)]
    assert items[0]["text"].startswith("NovaTech will provide QuantumEdge with valid VMware Aria")


def test_a_clean_simple_numbered_list_is_read_and_stops_at_the_next_heading():
    items = parse_raid_sections((DATASETS / "inputs" / "input-sow-3.txt").read_text())
    assert [i["number"] for i in items] == [str(n) for n in range(1, 10)]  # NOT the OUT OF SCOPE list that follows
    assert {i["item_type"] for i in items} == {"assumption"}


@pytest.mark.parametrize("name", ["input-sow-1.txt", "input-sow-2.txt", "input-sow-4.txt", "input-sow-forloop.txt"])
def test_messy_or_missing_lists_yield_nothing_instead_of_a_guess(name):
    # -1 is bulleted, -2 is a PDF extraction with the numbers split from their text, -4 and forloop have no such list
    assert _counts(DATASETS / "inputs" / name) == {}


def test_edge_cases():
    assert parse_raid_sections("") == []
    assert parse_raid_sections("Assumptions\n1. Only one item\n") == []  # a lone line is not a list
    two = parse_raid_sections("ASSUMPTIONS\n\n1. First\n\n2. Second\n\nNEXT SECTION\n3. Not included\n")
    assert [i["text"] for i in two] == ["First", "Second"]
    both = parse_raid_sections("Assumptions\n1. A1\n2. A2\nDependencies\n1. D1\n2. D2\n")
    assert [(i["item_type"], i["number"]) for i in both] == [("assumption", "1"), ("assumption", "2"), ("dependency", "1"), ("dependency", "2")]


def test_a_title_case_heading_also_ends_the_list_and_short_caps_items_are_still_items():
    text = "Assumptions\n1. API\n2. SOW\nOut of Scope\n1. Not an assumption\n2. Neither\n"
    assert [i["text"] for i in parse_raid_sections(text)] == ["API", "SOW"]


def test_title_and_description():
    assert title_and_description("Short sentence.") == ("Short sentence.", None)
    title, description = title_and_description("First sentence here. A second sentence follows with more detail.")
    assert title == "First sentence here." and description.endswith("with more detail.")
    long_one = "x" * 200
    title, description = title_and_description(long_one)
    assert len(title) == 110 and title.endswith("…") and description == long_one
