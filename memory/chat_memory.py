import logging
from typing import List, Optional

from .config import memory_enabled, db_connection_string, is_postgres_connection

logger = logging.getLogger(__name__)


def _get_pg_history(audit_id: int):
    try:
        from langchain_postgres import PostgresChatMessageHistory

        conn = db_connection_string()
        if not (memory_enabled() and is_postgres_connection(conn)):
            return None

        session_id = f"audit-{audit_id}"
        history = PostgresChatMessageHistory(connection_string=conn, session_id=session_id)
        return history
    except Exception as e:
        logger.debug("PostgresChatMessageHistory unavailable: %s", e)
        return None


def save_message(audit_id: int, domain: str, role: str, content: str) -> None:
    """Persist a single message to memory when enabled; otherwise no-op here.

    Upstream callers must still write to AgentConversation for legacy/fallback paths.
    """
    history = _get_pg_history(audit_id)
    if not history:
        return

    # Map roles to message types supported by LangChain history
    try:
        if role == "user":
            history.add_user_message(f"[{domain}] {content}")
        elif role == "assistant":
            history.add_ai_message(f"[{domain}] {content}")
        else:  # system or other
            # LangChain history does not have a dedicated system method everywhere; store as ai tagged
            history.add_ai_message(f"[system/{domain}] {content}")
    except Exception as e:
        logger.warning("Failed to save message to PG history: %s", e)


def get_recent_messages(audit_id: int, limit: int = 12) -> List[str]:
    """Return the last N messages from persistent chat history formatted as strings.

    Falls back to empty list if memory disabled or unavailable. Legacy callers can merge with
    AgentConversation rows if desired.
    """
    history = _get_pg_history(audit_id)
    if not history:
        return []
    try:
        # PostgresChatMessageHistory stores messages in order;
        # .messages returns list of BaseMessage with .content and .type
        msgs = history.messages
        tail = msgs[-limit:] if limit and len(msgs) > limit else msgs
        out: List[str] = []
        for m in tail:
            role = getattr(m, "type", "user")
            if role == "human":
                role = "user"
            elif role == "ai":
                role = "assistant"
            text = getattr(m, "content", "")
            out.append(f"[{role}] {text}")
        return out
    except Exception as e:
        logger.warning("Failed to load messages from PG history: %s", e)
        return []