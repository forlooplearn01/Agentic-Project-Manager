"""Excel export of the registers: content, the inferred-Responsible marker, the formula-injection guard, what is excluded, HTTP."""
import io

from fastapi.testclient import TestClient
from openpyxl import load_workbook

from apps.api import app
from apps.db.db import (
    create_deliverable,
    create_raid_item,
    insert_task,
    set_deliverable_raci,
    set_task_raci,
)
from apps.utils.registers_export import build_registers_workbook

client = TestClient(app)


def _wb(project_id):
    return load_workbook(io.BytesIO(build_registers_workbook(project_id)))


def _rows(ws):
    # an empty string is stored as an empty cell and reads back as None - treat them the same
    return [["" if c.value is None else c.value for c in row] for row in ws.iter_rows()]


def test_the_workbook_has_the_expected_sheets_and_content(test_project):
    create_raid_item(test_project, "risk", "Whitelist may slip", owner="Priya Nair", probability="high", impact="medium")
    create_raid_item(test_project, "issue", "VPN blocked", status="mitigating")
    d = create_deliverable(test_project, "Data mapping document", number="1.1", owner="Sofia Martins", deadline="Week 2")
    insert_task({"task_text": "Draft the mapping", "normalized_task_text": "draft the mapping", "owner": "Sofia Martins"}, test_project)
    wb = _wb(test_project)
    assert wb.sheetnames == ["Summary", "RAID log", "Deliverables", "RACI - Deliverables", "RACI - Tasks"]

    raid = _rows(wb["RAID log"])
    assert raid[0][:5] == ["Type", "Title", "Description", "Owner", "Status"]
    assert [r[0] for r in raid[1:]] == ["Risk", "Issue"]  # risks before issues
    assert raid[1][1:8] == ["Whitelist may slip", "", "Priya Nair", "Open", "High", "Medium", ""]

    dl = _rows(wb["Deliverables"])
    assert dl[1] == ["1.1", "Data mapping document", "Sofia Martins", "Week 2", "Not started", 0, "Added by hand"]
    summary = dict((r[0], r[1]) for r in _rows(wb["Summary"]) if r[0])
    assert summary["Project"] == test_project and summary["Open risks"] == 1 and summary["Deliverables"] == 1


def test_an_inferred_responsible_is_marked_and_an_assigned_role_is_not(test_project):
    d = create_deliverable(test_project, "Final report", owner="Marcus Webb")
    t = insert_task({"task_text": "Write report", "normalized_task_text": "write report", "owner": "Jordan Mills"}, test_project)
    set_deliverable_raci(str(d["deliverable_id"]), "Sarah Okonkwo", ["A"])
    set_task_raci(str(t), "Jordan Mills", ["R", "A"])
    wb = _wb(test_project)

    rows = _rows(wb["RACI - Deliverables"])
    header, row = rows[0], rows[1]
    cell = lambda name: row[header.index(name)]
    assert cell("Marcus Webb") == "(R)" and cell("Sarah Okonkwo") == "A"  # inferred vs assigned
    task_rows = _rows(wb["RACI - Tasks"])
    assert task_rows[1][task_rows[0].index("Jordan Mills")] == "R/A"


def test_a_warning_row_says_why(test_project):
    d = create_deliverable(test_project, "Sign-off")
    set_deliverable_raci(str(d["deliverable_id"]), "Marcus Webb", ["R"])  # started, nobody Accountable
    rows = _rows(_wb(test_project)["RACI - Deliverables"])
    assert rows[1][-1] == "No Accountable yet"


def test_text_that_looks_like_a_formula_stays_text(test_project):
    create_raid_item(test_project, "risk", '=HYPERLINK("http://evil.example","click")', description="=1+1")
    create_deliverable(test_project, "+cmd|' /C calc'!A0", owner="@SUM(A1)")
    wb = _wb(test_project)
    title_cell = wb["RAID log"].cell(row=2, column=2)
    assert title_cell.data_type != "f" and title_cell.value == '=HYPERLINK("http://evil.example","click")'
    assert wb["RAID log"].cell(row=2, column=3).data_type != "f"
    assert wb["Deliverables"].cell(row=2, column=2).data_type != "f"


def test_items_waiting_for_review_are_left_out_but_counted(test_project):
    create_raid_item(test_project, "risk", "Real risk")
    create_raid_item(test_project, "risk", "Unreviewed AI risk", review_state="suggested", origin="agent")
    create_deliverable(test_project, "Unreviewed deliverable", review_state="suggested", origin="agent")
    wb = _wb(test_project)
    assert [r[1] for r in _rows(wb["RAID log"])[1:]] == ["Real risk"]
    assert _rows(wb["Deliverables"])[1:] == []
    summary = dict((r[0], r[1]) for r in _rows(wb["Summary"]) if r[0])
    assert summary["Waiting for review (NOT in this file)"] == 2


def test_an_empty_project_still_exports_a_valid_workbook(test_project):
    wb = _wb(test_project)
    assert wb.sheetnames[0] == "Summary" and _rows(wb["RAID log"]) == [["Type", "Title", "Description", "Owner", "Status",
                                                                       "Probability", "Impact", "Review by", "Source", "Created"]]


def test_export_endpoint(test_project):
    create_raid_item(test_project, "risk", "Endpoint risk")
    resp = client.get(f"/projects/{test_project}/registers/export")
    assert resp.status_code == 200
    assert resp.headers["content-type"].startswith("application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")
    assert resp.headers["content-disposition"] == f'attachment; filename="{test_project}-registers.xlsx"'
    assert "RAID log" in load_workbook(io.BytesIO(resp.content)).sheetnames


def test_the_download_filename_cannot_inject_header_content():
    resp = client.get('/projects/evil"; x=y/registers/export')
    disp = resp.headers.get("content-disposition", "")
    assert '"' not in disp.split("filename=")[-1].strip('"') and "\n" not in disp
