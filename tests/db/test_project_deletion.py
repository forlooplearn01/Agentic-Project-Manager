"""Regression test: delete_project() must remove a project's tasks but deliberately leave
project_people untouched (see delete_project's own docstring for the reasoning) - the
demo workflow depends on delete-and-rerun not requiring people to be re-added by hand."""
from apps.db.db import delete_project, insert_task, upsert_project_person, list_project_people


def test_tasks_are_deleted_but_people_survive(test_project):
    insert_task({"task_text": "Doomed task", "normalized_task_text": "doomed task"}, test_project)
    upsert_project_person(test_project, "Nirmal Babu", "nirmal@example.com", "Delivery Manager")

    deleted_count = delete_project(test_project)

    assert deleted_count == 1
    people = list_project_people(test_project)
    assert len(people) == 1
    assert people[0]["name"] == "Nirmal Babu"


def test_deleting_a_project_with_no_tasks_returns_zero(test_project):
    # test_project was never given any tasks - a no-op delete, not an error.
    assert delete_project(test_project) == 0
