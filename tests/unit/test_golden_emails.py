"""The golden exports hold <<email:Name>> instead of addresses. A restore fills them from datasets/golden/emails.local.json (gitignored),
then from any project that already has the person, and on a fresh checkout with no such file it uses @example.com placeholders."""
import json

import pytest

from apps.utils import golden

TEXT = 'owner <<email:Ana Beltran>> and <<email:Jonas Weber>>'


class _Cursor:
    def __init__(self, rows):
        self.rows = rows

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def execute(self, sql, params):
        pass

    def fetchall(self):
        return self.rows


class _Conn:
    def __init__(self, rows):
        self.rows = rows

    def cursor(self):
        return _Cursor(self.rows)

    def close(self):
        pass


@pytest.fixture
def golden_dir(tmp_path, monkeypatch):
    monkeypatch.setattr(golden, "GOLDEN_DIR", tmp_path)
    monkeypatch.setattr(golden, "get_connection", lambda: _Conn([]))
    return tmp_path


def test_without_an_emails_file_placeholders_are_used(golden_dir):
    assert golden._show_emails(TEXT) == "owner ana.beltran@example.com and jonas.weber@example.com"


def test_the_emails_file_wins_when_present(golden_dir):
    (golden_dir / golden.EMAILS_FILE).write_text(json.dumps({"Ana Beltran": "ana@real.test", "Jonas Weber": "jonas@real.test"}))
    assert golden._show_emails(TEXT) == "owner ana@real.test and jonas@real.test"


def test_a_person_missing_from_an_existing_file_still_raises(golden_dir):
    (golden_dir / golden.EMAILS_FILE).write_text(json.dumps({"Ana Beltran": "ana@real.test"}))
    with pytest.raises(RuntimeError, match="Jonas Weber"):
        golden._show_emails(TEXT)


def test_an_address_already_on_a_project_roster_beats_a_placeholder(golden_dir, monkeypatch):
    monkeypatch.setattr(golden, "get_connection", lambda: _Conn([("Jonas Weber", "jonas@roster.test")]))
    assert golden._show_emails(TEXT) == "owner ana.beltran@example.com and jonas@roster.test"


def test_text_without_email_tokens_is_returned_unchanged(golden_dir):
    assert golden._show_emails("nothing to fill in") == "nothing to fill in"
