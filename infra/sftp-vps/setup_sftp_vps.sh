#!/usr/bin/env bash
# setup_sftp_vps.sh — Bootstrap an Ubuntu/Debian VPS as an SFTP endpoint
# for SDG&E Green Button file delivery.
#
# Run as root on a fresh VPS:
#   curl -sL https://raw.githubusercontent.com/.../setup_sftp_vps.sh | bash
#
# After running, you still need to:
#   1. Import SDG&E SSH public keys into the SFTP users' authorized_keys
#   2. Set BACKEND_URL and INGEST_SECRET in /etc/sftp-watcher/env
#   3. Start the watcher: systemctl enable --now sftp-watcher

set -euo pipefail

echo "=== SDG&E SFTP VPS Setup ==="

# ── 1. Create SFTP group and users ───────────────────────────────────────────
SFTP_ROOT="/srv/sftp"

groupadd -f sftponly

for ENV in prod test; do
    USER="sdge-${ENV}"
    HOME_DIR="${SFTP_ROOT}/${ENV}"

    if ! id "$USER" &>/dev/null; then
        useradd -g sftponly -s /usr/sbin/nologin -d "/${ENV}" "$USER"
        echo "Created user: $USER"
    fi

    # ChrootDirectory requires root ownership of the user's home
    mkdir -p "${HOME_DIR}/incoming" "${HOME_DIR}/processed" "${HOME_DIR}/failed" "${HOME_DIR}/unrecognized"
    chown root:root "$HOME_DIR"
    chmod 755 "$HOME_DIR"
    # Users can write to incoming/ only
    chown "$USER":sftponly "${HOME_DIR}/incoming"
    chmod 775 "${HOME_DIR}/incoming"

    # SSH authorized_keys
    mkdir -p "${HOME_DIR}/.ssh"
    touch "${HOME_DIR}/.ssh/authorized_keys"
    chown "$USER":sftponly "${HOME_DIR}/.ssh" "${HOME_DIR}/.ssh/authorized_keys"
    chmod 700 "${HOME_DIR}/.ssh"
    chmod 600 "${HOME_DIR}/.ssh/authorized_keys"
done

# ── 2. Configure SSHD for chrooted SFTP ──────────────────────────────────────
SSHD_CONF="/etc/ssh/sshd_config"

# Backup original
cp "$SSHD_CONF" "${SSHD_CONF}.bak.$(date +%s)"

# Add SFTP chroot config if not already present
if ! grep -q "Match Group sftponly" "$SSHD_CONF"; then
    cat >> "$SSHD_CONF" <<'EOF'

# --- SDG&E SFTP chroot configuration ---
Match Group sftponly
    ChrootDirectory /srv/sftp/%u
    ForceCommand internal-sftp -d /incoming
    AllowTcpForwarding no
    X11Forwarding no
    PasswordAuthentication no
EOF
    echo "Added SFTP chroot config to sshd_config"
fi

# Validate and restart
sshd -t && systemctl restart sshd
echo "SSHD configured and restarted"

# ── 3. Firewall (UFW) ────────────────────────────────────────────────────────
if command -v ufw &>/dev/null; then
    ufw --force reset
    ufw default deny incoming
    ufw default allow outgoing

    # SSH from everywhere (for your admin access)
    ufw allow 22/tcp

    # Restrict SFTP to SDG&E IPs only (applied at SSH level, but belt+suspenders)
    # SDG&E QA:   161.209.96.0/24
    # SDG&E PROD: 161.209.202.0/24
    # Note: UFW doesn't support per-user rules, so we allow SSH from all
    # and rely on SSH key auth + chroot for isolation.
    # For stricter control, use iptables directly:
    #   iptables -A INPUT -p tcp --dport 22 -s 161.209.96.0/24 -j ACCEPT
    #   iptables -A INPUT -p tcp --dport 22 -s 161.209.202.0/24 -j ACCEPT
    #   iptables -A INPUT -p tcp --dport 22 -s YOUR_ADMIN_IP -j ACCEPT
    #   iptables -A INPUT -p tcp --dport 22 -j DROP

    ufw --force enable
    echo "UFW firewall configured"
fi

# ── 4. Install watcher dependencies ──────────────────────────────────────────
apt-get update -qq
apt-get install -y -qq curl inotify-tools

# ── 5. Install watcher script ────────────────────────────────────────────────
WATCHER_DIR="/opt/sftp-watcher"
mkdir -p "$WATCHER_DIR"

# Copy the watcher script (assumes it's in the same directory)
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
if [[ -f "${SCRIPT_DIR}/sftp_watcher.sh" ]]; then
    cp "${SCRIPT_DIR}/sftp_watcher.sh" "${WATCHER_DIR}/sftp_watcher.sh"
else
    echo "WARNING: sftp_watcher.sh not found in $SCRIPT_DIR — copy it manually to $WATCHER_DIR/"
fi
chmod +x "${WATCHER_DIR}/sftp_watcher.sh"

# Environment file
mkdir -p /etc/sftp-watcher
cat > /etc/sftp-watcher/env <<'EOF'
# Set these values:
BACKEND_URL=https://your-app.up.railway.app
INGEST_SECRET=your-shared-secret
SFTP_ROOT=/srv/sftp
EOF
chmod 600 /etc/sftp-watcher/env
echo "Created /etc/sftp-watcher/env — edit with your actual values"

# ── 6. Systemd service (watch mode) ──────────────────────────────────────────
cat > /etc/systemd/system/sftp-watcher.service <<'EOF'
[Unit]
Description=SFTP File Watcher — uploads SDG&E files to Railway backend
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
EnvironmentFile=/etc/sftp-watcher/env
ExecStart=/opt/sftp-watcher/sftp_watcher.sh --watch
Restart=always
RestartSec=10
StandardOutput=journal
StandardError=journal

[Install]
WantedBy=multi-user.target
EOF

systemctl daemon-reload
echo "Systemd service created: sftp-watcher"
echo ""
echo "=== Setup complete ==="
echo ""
echo "Next steps:"
echo "  1. Import SDG&E SSH public keys:"
echo "     cat EDIX_MOD_RSA_PUB_KEY_QA.txt   >> /srv/sftp/test/.ssh/authorized_keys"
echo "     cat EDIX_MOD_RSA_PUB_KEY_PROD.txt  >> /srv/sftp/prod/.ssh/authorized_keys"
echo "  2. Edit /etc/sftp-watcher/env with your BACKEND_URL and INGEST_SECRET"
echo "  3. Start the watcher: systemctl enable --now sftp-watcher"
echo "  4. Verify: journalctl -u sftp-watcher -f"
echo ""
echo "Provide SDG&E with:"
echo "  SFTP hostname: $(hostname -f || curl -s ifconfig.me)"
echo "  Port: 22"
echo "  QA username: sdge-test"
echo "  Prod username: sdge-prod"
