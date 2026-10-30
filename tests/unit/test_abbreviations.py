"""expand_abbreviations: real abbreviations expand, ordinary words that happen to match an entry do not.

Regression for a bug found while documenting the pipeline: matching was case-insensitive for every entry, and the map
contains WAS and BE, so "the test was completed" became "the test WebSphere Application Server completed" and
"will be reviewed" became "will Backend reviewed"."""
import pytest

from apps.utils.abbreviations import ABBREVIATION_MAP, CASE_SENSITIVE_ABBREVIATIONS, expand_abbreviations


@pytest.mark.parametrize("text", [
    "Confirm the test was completed",
    "Ensure the report will be reviewed",
    "Be sure to call Dr. Smith",
    "Weigh the 5 lb part",
    "Order the ide fish for the sla party",
])
def test_ordinary_words_are_left_alone(text):
    assert expand_abbreviations(text) == text


@pytest.mark.parametrize("text,expected", [
    ("Patch the WAS server", "Patch the WebSphere Application Server server"),
    ("Get the BE API and FE ready", "Get the Backend API and Frontend ready"),
    ("Run the HA test", "Run the High Availability test"),
])
def test_the_upper_case_form_of_a_risky_entry_still_expands(text, expected):
    assert expand_abbreviations(text) == expected


@pytest.mark.parametrize("text,expected", [
    ("Deploy k8s poc and mvp", "Deploy Kubernetes Proof of Concept and Minimum Viable Product"),
    ("Fix the pr comments and the ci build", "Fix the Pull Request comments and the Continuous Integration build"),
    ("Draft QA plan for UAT", "Draft Quality Assurance plan for User Acceptance Testing"),
    ("Back up the db", "Back up the Database"),
])
def test_other_entries_stay_case_insensitive(text, expected):
    assert expand_abbreviations(text) == expected


def test_every_case_sensitive_entry_exists_in_the_map():
    assert CASE_SENSITIVE_ABBREVIATIONS <= {k.upper() for k in ABBREVIATION_MAP}
