# HPXML export + DOE Home Energy Score (HEScore)

Generates HPXML from an audit and submits it to the DOE Home Energy Score API
to produce an official Score (1–10) and label.

## Pipeline

```
Audit (Property + steps + AI summaries + Audit.hes_inputs)
  → mapper.build_hes_model()   → HesBuilding (normalized intermediate)
  → serializer.to_hpxml()      → HPXML 3.0 XML
  → validation (optional)      → hescore-hpxml translator (local check)
  → hes_client.score_building()→ DOE API → Score + label
```

| File | Responsibility |
|------|----------------|
| `hes_model.py` | `HesBuilding` Pydantic model + enums (normalized HES inputs) |
| `lookups.py` | Heuristics: free-text/qualitative audit values → R-values, enums, efficiencies |
| `mapper.py` | `build_hes_model(audit)` — precedence: `hes_inputs` override → step inference → default |
| `gaps.py` | `compute_gaps(model)` — required vs review gaps; `is_scoreable(model)` |
| `serializer.py` | `to_hpxml(model)` — HPXML 3.0 in canonical element order |
| `validation.py` | optional local validation via `hescore-hpxml` |
| `hes_client.py` | DOE HEScore API client (auth + submit + score + label) |

## Data model

- `Audit.hes_inputs` (JSONB) holds supplemental, auditor-entered inputs not
  captured by standard steps (bedrooms, stories, foundation, water heater,
  qualitative air leakage, window glazing, numeric HVAC efficiencies, PV).
  The mapper reads nested keys, e.g. `foundation.type`, `hot_water.fuel`.
- `HomeEnergyScore` stores each submission + result (score, label_url, raw).

## Endpoints (`routes/hpxml_routes.py`)

```
GET  /api/audits/<id>/hpxml[?download=true]   generated HPXML
GET  /api/audits/<id>/hpxml/validation        gaps + translator check
GET  /api/audits/<id>/hes-inputs              current supplemental inputs
PUT  /api/audits/<id>/hes-inputs              shallow-merge supplemental inputs
POST /api/audits/<id>/home-energy-score       generate official score
GET  /api/audits/<id>/home-energy-score       latest score record
```

## Configuration

DOE credentials (see `.env.example`) — obtained after Software Partner approval
(email `assessor@ee.doe.gov`); scoring requires a DOE-approved Qualified Assessor:

```
HESCORE_BASE_URL, HESCORE_API_KEY, HESCORE_ASSESSOR_USERNAME, HESCORE_ASSESSOR_PASSWORD
```

## Phase-0 validation loop (acceptance gate)

Element-level HPXML conformance is locked in by translating locally:

```bash
pip install hescore-hpxml
# GET /api/audits/<id>/hpxml/validation  → translator.ok must be true
```

Iterate on `serializer.py` until `translator.ok == true` for representative
audits before relying on the live API. The `<extension><HEScoreIntermediate>`
block embeds the normalized model to aid debugging.

## Known follow-ups

- One HVAC step carries a single `system_type`; furnace+AC homes need the
  `hes_inputs.heating` / `hes_inputs.cooling` overrides (mapper supports both).
- Conditioned floor area is derived from gross `Property.sqft` (×0.92) and
  flagged as a `review` gap; auditor confirms or overrides.
- Wire format of `_call()` in `hes_client.py` must be confirmed against the
  partner docs when the API key arrives (sandbox vs prod envelope).
