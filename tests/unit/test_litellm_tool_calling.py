"""Dependency smoke test. The suite mocks the LLM and the evals avoid `tools`, so nothing else exercises
litellm's real tool-calling code path - and a litellm/mcp version mismatch once broke exactly that path
(ImportError: cannot import name 'CacheableResult' from 'mcp.types') without any test noticing. litellm's
`mock_response` skips the network but still runs the library's own call path, including the tools branch."""
import litellm


def test_litellm_completion_with_tools_runs_offline():
    tools = [{"type": "function", "function": {"name": "noop", "description": "does nothing",
                                              "parameters": {"type": "object", "properties": {}}}}]
    response = litellm.completion(
        model="gpt-4o-mini", messages=[{"role": "user", "content": "hi"}], tools=tools, mock_response="ok"
    )
    assert response.choices[0].message.content == "ok"
