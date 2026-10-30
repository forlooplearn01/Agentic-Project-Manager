"""AI RAID suggestions: quote verification, probability/impact honesty, de-duplication (incl. rejected items), which
documents get scanned, and the HTTP contract. The LLM is scripted - what is under test is our own logic, never a model's
judgment (eval/nodes/eval_raid_suggest.py measures that)."""
import pytest
from fastapi.testclient import TestClient

from apps.api import app
from apps.db.db import create_raid_item, list_raid_items, update_raid_item, upsert_source_document
from apps.utils.raid_ai import suggest_raid_items_from_text
from tests.conftest import make_final_json_response

client = TestClient(app)
DOC = ("Weekly sync. Priya Nair said whitelisting approval is a high risk because it is the only thing blocking go-live. "
       "The staging server keeps crashing under load and nobody has fixed it yet. "
       "We are assuming the client will deliver the CSV datasets by Friday.")


def _fake_llm(monkeypatch, payload):
    calls = []

    def fake(**kwargs):
        calls.append(kwargs["messages"][0]["content"])
        if isinstance(payload, Exception):
            raise payload
        return make_final_json_response(payload(kwargs["messages"][0]["content"]) if callable(payload) else payload)

    monkeypatch.setattr("apps.utils.raid_ai.complete", fake)
    return calls


def _item(item_type, title, evidence=None, **extra):
    return {"item_type": item_type, "title": title, "description": None, "owner": None, "probability": None, "impact": None,
            "evidence": evidence, **extra}


# ------------------------------------------------------------------ extraction guards

def test_wrapped_and_elided_quotes_are_verified_but_invented_ones_are_dropped(monkeypatch):
    _fake_llm(monkeypatch, {"items": [
        _item("risk", "Whitelisting approval blocks go-live", '"...whitelisting approval is a high risk because it is the only thing blocking go-live."'),
        _item("issue", "Staging server crashes", "The staging server ... has been down for a week"),
        _item("assumption", "CSV datasets arrive Friday", "We are assuming the client will deliver the CSV datasets by Friday."),
    ]})
    items = suggest_raid_items_from_text(DOC)
    assert [bool(i["evidence"]) for i in items] == [True, False, True]


def test_probability_and_impact_are_kept_only_when_the_quote_is_verified(monkeypatch):
    quote = "whitelisting approval is a high risk"
    _fake_llm(monkeypatch, {"items": [
        _item("risk", "Whitelisting approval", quote, probability="high", impact="medium"),
        _item("risk", "Invented one", "this sentence is nowhere in the document", probability="high", impact="high"),
        _item("risk", "Bad level", quote, probability="severe", impact="low"),
    ]})
    a, b, c = suggest_raid_items_from_text(DOC)
    assert (a["probability"], a["impact"]) == ("high", "medium")
    assert (b["probability"], b["impact"]) == (None, None)
    assert (c["probability"], c["impact"]) == (None, "low")  # an unknown level is discarded, a valid one kept


def test_unknown_types_and_empty_titles_are_discarded(monkeypatch):
    _fake_llm(monkeypatch, {"items": [_item("task", "Do a thing"), _item("risk", "   "), "junk", _item("risk", "Real risk")]})
    assert [i["title"] for i in suggest_raid_items_from_text(DOC)] == ["Real risk"]


def test_a_failed_call_raises_so_the_caller_does_not_mark_the_document_scanned(monkeypatch):
    _fake_llm(monkeypatch, ValueError("boom"))
    with pytest.raises(RuntimeError):
        suggest_raid_items_from_text(DOC)


# ------------------------------------------------------------------ storing

def test_suggestions_are_stored_as_suggested_agent_items_with_evidence(monkeypatch, test_project):
    from apps.db.db import store_suggested_raid_items
    out = store_suggested_raid_items(test_project, [_item("risk", "Whitelisting approval may slip", "a quote", owner="Priya Nair")])
    assert len(out["created"]) == 1 and out["skipped_existing"] == 0
    row = list_raid_items(test_project)[0]
    assert (row["review_state"], row["origin"], row["evidence"], row["owner"]) == ("suggested", "agent", "a quote", "Priya Nair")


def test_restated_items_are_skipped_including_rejected_ones_and_repeats_in_one_batch(test_project):
    from apps.db.db import store_suggested_raid_items
    create_raid_item(test_project, "risk", "Whitelisting approval may be delayed")
    rejected = create_raid_item(test_project, "issue", "Staging server crashes under load", review_state="suggested", origin="agent")
    update_raid_item(rejected["item_id"], {"review_state": "rejected"})
    out = store_suggested_raid_items(test_project, [
        _item("risk", "Whitelisting approval may be delayed"),          # same as a confirmed item
        _item("issue", "Staging server crashes under load"),            # same as a rejected one: a rejection is remembered
        _item("dependency", "Production environment ready before deployment"),
        _item("dependency", "Production environment ready before deployment"),  # repeated within the batch
    ])
    assert (len(out["created"]), out["skipped_existing"]) == (1, 3)


def test_the_same_title_under_a_different_type_is_not_a_duplicate(test_project):
    from apps.db.db import store_suggested_raid_items
    create_raid_item(test_project, "dependency", "API credentials provided by the client")
    out = store_suggested_raid_items(test_project, [_item("assumption", "API credentials provided by the client")])
    assert len(out["created"]) == 1


# ------------------------------------------------------------------ HTTP: which documents are scanned

def test_suggest_scans_only_unscanned_documents_unless_forced(monkeypatch, test_project):
    upsert_source_document(DOC, "meeting_minutes", test_project)
    calls = _fake_llm(monkeypatch, {"items": [_item("risk", "Whitelisting approval blocks go-live", "whitelisting approval is a high risk")]})

    first = client.post(f"/projects/{test_project}/raid/suggest").json()
    assert (first["documents_scanned"], len(first["created"])) == (1, 1) and len(calls) == 1

    again = client.post(f"/projects/{test_project}/raid/suggest").json()  # nothing new to read: no model call at all
    assert (again["documents_scanned"], again["created"]) == (0, []) and len(calls) == 1

    forced = client.post(f"/projects/{test_project}/raid/suggest", json={"force": True}).json()
    assert forced["documents_scanned"] == 1 and len(calls) == 2
    assert (len(forced["created"]), forced["skipped_existing"]) == (0, 1)  # restated -> not added twice


def test_a_document_the_assistant_could_not_read_stays_unscanned(monkeypatch, test_project):
    upsert_source_document(DOC, "meeting_minutes", test_project)
    _fake_llm(monkeypatch, ValueError("boom"))
    assert client.post(f"/projects/{test_project}/raid/suggest").status_code == 502

    _fake_llm(monkeypatch, {"items": []})  # retry works because the document was not marked scanned
    ok = client.post(f"/projects/{test_project}/raid/suggest")
    assert ok.status_code == 200 and ok.json()["documents_scanned"] == 1


def test_suggest_with_no_documents_is_a_clear_400(test_project):
    r = client.post(f"/projects/{test_project}/raid/suggest")
    assert r.status_code == 400 and "No documents" in r.json()["detail"]
