# ADR-001: Enphase API Integration Architecture

## Status
Accepted

## Context
The Agentic Audit app integrates with utility providers (SDG&E CMD, UtilityAPI) to pull Green Button consumption data for energy analysis. However, many homeowners have existing solar/battery systems monitored by Enphase, the dominant residential solar monitoring platform.

To provide accurate recommendations for solar expansion, battery additions, and energy optimization, we need access to actual production data—not just consumption. Enphase API v4 provides:
- System discovery (inverters, batteries, meters)
- Granular telemetry (5-min, 15-min, hourly intervals)
- Production, consumption, battery state, and grid import/export

We needed to decide how to integrate this data alongside our existing utility integration.

## Decision
We chose to create **separate database models** (`EnphaseConnection` and `EnphaseTelemetryInterval`) rather than extending the existing `UtilityConnection` model.

## Rationale

### Why Separate Models

1. **Semantic Clarity**
   - Enphase is not a utility provider—it's a solar monitoring platform
   - Data contains fundamentally different metrics (production, battery cycles)
   - API patterns and authentication flows differ from Green Button implementations

2. **Data Model Differences**
   - Enphase data includes: `system_id`, `production_kwh`, `grid_import_kwh`, `grid_export_kwh`, `battery_charge_kwh`, `battery_discharge_kwh`
   - Utility data is consumption-only with different schemas per provider
   - Combining would require many nullable columns and complex type discrimination

3. **Query Simplicity**
   - Agent context builders can query Enphase data independently
   - No need for `WHERE provider_type = 'enphase'` filtering everywhere
   - Cleaner foreign key relationships and indices

4. **Independent Lifecycle**
   - Enphase sync cadence differs from utility data (telemetry vs billing cycles)
   - Error handling and retry logic is API-specific
   - Token refresh patterns differ from Green Button OAuth

### Alternatives Considered

**Extend UtilityConnection with `provider_type='enphase'`**
- Rejected: Would pollute utility models with nullable solar-specific columns
- Rejected: Semantic confusion—Enphase is a monitoring platform, not a utility
- Rejected: Complex querying with type discrimination

**Generic "DataConnection" abstraction**
- Rejected: Over-engineering for current scope (only two connection types)
- Rejected: Would require significant refactoring of existing utility code
- Considered: May revisit if more data sources are added

## Consequences

### Positive
- Clean separation of concerns between utility and solar data
- Straightforward agent context building with dedicated schemas
- Independent evolution of each integration
- Clear API surface for frontend components

### Negative
- Some code patterns duplicated (OAuth flow, encrypted token storage)
- Two separate connection cards on interview screen
- Must maintain parallel sync infrastructure

### Mitigation
- Shared utilities extracted: `utils/encryption.py` for token handling
- Common patterns documented for future integrations
- EnphaseConnectionCard follows UtilityConnectionCard patterns for UX consistency

## Implementation Notes

### Database Models
```python
# EnphaseConnection - OAuth credentials and system metadata
class EnphaseConnection(db.Model):
    id, user_id, audit_id, property_id
    system_id, system_name, status
    access_token_enc, refresh_token_enc, token_expires_at
    oauth_state, provider_metadata (JSONB)
    last_sync_at, last_sync_error

# EnphaseTelemetryInterval - 15-min production/consumption data
class EnphaseTelemetryInterval(db.Model):
    id, audit_id, property_id, connection_id, system_id
    interval_start, interval_end, granularity
    production_kwh, consumption_kwh
    grid_import_kwh, grid_export_kwh
    battery_charge_kwh, battery_discharge_kwh
    raw_payload (JSONB)
```

### API Routes
- `POST /api/enphase/connect` — Initiate OAuth flow
- `GET /api/enphase/oauth/callback` — Handle OAuth redirect
- `GET /api/audits/{id}/enphase-connection` — Get connection status
- `POST /api/audits/{id}/enphase-connection/sync` — Trigger data sync
- `GET /api/audits/{id}/enphase-telemetry` — Query telemetry data

### Agent Integration
The `get_energy_usage_context()` function now includes Enphase telemetry summary when available, providing agents with:
- Actual production data (vs estimated)
- Self-consumption ratio
- Battery utilization patterns
- Grid import/export statistics

## References
- [Enphase Developer API v4 Documentation](https://developer-v4.enphase.com/docs.html)
- [OpenSpec Design Document](../../../openspec/changes/enphase-api-connection/design.md)
- [UtilityConnection Implementation](../routes/utility_routes.py)
