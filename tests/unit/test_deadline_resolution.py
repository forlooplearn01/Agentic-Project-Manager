"""resolve_deadline turns a deadline phrase into an ISO date ONLY when the text really contains or implies one.

Regression for a bug found while documenting the pipeline: any text with a digit fell through to a fuzzy date parse that
read the digit as a day of the current month, so "Week 7" became the 7th, and "end of Week 2" matched the "end of week"
rule and became the coming Friday - "end of Week 2" and "end of Week 8" ended up with the same date. Positions in a plan
and durations now keep their original wording."""
from datetime import date

import pytest

from apps.utils.deadline import resolve_deadline

TODAY = date(2026, 10, 27)  # a Tuesday


@pytest.mark.parametrize("phrase", [
    "end of Week 2", "by end of Week 8", "Week 2", "Week 7", "wk 3", "end of Q2", "Q2 2026", "Q3",
    "Sprint 4", "Phase 2", "Day 5", "Milestone 3",
])
def test_positions_in_a_plan_keep_their_wording(phrase):
    assert resolve_deadline(phrase, TODAY) == phrase


@pytest.mark.parametrize("phrase", ["in 3 days", "in 2 weeks", "next 2 weeks", "within 10 business days", "may need 3 days"])
def test_durations_and_stray_digits_are_not_dates(phrase):
    assert resolve_deadline(phrase, TODAY) == phrase


@pytest.mark.parametrize("phrase", ["ASAP", "EOD", "mid-May", "next week", "April 2026", "by 15th"])
def test_text_with_no_resolvable_date_is_returned_unchanged(phrase):
    assert resolve_deadline(phrase, TODAY) == phrase


def test_two_different_weeks_never_collapse_to_the_same_date():
    assert resolve_deadline("end of Week 2", TODAY) != resolve_deadline("end of Week 8", TODAY)


@pytest.mark.parametrize("phrase,expected", [
    ("April 14", "2026-04-14"), ("14 April", "2026-04-14"), ("14th of April", "2026-04-14"),
    ("May 12, 2026", "2026-05-12"), ("2026-05-12", "2026-05-12"), ("5/12/2026", "2026-05-12"),
    ("May 12, 2026 EOD", "2026-05-12"),
])
def test_explicit_calendar_dates_still_resolve(phrase, expected):
    assert resolve_deadline(phrase, TODAY) == expected


@pytest.mark.parametrize("phrase,expected", [
    ("Week 6 (May 12, 2026)", "2026-05-12"),
    ("Week 6, May 12, 2026", "2026-05-12"),            # the "6" must not leak into the date
    ("Sprint 3 ends April 14, 2026", "2026-04-14"),
    ("next Monday, May 4", "2026-05-04"),              # an explicit date wins over a relative word
])
def test_a_real_date_next_to_a_plan_reference_wins(phrase, expected):
    assert resolve_deadline(phrase, TODAY) == expected


@pytest.mark.parametrize("phrase,expected", [
    ("today", "2026-10-27"), ("tomorrow EOD", "2026-10-28"), ("next Monday", "2026-11-02"), ("Friday", "2026-10-30"),
    ("end of week", "2026-10-30"), ("EOW", "2026-10-30"), ("EOM", "2026-10-31"),
])
def test_relative_phrases_still_resolve_against_today(phrase, expected):
    assert resolve_deadline(phrase, TODAY) == expected


def test_empty_and_non_string_input_pass_through():
    assert resolve_deadline("", TODAY) == ""
    assert resolve_deadline(None, TODAY) is None
