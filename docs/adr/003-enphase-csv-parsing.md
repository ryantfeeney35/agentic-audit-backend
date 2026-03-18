# ADR 003: Enphase CSV Parsing for PPA/Lease Solar Data

## Status
Accepted

## Context

Homeowners with PPA (Power Purchase Agreement) or Lease solar systems cannot grant API access to their Enphase telemetry data—the installer owns the system and Enphase account. These homeowners need an alternative way to submit their solar production data for energy audit analysis.

Enphase provides an "Enlighten Custom Report" feature that allows any user with portal access to export their telemetry data as a CSV file. This report includes detailed interval data for production, consumption, and battery metrics.

## Decision

We will support manual CSV upload of Enlighten custom reports with the following approach:

### CSV Format Support

**Expected Headers (Enlighten Custom Report format):**
```
Date/Time,Energy Produced (Wh),Energy Consumed (Wh),Exported to Grid (Wh),Imported from Grid (Wh),Stored in batteries (Wh),Discharged from batteries (Wh)
```

**Date/Time Format:** `MM/DD/YYYY HH:MM` (e.g., `06/25/2025 00:00`)

**Data Format:** 
- All energy values in Wh (Watt-hours)
- Converted to kWh on import for consistency with API data
- 15-minute intervals typical, but parser auto-detects granularity

### Storage Architecture

1. **Connection Tracking**: Create `EnphaseConnection` with `system_id='spreadsheet-{audit_id}'` and `status='connected'`
2. **Telemetry Records**: Insert `EnphaseTelemetryInterval` records with `source='spreadsheet'`
3. **Upsert Logic**: Use `(connection_id, interval_start)` unique constraint to update existing records on re-upload

### Parser Implementation

Located at `backend/utils/enphase_csv_parser.py`:

- **Header Validation**: Flexible matching - requires Date/Time + at least one energy column
- **Date Parsing**: Supports MM/DD/YYYY HH:MM with fallbacks for ISO format
- **Unit Conversion**: Wh → kWh (divide by 1000)
- **Granularity Detection**: Auto-detects from interval spacing (5m, 15m, hourly, daily)
- **Streaming**: Chunks large files (50MB limit) to handle multi-year exports

### API Endpoint

`POST /api/homeowner/enphase/upload`
- Accepts multipart file upload (CSV only)
- Returns processing summary with record counts, date range, and totals
- Requires homeowner JWT authentication

## Consequences

### Positive
- PPA/Lease homeowners can participate in solar energy analysis
- Consistent data model whether from API or spreadsheet
- Source tracking enables data quality assessment
- Re-upload capability for corrections without duplicates

### Negative
- Manual process requires homeowner effort
- Data freshness depends on homeowner re-uploading periodically
- Enlighten format may change without notice (parser should be resilient)

### Mitigations
- Clear instructions in portal for generating Enlighten custom reports
- Informative error messages for parsing failures
- Flexible header matching for minor format variations

## Sample Data Reference

Test file: `backend/tests/data/5887542_custom_report.csv`
- ~25,000 rows (262 days at 15-minute intervals)
- All columns populated with realistic values
- Used for parser unit tests

## Related Documentation

- [Enphase Enlighten Portal Help](https://enlighten.enphaseenergy.com/help)
- ADR 002: Homeowner Portal Authentication
