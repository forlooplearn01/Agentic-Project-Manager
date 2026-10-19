"""run_react_loop must always return a JSON result to its caller (the pipeline nodes
expect a dict). If the model's first final answer isn't parseable JSON, the loop should
force one more call with tools disabled and an explicit instruction to return JSON, not
just fail outright."""
from apps.agent.react_loop import run_react_loop
from tests.conftest import make_text_response, make_final_json_response


def test_non_json_first_answer_triggers_a_forced_retry(scripted_llm):
    scripted_llm([
        make_text_response("I've reviewed everything and there's nothing more to add here."),
        make_final_json_response({"followup_warranted": False, "reason": "no action needed"}),
    ])

    result, trace = run_react_loop(
        agent_name="test_agent",
        system_prompt="test",
        user_message="test",
        tool_schemas=[],
        tool_impls={},
        max_tool_calls=4,
    )

    assert result == {"followup_warranted": False, "reason": "no action needed"}
    assert trace == []  # no tool calls were made in this scenario


def test_still_unparseable_after_retry_returns_empty_dict_not_a_crash(scripted_llm):
    scripted_llm([
        make_text_response("not json"),
        make_text_response("still not json"),
    ])

    result, trace = run_react_loop(
        agent_name="test_agent",
        system_prompt="test",
        user_message="test",
        tool_schemas=[],
        tool_impls={},
        max_tool_calls=4,
    )

    assert result == {}
    assert trace == []
