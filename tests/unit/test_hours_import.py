"""Plan / timesheet file reading (apps/utils/hours_import.py): layout detection, header recognition, people-name matching and
the task matcher's deterministic parts. The synthetic forloop files in datasets/hours/forloop are the ground truth."""
import io
import json
from datetime import date
from pathlib import Path

import pytest
from openpyxl import Workbook

from apps.utils import hours_import as hi

DATA = Path(__file__).resolve().parents[2] / "datasets" / "hours" / "forloop"
KICKOFF = date(2026, 9, 28)


def _read(name):
    return (DATA / name).read_bytes()


def _csv(text):
    return text.encode()


# ---------------------------------------------------------------- the three timesheet layouts

def test_flat_csv_layout():
    r = hi.parse_timesheet("forloop-timesheet-flat.csv", _read("forloop-timesheet-flat.csv"), KICKOFF)
    assert r["layout"] == "flat" and r["warnings"] == []
    assert len(r["rows"]) == 43 and sum(x["hours"] for x in r["rows"]) == 258  # the TOTAL footer row is ignored
    assert r["mapping"] == {"person": "Employee", "task": "Activity", "hours": "Hours", "date": "Week Ending"}
    first = r["rows"][0]
    assert first["week_start"] == date(2026, 9, 28) and first["source_ref"].endswith("row 2")  # a Friday maps to its Monday
    assert any(x["hours"] == 13.5 for x in r["rows"])  # decimals survive


def test_weekly_grid_layout_sums_the_days_per_person_per_week():
    r = hi.parse_timesheet("forloop-timesheet-grid.xlsx", _read("forloop-timesheet-grid.xlsx"), KICKOFF)
    assert r["layout"] == "grid" and sum(x["hours"] for x in r["rows"]) == 105
    by_person = {}
    for x in r["rows"]:
        by_person[x["person_raw"]] = by_person.get(x["person_raw"], 0) + x["hours"]
    assert by_person == {"Priya Patel": 58, "David Osei": 20, "Ram Anandkumar": 27}  # person comes from the sheet


def test_person_by_week_matrix_has_no_task_detail():
    r = hi.parse_timesheet("forloop-timesheet-summary.xlsx", _read("forloop-timesheet-summary.xlsx"), KICKOFF)
    assert r["layout"] == "matrix" and sum(x["hours"] for x in r["rows"]) == 363
    assert all(x["label"] is None for x in r["rows"])  # 'Wk 1 (28 Sep)' headers resolved to 2026-09-28 via the kickoff date
    assert {x["week_start"] for x in r["rows"]} == {date(2026, 9, 28), date(2026, 10, 5), date(2026, 10, 12), date(2026, 10, 19)}
    assert any("no tasks" in w for w in r["warnings"])


def test_flat_and_grid_together_equal_the_summary():
    flat = hi.parse_timesheet("a.csv", _read("forloop-timesheet-flat.csv"), KICKOFF)["rows"]
    grid = hi.parse_timesheet("b.xlsx", _read("forloop-timesheet-grid.xlsx"), KICKOFF)["rows"]
    assert sum(r["hours"] for r in flat + grid) == 363


# ---------------------------------------------------------------- headers that look different

def test_other_header_words_and_hour_formats():
    csv_text = "Resource,Date,Ticket,Time Spent\nAnn Lee,2026-10-05,PROJ-1 Login page,4h\nAnn Lee,2026-10-06,PROJ-2 API,2.5 hrs\nTotals,,,6.5\n"
    r = hi.parse_timesheet("t.csv", _csv(csv_text), KICKOFF)
    assert [(x["label"], x["hours"]) for x in r["rows"]] == [("PROJ-1 Login page", 4.0), ("PROJ-2 API", 2.5)]


def test_bad_rows_are_skipped_with_a_warning_not_silently():
    csv_text = "Employee,Date,Task,Hours\nAnn Lee,2026-10-05,A,eight\nAnn Lee,not a date,B,3\nAnn Lee,2026-10-05,C,-2\nAnn Lee,2026-10-06,D,5\n"
    r = hi.parse_timesheet("t.csv", _csv(csv_text), KICKOFF)
    assert [x["label"] for x in r["rows"]] == ["D"]
    assert len(r["warnings"]) == 3


def test_unrecognised_headers_use_the_proposed_mapping_only_when_it_is_complete():
    csv_text = "who,when_,thing,qty\nAnn Lee,2026-10-05,A,4\n"
    with pytest.raises(hi.HoursImportError):
        hi.parse_timesheet("t.csv", _csv(csv_text), KICKOFF)
    r = hi.parse_timesheet("t.csv", _csv(csv_text), KICKOFF, llm_mapper=lambda header, rows: {"person": 0, "date": 1, "task": 2, "hours": 3})
    assert r["rows"][0]["hours"] == 4.0 and any("proposed" in w for w in r["warnings"])
    bad = hi.parse_timesheet if False else None
    with pytest.raises(hi.HoursImportError):  # an incomplete proposal is ignored, not trusted
        hi.parse_timesheet("t.csv", _csv(csv_text), KICKOFF, llm_mapper=lambda header, rows: {"person": 0})
    assert bad is None


def test_user_supplied_mapping_overrides_detection():
    csv_text = "who,when_,thing,qty\nAnn Lee,2026-10-05,A,4\n"
    r = hi.parse_timesheet("t.csv", _csv(csv_text), KICKOFF, mapping={"person": "who", "date": "when_", "task": "thing", "hours": "qty"})
    assert r["rows"][0]["person_raw"] == "Ann Lee" and r["rows"][0]["hours"] == 4.0


def test_matrix_with_plain_week_numbers_needs_the_kickoff_date():
    wb = Workbook()
    ws = wb.active
    ws.append(["Name", "Week 1", "Week 2"])
    ws.append(["Ann Lee", 30, 25])
    buf = io.BytesIO()
    wb.save(buf)
    r = hi.parse_timesheet("m.xlsx", buf.getvalue(), KICKOFF)
    assert {(x["week_start"], x["hours"]) for x in r["rows"]} == {(date(2026, 9, 28), 30.0), (date(2026, 10, 5), 25.0)}


def test_unsupported_and_empty_files_give_a_clear_error():
    with pytest.raises(hi.HoursImportError, match="xlsx"):
        hi.parse_timesheet("old.xls", b"x", KICKOFF)
    with pytest.raises(hi.HoursImportError, match="No hours"):
        hi.parse_timesheet("e.csv", _csv("Employee,Date,Hours\n"), KICKOFF)
    with pytest.raises(hi.HoursImportError, match="5 MB"):
        hi.read_sheets("big.csv", b"x" * (6 * 1024 * 1024))


# ---------------------------------------------------------------- plan

def test_plan_file():
    p = hi.parse_plan("forloop-plan.xlsx", _read("forloop-plan.xlsx"))
    assert len(p["rows"]) == 22 and sum(r["estimate"] for r in p["rows"]) == 612  # the Total row is not a task
    assert p["budget_hours"] == 700 and sum(p["role_budgets"].values()) == 700 and len(p["role_budgets"]) == 8
    assert p["kickoff_date"] == KICKOFF
    row = next(r for r in p["rows"] if r["label"].startswith("Technical discovery"))
    assert (row["owner"], row["start_wk"], row["finish_wk"], row["estimate"]) == ("Elena Ruiz", 1, 2, 16)


def test_plan_without_an_estimate_column_is_rejected_clearly():
    with pytest.raises(hi.HoursImportError, match="estimated-hours"):
        hi.parse_plan("p.csv", _csv("Task,Owner\nDo a thing,Ann\n"))


# ---------------------------------------------------------------- people names

NAMES = ["Elena Ruiz", "Marcus Chen", "Marcus Cole", "Rucheek", "Ram Anandkumar"]


@pytest.mark.parametrize("raw,expected", [
    ("Elena Ruiz", "Elena Ruiz"), ("ruiz, elena", "Elena Ruiz"), ("E. Ruiz", "Elena Ruiz"), ("Rucheek", "Rucheek"),
    ("Anandkumar", "Ram Anandkumar"), ("M. Chen", "Marcus Chen"),
    ("Marcus", None),  # two Marcuses: ambiguous, never guessed
    ("Nobody Here", None), ("", None),
])
def test_resolve_person(raw, expected):
    assert hi.resolve_person(raw, NAMES) == expected


# ---------------------------------------------------------------- task matching (the parts that need no model)

TASKS = [
    {"task_id": "1", "task_text": "Perform load testing on the backend API", "owner": "David Osei"},
    {"task_id": "2", "task_text": "Build secure Extract Transform Load pipeline to ingest daily sales and inventory data", "owner": "Marcus Chen"},
]


def test_exact_text_matches_even_through_abbreviations():
    out = hi.propose_task_matches([{"label": "perform load testing on the backend api!", "person": None},
                                   {"label": "Build secure ETL pipeline to ingest daily sales and inventory data", "person": "Marcus Chen"}],
                                  TASKS, use_llm=False)
    assert [o["match"] for o in out] == ["exact", "exact"] and [o["task_id"] for o in out] == ["1", "2"]


def test_overhead_labels_are_other_and_blank_labels_stay_none():
    out = hi.propose_task_matches([{"label": "Team standup", "person": "Ann"}, {"label": "", "person": "Ann"}], TASKS, use_llm=False)
    assert out[0]["match"] == "other" and out[0]["task_id"] is None
    assert out[1]["match"] == "none"


def test_llm_choice_is_only_accepted_from_the_offered_candidates(monkeypatch):
    items = [{"label": "something vague", "person": "Marcus Chen"}]
    calls = {}

    def fake_complete(messages, **kw):
        calls["prompt"] = messages[0]["content"]

        class R:
            choices = [type("C", (), {"message": type("M", (), {"content": json.dumps({"matches": [{"i": 0, "candidate": 2}]})})()})()]
        return R()

    monkeypatch.setattr("apps.llm.complete", fake_complete)
    out = hi.propose_task_matches(items, TASKS, use_llm=True)
    assert out[0]["match"] == "llm" and "owner: Marcus Chen" in calls["prompt"]

    def out_of_range(messages, **kw):
        class R:
            choices = [type("C", (), {"message": type("M", (), {"content": json.dumps({"matches": [{"i": 0, "candidate": 9}]})})()})()]
        return R()

    monkeypatch.setattr("apps.llm.complete", out_of_range)
    assert hi.propose_task_matches(items, TASKS, use_llm=True)[0]["match"] in ("none", "suggested")  # an invalid pick is ignored


def test_a_failed_llm_call_falls_back_to_the_embedding_result(monkeypatch):
    def boom(*a, **k):
        raise RuntimeError("down")

    monkeypatch.setattr("apps.llm.complete", boom)
    out = hi.propose_task_matches([{"label": "vague thing", "person": "X"}], TASKS, use_llm=True)
    assert out[0]["match"] in ("none", "suggested", "other")


@pytest.mark.parametrize("start_day,flat_first_week,grid_first_week", [(0, date(2026, 9, 28), date(2026, 9, 28)), (6, date(2026, 9, 27), date(2026, 9, 27)), (5, date(2026, 9, 26), date(2026, 9, 26))])
def test_dates_are_filed_under_the_projects_week(start_day, flat_first_week, grid_first_week):
    flat = hi.parse_timesheet("f.csv", _read("forloop-timesheet-flat.csv"), KICKOFF, week_start_day=start_day)["rows"]
    assert min(r["week_start"] for r in flat) == flat_first_week  # week ending Fri 2 Oct belongs to the week containing it
    grid = hi.parse_timesheet("g.xlsx", _read("forloop-timesheet-grid.xlsx"), KICKOFF, week_start_day=start_day)["rows"]
    assert min(r["week_start"] for r in grid) == grid_first_week  # "Week commencing 28 Sep" falls in the same week
    assert sum(r["hours"] for r in flat) == 258 and sum(r["hours"] for r in grid) == 105  # nothing is lost whatever the convention
    matrix = hi.parse_timesheet("s.xlsx", _read("forloop-timesheet-summary.xlsx"), KICKOFF, week_start_day=start_day)["rows"]
    assert sum(r["hours"] for r in matrix) == 363 and len({r["week_start"] for r in matrix}) == 4
