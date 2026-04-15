# ADR-004: SDG&E SFTP Ingestion via SFTP To Go

**Status:** Accepted (supersedes AWS Transfer Family proposal)  
**Date:** 2026-07-12 (revised 2026-07-13)  
**Decision makers:** Ryan Feeney  

## Context

SDG&E delivers Green Button ESPI XML consumption files and subscription CSVs
via SFTP push. Files arrive daily (CEN_D_*), historically on first connect
(CEN_H_*), and on correction (CEN_C_*). Subscription CSVs are pushed when
customers enroll, un-enroll, or change meters.

Our backend runs on Railway, which does not support an always-on SFTP
listener. We need a managed SFTP endpoint that SDG&E can push to and a
pipeline that routes files into our Flask backend.

## Decision

Use **SFTP To Go** (managed SFTP service backed by AWS S3) with its
built-in **webhook notifications** to bridge files into the backend REST API.

This replaces the original AWS Transfer Family + Lambda proposal, which
required managing three AWS services (Transfer Family, S3, Lambda) and cost
~$220/month at idle.

### Architecture

```
SDG&E SFTP push
      │
      ▼
SFTP To Go (managed SFTP endpoint)
      │  backed by AWS S3
      ▼
S3 storage (managed by SFTP To Go)
  ├── prod/incoming/    ← SDG&E production pushes here
  ├── prod/processed/   ← after successful ingestion
  ├── prod/failed/      ← parser / route errors
  ├── prod/unrecognized/← files that don't match known patterns
  ├── test/incoming/    ← SDG&E QA pushes here
  └── test/...          ← same structure as prod
      │
      ▼
SFTP To Go webhook → POST /api/utility/sftp-ingest
      │  Header: X-Ingest-Secret
      ▼
Railway backend (downloads file via S3-compatible API)
```

### Components

| Component | Purpose |
|-----------|---------|
| **SFTP To Go** | Managed SFTP server; SSH key auth; static IP; S3-backed storage |
| **S3-compatible API** | Backend downloads files using boto3 with SFTP To Go S3 credentials |
| **Webhook notification** | SFTP To Go fires webhook on file upload; sends file path to backend |
| **Backend route** | `POST /api/utility/sftp-ingest` — downloads file via S3 API, routes to parser |

### File routing rules

| Pattern | Parser |
|---------|--------|
| `*_SUBSCRIPTIONS_*.CSV` | `sdge_subscriptions.parse_subscription_csv()` |
| `CEN_{H,D,C}_*.xml` | `green_button.parse_espi_atom_xml()` |
| Other | Move to `unrecognized/`, log warning |

### Split-file reassembly

SDG&E splits large files with naming convention `..._XX-YY.xml` where
XX = part number, YY = total parts. The backend buffers parts in S3 by
transaction ID and triggers processing when all parts arrive. A 1-hour
timeout handles missing parts (moved to `failed/`).

### Security

- SFTP: SSH key authentication only; no password auth.
- IP whitelisting: SDG&E QA (161.209.96.0/24) and PROD (161.209.202.0/24) IPs whitelisted in SFTP To Go.
- Webhook → backend: shared `X-Ingest-Secret` header validated by backend route.
- S3 API access: credentials stored in Railway env vars (`SFTPTOGO_S3_*`); never committed to source.
- No customer PII transits the webhook; it only sends the file path.

## Alternatives considered

| Alternative | Reason rejected |
|-------------|-----------------|
| AWS Transfer Family + Lambda | ~$220/mo at idle; 3 services to manage; operational complexity |
| Self-hosted SFTP on Railway | Railway has no persistent TCP port; not supported |
| AWS EC2 with vsftpd | Operational burden; no managed equivalent benefits |
| S3 presigned upload URLs | SDG&E's system pushes via SFTP, not HTTP; incompatible |
| Azure Blob + SFTP | No existing Azure footprint; adds vendor complexity |
| Cheap VPS (Hetzner, DigitalOcean) | $5/mo but self-managed; no webhook integration; patching burden |

## Consequences

- **Positive:** Fully managed SFTP with zero maintenance; built-in webhook eliminates Lambda; SSH key + IP whitelisting for SDG&E; S3-compatible API for file access; simpler architecture (one service vs three).
- **Negative:** ~$150/mo fixed cost regardless of volume; vendor lock-in to SFTP To Go (mitigated by standard SFTP + S3 API).
- **Costs:** SFTP To Go Launch plan ~$150/month (20 credentials, 100 GiB storage, webhook notifications, S3 API access). Expected volume is low (< 100 files/day, < 50 MB total).

## Setup steps

1. Sign up for SFTP To Go Launch plan.
2. Create SFTP credentials for SDG&E: separate users for `prod/` and `test/` folders.
3. Import SDG&E SSH public keys (`EDIX_MOD_RSA_PUB_KEY_QA.txt`, `EDIX_MOD_RSA_PUB_KEY_PROD.txt`).
4. Create folder structure: `{prod,test}/{incoming,processed,failed,unrecognized}/`.
5. Whitelist SDG&E IPs: QA `161.209.96.0/24`, PROD `161.209.202.0/24`.
6. Configure webhook: URL = `https://<railway-host>/api/utility/sftp-ingest`, header `X-Ingest-Secret`.
7. Copy S3 API credentials from SFTP To Go dashboard → set Railway env vars: `SFTPTOGO_S3_ACCESS_KEY_ID`, `SFTPTOGO_S3_SECRET_ACCESS_KEY`, `SFTPTOGO_S3_ENDPOINT`, `SFTPTOGO_S3_BUCKET`.
8. Set `SFTP_INGEST_SECRET` in Railway env (must match webhook header value).
9. Provide SDG&E with: SFTP hostname, port 22, SSH usernames for prod and test.
