"""Reading transcripts and turning a meeting's task mentions into suggestions (apps/utils/meeting_suggest.py). No database, no model."""
import io
from datetime import date

import pytest

from apps.utils.hours_import import HoursImportError
from apps.utils.meeting_suggest import ask_model, build_suggestions, finish_week_of, read_transcript

KICKOFF = date(2026, 9, 28)  # a Monday; week 5 = 26 Oct, week 6 = 2 Nov
TASKS = [{"task_id": "t1", "task_text": "Build search", "owner": "Bo", "status": "in_progress", "plan_finish_week": 5},
         {"task_id": "t2", "task_text": "Write the user guide", "owner": "Cy", "status": "not_started", "plan_finish_week": 8}]


def ev(text, matched=None, **kw):
    return {"task_text": text, "task_id": matched, "owner": None, "status": None, "deadline": None, "blocker": None, "quote": None, "confidence": "high", **kw}


def test_subtitle_files_lose_their_timestamps_and_cue_numbers():
    vtt = b"WEBVTT\n\n1\n00:00:01.000 --> 00:00:04.000\nAna: we will ship search on Friday.\n\n2\n00:00:05.000 --> 00:00:08.000\nBo: I can take the migration.\n"
    assert read_transcript("call.vtt", vtt) == "Ana: we will ship search on Friday.\nBo: I can take the migration."
    srt = b"1\n00:00:01,000 --> 00:00:04,000\nAna: we will ship search on Friday.\n"
    assert read_transcript("call.srt", srt) == "Ana: we will ship search on Friday."
    assert read_transcript("notes.md", b"# Standup\nAna will ship search on Friday.") .startswith("# Standup")


def test_a_word_transcript_is_read():
    from docx import Document
    doc = Document()
    doc.add_paragraph("Standup 29 Oct")
    doc.add_paragraph("Ana will ship the search page by Friday.")
    buf = io.BytesIO()
    doc.save(buf)
    assert read_transcript("standup.docx", buf.getvalue()) == "Standup 29 Oct\nAna will ship the search page by Friday."


@pytest.mark.parametrize("name,content,msg", [("a.pdf", b"x" * 50, "should be"), ("a.txt", b"short", "too short"), ("a.txt", b"x" * (2 * 1024 * 1024 + 1), "larger")])
def test_unreadable_transcripts_say_why(name, content, msg):
    with pytest.raises(HoursImportError, match=msg):
        read_transcript(name, content)


def test_a_date_becomes_the_plans_week_number():
    assert finish_week_of("2026-10-30", KICKOFF, 0) == 5 and finish_week_of("2026-11-02", KICKOFF, 0) == 6 and finish_week_of("2026-09-28", KICKOFF, 0) == 1
    assert finish_week_of("2026-09-01", KICKOFF, 0) is None and finish_week_of("next Friday", KICKOFF, 0) is None
    assert finish_week_of("2026-10-30", None, 0) is None and finish_week_of(None, KICKOFF, 0) is None
    assert finish_week_of("2026-11-01", KICKOFF, 6) == 6 and finish_week_of("2026-10-31", KICKOFF, 6) == 5  # weeks starting on Sunday: 1 Nov opens week 6


def test_new_tasks_and_changes_are_told_apart_and_only_real_differences_are_suggested():
    events = [ev("Prepare the data migration plan", owner="Bo", deadline="2026-11-06", status="in progress"),  # nothing like it in the plan
              ev("Build search", matched="t1", owner="Cy", status="Done", deadline="2026-11-06"),  # owner, status and week all differ
              ev("Write the user guide", matched="t2", owner="cy", status="not started", deadline="2026-11-20"),  # week 8 -> week8: nothing new
              ev("Build search", matched="gone-task", owner="Bo")]  # matched to a task that is no longer in the plan: dropped
    out = build_suggestions(events, TASKS, KICKOFF)
    assert [(s["kind"], s["task_text"]) for s in out] == [("new_task", "Prepare the data migration plan"), ("change", "Build search")]
    assert out[0]["fields"] == {"owner": "Bo", "status": "in_progress", "finish_week": 6, "deadline": "2026-11-06"}
    assert out[1]["task_id"] == "t1" and out[1]["changes"] == {"owner": {"from": "Bo", "to": "Cy"}, "status": {"from": "in_progress", "to": "completed"},
                                                               "finish_week": {"from": 5, "to": 6}}


def test_a_blocker_means_blocked_and_a_deadline_without_a_kickoff_is_not_suggested():
    out = build_suggestions([ev("Build search", matched="t1", blocker="Waiting for the VPN", deadline="2026-11-06")], TASKS, None)
    assert out[0]["changes"] == {"status": {"from": "in_progress", "to": "blocked"}} and out[0]["fields"] == {"blocker": "Waiting for the VPN"}
    assert build_suggestions([ev("Build search", matched="t1", deadline="2026-11-06")], TASKS, None) == []


PEOPLE = ["Bo Li", "Cy Ward", "David Osei"]


def test_owners_are_written_as_the_roster_writes_them_and_the_same_person_is_not_a_change():
    tasks = [{**TASKS[0], "owner": "Bo Li"}, TASKS[1]]  # the plan already has Bo Li on search; "Cy" (not the full name) on the user guide
    out = build_suggestions([ev("Build search", matched="t1", owner="bo", status="in progress"),  # "bo" is Bo Li, who already owns it: no change
                             ev("Write the user guide", matched="t2", owner="Cy"),  # "Cy" is Cy Ward: written as the roster writes it
                             ev("Prepare the migration plan", owner="David")], tasks, KICKOFF, 0, PEOPLE)
    assert [(s["task_text"], s["changes"].get("owner")) for s in out if s["kind"] == "change"] == [("Write the user guide", {"from": "Cy", "to": "Cy Ward"})]
    assert [s["fields"]["owner"] for s in out if s["kind"] == "new_task"] == ["David Osei"]


def test_the_quote_the_suggestion_rests_on_is_kept(monkeypatch):
    out = build_suggestions([ev("Prepare the migration plan", owner="Bo", quote="Bo: I will write the migration plan by Friday."),
                             ev("Build search", matched="t1", status="done", quote="Search is done.")], TASKS, KICKOFF, 0, PEOPLE)
    assert out[0]["fields"]["evidence"] == "Bo: I will write the migration plan by Friday." and out[1]["fields"]["evidence"] == "Search is done."


def test_the_model_answer_is_checked_before_it_is_used(monkeypatch):
    answer = {"items": [{"task_number": 1, "owner": "Cy", "status": "done", "quote": "q" * 300, "confidence": "high"},  # plan task 1, long quote cut
                        {"task_number": 9, "task_text": "Pointing past the list", "owner": None},  # a bad number: kept as a new task by its name
                        {"task_number": None, "task_text": "write the user guide"},  # said to be new but it is the plan's own wording: task 2
                        {"task_number": 7},  # a bad number and no name: dropped
                        "not an object", {"task_number": None, "task_text": "Ship it", "deadline": "2026-11-06", "confidence": "sure"}]}
    monkeypatch.setattr("apps.utils.meeting_suggest._ask", lambda prompt: answer)
    items = ask_model("some transcript", TASKS, ["Cy Ward"], date(2026, 10, 29))
    assert [(i["task_id"], i["task_text"]) for i in items] == [("t1", "Build search"), (None, "Pointing past the list"), ("t2", "Write the user guide"), (None, "Ship it")]
    assert len(items[0]["quote"]) == 240 and items[3]["deadline"] == "2026-11-06" and items[3]["confidence"] == "medium"


def test_a_failed_or_unreadable_model_call_raises_instead_of_reporting_no_suggestions(monkeypatch):
    def boom(**kw):
        raise RuntimeError("model down")
    monkeypatch.setattr("apps.llm.complete", boom)
    with pytest.raises(RuntimeError):
        ask_model("some transcript", TASKS, [])

    class R:  # a model answer that is not JSON
        choices = [type("C", (), {"message": type("M", (), {"content": "Sorry, I cannot."})()})()]
    monkeypatch.setattr("apps.llm.complete", lambda **kw: R())
    with pytest.raises(HoursImportError, match="could not be read"):
        ask_model("some transcript", TASKS, [])

    class Fenced:
        choices = [type("C", (), {"message": type("M", (), {"content": '```json\n{"items": []}\n```'})()})()]
    monkeypatch.setattr("apps.llm.complete", lambda **kw: Fenced())
    assert ask_model("some transcript", TASKS, []) == []


def test_a_week_the_meeting_names_becomes_the_finish_week_without_a_date():
    out = build_suggestions([ev("Write the user guide", matched="t2", finish_week=10)], TASKS, KICKOFF)
    assert out[0]["changes"] == {"finish_week": {"from": 8, "to": 10}}
    assert build_suggestions([ev("Write the user guide", matched="t2", finish_week=8)], TASKS, KICKOFF) == []  # the plan already says week 8


def test_the_model_answer_may_carry_a_week_number(monkeypatch):
    monkeypatch.setattr("apps.utils.meeting_suggest._ask", lambda prompt: {"items": [{"task_number": 2, "finish_week": 10}, {"task_number": 1, "finish_week": "soon"}]})
    items = ask_model("some transcript", TASKS, [], date(2026, 10, 29))
    assert [i["finish_week"] for i in items] == [10, None]
