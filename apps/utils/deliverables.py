"""Deterministic parse of a numbered DELIVERABLES section, for the rare SOW that has one.

Most real SOWs do not (0 of 5 real-style inputs checked - see DECISIONS.md), so this is a cheap bonus, never the
main path: it returns [] unless it finds a section headed "Deliverables" with at least two dotted-number items
(4.1, 4.2, ...). Each item may be followed by "Owner:" and "Deadline:" lines. Anything it cannot find is simply
not invented."""
import re

_HEADING = re.compile(r"^\s*(?:\d+\.?\s+)?deliverables?\s*:?\s*$", re.IGNORECASE)
_TOP_LEVEL_HEADING = re.compile(r"^\s*\d+\.\s+\S")  # "5. PROJECT TIMELINE" - not "4.1 Item" (digit right after the dot)
_SEPARATOR = re.compile(r"^\s*[━=_\-─]{5,}\s*$")
_ITEM = re.compile(r"^\s*(\d+\.\d+)\s+(\S.*?)\s*$")
_OWNER = re.compile(r"^\s*owners?\s*:\s*(.+?)\s*$", re.IGNORECASE)
_DEADLINE = re.compile(r"^\s*(?:deadline|due(?:\s+date)?)\s*:\s*(.+?)\s*$", re.IGNORECASE)


def normalize_owners(raw: str) -> str:
    """'Jordan Mills (AI Engineer, QuantumEdge) + Priya Nair (VMware Engineer, NovaTech)' -> 'Jordan Mills, Priya Nair'."""
    without_roles = re.sub(r"\([^)]*\)", "", raw)
    names = [n.strip() for n in re.split(r"\+|&|;|,|\band\b", without_roles) if n.strip()]
    return ", ".join(names)


def number_sort_key(number):
    """'4.2' < '4.10'; non-numeric or missing numbers sort last."""
    if number and re.fullmatch(r"\d+(\.\d+)*", number):
        return (0, tuple(int(p) for p in number.split(".")))
    return (1, ())


def parse_deliverables_section(text: str) -> list:
    lines = (text or "").splitlines()
    start = next((i for i, line in enumerate(lines) if _HEADING.match(line)), None)
    if start is None:
        return []

    items, current = [], None
    for line in lines[start + 1:]:
        if _TOP_LEVEL_HEADING.match(line) or _SEPARATOR.match(line):
            break
        item = _ITEM.match(line)
        if item:
            current = {"number": item.group(1), "title": item.group(2), "owner": None, "deadline": None}
            items.append(current)
            continue
        if current is None:
            continue
        owner, deadline = _OWNER.match(line), _DEADLINE.match(line)
        if owner:
            current["owner"] = normalize_owners(owner.group(1)) or None
        elif deadline:
            current["deadline"] = deadline.group(1)
    return items if len(items) >= 2 else []
