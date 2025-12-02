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

    # Map roles to message types supported by LangChain history
    # Best-effort: write via LangChain history if available
    if history:
        try:
            if role == "user":
                history.add_user_message(f"[{domain}] {content}")
            elif role == "assistant":
                history.add_ai_message(f"[{domain}] {content}")
            else:  # system or other
                history.add_ai_message(f"[system/{domain}] {content}")
        except Exception as e:
            logger.warning("Failed to save message to PG history: %s", e)

    # Additionally persist to our custom langchain_memory table for visibility
    try:
        from models import db
        db.session.execute(
            db.text(
                "INSERT INTO langchain_memory (session_id, audit_id, role, content, metadata) "
                "VALUES (:sid, :aid, :role, :content, :meta)"
            ),
            {
                "sid": f"audit-{audit_id}",
                "aid": audit_id,
                "role": role,
                "content": content,
                "meta": f'{{"domain":"{domain}"}}',
            },
        )
        db.session.commit()
    except Exception as e:
        # Table may not exist or DB may be readonly; ignore silently but log at debug
        try:
            from models import db as _db
            _db.session.rollback()
        except Exception:
            pass
        logger.debug("langchain_memory insert skipped: %s", e)


def get_recent_messages(audit_id: int, limit: int = 12) -> List[str]:
    """Return the last N messages from persistent chat history formatted as strings.

    Falls back to empty list if memory disabled or unavailable. Legacy callers can merge with
    AgentConversation rows if desired.
    """
    history = _get_pg_history(audit_id)
    if history:
        try:
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

    # Fallback: read from custom langchain_memory table
    try:
        from models import db
        rows = db.session.execute(
            db.text(
                "SELECT role, content, metadata FROM langchain_memory WHERE audit_id = :aid "
                "ORDER BY created_at ASC"
            ),
            {"aid": audit_id},
        ).fetchall()
        if not rows:
            return []
        # take tail
        rows = rows[-limit:] if limit and len(rows) > limit else rows
        out: List[str] = []
        for r in rows:
            role = r.role
            content = r.content
            out.append(f"[{role}] {content}")
        return out
    except Exception as e:
        # Ensure the session is clean for subsequent queries if this select failed
        try:
            from models import db as _db
            _db.session.rollback()
        except Exception:
            pass
        logger.debug("langchain_memory select skipped: %s", e)
        return []