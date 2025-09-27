# memory/postgres_memory.py
from models import AgentConversation, db
from datetime import datetime

class PostgresConversationMemory:
    def __init__(self, audit_id: int, domain: str = "orchestrator"):
        self.audit_id = audit_id
        self.domain = domain

    def load(self):
        """Return conversation as LangChain-style messages"""
        rows = (
            AgentConversation.query
            .filter_by(audit_id=self.audit_id)
            .order_by(AgentConversation.created_at.asc())
            .all()
        )
        return [
            {"role": r.role, "content": r.content, "domain": r.domain}
            for r in rows
        ]

    def save(self, role: str, content: str, domain: str = None):
        """Save a new message turn"""
        msg = AgentConversation(
            audit_id=self.audit_id,
            domain=domain or self.domain,
            role=role,
            content=content,
            created_at=datetime.utcnow()
        )
        db.session.add(msg)
        db.session.commit()
        return msg