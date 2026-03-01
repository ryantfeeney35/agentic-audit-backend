import logging
from typing import Iterable, List, Tuple

from sqlalchemy import text

from models import AuditStep, db
from memory.config import embedding_model, semantic_enabled, semantic_top_k

logger = logging.getLogger(__name__)


def _cosine(a: List[float], b: List[float]) -> float:
    try:
        dot = sum(x * y for x, y in zip(a, b))
        na = sum(x * x for x in a) ** 0.5
        nb = sum(y * y for y in b) ** 0.5
        if na == 0 or nb == 0:
            return 0.0
        return dot / (na * nb)
    except Exception:
        return 0.0


def _extract_structured_snippets(audit_id: int) -> List[str]:
    snippets: List[str] = []
    steps = AuditStep.query.filter_by(audit_id=audit_id).all()
    for s in steps:
        if s.summary:
            snippets.append(f"{s.step_type}: {s.summary}")
        if s.ai_summary and isinstance(s.ai_summary, dict):
            for k, v in s.ai_summary.items():
                if isinstance(v, (str, int, float)):
                    snippets.append(f"{s.step_type} {k}: {v}")
    return snippets


def upsert_embeddings_for_audit(audit_id: int) -> int:
    """Rebuild embeddings for an audit by embedding step summaries and simple AI findings.

    Returns number of rows inserted. Best-effort: exceptions are logged and result may be partial.
    """
    if not semantic_enabled():
        return 0

    # Ensure clean transaction state before starting
    try:
        db.session.rollback()
    except Exception:
        pass

    # Prepare content to embed
    try:
        contents = _extract_structured_snippets(audit_id)
    except Exception as e:
        logger.warning("Failed to extract snippets for semantic embedding: %s", e)
        try:
            db.session.rollback()
        except Exception:
            pass
        return 0
        
    if not contents:
        # clear old rows
        try:
            db.session.execute(text("DELETE FROM audit_memory_embeddings WHERE audit_id = :aid"), {"aid": audit_id})
            db.session.commit()
        except Exception:
            db.session.rollback()
        return 0

    # Embed using OpenAI
    try:
        from openai import OpenAI

        client = OpenAI()
        model = embedding_model()
        # Batch request if supported; fallback to per-item
        resp = client.embeddings.create(model=model, input=contents)
        vectors = [d.embedding for d in getattr(resp, "data", [])]
        if len(vectors) != len(contents):
            # fallback to per-item embedding
            vectors = []
            for c in contents:
                r = client.embeddings.create(model=model, input=c)
                vectors.append(r.data[0].embedding)
    except Exception as e:
        logger.exception("Embedding generation failed: %s", e)
        return 0

    # Replace rows for this audit
    try:
        db.session.execute(text("DELETE FROM audit_memory_embeddings WHERE audit_id = :aid"), {"aid": audit_id})
        for content, emb in zip(contents, vectors):
            db.session.execute(
                text(
                    "INSERT INTO audit_memory_embeddings (audit_id, content, embedding) "
                    "VALUES (:aid, :content, :emb::jsonb)"
                ),
                {"aid": audit_id, "content": content, "emb": str(emb).replace("'", '"')},
            )
        db.session.commit()
        return len(contents)
    except Exception as e:
        db.session.rollback()
        logger.exception("Failed to upsert embeddings: %s", e)
        return 0


def retrieve_relevant_snippets(audit_id: int, query_text: str, k: int | None = None) -> List[str]:
    """Return top-k content snippets by cosine similarity to the query embedding.

    If embeddings or OpenAI are unavailable, returns an empty list.
    """
    if not semantic_enabled():
        return []
    try:
        from openai import OpenAI
    except Exception:
        return []

    # Ensure clean transaction state
    try:
        db.session.rollback()
    except Exception:
        pass

    try:
        # 1) Fetch all rows for audit
        rows = db.session.execute(text("SELECT content, embedding FROM audit_memory_embeddings WHERE audit_id = :aid"), {"aid": audit_id}).fetchall()
        if not rows:
            return []

        # 2) Embed the query
        client = OpenAI()
        model = embedding_model()
        q = client.embeddings.create(model=model, input=query_text)
        qv = q.data[0].embedding

        # 3) Rank by cosine similarity
        scored: List[Tuple[float, str]] = []
        for content, emb in rows:
            # JSONB returns a Python list in SQLAlchemy; if string, try to parse
            vec = emb if isinstance(emb, list) else []
            if not vec:
                continue
            scored.append((_cosine(qv, vec), content))

        scored.sort(key=lambda t: t[0], reverse=True)
        topk = (k or semantic_top_k())
        return [c for _, c in scored[:topk] if _ > 0.0]
    except Exception as e:
        logger.debug("retrieve_relevant_snippets fallback: %s", e)
        try:
            db.session.rollback()
        except Exception:
            pass
        return []
