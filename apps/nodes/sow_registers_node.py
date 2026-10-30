import hashlib

from apps.db.db import import_document_raid_items, upsert_document_deliverables
from apps.logger import get_logger
from apps.state import AgentState
from apps.utils.deliverables import parse_deliverables_section
from apps.utils.sow_sections import parse_raid_sections

logger = get_logger("SOW_REGISTERS")


def task_sow_registers_node(state: AgentState) -> AgentState:
    """Fills the registers from the explicit, numbered lists a SOW sometimes contains: the Deliverables section (-> the
    Deliverables register) and the Assumptions / Dependencies lists (-> the RAID log). A cheap, optional bonus: no LLM,
    a no-op for meeting minutes and for SOWs without such lists (the common case for real ones), idempotent on re-runs,
    and each part is isolated so neither can fail a run or block the other."""
    if state.get("document_type") != "sow":
        return state
    project_id = state.get("project_id", "default")
    raw = state.get("raw_input", "")

    try:
        deliverables = parse_deliverables_section(raw)
        if deliverables:
            created = upsert_document_deliverables(project_id, deliverables)
            logger.info(f"Deliverables: parsed {len(deliverables)} from the SOW's numbered section, {len(created)} new")
    except Exception as e:
        logger.error(f"Deliverables import skipped, run continues: {e}")

    try:
        raid = parse_raid_sections(raw)
        if raid:
            doc_key = hashlib.sha1(raw.encode()).hexdigest()[:10]
            created = import_document_raid_items(project_id, doc_key, raid)
            logger.info(f"RAID: parsed {len(raid)} assumptions/dependencies from the SOW's numbered lists, {len(created)} new")
    except Exception as e:
        logger.error(f"RAID import skipped, run continues: {e}")
    return state
