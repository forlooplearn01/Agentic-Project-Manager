"""The numbered-DELIVERABLES-section parser, checked against the real input files: the synthetic SOW has such a
section (11 items); the real-style SOWs do not and must produce nothing (no invented deliverables)."""
import glob
from pathlib import Path

import pytest

from apps.utils.deliverables import normalize_owners, number_sort_key, parse_deliverables_section

DATASETS = Path(__file__).resolve().parents[2] / "datasets"


def test_the_synthetic_sow_yields_its_eleven_deliverables_with_owners_and_deadlines():
    items = parse_deliverables_section((DATASETS / "simulation-ai-x-vmware" / "sow.txt").read_text())
    assert [d["number"] for d in items] == [f"4.{n}" for n in range(1, 12)]
    first, multi = items[0], items[3]
    assert first == {"number": "4.1", "title": "Data Source Integration Module (codebase + unit tests)",
                     "owner": "Jordan Mills", "deadline": "Week 2 (April 14, 2026)"}
    assert multi["owner"] == "Jordan Mills, Priya Nair"  # two owners, role descriptions stripped
    assert all(d["owner"] and d["deadline"] for d in items)


@pytest.mark.parametrize("path", sorted(glob.glob(str(DATASETS / "inputs" / "input-sow-*.txt"))))
def test_real_style_sows_without_such_a_section_yield_nothing(path):
    assert parse_deliverables_section(Path(path).read_text()) == []


def test_it_needs_a_deliverables_heading_and_at_least_two_numbered_items():
    assert parse_deliverables_section("5. Timeline\n5.1 Something\n5.2 Another\n") == []  # no such heading
    assert parse_deliverables_section("4. DELIVERABLES\n\n4.1 Only one item\n") == []
    two = parse_deliverables_section("4. Deliverables\n4.1 First\n4.2 Second\n")
    assert [d["title"] for d in two] == ["First", "Second"]
    assert two[0]["owner"] is None and two[0]["deadline"] is None  # missing fields stay empty, never invented


def test_the_section_ends_at_the_next_top_level_heading():
    text = "4. DELIVERABLES\n4.1 A\n   Owner: X\n4.2 B\n5. PROJECT TIMELINE\n5.1 Not a deliverable\n"
    assert [d["number"] for d in parse_deliverables_section(text)] == ["4.1", "4.2"]


def test_owner_and_number_helpers():
    assert normalize_owners("Marcus Webb (Business Analyst, QuantumEdge) + Priya Nair (VMware Engineer)") == "Marcus Webb, Priya Nair"
    assert normalize_owners("A and B") == "A, B"
    assert sorted(["4.10", "4.2", "4.1", None, "x"], key=number_sort_key) == ["4.1", "4.2", "4.10", None, "x"]
