"""Possible-duplicate handling: the match gate flags the cases a person should look at, format
persists the flag, and merge / not-duplicate resolve it (merge moves history and dependencies,
nothing is hard-deleted, every decision is recorded)."""
import pytest

from apps.db.db import (
    add_task_dependency,
    dismiss_possible_duplicate,
    fetch_project_tasks_with_dependencies,
    get_connection,
    insert_event,
    insert_task,
    merge_tasks,
    set_possible_duplicate,
)
from apps.nodes.format import store_tasks
from apps.nodes.match import cosine_sim, task_match_node
from apps.utils.embeddings import get_embedding
from tests.conftest import make_final_json_response


def _seed(project_id, text, owner=None, deadline=None, jira=None):
    task_id = insert_task(
        {"task_text": text, "normalized_task_text": text.lower(), "owner": owner, "deadline": deadline}, project_id
    )
    if jira:
        conn = get_connection()
        cur = conn.cursor()
        cur.execute("UPDATE tasks SET jira_issue_key = %s WHERE task_id = %s", (jira, task_id))
        conn.commit()
        cur.close()
        conn.close()
    return task_id


def _row(task_id):
    conn = get_connection()
    cur = conn.cursor()
    cur.execute(
        "SELECT is_active, owner, jira_issue_key, possible_duplicate_of, possible_duplicate_reason "
        "FROM tasks WHERE task_id = %s", (task_id,),
    )
    row = cur.fetchone()
    cur.close()
    conn.close()
    return {"is_active": row[0], "owner": row[1], "jira": row[2], "dup_of": row[3], "reason": row[4]}


def _decisions(project_id):
    conn = get_connection()
    cur = conn.cursor()
    cur.execute("SELECT decision, reason FROM duplicate_decisions WHERE project_id = %s", (project_id,))
    rows = cur.fetchall()
    cur.close()
    conn.close()
    return rows


def _gate(monkeypatch, decision):
    def fake_complete(**kwargs):
        return make_final_json_response({"verdicts": [{"id": 0, "decision": decision}]})
    monkeypatch.setattr("apps.nodes.match.complete", fake_complete)


def _match(project_id, text):
    return task_match_node({"normalized_events": [{"task_text": text}], "project_id": project_id})["matched_events"][0]


# ---------------------------------------------------------------- flagging in match / format

def test_unsure_verdict_flags_the_closest_existing_task(test_project, monkeypatch):
    existing = _seed(test_project, "Write unit tests for CSV module")
    _gate(monkeypatch, "unsure")
    event = _match(test_project, "Complete CSV module coding and unit tests")
    assert event["possible_duplicate_of"] == existing
    assert event["possible_duplicate_reason"] == "llm_unsure"


def test_rejection_of_a_very_similar_candidate_is_flagged(test_project, monkeypatch):
    existing = _seed(test_project, "Create Slack channel for API questions")
    mention = "Create Slack channel for API questions today"
    assert cosine_sim(get_embedding(mention), get_embedding("Create Slack channel for API questions")) >= 0.85
    _gate(monkeypatch, "different")
    event = _match(test_project, mention)
    assert event["is_matched"] is False
    assert event["possible_duplicate_of"] == existing
    assert event["possible_duplicate_reason"] == "rejected_high_similarity"


def test_ordinary_rejection_is_not_flagged(test_project, monkeypatch):
    _seed(test_project, "Write unit tests for CSV module")
    mention = "Send CSV module test case document to Marcus"
    score = cosine_sim(get_embedding(mention), get_embedding("Write unit tests for CSV module"))
    assert 0.65 <= score < 0.85  # offered to the gate, but not a strong enough signal to flag
    _gate(monkeypatch, "different")
    event = _match(test_project, mention)
    assert event["is_matched"] is False
    assert "possible_duplicate_of" not in event


def test_store_tasks_persists_the_flag_and_the_board_exposes_it(test_project):
    existing = _seed(test_project, "Write unit tests for CSV module")
    event = {
        "task_text": "Complete CSV module coding and unit tests", "event_type": "new_task", "is_matched": False,
        "llm_adjudicated": True, "possible_duplicate_of": existing, "possible_duplicate_reason": "llm_unsure",
    }
    store_tasks({"events_to_create": [event], "project_id": test_project, "planned_tasks": []})
    board = {t["task_text"]: t for t in fetch_project_tasks_with_dependencies(test_project)}
    flagged = board["Complete CSV module coding and unit tests"]
    assert flagged["possible_duplicate_of"] == {"task_id": existing, "task_text": "Write unit tests for CSV module"}
    assert board["Write unit tests for CSV module"]["possible_duplicate_of"] is None


# ---------------------------------------------------------------- merge

def test_merge_moves_history_and_dependencies_and_soft_deletes_the_source(test_project):
    target = _seed(test_project, "Write unit tests for CSV module")
    source = _seed(test_project, "Complete CSV module coding and unit tests", owner="Jordan Mills", jira="KAN-9")
    upstream = _seed(test_project, "Set up CI")
    downstream = _seed(test_project, "Release CSV module")
    add_task_dependency(source, upstream)
    add_task_dependency(downstream, source)
    add_task_dependency(source, target)  # would become a self-loop after the merge
    insert_event({"matched_task_id": source, "event_type": "status_change", "task_text": "x", "match_method": "created"})
    set_possible_duplicate(source, target, "llm_unsure")

    result = merge_tasks(source, target)

    assert result["filled_fields"] == {"owner": "Jordan Mills", "jira_issue_key": "KAN-9"}
    assert _row(source)["is_active"] is False and _row(source)["dup_of"] is None
    assert _row(target)["owner"] == "Jordan Mills" and _row(target)["jira"] == "KAN-9"

    conn = get_connection()
    cur = conn.cursor()
    cur.execute("SELECT count(*) FROM task_events WHERE task_id = %s", (target,))
    assert cur.fetchone()[0] == 1
    cur.execute("SELECT task_id, depends_on_task_id FROM task_dependencies WHERE task_id IN (%s, %s, %s)",
                (source, target, downstream))
    edges = {(str(a), str(b)) for a, b in cur.fetchall()}
    cur.close()
    conn.close()
    assert edges == {(target, upstream), (downstream, target)}  # re-pointed, self-loop dropped, none on source
    assert _decisions(test_project) == [("merged", "llm_unsure")]


def test_merge_does_not_overwrite_the_targets_own_fields(test_project):
    target = _seed(test_project, "Write unit tests for CSV module", owner="Marcus Webb", jira="KAN-1")
    source = _seed(test_project, "Complete CSV module coding and unit tests", owner="Jordan Mills", jira="KAN-2")
    result = merge_tasks(source, target)
    assert result["filled_fields"] == {}
    assert result["source_jira_issue_key_not_transferred"] == "KAN-2"
    assert _row(target)["owner"] == "Marcus Webb" and _row(target)["jira"] == "KAN-1"


def test_merge_rejects_invalid_requests(test_project):
    a = _seed(test_project, "Task A")
    other_project = f"{test_project}-other"
    b = _seed(other_project, "Task B")
    try:
        with pytest.raises(ValueError):
            merge_tasks(a, a)
        with pytest.raises(ValueError):
            merge_tasks(a, b)  # different projects
        with pytest.raises(LookupError):
            merge_tasks(a, "00000000-0000-0000-0000-000000000000")
    finally:
        conn = get_connection()
        cur = conn.cursor()
        cur.execute("DELETE FROM tasks WHERE project_id = %s", (other_project,))
        conn.commit()
        cur.close()
        conn.close()


# ---------------------------------------------------------------- keep separate

def test_not_duplicate_clears_the_flag_and_records_the_decision(test_project):
    existing = _seed(test_project, "Write unit tests for CSV module")
    flagged = _seed(test_project, "Complete CSV module coding and unit tests")
    set_possible_duplicate(flagged, existing, "rejected_high_similarity")

    dismiss_possible_duplicate(flagged)

    assert _row(flagged)["dup_of"] is None and _row(flagged)["is_active"] is True
    assert _decisions(test_project) == [("not_duplicate", "rejected_high_similarity")]
    with pytest.raises(ValueError):
        dismiss_possible_duplicate(flagged)  # no longer flagged


def test_a_flag_pointing_at_a_merged_away_task_disappears_from_the_board(test_project):
    keeper = _seed(test_project, "Keep me")
    gone = _seed(test_project, "Duplicate of keep me")
    flagged = _seed(test_project, "Another possible duplicate")
    set_possible_duplicate(flagged, gone, "llm_unsure")
    merge_tasks(gone, keeper)
    board = {t["task_text"]: t for t in fetch_project_tasks_with_dependencies(test_project)}
    assert board["Another possible duplicate"]["possible_duplicate_of"] is None
    assert "Duplicate of keep me" not in board
