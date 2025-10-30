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
