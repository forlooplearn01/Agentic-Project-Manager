"""Regression tests for the meeting-approval gate: Meeting Coordinator only ever proposes
a meeting (status=pending_approval); the real Google Calendar event only gets created on
explicit human approval. A rejected proposal must never end up with a google_event_id -
that would mean a rejected meeting somehow got scheduled anyway."""
from apps.db.db import create_pending_meeting, get_meeting, approve_meeting_record, reject_meeting_record


def _make_pending(project_id):
    return create_pending_meeting(
        project_id=project_id,
        title="Weekly sync",
        description="Discuss blockers",
        attendee_emails=["a@example.com", "b@example.com"],
        unresolved_attendees=[],
        start_iso="2026-10-01T10:00:00-07:00",
        end_iso="2026-10-01T10:30:00-07:00",
        reason="Explicit reconvene statement in MoM",
    )


def test_new_proposal_starts_pending_with_no_event_id(test_project):
    meeting_id = _make_pending(test_project)

    meeting = get_meeting(meeting_id)
    assert meeting["status"] == "pending_approval"
    assert meeting["google_event_id"] is None


def test_approval_transitions_to_scheduled_with_event_id(test_project):
    meeting_id = _make_pending(test_project)

    approve_meeting_record(
        meeting_id, google_event_id="evt_123", meeting_link="https://meet.example.com/x",
        title="Weekly sync", attendee_emails=["a@example.com", "b@example.com"],
        start_iso="2026-10-01T10:00:00-07:00", end_iso="2026-10-01T10:30:00-07:00",
    )

    meeting = get_meeting(meeting_id)
    assert meeting["status"] == "scheduled"
    assert meeting["google_event_id"] == "evt_123"


def test_rejection_never_produces_an_event_id(test_project):
    meeting_id = _make_pending(test_project)

    reject_meeting_record(meeting_id)

    meeting = get_meeting(meeting_id)
    assert meeting["status"] == "rejected"
    assert meeting["google_event_id"] is None
