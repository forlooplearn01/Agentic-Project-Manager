from pathlib import Path

from apps.agent.react_loop import run_chat_loop
from apps.agent.tool_registry import CHAT_TOOL_SCHEMAS, CHAT_TOOL_IMPLS
from apps.logger import get_logger

logger = get_logger("CHAT")

_MANIFEST_PATH = Path(__file__).parent / "manifests" / "chat.md"
_SYSTEM_PROMPT = _MANIFEST_PATH.read_text()


def run_chat_agent(history: list, user_message: str) -> tuple:
    """history: prior turns as [{role: 'user'|'assistant', content}, ...], oldest first.
    Returns (reply_text: str, trace: list)."""
    return run_chat_loop(
        agent_name="CHAT",
        system_prompt=_SYSTEM_PROMPT,
        history=history,
        user_message=user_message,
        tool_schemas=CHAT_TOOL_SCHEMAS,
        tool_impls=CHAT_TOOL_IMPLS,
    )
