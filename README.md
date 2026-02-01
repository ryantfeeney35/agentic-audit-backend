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


Green Button Utility Data Integration
-------------------------------------

The application supports connecting to utility accounts via Green Button standard for importing historical energy usage data.

### Provider Waterfall Architecture

The system uses a waterfall pattern to connect to utilities:

1. **SDG&E Connect My Data (CMD)** — Direct OAuth connection to SDG&E's My Energy Center
2. **UtilityAPI** — Third-party aggregator supporting multiple utilities
3. **Manual Entry** — User can manually input usage data as fallback

### SDG&E Developer Application Process

To enable direct SDG&E Connect My Data integration:

1. **Submit Application**
   - Visit [SDG&E Green Button Developer Portal](https://www.sdge.com/more-information/environment/green-button/developers)
   - Complete the third-party developer application form
   - Provide business identity, app description, and data access requirements

2. **Specify Data Pull Frequency**
   - Choose between one-time pull or recurring subscription
   - Recommended: Recurring for continuous monitoring

3. **Security Documentation**
   - Submit security posture documentation
   - Timeline for security certification if not yet completed
   - SDG&E may require SOC 2 or equivalent

4. **Approval & Credentials**
   - Typical timeline: 2-4 weeks
   - Receive `SDGE_CMD_CLIENT_ID` and `SDGE_CMD_CLIENT_SECRET`
   - Configure redirect URI in SDG&E portal

5. **Testing**
   - Use SDG&E sandbox environment (if available)
   - Test OAuth flow and ESPI data retrieval

### Environment Variables

See `.env.example` for all required variables:

```bash
# SDG&E CMD (Direct Connection)
SDGE_CMD_CLIENT_ID=your-client-id
SDGE_CMD_CLIENT_SECRET=your-client-secret
SDGE_CMD_REDIRECT_URI=https://your-app.com/api/utility/callback

# UtilityAPI (Fallback)
UTILITYAPI_API_KEY=your-api-key
UTILITYAPI_WEBHOOK_SECRET=your-webhook-secret

# Token Encryption
TOKEN_ENCRYPTION_KEY=your-32-byte-hex-key
```

### API Endpoints

**Provider Discovery:**
- `GET /api/utility/providers` — Returns available providers and waterfall chains

**Connection Flow:**
- `POST /api/utility/connect` — Initiate OAuth connection
  ```json
  {
    "audit_id": 123,
    "utility_name": "SDGE",
    "data_scope": "electric"
  }
  ```
  Returns `auth_url` for OAuth redirect

- `GET /api/utility/callback` — OAuth callback handler

**Data Access:**
- `GET /api/audits/<id>/utility-connection` — Connection status
- `GET /api/audits/<id>/utility-summary` — Usage summary
- `POST /api/audits/<id>/utility-data/sync` — Trigger data sync
- `POST /api/audits/<id>/utility-data/manual` — Manual entry
- `DELETE /api/audits/<id>/utility-connection` — Disconnect

### Energy Usage Agent

When utility data is connected, the Energy Usage Agent automatically analyzes:
- Baseline usage vs. typical homes
- Seasonal patterns (summer/winter comparison)
- Time-of-Use (TOU) optimization opportunities
- Usage anomalies and spikes
- Correlation with observed equipment

Recommendations from this agent appear on the Recommendations page alongside other agent outputs, with:
- "Behavior Change" badge for usage-pattern recommendations
- "Utility Data" source attribution
- User status actions (Interested, Not Relevant, Completed)
