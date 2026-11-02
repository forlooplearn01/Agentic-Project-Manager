"""Asks each demo project's 10 AI Chat questions (datasets/golden/<id>/questions.json) to the real chat and checks the answers.

  python -m scripts.chat_check all          every project
  python -m scripts.chat_check halvane -v   one project, printing every answer

An answer passes when it contains every expect_all text and at least one expect_any text. Chat history is wiped afterwards."""
import json
import sys
import time
import urllib.request
import uuid

from apps.db.db import get_connection
from apps.utils import golden

API = "http://localhost:8000"


def ask(question: str) -> dict:
    req = urllib.request.Request(API + "/chat", data=json.dumps({"session_id": f"check-{uuid.uuid4()}", "message": question}).encode(), method="POST",
                                 headers={"content-type": "application/json"})
    with urllib.request.urlopen(req, timeout=300) as r:
        return json.loads(r.read())


def passes(answer: str, q: dict) -> bool:
    text = answer.lower()
    return all(w.lower() in text for w in q["expect_all"]) and (not q["expect_any"] or any(w.lower() in text for w in q["expect_any"]))


def main(argv):
    verbose = "-v" in argv
    ids = [a for a in argv if not a.startswith("-")] or ["all"]
    if ids == ["all"]:
        ids = sorted(p.name for p in golden.GOLDEN_DIR.iterdir() if (p / "questions.json").exists())
    total = ok = 0
    for pid in ids:
        qs = json.loads((golden.GOLDEN_DIR / pid / "questions.json").read_text())
        good = 0
        print(f"\n{pid}")
        for q in qs:
            t0 = time.time()
            r = ask(q["q"])
            tools = [t.get("tool") or t.get("name") for t in r.get("trace", []) if isinstance(t, dict)]
            passed = passes(r["text"], q)
            good += passed
            print(f"  {'PASS' if passed else 'FAIL'}  ({time.time() - t0:3.0f}s) {q['q'][:80]}")
            if verbose or not passed:
                print(f"        expected {q['expect_all'] + q['expect_any']}; tools {tools}\n        {r['text'][:420].replace(chr(10), ' ')}")
        print(f"  -> {good}/{len(qs)}")
        total += len(qs)
        ok += good
    conn = get_connection()
    with conn.cursor() as cur:
        cur.execute("DELETE FROM chat_messages")
    conn.commit()
    conn.close()
    print(f"\n{ok}/{total} answers passed")
    raise SystemExit(0 if ok == total else 1)


main(sys.argv[1:])
