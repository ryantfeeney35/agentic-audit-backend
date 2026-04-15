# ADR-004: SDG&E SFTP Ingestion via Self-Hosted VPS

**Status:** Accepted (supersedes SFTP To Go / AWS Transfer Family proposals)  
**Date:** 2026-07-12 (revised 2026-07-14)  
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

Use a **low-cost VPS** (~$4-6/month) running **OpenSSH** in SFTP-only chroot
mode, with a **watcher script** that uploads received files directly to the
backend via HTTP multipart POST.

This is the cheapest approach — no S3 dependency, no third-party SFTP SaaS.

### Architecture

```
SDG&E SFTP push
      │
      ▼
VPS (Hetzner/DigitalOcean/Vultr)
  OpenSSH (chrooted SFTP-only)
      │
      ▼
Local filesystem:
  /srv/sftp/
    ├── prod/incoming/    ← SDG&E production pushes here
    ├── prod/processed/   ← after successful upload
    ├── prod/failed/      ← upload errors
    ├── test/incoming/    ← SDG&E QA pushes here
    └── test/...          ← same structure as prod
      │
      ▼
sftp_watcher.sh (inotifywait or cron)
      │  POST /api/utility/sftp-ingest
      │  Header: X-Ingest-Secret
      │  Body: multipart/form-data (file + filename)
      ▼
Railway backend
```

### Components

| Component | Purpose |
|-----------|---------|
| **VPS** | Hetzner CX22 (~$4/mo) or equivalent; runs OpenSSH + watcher |
| **OpenSSH (chroot)** | SFTP-only, no shell; separate users per environment |
| **sftp_watcher.sh** | Watches incoming dirs (inotifywait); POSTs files to backend |
| **Backend route** | `POST /api/utility/sftp-ingest` — accepts multipart file upload or JSON webhook |

### Watcher operation

- `sftp_watcher.sh --watch` (systemd): uses `inotifywait` for near-real-time
  detection of new files, then POSTs each file to the backend as a multipart
  upload with `X-Ingest-Secret` header.
- Falls back to cron mode (scan every 60s) if inotify-tools isn't available.
- Files are moved to `processed/YYYY-MM-DD/` on success, `failed/YYYY-MM-DD/`
  on error. Skips files modified in the last 10 seconds (still being written).

### File routing rules

| Pattern | Parser |
|---------|--------|
| `*_SUBSCRIPTIONS_*.CSV` | `sdge_subscriptions.parse_subscription_csv()` |
| `CEN_{H,D,C}_*.xml` | `green_button.parse_espi_atom_xml()` |
| Other | Log warning, return `unrecognized` type |

### Split-file reassembly

SDG&E splits large files with naming convention `..._XX-YY.xml` where
XX = part number, YY = total parts. In direct-upload mode (no S3), split files
are processed individually. S3-backed deployments (SFTP To Go, AWS) can buffer
parts and reassemble.

### Security

- SFTP: SSH key authentication only; no password auth.
- Chroot: each user is jailed to their own directory, forced `internal-sftp`.
- IP restriction: UFW/iptables limits port 22 to SDG&E IPs (161.209.96.0/24, 161.209.202.0/24) + admin IP.
- Watcher → backend: shared `X-Ingest-Secret` validated by backend route.
- File content travels over HTTPS (TLS) from VPS to Railway.
- OS hardening: unattended-upgrades, fail2ban recommended.

## Alternatives considered

| Alternative | Reason rejected |
|-------------|-----------------|
| SFTP To Go | $150/month — unnecessary cost for low volume |
| AWS Transfer Family + Lambda | ~$220/mo at idle; 3 services to manage |
| Self-hosted SFTP on Railway | Railway has no persistent TCP port; not supported |
| AWS EC2 with vsftpd | More expensive than a $5 VPS; same ops burden |
| S3 presigned upload URLs | SDG&E pushes via SFTP, not HTTP; incompatible |

## Consequences

- **Positive:** Cheapest option (~$4-6/mo); full control; no S3 dependency; simple architecture; watcher script is easy to debug.
- **Negative:** You manage OS updates, SSH patches, disk space, uptime. If VPS goes down, SDG&E file pushes fail (files are retried by SDG&E, but gap in data until VPS is restored).
- **Costs:** ~$4-6/month for VPS. No other services required.

## Setup steps

1. Provision a VPS (Hetzner CX22 recommended, ~$4/mo).
2. Run `setup_sftp_vps.sh` on the VPS (creates users, configures chroot, installs watcher).
3. Import SDG&E SSH public keys (`EDIX_MOD_RSA_PUB_KEY_QA.txt` → `sdge-test`, `EDIX_MOD_RSA_PUB_KEY_PROD.txt` → `sdge-prod`).
4. Edit `/etc/sftp-watcher/env` with `BACKEND_URL` and `INGEST_SECRET`.
5. Start watcher: `systemctl enable --now sftp-watcher`
6. Set `SFTP_INGEST_SECRET` in Railway env (must match VPS watcher value).
7. Provide SDG&E with: VPS hostname/IP, port 22, usernames (`sdge-prod`, `sdge-test`).

## Scripts

- `backend/infra/sftp-vps/setup_sftp_vps.sh` — VPS bootstrap (users, chroot, systemd)
- `backend/infra/sftp-vps/sftp_watcher.sh` — File watcher / uploader
