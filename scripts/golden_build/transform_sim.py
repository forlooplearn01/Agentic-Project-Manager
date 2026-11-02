"""Turns the VMware simulation (datasets/simulation-ai-x-vmware) into the source files of the demo project `tarnwood`, without touching the
originals: fictional company names, the three real people in the lead roles (Sarah -> Nirmal, Jordan -> Rucheek, Marcus -> Ram), and every
date moved forward by exactly 25 weeks (175 days, so a Wednesday stays a Wednesday and "by Friday" stays true).

    python -m scripts.golden_build.transform_sim
Writes datasets/golden-src/tarnwood/ (sow.txt, meeting-01..08.txt, expected-output-*.txt)."""
import re
from datetime import date, timedelta
from pathlib import Path

SRC = Path(__file__).resolve().parents[2] / "datasets" / "simulation-ai-x-vmware"
OUT = Path(__file__).resolve().parents[2] / "datasets" / "golden-src" / "tarnwood"
SHIFT = timedelta(days=175)
MONTHS = ["January", "February", "March", "April", "May", "June", "July", "August", "September", "October", "November", "December"]
DATE = re.compile(r"\b(" + "|".join(MONTHS) + r") (\d{1,2})(?:, (\d{4}))?\b")
# longest first, so "NovaTech Solutions Inc." is not cut short by "NovaTech"
WORDS = [
    ("QuantumEdge AI Consulting LLC", "Forloop"), ("QuantumEdge AI Consulting", "Forloop"), ("QuantumEdge", "Forloop"),
    ("NovaTech Solutions Inc.", "Tarnwood Industrial"), ("NovaTech Solutions", "Tarnwood Industrial"), ("NovaTech-Solutions", "Tarnwood-Industrial"),
    ("finance@novatech.com", "finance@tarnwood.example"), ("novatech.com", "tarnwood.example"), ("NovaTech", "Tarnwood"),
    ("Sarah Okonkwo", "Nirmal Babu"), ("Jordan Mills", "Rucheek"), ("Marcus Webb", "Ram Anandkumar"),
]
FIRST = [(r"\bSarah\b", "Nirmal"), (r"\bJordan\b", "Rucheek"), (r"\bMarcus\b", "Ram")]


def shift_dates(text: str) -> str:
    def one(m):
        month, day, year = MONTHS.index(m.group(1)) + 1, int(m.group(2)), m.group(3)
        d = date(int(year) if year else 2026, month, day) + SHIFT
        return f"{MONTHS[d.month - 1]} {d.day}" + (f", {d.year}" if year else "")
    return DATE.sub(one, text)


def transform(text: str) -> str:
    for old, new in WORDS:
        text = text.replace(old, new)
    for pat, new in FIRST:
        text = re.sub(pat, new, text)
    text = re.sub(r"Ram Anandkumar( +\| +)Business Analyst", r"Ram Anandkumar\1Technical Director", text)
    text = text.replace("Ram Anandkumar (Business Analyst", "Ram Anandkumar (Technical Director")
    text = text.replace("Project Codename: AI-X-VMware", "Project Codename: Tarnwood AI Analytics")
    return shift_dates(text)


def main():
    OUT.mkdir(parents=True, exist_ok=True)
    files = [SRC / "sow.txt", *sorted(SRC.glob("meeting-0*.txt")), *sorted((SRC / "expected-outputs").glob("expected-output-*.txt"))]
    for f in files:
        (OUT / f.name).write_text(transform(f.read_text()))
    print(f"wrote {len(files)} files to {OUT}")


if __name__ == "__main__":
    main()
