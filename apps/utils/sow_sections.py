"""Deterministic parse of numbered ASSUMPTIONS and DEPENDENCIES lists in a SOW, for RAID.

Same philosophy as deliverables.py: only clean numbered lists ("7.1 text" or "1. text" under a heading literally
called Assumptions / Dependencies) are read; anything messier (bullets, PDF extractions with the numbers split from
their text) yields nothing rather than a guess. An item is the first line of text after its number."""
import re

_HEADING = re.compile(r"^\s*(?:\d+\.?\s+)?(assumptions?|dependenc(?:y|ies))\s*:?\s*$", re.IGNORECASE)
_ITEM = re.compile(r"^\s*(\d+(?:\.\d+)*)[.)]?\s+(\S.*?)\s*$")
_SEPARATOR = re.compile(r"^\s*[━=_\-─]{5,}\s*$")
_TYPE = {"assumption": "assumption", "assumptions": "assumption", "dependency": "dependency", "dependencies": "dependency"}


def _is_caps_heading(line: str) -> bool:
    """'OUT OF SCOPE', '8. DEPENDENCIES': an all-caps line (optionally numbered), at least 4 letters so a short
    all-caps ITEM such as '1. API' is not mistaken for a heading."""
    body = re.sub(r"^\s*\d+(?:\.\d+)*[.)]?\s+", "", line).strip()
    letters = [c for c in body if c.isalpha()]
    return len(letters) >= 4 and all(c.isupper() for c in letters) and len(body) <= 60


def _ends_list(line: str) -> bool:
    """Does this non-item line start a new section? A separator, another Assumptions/Dependencies heading, an all-caps
    heading, or a short unnumbered line with no sentence punctuation (a title-case heading such as 'Out of Scope')."""
    if _SEPARATOR.match(line) or _HEADING.match(line) or _is_caps_heading(line):
        return True
    stripped = line.strip()
    return not _ITEM.match(line) and len(stripped) <= 50 and not stripped.endswith((".", ";", ",", ":")) and not stripped.startswith(("-", "•", "*"))


def parse_raid_sections(text: str) -> list:
    """-> [{"item_type": "assumption"|"dependency", "number": "7.1", "text": "..."}], in document order."""
    lines = (text or "").splitlines()
    out = []
    for start, line in enumerate(lines):
        heading = _HEADING.match(line)
        if not heading:
            continue
        item_type = _TYPE[heading.group(1).lower()]
        found = []
        for nxt in lines[start + 1:]:
            if not nxt.strip():
                continue
            item = _ITEM.match(nxt)
            if item and not _is_caps_heading(nxt):
                found.append({"item_type": item_type, "number": item.group(1), "text": item.group(2)})
            elif _ends_list(nxt):
                break
        if len(found) >= 2:  # a lone numbered line is not a list
            out.extend(found)
    return out


def title_and_description(text: str, limit: int = 110) -> tuple:
    """A readable one-line title for the table; the full sentence(s) go to the description when it was shortened."""
    first = re.split(r"(?<=[.!?])\s+", text.strip(), maxsplit=1)[0]
    if len(first) <= limit and first == text.strip():
        return first, None
    title = first if len(first) <= limit else first[: limit - 1].rstrip() + "…"
    return title, text.strip()
