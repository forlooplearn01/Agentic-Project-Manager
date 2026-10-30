"""Hours arithmetic (apps/utils/hours.py): remaining/forecast rules, the due-by-now burn check with partial credit, and
availability. The forloop case uses the synthetic dataset in datasets/hours/forloop, whose expected answers were checked
against the generated files independently of this code."""
import importlib.util
import json
from datetime import date, timedelta
from pathlib import Path

import pytest

from apps.utils.hours import (
    business_days, compute_availability, compute_project_hours, last_workday_offset, task_remaining, week_start, weeks_elapsed,
)

DATA = Path(__file__).resolve().parents[2] / "datasets" / "hours" / "forloop"
KICKOFF = date(2026, 9, 28)
END_OF_WEEK_4 = date(2026, 10, 23)


def _forloop_inputs():
    spec = importlib.util.spec_from_file_location("gen", Path(__file__).resolve().parents[2] / "scripts" / "generate_forloop_hours_dataset.py")
    gen = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(gen)
    tasks = [{"task_id": t[0], "task_text": t[1] or t[2], "owner": t[4], "status": t[8], "estimate_hours": t[7],
              "plan_start_week": t[5], "plan_finish_week": t[6], "hours_still_needed": None} for t in gen.TASKS]
    entries = [{"person": p, "week_start": gen.week_monday(w), "task_id": None if k == "other" else k, "hours": h}
               for p, w, k, h, _ in gen.HOURS]
    return {"budget_hours": gen.BUDGET_HOURS, "kickoff_date": gen.KICKOFF}, tasks, entries


# ---------------------------------------------------------------- weeks and remaining

def test_week_start_is_the_monday():
    assert week_start(date(2026, 10, 2)) == date(2026, 9, 28)  # a Friday
    assert week_start(date(2026, 10, 4)) == date(2026, 9, 28)  # a Sunday
    assert week_start(date(2026, 9, 28)) == date(2026, 9, 28)


def test_weeks_elapsed_counts_working_days_of_the_current_week():
    assert weeks_elapsed(KICKOFF, date(2026, 9, 28)) == pytest.approx(0.2)  # Monday of week 1
    assert weeks_elapsed(KICKOFF, date(2026, 10, 2)) == pytest.approx(1.0)  # Friday of week 1
    assert weeks_elapsed(KICKOFF, date(2026, 10, 4)) == pytest.approx(1.0)  # the weekend after
    assert weeks_elapsed(KICKOFF, END_OF_WEEK_4) == pytest.approx(4.0)
    assert weeks_elapsed(KICKOFF, date(2026, 9, 1)) == 0.0  # before kickoff


@pytest.mark.parametrize("estimate,logged,status,ets,expected", [
    (20, 8, "in_progress", None, 12),
    (20, 26, "in_progress", None, 0),  # over its estimate: nothing left on paper (flagged separately)
    (20, 26, "in_progress", 15, 15),  # someone said how much more is needed
    (20, 8, "completed", None, 0),
    (20, 8, "completed", 15, 0),  # completed wins over a stale "still needed"
    (None, 8, "in_progress", None, None),  # unknowable: never guessed
    (None, 8, "in_progress", 5, 5),
    (20, 0, "not_started", None, 20),
])
def test_task_remaining(estimate, logged, status, ets, expected):
    assert task_remaining(estimate, logged, status, ets) == expected


# ---------------------------------------------------------------- project numbers

def test_forloop_totals_match_the_expected_answers():
    settings, tasks, entries = _forloop_inputs()
    exp = json.loads((DATA / "forloop-hours-expected.json").read_text())["metrics"]
    r = compute_project_hours(settings, tasks, entries, END_OF_WEEK_4)
    assert r["logged_hours"] == exp["actual_hours_total"] == 363
    assert r["hours_left_in_budget"] == exp["hours_left_in_budget"] == 337
    assert r["remaining_hours_on_open_tasks"] == exp["remaining_hours_on_open_estimated_tasks"] == 346
    assert r["forecast_at_completion"] == exp["forecast_at_completion"] == 709
    assert r["budget_minus_forecast"] == exp["budget_minus_forecast"] == -9
    assert r["hours_not_tied_to_a_task"] == exp["hours_not_tied_to_a_task"] == 90
    assert r["sum_of_task_estimates"] == exp["sum_of_task_estimates"] == 612
    assert r["open_tasks_without_estimate"] == exp["open_tasks_without_estimate"]
    assert {o["task"] for o in r["open_tasks_over_their_estimate"]} == {o["task"] for o in exp["open_tasks_over_their_estimate"]}


def test_forloop_burn_check_compares_work_done_with_work_due_by_now():
    """The point of the due-by-now design: 52% of the budget is used but the plan is on schedule, so it must not read 'behind'."""
    settings, tasks, entries = _forloop_inputs()
    b = compute_project_hours(settings, tasks, entries, END_OF_WEEK_4)["burn_check"]
    assert b["budget_used_pct"] == pytest.approx(51.9, abs=0.1)
    assert b["schedule"] == "in_line"
    assert b["work_done_pct"] >= b["work_due_by_now_pct"] - 5
    assert b["cost"] == "over"  # forecast 709 against a 700 budget
    assert b["status"] == "watch"


def test_partial_credit_for_an_open_task_is_capped_below_done():
    settings = {"budget_hours": 100, "kickoff_date": KICKOFF}
    t = {"task_id": "a", "task_text": "A", "owner": "x", "status": "in_progress", "estimate_hours": 10,
         "plan_start_week": 1, "plan_finish_week": 2, "hours_still_needed": None}
    half = compute_project_hours(settings, [t], [{"person": "x", "week_start": KICKOFF, "task_id": "a", "hours": 5}], END_OF_WEEK_4)
    assert half["burn_check"]["work_done_pct"] == pytest.approx(50.0)
    over = compute_project_hours(settings, [t], [{"person": "x", "week_start": KICKOFF, "task_id": "a", "hours": 30}], END_OF_WEEK_4)
    assert over["burn_check"]["work_done_pct"] == pytest.approx(90.0)  # never 100% until marked completed
    done = compute_project_hours(settings, [{**t, "status": "completed"}], [{"person": "x", "week_start": KICKOFF, "task_id": "a", "hours": 30}], END_OF_WEEK_4)
    assert done["burn_check"]["work_done_pct"] == pytest.approx(100.0)


def test_hours_still_needed_replaces_the_zero_left_on_an_overrunning_task():
    settings = {"budget_hours": 100, "kickoff_date": KICKOFF}
    t = {"task_id": "a", "task_text": "A", "owner": "x", "status": "in_progress", "estimate_hours": 20,
         "plan_start_week": 1, "plan_finish_week": 2, "hours_still_needed": None}
    entries = [{"person": "x", "week_start": KICKOFF, "task_id": "a", "hours": 26}]
    without = compute_project_hours(settings, [t], entries, END_OF_WEEK_4)
    assert without["forecast_at_completion"] == 26 and without["open_tasks_over_their_estimate"]
    assert any("probably too low" in n for n in without["notes"])
    with_ets = compute_project_hours(settings, [{**t, "hours_still_needed": 15}], entries, END_OF_WEEK_4)
    assert with_ets["forecast_at_completion"] == 41
    assert with_ets["open_tasks_over_their_estimate"] == []  # someone has accounted for it


def test_missing_budget_and_kickoff_are_reported_not_guessed():
    t = {"task_id": "a", "task_text": "A", "owner": None, "status": "not_started", "estimate_hours": 10,
         "plan_start_week": None, "plan_finish_week": None, "hours_still_needed": None}
    r = compute_project_hours({"budget_hours": None, "kickoff_date": None}, [t], [], END_OF_WEEK_4)
    assert r["hours_left_in_budget"] is None and r["budget_minus_forecast"] is None
    assert r["burn_check"]["status"] == "unknown" and r["burn_check"]["work_due_by_now_pct"] is None
    assert any("No hours budget" in n for n in r["notes"]) and any("kickoff" in n for n in r["notes"])


def test_hours_on_an_inactive_or_unknown_task_still_count_toward_the_project_total():
    settings = {"budget_hours": 100, "kickoff_date": None}
    entries = [{"person": "x", "week_start": KICKOFF, "task_id": "gone", "hours": 7}, {"person": "x", "week_start": KICKOFF, "task_id": None, "hours": 3}]
    r = compute_project_hours(settings, [], entries, END_OF_WEEK_4)
    assert r["logged_hours"] == 10 and r["hours_not_tied_to_a_task"] == 10 and r["hours_left_in_budget"] == 90


# ---------------------------------------------------------------- availability

def _people():
    return [
        {"name": "Ana", "role": "Backend Engineer", "skills": ["python"], "weekly_capacity_hours": 40},
        {"name": "Bo", "role": "QA Engineer", "skills": [], "weekly_capacity_hours": 20},
        {"name": "Cy", "role": "Designer", "skills": [], "weekly_capacity_hours": None},
    ]


def test_availability_subtracts_remaining_hours_spread_to_the_due_date():
    settings = {"kickoff_date": KICKOFF}
    # due Friday of week 5 (2026-10-30); 20 h left, spread over the 5 working days from Mon 26 Oct
    tasks = [{"task_id": "t", "task_text": "Build X", "owner": "Ana", "status": "in_progress", "estimate_hours": 20,
              "plan_finish_week": 5, "hours_still_needed": None, "deadline": None}]
    as_of = date(2026, 10, 26)
    this_week = compute_availability(_people(), tasks, [], settings, date(2026, 10, 26), date(2026, 10, 30), as_of)
    ana = next(p for p in this_week["people"] if p["name"] == "Ana")
    assert ana["capacity_in_window"] == 40 and ana["planned_load_in_window"] == 20 and ana["available_hours"] == 20
    next_week = compute_availability(_people(), tasks, [], settings, date(2026, 11, 2), date(2026, 11, 6), as_of)
    assert next(p for p in next_week["people"] if p["name"] == "Ana")["available_hours"] == 40


def test_availability_lists_unknowns_instead_of_guessing():
    settings = {"kickoff_date": KICKOFF}
    tasks = [{"task_id": "t", "task_text": "No estimate", "owner": "Bo", "status": "in_progress", "estimate_hours": None,
              "plan_finish_week": 5, "hours_still_needed": None, "deadline": None}]
    r = compute_availability(_people(), tasks, [], settings, date(2026, 10, 26), date(2026, 10, 30), date(2026, 10, 26))
    bo = next(p for p in r["people"] if p["name"] == "Bo")
    assert bo["planned_load_in_window"] == 0 and bo["unknown_items"] == [{"task_id": "t", "task": "No estimate", "reason": "no estimate"}]
    cy = next(p for p in r["people"] if p["name"] == "Cy")
    assert cy["available_hours"] is None
    assert r["people"][-1]["name"] == "Cy"  # unknown capacity sorts last
    assert len(r["notes"]) == 2


def test_availability_by_week_planned_actual_and_over():
    settings = {"kickoff_date": KICKOFF}
    # estimate 43 h, 27 h logged (below), so 16 h left, due Friday of week 6 (2026-11-06): 8 working days from Wed 28 Oct = 2 h a day
    tasks = [{"task_id": "t", "task_text": "Build X", "owner": "Bo", "status": "in_progress", "estimate_hours": 43,
              "plan_finish_week": 6, "hours_still_needed": None, "deadline": None}]
    as_of = date(2026, 10, 28)  # Wednesday of week 5
    # Bo (capacity 20) logged 18 h in the week of 19 Oct (week 4, already over) and 9 h so far this week
    entries = [{"person": "Bo", "week_start": date(2026, 10, 19), "task_id": "t", "hours": 18.0},
               {"person": "bo", "week_start": date(2026, 10, 26), "task_id": "t", "hours": 9.0}]
    r = compute_availability(_people(), tasks, entries, settings, date(2026, 10, 19), date(2026, 11, 6), as_of)
    assert r["weeks"] == ["2026-10-19", "2026-10-26", "2026-11-02"]
    bo = next(p for p in r["people"] if p["name"] == "Bo")
    past, current, nxt = bo["weeks"]
    assert past["kind"] == "actual" and past["load"] == 18 and past["capacity"] == 20 and not past["over"]
    # the current week shows the planned 6 h (Wed-Fri); the 9 h logged so far is given separately and not added to it
    assert current["kind"] == "planned" and current["load"] == 6 and current["logged"] == 9 and not current["over"]
    assert nxt["kind"] == "planned" and nxt["load"] == 10 and nxt["capacity"] == 20 and not nxt["over"]
    assert bo["over_weeks"] == 0


def test_availability_flags_an_over_allocated_week_and_summarises():
    settings = {"kickoff_date": KICKOFF}
    tasks = [{"task_id": "t", "task_text": "Big", "owner": "Bo", "status": "in_progress", "estimate_hours": 60,
              "plan_finish_week": 5, "hours_still_needed": None, "deadline": None},
             {"task_id": "u", "task_text": "No date", "owner": "Ana", "status": "in_progress", "estimate_hours": 10,
              "plan_finish_week": None, "hours_still_needed": None, "deadline": None}]
    r = compute_availability(_people(), tasks, [], settings, date(2026, 10, 26), date(2026, 10, 30), date(2026, 10, 26))
    bo = next(p for p in r["people"] if p["name"] == "Bo")
    assert bo["weeks"][0]["load"] == 60 and bo["weeks"][0]["over"] and bo["over_weeks"] == 1 and bo["available_hours"] == -40
    s = r["summary"]
    assert s["over_allocated"] == 1 and s["no_capacity"] == 1 and s["unknown_tasks"] == 1 and s["people"] == 3
    assert s["capacity_in_window"] == 40 + 20 and s["planned_load_in_window"] == 60 and s["available_hours"] == 0


@pytest.mark.parametrize("query,expected", [
    ("backend", ["Ana"]), ("engineering work", ["Ana", "Bo"]), ("QA", ["Bo"]), ("python", ["Ana"]), ("designer", ["Cy"]),
    ("", ["Ana", "Bo", "Cy"]),
])
def test_skill_filter(query, expected):
    r = compute_availability(_people(), [], [], {"kickoff_date": None}, date(2026, 10, 26), date(2026, 10, 30), date(2026, 10, 26), query)
    assert sorted(p["name"] for p in r["people"]) == expected


def test_business_days():
    assert len(business_days(date(2026, 10, 26), date(2026, 11, 1))) == 5


def test_forloop_week5_upload_moves_the_numbers_as_derived_by_hand():
    """Week 5 adds 100 h: 72 h reduce remaining work, 22 h are other work or on a task with no estimate, and 6 h overrun estimates
    (API by 2, QA test plans by 4), so the forecast rises 709 -> 737 and 100 h less budget is left."""
    spec = importlib.util.spec_from_file_location("gen", Path(__file__).resolve().parents[2] / "scripts" / "generate_forloop_hours_dataset.py")
    gen = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(gen)
    settings, tasks, entries = _forloop_inputs()
    entries += [{"person": p, "week_start": gen.week_monday(gen.WEEK5), "task_id": None if k == "other" else k, "hours": h}
                for p, k, h, _ in gen.HOURS_WEEK5]
    r = compute_project_hours(settings, tasks, entries, date(2026, 10, 30))
    after = json.loads((DATA / "forloop-hours-expected.json").read_text())["week5_upload"]
    assert r["logged_hours"] == after["logged_after"] == 463
    assert r["hours_left_in_budget"] == after["hours_left_in_budget_after"] == 237
    assert r["remaining_hours_on_open_tasks"] == 274
    assert r["forecast_at_completion"] == after["forecast_after"] == 737 and r["budget_minus_forecast"] == -37


def test_burn_check_summary_is_one_plain_sentence_with_the_apps_own_status_word():
    settings, tasks, entries = _forloop_inputs()
    b = compute_project_hours(settings, tasks, entries, END_OF_WEEK_4)["burn_check"]
    assert b["summary"].startswith("Watch: ") and "in line with the plan" in b["summary"] and "9.0 h over the budget" in b["summary"]
    none = compute_project_hours({"budget_hours": None, "kickoff_date": None}, [], [], END_OF_WEEK_4)["burn_check"]
    assert none["summary"].startswith("Not enough data")


# ---------------------------------------------------------------- the week can start on another day (0 Monday, 5 Saturday, 6 Sunday)

@pytest.mark.parametrize("start_day,expected", [(0, date(2026, 10, 26)), (6, date(2026, 10, 25)), (5, date(2026, 10, 24))])
def test_week_start_for_each_convention(start_day, expected):
    assert week_start(date(2026, 10, 29), start_day) == expected  # a Thursday
    assert week_start(expected, start_day) == expected  # the first day is its own week start


def test_the_last_working_day_of_a_week_is_always_a_friday():
    for start_day in (0, 5, 6):
        assert (week_start(date(2026, 10, 29), start_day) + timedelta(days=last_workday_offset(start_day))).weekday() == 4


@pytest.mark.parametrize("start_day", [0, 5, 6])
def test_weeks_elapsed_is_the_same_whichever_day_the_week_starts_on(start_day):
    """Working weeks are counted by working days, so the answer must not depend on the convention."""
    assert weeks_elapsed(KICKOFF, END_OF_WEEK_4, start_day) == pytest.approx(4.0)
    assert weeks_elapsed(KICKOFF, date(2026, 10, 2), start_day) == pytest.approx(1.0)
    assert weeks_elapsed(KICKOFF, date(2026, 9, 1), start_day) == 0.0


@pytest.mark.parametrize("start_day", [0, 5, 6])
def test_forloop_numbers_do_not_depend_on_the_week_start(start_day):
    settings, tasks, entries = _forloop_inputs()
    r = compute_project_hours({**settings, "week_start_day": start_day}, tasks, entries, END_OF_WEEK_4)
    assert r["forecast_at_completion"] == 709 and r["burn_check"]["work_due_by_now_pct"] == pytest.approx(44.3, abs=0.1)
    people = [{"name": "Marcus Chen", "role": "Backend Engineer", "skills": [], "weekly_capacity_hours": 40}]
    free = compute_availability(people, tasks, entries, {**settings, "week_start_day": start_day}, date(2026, 10, 29), date(2026, 11, 25), date(2026, 10, 29))
    assert free["people"][0]["available_hours"] == 140  # the planned finish week resolves to the same Friday
