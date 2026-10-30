"""RAID log and RACI matrix: storage rules, filters, implied-Responsible from task owners, warnings,
and the HTTP statuses. Registers are manual-first; 'suggested' items are the hook for later AI proposals."""
import pytest
from fastapi.testclient import TestClient

from apps.api import app
from apps.db.db import (
    create_raid_item,
    ensure_registers_schema,
    get_connection,
    promote_risk_to_issue,
    delete_project,
    delete_raid_item,
    fetch_raci_matrix,
    insert_task,
    list_raid_items,
    set_task_raci,
    update_raid_item,
    upsert_project_person,
)

client = TestClient(app)


def _task(project_id, text, owner=None):
    return insert_task({"task_text": text, "normalized_task_text": text.lower(), "owner": owner}, project_id)


def _row(matrix, task_id):
    return next(t for t in matrix["rows"] if t["subject_id"] == str(task_id))


# ------------------------------------------------------------------ RAID

def test_create_filter_update_delete(test_project):
    risk = create_raid_item(test_project, "risk", "Whitelist approval may slip", probability="high", impact="medium")
    create_raid_item(test_project, "assumption", "Dev credentials arrive by Friday")
    create_raid_item(test_project, "issue", "VPN access blocked", status="mitigating")

    assert [i["item_type"] for i in list_raid_items(test_project)] == ["risk", "issue", "assumption"]
    assert [i["title"] for i in list_raid_items(test_project, item_type="issue")] == ["VPN access blocked"]
    assert [i["title"] for i in list_raid_items(test_project, status="mitigating")] == ["VPN access blocked"]

    updated = update_raid_item(str(risk["item_id"]), {"status": "mitigating", "owner": "Priya Nair"})
    assert updated["status"] == "mitigating" and updated["owner"] == "Priya Nair"
    assert updated["updated_at"] > risk["updated_at"]

    assert delete_raid_item(str(risk["item_id"])) is True
    assert delete_raid_item(str(risk["item_id"])) is False
    assert len(list_raid_items(test_project)) == 2


def test_suggested_items_sort_first_and_default_to_manual_confirmed(test_project):
    manual = create_raid_item(test_project, "risk", "Manual risk")
    assert manual["review_state"] == "confirmed" and manual["origin"] == "manual"
    create_raid_item(test_project, "risk", "AI-proposed risk", review_state="suggested", origin="agent")
    assert [i["title"] for i in list_raid_items(test_project)] == ["AI-proposed risk", "Manual risk"]
    assert [i["title"] for i in list_raid_items(test_project, review_state="suggested")] == ["AI-proposed risk"]


def test_invalid_values_are_rejected(test_project):
    with pytest.raises(ValueError):
        create_raid_item(test_project, "opportunity", "not a RAID type")
    with pytest.raises(ValueError):
        create_raid_item(test_project, "risk", "   ")
    item = create_raid_item(test_project, "risk", "ok")
    with pytest.raises(ValueError):
        update_raid_item(str(item["item_id"]), {"status": "winning"})
    with pytest.raises(ValueError):
        update_raid_item(str(item["item_id"]), {"status": "materialized"})  # dropped: promote the risk to an issue
    with pytest.raises(ValueError):
        update_raid_item(str(item["item_id"]), {"project_id": "other"})  # not an editable field
    assert update_raid_item("00000000-0000-0000-0000-000000000000", {"status": "closed"}) is None


def test_deleting_a_project_removes_its_raid_items(test_project):
    _task(test_project, "Anything")  # delete_project reports 0 deleted tasks as "no such project"
    create_raid_item(test_project, "risk", "Goes away with the project")
    delete_project(test_project)
    assert list_raid_items(test_project) == []


def test_promoting_a_risk_creates_an_issue_and_closes_the_risk(test_project):
    risk = create_raid_item(test_project, "risk", "Whitelist approval may slip", description="Client IT is slow",
                            owner="Priya Nair", probability="high", impact="high")
    result = promote_risk_to_issue(str(risk["item_id"]))

    issue, closed = result["issue"], result["risk"]
    assert issue["item_type"] == "issue" and issue["status"] == "open" and issue["review_state"] == "confirmed"
    assert (issue["title"], issue["description"], issue["owner"], issue["impact"]) == (
        "Whitelist approval may slip", "Client IT is slow", "Priya Nair", "high")
    assert issue["probability"] is None  # an issue already happened, so it has no probability
    assert closed["status"] == "closed" and closed["promoted_to"] == issue["item_id"]
    assert {i["item_type"]: i["status"] for i in list_raid_items(test_project)} == {"risk": "closed", "issue": "open"}


def test_promotion_rules(test_project):
    risk = create_raid_item(test_project, "risk", "A risk")
    promote_risk_to_issue(str(risk["item_id"]))
    with pytest.raises(ValueError):
        promote_risk_to_issue(str(risk["item_id"]))  # already promoted
    assumption = create_raid_item(test_project, "assumption", "An assumption")
    with pytest.raises(ValueError):
        promote_risk_to_issue(str(assumption["item_id"]))  # only risks happen
    suggested = create_raid_item(test_project, "risk", "Unreviewed", review_state="suggested", origin="agent")
    with pytest.raises(ValueError):
        promote_risk_to_issue(str(suggested["item_id"]))  # accept the suggestion first
    with pytest.raises(LookupError):
        promote_risk_to_issue("00000000-0000-0000-0000-000000000000")


def test_the_old_materialized_status_is_migrated_to_closed(test_project):
    """Tables created before 'materialized' was dropped still carry the old CHECK; startup must convert such
    rows and tighten the constraint. Re-creates that old state, then runs the real migration."""
    risk = create_raid_item(test_project, "risk", "Old row")
    conn = get_connection()
    cur = conn.cursor()
    cur.execute("ALTER TABLE raid_items DROP CONSTRAINT IF EXISTS raid_items_status_check")
    cur.execute("ALTER TABLE raid_items ADD CONSTRAINT raid_items_status_check "
                "CHECK (status IN ('open', 'mitigating', 'closed', 'materialized'))")
    cur.execute("UPDATE raid_items SET status = 'materialized' WHERE item_id = %s", (str(risk["item_id"]),))
    conn.commit()

    ensure_registers_schema()

    cur.execute("SELECT status FROM raid_items WHERE item_id = %s", (str(risk["item_id"]),))
    assert cur.fetchone()[0] == "closed"
    with pytest.raises(Exception):  # the tightened constraint now refuses it
        cur.execute("UPDATE raid_items SET status = 'materialized' WHERE item_id = %s", (str(risk["item_id"]),))
    conn.rollback()
    cur.close()
    conn.close()
    ensure_registers_schema()  # idempotent: a second run changes nothing


# ------------------------------------------------------------------ RACI

def test_task_owner_is_an_implied_responsible_until_someone_assigns_explicitly(test_project):
    t = _task(test_project, "Build import module", owner="Jordan Mills")
    upsert_project_person(test_project, "Jordan Mills", "j@example.com")
    row = _row(fetch_raci_matrix(test_project), t)
    assert row["assignments"] == [{"person": "Jordan Mills", "roles": ["R"], "implied": True}]
    assert row["warnings"] == []  # untouched rows are not flagged

    set_task_raci(t, "Priya Nair", ["R"])  # an explicit R now exists -> the owner is no longer implied
    row = _row(fetch_raci_matrix(test_project), t)
    assert [(a["person"], a["implied"]) for a in row["assignments"]] == [("Priya Nair", False)]


def test_an_explicit_role_replaces_the_implied_guess_for_that_person(test_project):
    t = _task(test_project, "Joint task", owner="Jordan Mills, Priya Nair")
    set_task_raci(t, "Jordan Mills", ["C"])  # Jordan explicitly Consulted: he must not ALSO be guessed as R
    row = _row(fetch_raci_matrix(test_project), t)
    assert [(a["person"], a["roles"], a["implied"]) for a in row["assignments"]] == [
        ("Jordan Mills", ["C"], False), ("Priya Nair", ["R"], True)]


def test_multiple_owners_are_split(test_project):
    t = _task(test_project, "Joint task", owner="Jordan Mills, Priya Nair and Marcus Webb")
    people = [a["person"] for a in _row(fetch_raci_matrix(test_project), t)["assignments"]]
    assert people == ["Jordan Mills", "Priya Nair", "Marcus Webb"]


def test_set_roles_replaces_and_clears(test_project):
    t = _task(test_project, "Review plan")
    assert set_task_raci(t, "Marcus Webb", ["A", "R"]) == ["R", "A"]
    assert [a["roles"] for a in _row(fetch_raci_matrix(test_project), t)["assignments"]] == [["R", "A"]]
    set_task_raci(t, "Marcus Webb", ["C"])
    assert [a["roles"] for a in _row(fetch_raci_matrix(test_project), t)["assignments"]] == [["C"]]
    set_task_raci(t, "Marcus Webb", [])
    assert _row(fetch_raci_matrix(test_project), t)["assignments"] == []


def test_warnings_only_fire_on_started_rows(test_project):
    t = _task(test_project, "Sign off portal")
    set_task_raci(t, "Marcus Webb", ["A"])
    assert _row(fetch_raci_matrix(test_project), t)["warnings"] == ["no_responsible"]  # A but nobody does the work

    set_task_raci(t, "Jordan Mills", ["R"])
    assert _row(fetch_raci_matrix(test_project), t)["warnings"] == []

    set_task_raci(t, "Priya Nair", ["A"])
    assert _row(fetch_raci_matrix(test_project), t)["warnings"] == ["multiple_accountable"]

    t2 = _task(test_project, "Write docs")
    set_task_raci(t2, "Jordan Mills", ["R"])
    assert _row(fetch_raci_matrix(test_project), t2)["warnings"] == ["no_accountable"]


def test_people_columns_combine_roster_assignments_and_owners(test_project):
    upsert_project_person(test_project, "Sarah Okonkwo", "s@example.com")
    t = _task(test_project, "Task", owner="Jordan Mills")
    set_task_raci(t, "Lisa Chen", ["I"])
    assert set(fetch_raci_matrix(test_project)["people"]) == {"Sarah Okonkwo", "Lisa Chen", "Jordan Mills"}


def test_invalid_roles_and_unknown_task(test_project):
    t = _task(test_project, "Task")
    with pytest.raises(ValueError):
        set_task_raci(t, "Jordan", ["Z"])
    with pytest.raises(ValueError):
        set_task_raci(t, "  ", ["R"])
    with pytest.raises(LookupError):
        set_task_raci("00000000-0000-0000-0000-000000000000", "Jordan", ["R"])


# ------------------------------------------------------------------ HTTP

def test_raid_endpoints(test_project):
    created = client.post(f"/projects/{test_project}/raid", json={"item_type": "risk", "title": "Scope creep", "impact": "high"})
    assert created.status_code == 200
    item_id = created.json()["item_id"]
    assert client.get(f"/projects/{test_project}/raid?item_type=risk").json()["items"][0]["title"] == "Scope creep"

    patched = client.patch(f"/raid/{item_id}", json={"status": "closed", "review_date": "2026-11-01"})
    assert patched.status_code == 200 and patched.json()["status"] == "closed" and patched.json()["review_date"] == "2026-11-01"

    assert client.post(f"/projects/{test_project}/raid", json={"item_type": "nonsense", "title": "x"}).status_code == 422
    assert client.post(f"/projects/{test_project}/raid", json={"item_type": "risk", "title": "  "}).status_code == 400
    assert client.patch(f"/raid/{item_id}", json={"status": "bogus"}).status_code == 422
    assert client.patch("/raid/00000000-0000-0000-0000-000000000000", json={"status": "closed"}).status_code == 404
    assert client.delete(f"/raid/{item_id}").status_code == 200
    assert client.delete(f"/raid/{item_id}").status_code == 404


def test_promote_endpoint(test_project):
    item_id = client.post(f"/projects/{test_project}/raid", json={"item_type": "risk", "title": "May happen"}).json()["item_id"]
    resp = client.post(f"/raid/{item_id}/promote-to-issue")
    assert resp.status_code == 200
    assert resp.json()["issue"]["item_type"] == "issue" and resp.json()["risk"]["status"] == "closed"
    assert client.post(f"/raid/{item_id}/promote-to-issue").status_code == 400
    assert client.post("/raid/00000000-0000-0000-0000-000000000000/promote-to-issue").status_code == 404
    assert client.patch(f"/raid/{item_id}", json={"status": "materialized"}).status_code == 422


def test_raci_endpoints(test_project):
    t = _task(test_project, "Review test plan", owner="Priya Nair")
    resp = client.put(f"/tasks/{t}/raci", json={"person": "Marcus Webb", "roles": ["A"]})
    assert resp.status_code == 200 and resp.json()["roles"] == ["A"]
    matrix = client.get(f"/projects/{test_project}/raci").json()
    row = next(r for r in matrix["rows"] if r["subject_id"] == str(t))
    assert row["warnings"] == []  # the owner is an implied R, Marcus is the A
    assert {(a["person"], tuple(a["roles"]), a["implied"]) for a in row["assignments"]} == {
        ("Marcus Webb", ("A",), False), ("Priya Nair", ("R",), True)}
    assert client.put(f"/tasks/{t}/raci", json={"person": "X", "roles": ["Q"]}).status_code == 422
    assert client.put("/tasks/00000000-0000-0000-0000-000000000000/raci", json={"person": "X", "roles": ["R"]}).status_code == 404
