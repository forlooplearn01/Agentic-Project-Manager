import json

from apps.llm import client
from apps.logger import get_logger

logger = get_logger("REACT_LOOP")


def _execute_tool_calls(agent_name: str, tool_calls, tool_impls: dict, trace: list, messages: list) -> int:
    """Runs one batch of tool calls, appending a {tool, args, result} entry to trace and
    a matching 'tool' role message for each - shared by run_react_loop and run_chat_loop
    since the dispatch mechanics (parse args, invoke, stringify, log) never differ between
    a single-shot document run and a multi-turn chat turn. Returns how many calls ran."""
    for call in tool_calls:
        fn_name = call.function.name
        try:
            fn_args = json.loads(call.function.arguments or "{}")
        except json.JSONDecodeError:
            fn_args = {}

        impl = tool_impls.get(fn_name)
        if impl is None:
            result_str = json.dumps({"error": f"Unknown tool '{fn_name}'"})
        else:
            try:
                result_str = impl(**fn_args)
                if not isinstance(result_str, str):
                    result_str = json.dumps(result_str, default=str)
            except Exception as e:
                logger.error(f"[{agent_name}] tool '{fn_name}' failed: {e}")
                result_str = json.dumps({"error": str(e)})

        logger.info(f"[{agent_name}] tool_call {fn_name}({fn_args}) -> {result_str[:200]}")
        trace.append({"tool": fn_name, "args": fn_args, "result": result_str})

        messages.append({
            "role": "tool",
            "tool_call_id": call.id,
            "content": result_str,
        })

    return len(tool_calls)


def run_react_loop(
    agent_name: str,
    system_prompt: str,
    user_message: str,
    tool_schemas: list,
    tool_impls: dict,
    max_tool_calls: int = 5,
    model: str = "gpt-4o-mini",
):
    """Shared ReAct loop for the Planning / Sync-Reporting / Meeting Coordinator agents.

    Each agent supplies its own system prompt and a scoped set of tools - tool_schemas
    (OpenAI function-calling format) and tool_impls (name -> python callable) are never
    shared wholesale between agents, only the loop mechanics are shared.

    Returns (final_result: dict, trace: list) where trace is [{tool, args, result}, ...]
    for the dashboard's run/trace panel. The model's final (non-tool-call) response must
    be JSON - this is enforced via response_format.
    """
    messages = [
        {"role": "system", "content": system_prompt},
        {"role": "user", "content": user_message},
    ]
    trace = []
    tool_calls_made = 0

    while True:
        response = client.chat.completions.create(
            model=model,
            messages=messages,
            tools=tool_schemas if tool_calls_made < max_tool_calls else None,
            temperature=0,
        )
        choice = response.choices[0].message

        if choice.tool_calls and tool_calls_made < max_tool_calls:
            messages.append(choice.model_dump(exclude_none=True))

            # Every tool_call_id in the assistant message above must get a matching
            # tool response message, or the next API call is rejected - so once a
            # batch starts, respond to all calls in it even if that pushes the count
            # past max_tool_calls. The cap only decides whether `tools` is offered on
            # the *next* iteration, never whether a call already made gets a reply.
            tool_calls_made += _execute_tool_calls(agent_name, choice.tool_calls, tool_impls, trace, messages)
            continue

        # Final response - no more tool calls, or budget exhausted. Force a JSON summary.
        content = choice.content
        if content:
            try:
                return json.loads(content), trace
            except json.JSONDecodeError:
                pass

        # Model didn't return parseable JSON (or made no final statement) - force one more
        # call with tools disabled and an explicit instruction to synthesize what it has.
        messages.append({
            "role": "user",
            "content": "Tool budget reached or no further tool calls needed. "
                        "Return your final answer now as a single JSON object, no other text.",
        })
        final = client.chat.completions.create(
            model=model,
            messages=messages,
            temperature=0,
            response_format={"type": "json_object"},
        )
        try:
            return json.loads(final.choices[0].message.content), trace
        except json.JSONDecodeError:
            logger.error(f"[{agent_name}] failed to get parseable JSON after forced synthesis")
            return {}, trace


def run_chat_loop(
    agent_name: str,
    system_prompt: str,
    history: list,
    user_message: str,
    tool_schemas: list,
    tool_impls: dict,
    max_tool_calls: int = 5,
    model: str = "gpt-4o-mini",
):
    """Chat variant of the ReAct loop: accepts prior conversation turns (history, a list of
    {role, content} dicts) and returns a free-text reply instead of forcing JSON - a chat
    answer is prose for a human, not a structured result for the pipeline to consume.
    Shares tool-dispatch mechanics with run_react_loop via _execute_tool_calls.

    Returns (reply_text: str, trace: list).
    """
    messages = [{"role": "system", "content": system_prompt}, *history, {"role": "user", "content": user_message}]
    trace = []
    tool_calls_made = 0

    while True:
        response = client.chat.completions.create(
            model=model,
            messages=messages,
            tools=tool_schemas if tool_calls_made < max_tool_calls else None,
            temperature=0,
        )
        choice = response.choices[0].message

        if choice.tool_calls and tool_calls_made < max_tool_calls:
            messages.append(choice.model_dump(exclude_none=True))
            tool_calls_made += _execute_tool_calls(agent_name, choice.tool_calls, tool_impls, trace, messages)
            continue

        if choice.content:
            return choice.content, trace

        # Model made no final statement (rare - usually only after the tool budget cuts
        # off mid-reasoning). Force one more call with tools disabled.
        messages.append({
            "role": "user",
            "content": "Tool budget reached or no further tool calls needed. Answer now in "
                        "plain text using what you've already found.",
        })
        final = client.chat.completions.create(model=model, messages=messages, temperature=0)
        return final.choices[0].message.content or "I wasn't able to find an answer to that.", trace
