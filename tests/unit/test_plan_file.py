"""The plan file: reading ids / status, and writing approved changes back safely (apps/utils/plan_file.py). Pure file work, no database."""
import shutil
from pathlib import Path

import pytest
from openpyxl import Workbook, load_workbook

from apps.utils.hours_import import map_status, parse_plan
from apps.utils.plan_file import Blocked, current_plan_file, is_open_elsewhere, merge_into_workbook, next_ref, plan_files, ref_key

DEMO = Path(__file__).resolve().parents[2] / "datasets" / "hours" / "forloop" / "forloop-plan.xlsx"


def demo_copy(tmp_path):
    dst = tmp_path / "forloop-plan.xlsx"
    shutil.copy(DEMO, dst)
    return dst, parse_plan(dst.name, dst.read_bytes())["layout"]


def rows_of(path, sheet="Plan"):
    return [list(r) for r in load_workbook(path)[sheet].iter_rows(values_only=True)]


# ---------------------------------------------------------------- reading

def test_the_plan_parser_finds_ids_status_and_where_the_tasks_sit():
    r = parse_plan(DEMO.name, DEMO.read_bytes())
    assert r["layout"]["sheet"] == "Plan" and r["layout"]["cols"]["task_ref"] == 0 and r["layout"]["cols"]["task"] == 2
    assert r["layout"]["headers"]["task_ref"] == "WBS"
    assert r["rows"][0]["task_ref"] == "1.0" and len(r["rows"]) == 22 and r["rows"][0]["status"] == "completed" and r["layout"]["cols"]["status"] == 8


def test_a_task_id_column_is_not_mistaken_for_the_task_name():
    wb = Workbook()
    ws = wb.active
    ws.append(["Task ID", "Task Name", "Owner", "Est hours", "Status", "% Complete"])
    ws.append(["T-001", "Build login", "Ana", 12, "Done", 1])
    ws.append(["T-002", "Build search", "Bo", 8, "In progress", 0.4])
    import io
    buf = io.BytesIO()
    wb.save(buf)
    r = parse_plan("p.xlsx", buf.getvalue())
    assert [x["label"] for x in r["rows"]] == ["Build login", "Build search"] and [x["task_ref"] for x in r["rows"]] == ["T-001", "T-002"]
    assert [x["status"] for x in r["rows"]] == ["completed", "in_progress"]


@pytest.mark.parametrize("raw,expected", [("Done", "completed"), ("In progress", "in_progress"), ("not started", "not_started"), ("40%", "in_progress"),
                                          (0, "not_started"), (1, "completed"), (0.4, "in_progress"), ("Blocked - waiting on VPN", "blocked"),
                                          ("on hold", "on_hold"), ("100", "completed"), ("", None), (None, None), ("who knows", None)])
def test_status_words_and_percentages(raw, expected):
    assert map_status(raw) == expected


def test_ids_and_the_next_id():
    assert ref_key(1.0) == ref_key("1.0") == ref_key("1") == "1" and ref_key(" T-1 ") == "t-1"
    assert next_ref(["1.0", "22.0", "3.0"]) == "23.0" and next_ref(["T-014", "T-009"]) == "T-015" and next_ref(["7", "8"]) == "9"
    assert next_ref(["alpha", "beta"]) is None and next_ref([]) is None


def test_the_current_plan_is_the_newest_file_and_locks_are_seen(tmp_path):
    a = tmp_path / "plan-v1.xlsx"
    a.write_bytes(b"x")
    (tmp_path / "~$plan-v1.xlsx").write_bytes(b"lock")
    (tmp_path / ".hidden.xlsx").write_bytes(b"x")
    (tmp_path / "notes.docx").write_bytes(b"x")
    (tmp_path / "versions").mkdir()
    import os
    b = tmp_path / "plan-v2.xlsx"
    b.write_bytes(b"y")
    os.utime(a, (1, 1))
    assert [p.name for p in plan_files(tmp_path)] == ["plan-v1.xlsx", "plan-v2.xlsx"] and current_plan_file(tmp_path) == b
    assert is_open_elsewhere(a) and not is_open_elsewhere(b)
    assert not plan_files(tmp_path / "missing") and current_plan_file(tmp_path / "missing") is None


# ---------------------------------------------------------------- writing

def test_a_new_task_is_appended_after_the_last_task_and_the_total_moves_down(tmp_path):
    path, layout = demo_copy(tmp_path)
    before = rows_of(path)
    res = merge_into_workbook(path, layout, [{"op": "add", "task": "Prepare the data migration plan", "owner": "Marcus Chen", "phase": "Phase 2",
                                              "start_week": 6, "finish_week": 7, "estimate": 20, "status": "not_started", "note": "Added from meeting 29 Oct"}],
                              backup_dir=tmp_path / "versions")
    assert res["added"] == [{"index": 0, "ref": "23.0", "row": 28}] and res["skipped"] == []
    after = rows_of(path)
    assert after[27][:8] == ["23.0", "Phase 2", "Prepare the data migration plan", "Marcus Chen", 6, 7, 20, "Added from meeting 29 Oct"] and after[27][8] == "Not started"
    assert [r[:8] for r in after[:27]] == [r[:8] for r in before[:27]]
    assert after[29][2] == "Total estimated hours" and after[29][6] == 632  # 612 + 20: the plain-number total follows the tasks
    assert len(list((tmp_path / "versions").glob("forloop-plan-*.xlsx"))) == 1  # the original was kept first
    assert not any(p.name.startswith(".") for p in tmp_path.iterdir())  # no temp file left behind
    again = parse_plan(path.name, path.read_bytes())
    assert len(again["rows"]) == 23 and again["rows"][-1]["task_ref"] == "23.0" and again["rows"][-1]["status"] == "not_started"
    assert again["budget_hours"] == 700  # the Budget sheet is untouched


def test_changes_write_only_the_changed_cells_and_add_a_missing_status_column(tmp_path):
    path, layout = demo_copy(tmp_path)
    before = rows_of(path)
    res = merge_into_workbook(path, layout, [
        {"op": "change", "ref": "2.0", "task": "x", "changes": {"owner": "Priya Patel", "finish_week": 4}},
        {"op": "change", "ref": None, "task": "Project management and status reporting", "changes": {"status": "in_progress"}},
        {"op": "change", "ref": "99.0", "task": "Nothing like it", "changes": {"owner": "Bo"}},
    ])
    assert res["changed"] == [0, 1] and res["skipped"] == [{"index": 2, "reason": "The task is not in the plan file any more."}]
    after = rows_of(path)
    assert after[6][3] == "Priya Patel" and after[6][5] == 4 and after[6][2] == before[6][2] and after[6][6] == before[6][6]
    assert after[4][8] == "Status" and after[26][8] == "In progress"  # the column was added beside the last header
    changed_cells = [(i, j) for i, (a, b) in enumerate(zip(after, before)) for j in range(len(b)) if a[j] != b[j]]
    assert sorted(changed_cells) == [(6, 3), (6, 5)]


def test_the_teams_own_status_words_are_reused(tmp_path):
    wb = Workbook()
    ws = wb.active
    ws.append(["ID", "Task", "Est hours", "Status"])
    ws.append([1, "One", 5, "Done"])
    ws.append([2, "Two", 5, "WIP"])
    path = tmp_path / "plan.xlsx"
    wb.save(path)
    layout = parse_plan("plan.xlsx", path.read_bytes())["layout"]
    merge_into_workbook(path, layout, [{"op": "change", "ref": "2", "task": "Two", "changes": {"status": "completed"}},
                                       {"op": "add", "task": "Three", "estimate": 3, "status": "in_progress"}])
    rows = rows_of(path, "Sheet")
    assert rows[2][3] == "Done" and rows[3] == [3, "Three", 3, "WIP"]  # numeric ids stay numeric; "Done" and "WIP" are theirs


def test_a_sum_over_the_table_is_extended(tmp_path):
    wb = Workbook()
    ws = wb.active
    ws.append(["Task", "Est hours"])
    ws.append(["One", 5])
    ws.append(["Two", 7])
    ws.append([])
    ws.append(["Total", "=SUM(B2:B3)"])
    path = tmp_path / "plan.xlsx"
    wb.save(path)
    layout = parse_plan("plan.xlsx", path.read_bytes())["layout"]
    merge_into_workbook(path, layout, [{"op": "add", "task": "Three", "estimate": 4}])
    ws2 = load_workbook(path).active
    assert [c.value for c in ws2[4]] == ["Three", 4] and ws2["B6"].value == "=SUM(B2:B4)" and ws2["A6"].value == "Total"


@pytest.mark.parametrize("build,why", [
    (lambda ws: ws.append(["Total", "=B2+B3+C9"]), "single cell"),
    (lambda ws: ws.merge_cells("A9:B9"), "merged"),
])
def test_unsafe_layouts_block_the_write_and_leave_the_file_untouched(tmp_path, build, why):
    wb = Workbook()
    ws = wb.active
    ws.append(["Task", "Est hours"])
    ws.append(["One", 5])
    ws.append(["Two", 7])
    ws.append([])
    ws.append(["Total", 12])
    if why == "merged":
        ws["A9"] = "Notes below"
    build(ws)
    path = tmp_path / "plan.xlsx"
    wb.save(path)
    layout = parse_plan("plan.xlsx", path.read_bytes())["layout"]
    original = path.read_bytes()
    with pytest.raises(Blocked):
        merge_into_workbook(path, layout, [{"op": "add", "task": "Three", "estimate": 4}], backup_dir=tmp_path / "versions")
    assert path.read_bytes() == original and not (tmp_path / "versions").exists()


def test_a_csv_plan_and_a_moved_header_are_blocked(tmp_path):
    csv = tmp_path / "plan.csv"
    csv.write_text("Task,Est hours\nOne,5\n")
    with pytest.raises(Blocked, match="only .xlsx"):
        merge_into_workbook(csv, {"sheet": "plan.csv", "headers": {"task": "Task", "estimate": "Est hours"}}, [{"op": "add", "task": "Two"}])
    path, layout = demo_copy(tmp_path)
    layout = {**layout, "headers": {**layout["headers"], "task": "Renamed column"}}
    with pytest.raises(Blocked, match="header row"):
        merge_into_workbook(path, layout, [{"op": "add", "task": "Two"}])
    with pytest.raises(Blocked, match="sheet"):
        merge_into_workbook(path, {**layout, "sheet": "Gone"}, [{"op": "add", "task": "Two"}])
