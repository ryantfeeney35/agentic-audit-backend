#!/usr/bin/env bash
# sftp_watcher.sh — Watch SFTP incoming directories and POST new files
# to the Railway backend.  Run via cron or systemd on the VPS.
#
# Usage:
#   BACKEND_URL=https://your-app.up.railway.app \
#   INGEST_SECRET=your-shared-secret \
#   ./sftp_watcher.sh
#
# Cron example (every 60 seconds):
#   * * * * * /opt/sftp-watcher/sftp_watcher.sh >> /var/log/sftp_watcher.log 2>&1
#
# Required: curl, inotifywait (from inotify-tools) OR cron
# Supports two modes:
#   1. Cron mode (default): scans incoming/ dirs once, uploads new files, exits.
#   2. Watch mode (--watch): uses inotifywait to react in near-real-time.

set -euo pipefail

# ── Configuration ─────────────────────────────────────────────────────────────
BACKEND_URL="${BACKEND_URL:?Set BACKEND_URL (e.g. https://your-app.up.railway.app)}"
INGEST_SECRET="${INGEST_SECRET:?Set INGEST_SECRET (shared secret for X-Ingest-Secret header)}"
INGEST_ENDPOINT="${BACKEND_URL}/api/utility/sftp-ingest"

# Base directory where SDG&E pushes files via SFTP
SFTP_ROOT="${SFTP_ROOT:-/srv/sftp}"

# Directories to watch (relative to SFTP_ROOT)
INCOMING_DIRS=("prod/incoming" "test/incoming")

# Where to move files after successful/failed upload
PROCESSED_DIR="processed"
FAILED_DIR="failed"

LOCK_FILE="/tmp/sftp_watcher.lock"

# ── Helpers ───────────────────────────────────────────────────────────────────
log() { echo "[$(date -u '+%Y-%m-%d %H:%M:%S UTC')] $*"; }

upload_file() {
    local filepath="$1"
    local filename
    filename=$(basename "$filepath")
    local dir
    dir=$(dirname "$filepath")
    local env_prefix
    # Determine prod vs test from path
    if [[ "$dir" == *"prod"* ]]; then
        env_prefix="prod"
    else
        env_prefix="test"
    fi

    log "Uploading: $filepath (env=$env_prefix)"

    local http_code
    http_code=$(curl -s -o /tmp/sftp_watcher_response.json -w "%{http_code}" \
        -X POST "$INGEST_ENDPOINT" \
        -H "X-Ingest-Secret: $INGEST_SECRET" \
        -F "file=@${filepath}" \
        -F "filename=${filename}" \
        --max-time 120 \
        --retry 2 \
        --retry-delay 5)

    if [[ "$http_code" == "200" ]]; then
        log "  ✓ Uploaded successfully (HTTP $http_code)"
        # Move to processed
        local date_dir
        date_dir=$(date -u '+%Y-%m-%d')
        local dest="${SFTP_ROOT}/${env_prefix}/${PROCESSED_DIR}/${date_dir}"
        mkdir -p "$dest"
        mv "$filepath" "$dest/"
        log "  → Moved to $dest/$filename"
        return 0
    else
        log "  ✗ Upload failed (HTTP $http_code)"
        cat /tmp/sftp_watcher_response.json 2>/dev/null || true
        # Move to failed
        local date_dir
        date_dir=$(date -u '+%Y-%m-%d')
        local dest="${SFTP_ROOT}/${env_prefix}/${FAILED_DIR}/${date_dir}"
        mkdir -p "$dest"
        mv "$filepath" "$dest/"
        log "  → Moved to $dest/$filename"
        return 1
    fi
}

scan_and_upload() {
    local total=0
    local success=0

    for inc_dir in "${INCOMING_DIRS[@]}"; do
        local full_dir="${SFTP_ROOT}/${inc_dir}"
        if [[ ! -d "$full_dir" ]]; then
            continue
        fi

        # Process all files (not directories) in incoming
        find "$full_dir" -maxdepth 1 -type f | while read -r filepath; do
            total=$((total + 1))
            # Skip files still being written (modified in last 10 seconds)
            local age
            if [[ "$(uname)" == "Darwin" ]]; then
                age=$(( $(date +%s) - $(stat -f %m "$filepath") ))
            else
                age=$(( $(date +%s) - $(stat -c %Y "$filepath") ))
            fi
            if [[ $age -lt 10 ]]; then
                log "Skipping (still being written): $filepath"
                continue
            fi

            if upload_file "$filepath"; then
                success=$((success + 1))
            fi
        done
    done

    if [[ $total -gt 0 ]]; then
        log "Scan complete: $success/$total files uploaded"
    fi
}

# ── Locking (prevent concurrent runs from cron) ──────────────────────────────
acquire_lock() {
    if [[ -f "$LOCK_FILE" ]]; then
        local lock_pid
        lock_pid=$(cat "$LOCK_FILE" 2>/dev/null || echo "")
        if [[ -n "$lock_pid" ]] && kill -0 "$lock_pid" 2>/dev/null; then
            log "Another instance is running (PID $lock_pid), exiting."
            exit 0
        fi
        # Stale lock
        rm -f "$LOCK_FILE"
    fi
    echo $$ > "$LOCK_FILE"
    trap 'rm -f "$LOCK_FILE"' EXIT
}

# ── Main ──────────────────────────────────────────────────────────────────────
main() {
    acquire_lock

    if [[ "${1:-}" == "--watch" ]]; then
        # inotifywait mode — near-real-time
        if ! command -v inotifywait &>/dev/null; then
            log "ERROR: inotifywait not found. Install inotify-tools or use cron mode."
            exit 1
        fi
        log "Starting watch mode on ${INCOMING_DIRS[*]}"

        local watch_paths=()
        for inc_dir in "${INCOMING_DIRS[@]}"; do
            local full_dir="${SFTP_ROOT}/${inc_dir}"
            mkdir -p "$full_dir"
            watch_paths+=("$full_dir")
        done

        # Process any files already sitting in incoming/
        scan_and_upload

        # Watch for new files
        inotifywait -m -e close_write --format '%w%f' "${watch_paths[@]}" | while read -r filepath; do
            # Small delay to ensure file is fully written
            sleep 2
            if [[ -f "$filepath" ]]; then
                upload_file "$filepath" || true
            fi
        done
    else
        # Cron mode — scan once and exit
        scan_and_upload
    fi
}

main "$@"
