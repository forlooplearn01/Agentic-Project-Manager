import json
import os
import re
from datetime import datetime, timedelta
from typing import Optional, List

from apps.db.db import resolve_person_email, list_project_people


# ---------------------------------------------------------------------------
# Auth - two modes, switchable via GOOGLE_AUTH_MODE without touching code:
#
#   "oauth" (default) - one Google account (personal Gmail or otherwise) grants this
#     app access once via a real consent screen (scripts/get_google_refresh_token.py),
#     and a stored refresh token is reused after that. Can only act on that ONE
#     connected account's calendar (plus any calendar explicitly shared with it) -
#     there is no company-domain concept to resolve arbitrary attendees against.
#
#   "workspace" - a service account with domain-wide delegation, pre-authorized once
#     by a Google Workspace admin for the whole org. Can act as ANY employee's
#     calendar with zero per-person setup - the right mode once the company is on
#     Workspace, but it does not exist for personal/non-Workspace accounts.
#
# Switching later is a .env change (GOOGLE_AUTH_MODE + the mode's own vars), not a
# code change - see INTEGRATIONS.md for the full comparison and setup steps.
# ---------------------------------------------------------------------------

MEETING_TIMEZONE = os.getenv("MEETING_TIMEZONE", "America/New_York")


def _event_time(iso: str) -> dict:
    """Google rejects a time with no UTC offset unless it names a time zone ("Missing time zone definition"), and a meeting proposed by the
    agent or a golden project is stored as a plain local time like 2026-11-03T10:00:00. Those are read in MEETING_TIMEZONE."""
    if re.search(r"(Z|[+-]\d{2}:?\d{2})$", iso.strip()):
        return {"dateTime": iso}
    return {"dateTime": iso, "timeZone": MEETING_TIMEZONE}


def _get_auth_mode() -> str:
    return os.getenv("GOOGLE_AUTH_MODE", "oauth").strip().lower()


def _build_oauth_credentials(scopes: List[str]):
    client_id = os.getenv("GOOGLE_OAUTH_CLIENT_ID")
    client_secret = os.getenv("GOOGLE_OAUTH_CLIENT_SECRET")
    refresh_token = os.getenv("GOOGLE_OAUTH_REFRESH_TOKEN")

    if not all([client_id, client_secret, refresh_token]):
        return None

    from google.oauth2.credentials import Credentials

    # No access token yet (token=None) - the client library fetches one automatically
    # on first use via the refresh token, and refreshes again whenever it expires.
    return Credentials(
        token=None,
        refresh_token=refresh_token,
        token_uri="https://oauth2.googleapis.com/token",
        client_id=client_id,
        client_secret=client_secret,
        scopes=scopes,
    )


def _build_workspace_credentials(scopes: List[str], subject: str):
    service_account_file = os.getenv("GOOGLE_SERVICE_ACCOUNT_FILE")
    if not service_account_file or not os.path.exists(service_account_file):
        return None

    from google.oauth2 import service_account

    credentials = service_account.Credentials.from_service_account_file(service_account_file, scopes=scopes)
    return credentials.with_subject(subject)


def _not_configured(mode: str, **extra) -> str:
    if mode == "workspace":
        message = (
            "Google Calendar (Workspace mode) is not configured. "
            "Set GOOGLE_SERVICE_ACCOUNT_FILE, GOOGLE_CALENDAR_DOMAIN, and GOOGLE_CALENDAR_ORGANIZER in .env."
        )
    else:
        message = (
            "Google Calendar (OAuth2 mode) is not configured. "
            "Set GOOGLE_OAUTH_CLIENT_ID and GOOGLE_OAUTH_CLIENT_SECRET in .env, then run "
            "scripts/get_google_refresh_token.py once to obtain GOOGLE_OAUTH_REFRESH_TOKEN."
        )
    return json.dumps({"status": "not_configured", "message": message, **extra})


def _parse_deadline_window(deadline: Optional[str]) -> tuple:
    # Convert a deadline string into a (time_min, time_max) window for the freebusy query.
    # Defaults to a 24-hour window starting from now if no deadline is provided.
    now = datetime.utcnow()

    if not deadline:
        time_min = now
        time_max = now + timedelta(days=1)
    else:
        try:
            # Attempt strict ISO date parse first (YYYY-MM-DD)
            target = datetime.strptime(deadline.strip(), "%Y-%m-%d")
            time_min = target.replace(hour=0, minute=0, second=0)
            time_max = target.replace(hour=23, minute=59, second=59)
        except ValueError:
            # Fall back to a 24-hour window from now for relative strings like "tomorrow EOD"
            time_min = now
            time_max = now + timedelta(days=1)

    return (
        time_min.strftime("%Y-%m-%dT%H:%M:%SZ"),
        time_max.strftime("%Y-%m-%dT%H:%M:%SZ"),
    )


# ---------------------------------------------------------------------------
# MCP tool
# ---------------------------------------------------------------------------

def check_owner_availability(owner: str, deadline: Optional[str] = None) -> str:
    """Check Google Calendar availability for a task owner around a deadline.
    In Workspace mode, `owner` can be a bare name (resolved against GOOGLE_CALENDAR_DOMAIN)
    or a full email. In OAuth2 mode there is no company domain to resolve a bare name
    against - pass a real email (e.g. from resolve_attendees) instead."""
    mode = _get_auth_mode()

    if mode == "workspace":
        scopes = ["https://www.googleapis.com/auth/calendar.readonly"]
        domain = os.getenv("GOOGLE_CALENDAR_DOMAIN")
        if not domain:
            return _not_configured(mode, owner=owner, deadline=deadline)
        query_calendar_id = owner if "@" in owner else f"{owner}@{domain}"
        try:
            credentials = _build_workspace_credentials(scopes, subject=query_calendar_id)
        except Exception as e:
            return json.dumps({"error": f"Google Calendar auth failed: {e}", "owner": owner})
    else:
        if "@" not in owner:
            return json.dumps({
                "status": "unavailable_to_check",
                "message": (
                    f"'{owner}' isn't an email address, and OAuth2 mode has no company domain "
                    "to guess one from - pass a resolved email (e.g. from resolve_attendees) instead."
                ),
                "owner": owner,
            })
        query_calendar_id = owner
        # Must match the scope the refresh token was actually granted (the full "calendar"
        # scope requested by scripts/get_google_refresh_token.py) - Google rejects a refresh
        # request for any other scope string, even a narrower one like calendar.readonly.
        scopes = ["https://www.googleapis.com/auth/calendar"]
        try:
            credentials = _build_oauth_credentials(scopes)
        except Exception as e:
            return json.dumps({"error": f"Google Calendar auth failed: {e}", "owner": owner})

    if not credentials:
        return _not_configured(mode, owner=owner, deadline=deadline)

    try:
        from googleapiclient.discovery import build
    except ImportError:
        return json.dumps({
            "error": "google-auth-oauthlib and google-api-python-client are required. "
                     "Run: pip install google-auth google-auth-oauthlib google-api-python-client",
            "owner": owner,
        })

    try:
        service = build("calendar", "v3", credentials=credentials)
        time_min, time_max = _parse_deadline_window(deadline)

        # Query the freebusy endpoint - returns busy time blocks for the given window
        body = {
            "timeMin": time_min,
            "timeMax": time_max,
            "items": [{"id": query_calendar_id}],
        }
        result = service.freebusy().query(body=body).execute()
        cal_result = result.get("calendars", {}).get(query_calendar_id, {})

        if cal_result.get("errors"):
            # OAuth mode commonly hits this for anyone but the connected account itself,
            # unless their calendar was explicitly shared with it - a real, expected limit
            # of single-account OAuth, not a bug.
            return json.dumps({
                "status": "unavailable_to_check",
                "message": f"Cannot read {query_calendar_id}'s calendar - not shared with the connected account.",
                "owner": owner,
            })

        busy_slots = cal_result.get("busy", [])
        return json.dumps({
            "status": "available" if not busy_slots else "busy",
            "owner": query_calendar_id,
            "deadline": deadline,
            "window": {"from": time_min, "to": time_max},
            "busy_slots": busy_slots,
        })

    except Exception as e:
        return json.dumps({"error": f"Google Calendar API failed: {e}", "owner": owner})


# ---------------------------------------------------------------------------
# MCP tools - Meeting Coordinator agent only
# ---------------------------------------------------------------------------

def resolve_attendees(project_id: str, names: List[str]) -> str:
    """Resolve a list of person names to email addresses via project_people.
    Names that don't resolve are returned separately, not guessed - the calling
    agent should surface them as a flagged item rather than silently dropping them
    or inventing an address."""
    resolved = []
    unresolved = []
    for name in names:
        person = resolve_person_email(project_id, name)
        if person:
            resolved.append(person)
        else:
            unresolved.append(name)

    return json.dumps({
        "resolved": resolved,
        "unresolved": unresolved,
        "known_people": list_project_people(project_id) if unresolved else None,
    })


def propose_meeting(
    title: str,
    attendee_emails: List[str],
    start_iso: str,
    end_iso: str,
    description: Optional[str] = None,
) -> str:
    """Stage a meeting for human approval - this is the Meeting Coordinator's only way
    to act on a decision to schedule. It never touches Google Calendar or sends an
    invite; it just returns the proposed details so the caller can persist them for a
    person to review. schedule_meeting (below) is what actually creates the event, and
    is only called once a person approves the proposal via the UI."""
    if not attendee_emails:
        return json.dumps({"error": "No attendee emails provided - cannot propose a meeting with no attendees."})
    return json.dumps({
        "status": "proposed",
        "title": title,
        "attendee_emails": attendee_emails,
        "start_iso": start_iso,
        "end_iso": end_iso,
        "description": description,
    })


def schedule_meeting(
    title: str,
    attendee_emails: List[str],
    start_iso: str,
    end_iso: str,
    description: Optional[str] = None,
) -> str:
    """Create a Google Calendar event with attendees and send invites (sendUpdates=all).
    Google emails every attendee the event details and meeting link automatically -
    this is the only notification step, there is no separate email send. In OAuth2
    mode the event is created on the connected account's own ("primary") calendar; in
    Workspace mode it's created on GOOGLE_CALENDAR_ORGANIZER via domain delegation."""
    if not attendee_emails:
        return json.dumps({"error": "No attendee emails provided - cannot schedule a meeting with no attendees."})

    mode = _get_auth_mode()
    scopes = ["https://www.googleapis.com/auth/calendar"]

    if mode == "workspace":
        organizer = os.getenv("GOOGLE_CALENDAR_ORGANIZER")
        if not organizer:
            return _not_configured(mode, title=title, attendee_emails=attendee_emails)
        try:
            credentials = _build_workspace_credentials(scopes, subject=organizer)
        except Exception as e:
            return json.dumps({"error": f"Google Calendar auth failed: {e}", "title": title})
        calendar_id = organizer
    else:
        try:
            credentials = _build_oauth_credentials(scopes)
        except Exception as e:
            return json.dumps({"error": f"Google Calendar auth failed: {e}", "title": title})
        calendar_id = "primary"

    if not credentials:
        return _not_configured(mode, title=title, attendee_emails=attendee_emails)

    try:
        from googleapiclient.discovery import build
    except ImportError:
        return json.dumps({
            "error": "google-auth-oauthlib and google-api-python-client are required. "
                     "Run: pip install google-auth google-auth-oauthlib google-api-python-client",
        })

    try:
        service = build("calendar", "v3", credentials=credentials)

        event_body = {
            "summary": title,
            "description": description or "",
            "start": _event_time(start_iso),
            "end": _event_time(end_iso),
            "attendees": [{"email": email} for email in attendee_emails],
            "conferenceData": {
                "createRequest": {
                    "requestId": f"pp-{datetime.utcnow().timestamp()}",
                    "conferenceSolutionKey": {"type": "hangoutsMeet"},
                }
            },
        }

        created = service.events().insert(
            calendarId=calendar_id,
            body=event_body,
            sendUpdates="all",
            conferenceDataVersion=1,
        ).execute()

        return json.dumps({
            "status": "scheduled",
            "event_id": created.get("id"),
            "meeting_link": created.get("hangoutLink") or created.get("htmlLink"),
            "attendees": attendee_emails,
            "start": start_iso,
            "end": end_iso,
        })

    except Exception as e:
        return json.dumps({"error": f"Google Calendar API failed: {e}", "title": title})
