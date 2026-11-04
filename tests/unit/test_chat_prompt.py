from apps.agent import chat


def test_the_chat_prompt_states_todays_date_and_the_week_rule(monkeypatch):
    monkeypatch.setenv("DEMO_TODAY", "2026-10-29")
    prompt = chat._system_prompt()
    assert prompt.startswith(chat._SYSTEM_PROMPT)
    assert "Today is Thursday 2026-10-29" in prompt
    assert "week start day" in prompt
