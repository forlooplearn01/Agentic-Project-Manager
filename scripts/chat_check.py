"""Asks each demo project's 10 AI Chat questions (datasets/golden/<id>/questions.json) to the real chat and checks the answers.

  python -m scripts.chat_check all          every project
  python -m scripts.chat_check halvane -v   one project, printing every answer
  python -m scripts.chat_check all --runs 5   each question 5 times: shows how stable the answers are (a fresh chat every time)

An answer passes when it contains every expect_all text and at least one expect_any text. With --runs, a question is stable when at least
--min of its runs (default 1.0, all of them) pass. Chat history is wiped afterwards."""
import json
import sys
import time
from concurrent.futures import ThreadPoolExecutor
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


def option(argv, name, default, cast):
    return cast(argv[argv.index(name) + 1]) if name in argv else default


def main(argv):
    verbose = "-v" in argv
    runs, minimum = option(argv, "--runs", 1, int), option(argv, "--min", 1.0, float)
    ids = [a for i, a in enumerate(argv) if not a.startswith("-") and not (i and argv[i - 1] in ("--runs", "--min"))] or ["all"]
    if ids == ["all"]:
        ids = sorted(p.name for p in golden.GOLDEN_DIR.iterdir() if (p / "questions.json").exists())
    total = ok = 0
    with ThreadPoolExecutor(max_workers=4) as pool:
        for pid in ids:
            qs = json.loads((golden.GOLDEN_DIR / pid / "questions.json").read_text())
            jobs = [[pool.submit(ask, q["q"]) for _ in range(runs)] for q in qs]
            good = 0
            print(f"\n{pid}")
            for q, futures in zip(qs, jobs):
                replies = [f.result() for f in futures]
                passed = [passes(r["text"], q) for r in replies]
                rate = sum(passed) / runs
                stable = rate >= minimum
                good += stable
                label = "PASS" if stable else "FAIL"
                print(f"  {label}  {sum(passed)}/{runs}  {q['q'][:90]}")
                if verbose or not stable:
                    for r, p in zip(replies, passed):
                        if verbose or not p:
                            tools = [t.get("tool") or t.get("name") for t in r.get("trace", []) if isinstance(t, dict)]
                            print(f"        {'ok  ' if p else 'MISS'} expected {q['expect_all'] + q['expect_any']}; tools {tools}\n        {r['text'][:300].replace(chr(10), ' ')}")
            print(f"  -> {good}/{len(qs)} questions stable")
            total += len(qs)
            ok += good
    conn = get_connection()
    with conn.cursor() as cur:
        cur.execute("DELETE FROM chat_messages")
    conn.commit()
    conn.close()
    print(f"\n{ok}/{total} questions passed" + (f" in at least {minimum:.0%} of {runs} runs" if runs > 1 else ""))
    raise SystemExit(0 if ok == total else 1)


main(sys.argv[1:])
