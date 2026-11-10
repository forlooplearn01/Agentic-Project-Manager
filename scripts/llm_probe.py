"""Does this model server do what the app needs? Run it before pointing the app at a new model.

    docker compose exec -e LLM_API_BASE=http://<host>:8000/v1 -e LLM_API_KEY=dummy api \
        python -m scripts.llm_probe --model openai/<served-model-name>

Goes through apps.llm.complete(), the same path the app uses, so a pass here means the app's own calls can work. Checks:
plain answer, JSON mode parsed with a bare json.loads (what the app does), tool calling, a fact buried in a long text, speed,
and a few requests at once. Prints PASS / FAIL per check; nothing is written anywhere.
"""
import argparse
import json
import os
import time
from concurrent.futures import ThreadPoolExecutor

os.environ.pop("LLM_FALLBACK_MODEL", None)  # test the server under test only: a fallback would let a dead server look like a pass

from apps.llm import complete  # noqa: E402

NEEDLE = "PLUM-4417"
TOOL = [{
    "type": "function",
    "function": {
        "name": "get_task_status",
        "description": "Look up the status of one task by its id.",
        "parameters": {"type": "object", "properties": {"task_id": {"type": "string"}}, "required": ["task_id"]},
    },
}]


def _msg(resp):
    return resp.choices[0].message


def _text(resp) -> str:
    return (_msg(resp).content or "").strip()


def check_plain(model):
    r = complete([{"role": "user", "content": "Reply with exactly one word: ready"}], model=model)
    t = _text(r)
    note = ""
    if getattr(_msg(r), "reasoning_content", None) or "<think>" in t:
        note = " (model emits reasoning text; the app does not strip it)"
    return bool(t), f"answered {t[:40]!r}{note}"


def check_json(model):
    r = complete([{"role": "user", "content": 'Return a JSON object with keys "name" and "hours" for: Ana logged 12 hours.'}],
                 response_format={"type": "json_object"}, model=model)
    raw = _text(r)
    try:
        data = json.loads(raw)  # bare, like the app
    except json.JSONDecodeError:
        return False, f"not parseable as-is: {raw[:80]!r}"
    return data.get("hours") == 12, f"parsed {data}"


def check_tools(model):
    r = complete([{"role": "user", "content": "What is the status of task T-12? Use the tool."}], tools=TOOL, model=model)
    calls = _msg(r).tool_calls or []
    if not calls:
        return False, f"no tool call; it answered in text: {_text(r)[:80]!r}"
    try:
        args = json.loads(calls[0].function.arguments)
    except json.JSONDecodeError:
        return False, f"tool arguments are not valid JSON: {calls[0].function.arguments[:80]!r}"
    return calls[0].function.name == "get_task_status" and args.get("task_id") == "T-12", f"called {calls[0].function.name}({args})"


def check_long(model, approx_tokens):
    filler = "The weekly status meeting covered routine items with no decisions recorded. " * 2
    n = max(1, approx_tokens * 4 // len(filler) // 2)
    text = filler * n + f"The vendor access code is {NEEDLE}. " + filler * n
    r = complete([{"role": "user", "content": text + "\n\nWhat is the vendor access code? Answer with the code only."}], model=model)
    return NEEDLE in _text(r), f"~{approx_tokens} tokens in, answered {_text(r)[:40]!r}"


def check_speed(model):
    t0 = time.time()
    r = complete([{"role": "user", "content": "Write about 150 words on why project plans slip."}], model=model)
    dt = time.time() - t0
    out = getattr(r.usage, "completion_tokens", 0) or 0
    return out > 0, f"{out} tokens in {dt:.1f}s = {out / dt:.1f} tokens/s"


def check_parallel(model, n=4):
    t0 = time.time()
    with ThreadPoolExecutor(n) as pool:
        results = list(pool.map(lambda _: _text(complete([{"role": "user", "content": "Reply with one word: ok"}], model=model)), range(n)))
    return all(results), f"{n} at once in {time.time() - t0:.1f}s"


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--model", required=True, help="the model name as LiteLLM should send it, e.g. openai/<served-model-name>")
    ap.add_argument("--ctx", type=int, nargs="*", default=[8000, 32000], help="approx token sizes for the long-text check")
    args = ap.parse_args()

    checks = [("plain answer", check_plain), ("JSON mode (bare json.loads)", check_json), ("tool calling", check_tools)]
    checks += [(f"long text (~{c} tokens)", lambda m, c=c: check_long(m, c)) for c in args.ctx]
    checks += [("speed", check_speed), ("parallel requests", check_parallel)]

    failed = 0
    for name, fn in checks:
        t0 = time.time()
        try:
            ok, detail = fn(args.model)
        except Exception as e:  # a server error is a result, not a crash
            ok, detail = False, f"{type(e).__name__}: {str(e)[:160]}"
        failed += not ok
        print(f"{'PASS' if ok else 'FAIL'}  {name:<30} {detail}  [{time.time() - t0:.1f}s]")
    print(f"\n{len(checks) - failed}/{len(checks)} passed")
    raise SystemExit(1 if failed else 0)


if __name__ == "__main__":
    main()
