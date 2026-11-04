from pathlib import Path

from apps.agent.react_loop import run_chat_loop
from apps.utils import clock
from apps.agent.tool_registry import CHAT_TOOL_SCHEMAS, CHAT_TOOL_IMPLS
from apps.logger import get_logger

logger = get_logger("CHAT")

_MANIFEST_PATH = Path(__file__).parent / "manifests" / "chat.md"
_SYSTEM_PROMPT = _MANIFEST_PATH.read_text()


def _system_prompt() -> str:
    """The manifest plus today's date (the app's own, which a demo can freeze): without it the model guesses a year and asks about the wrong dates."""
    today = clock.today()
    return (f"{_SYSTEM_PROMPT}\n\n# Today\n\nToday is {today:%A} {today.isoformat()}. Use this year for any date the user gives without one "
            f"(\"last week of October\", \"next Friday\"). Weeks follow each project's own week start day (usually Monday); the availability tool "
            f"widens a window to whole weeks and says which day they start on. Never use a date from an earlier year unless the user says the year.")


def run_chat_agent(history: list, user_message: str) -> tuple:
    """history: prior turns as [{role: 'user'|'assistant', content}, ...], oldest first.
    Returns (reply_text: str, trace: list)."""
    return run_chat_loop(
        agent_name="CHAT",
        system_prompt=_system_prompt(),
        history=history,
        user_message=user_message,
        tool_schemas=CHAT_TOOL_SCHEMAS,
        tool_impls=CHAT_TOOL_IMPLS,
    )
