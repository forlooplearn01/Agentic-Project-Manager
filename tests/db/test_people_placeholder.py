"""Regression tests for auto-populating project_people from extracted task owners:
(1) ensure_person_placeholder must never overwrite an existing email/role once a real one
is on file, even if called again later with the same name; (2) resolve_person_email must
treat a placeholder (email=NULL) row as UNRESOLVED, not resolved - without this, Meeting
Coordinator's resolve_attendees would try to invite a null address to a real calendar
event, which is exactly the bug this guards against."""
from apps.db.db import ensure_person_placeholder, resolve_person_email, upsert_project_person, list_project_people


def test_placeholder_creates_a_name_only_row(test_project):
    ensure_person_placeholder(test_project, "Marcus Chen")

    people = list_project_people(test_project)
    assert len(people) == 1
    assert people[0]["name"] == "Marcus Chen"
    assert people[0]["email"] is None


def test_placeholder_row_does_not_resolve_to_an_email(test_project):
    ensure_person_placeholder(test_project, "Marcus Chen")

    assert resolve_person_email(test_project, "Marcus Chen") is None


def test_placeholder_never_overwrites_a_real_email(test_project):
    upsert_project_person(test_project, "Nirmal Babu", "nirmal@example.com", "Delivery Manager")

    ensure_person_placeholder(test_project, "Nirmal Babu")

    people = list_project_people(test_project)
    assert len(people) == 1
    assert people[0]["email"] == "nirmal@example.com"
    assert people[0]["role"] == "Delivery Manager"


def test_placeholder_is_case_insensitive_and_does_not_duplicate(test_project):
    ensure_person_placeholder(test_project, "Ram Anandkumar")
    ensure_person_placeholder(test_project, "ram anandkumar")
    ensure_person_placeholder(test_project, "RAM ANANDKUMAR")

    people = list_project_people(test_project)
    assert len(people) == 1


def test_resolving_after_placeholder_makes_it_resolvable(test_project):
    ensure_person_placeholder(test_project, "Priya Nair")
    assert resolve_person_email(test_project, "Priya Nair") is None

    upsert_project_person(test_project, "Priya Nair", "priya@example.com")

    resolved = resolve_person_email(test_project, "Priya Nair")
    assert resolved is not None
    assert resolved["email"] == "priya@example.com"
