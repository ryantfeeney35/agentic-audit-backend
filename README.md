Recommendations API
-------------------

Endpoints added/updated for recommendations:

- GET `/api/audits/<audit_id>/recommendations`
  - Query params:
    - `include_hidden=true|false` (default: false)
    - `source=all|audio|ai` (default: all)
  - Response: list of recommendation objects including `source` field with value `audio` or `ai`.

- POST `/api/audits/<audit_id>/recommendations/regenerate`
  - Triggers generation (two-pass) and returns the newly created recommendations.

- PATCH `/api/audits/<audit_id>/recommendations/<rec_id>`
  - Body currently supports `{ "is_hidden": true|false }`.

Database migration
------------------

Add a migration that creates `source` column on `audit_recommendations` (non-nullable, server_default 'ai').


Memory architecture (hybrid)
----------------------------

Feature flags (all optional, default off):

- MEMORY_ENABLED=true|false (default: false)
- SEMANTIC_RECALL_ENABLED=true|false (default: false)
- EMBEDDING_MODEL (default: text-embedding-3-small)
- SEMANTIC_TOP_K (default: 5)
- MEMORY_CONTEXT_CHAR_CAP (default: 12000)

Tables:

- langchain_memory — persistent chat history (session_id = "audit-<id>")
- audit_memory_embeddings — semantic snippets per audit (content + embedding JSONB)

Runtime behavior:

- When MEMORY_ENABLED=true, conversational writes go to both persistent memory and legacy `agent_conversations` (for compatibility). Reads prefer memory but fall back to legacy.
- Unified context builder `get_audit_memory_context(audit_id, exclude_audio=False)` returns structured facts + recent discussion. With semantic recall enabled, it also appends top-k relevant prior findings.
- Orchestrator calls `get_audit_memory_context` for bootstrap, follow-ups, and contextual recommendations; before recommendations, it refreshes semantic embeddings.

Local dev
---------

1. Ensure DATABASE_URL points to Postgres.
2. Enable flags as needed:

  - export MEMORY_ENABLED=true
  - export SEMANTIC_RECALL_ENABLED=true

3. Run migrations:

  - export FLASK_APP=app.py
  - flask db upgrade
