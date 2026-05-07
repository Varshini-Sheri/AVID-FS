#!/usr/bin/env bash
# deploy/setup_ec2.sh
#
# Run ONCE on each EC2 instance after cloning the repo.
# Installs dependencies and registers verdictfs as a systemd service.
#
# Usage (on the EC2 instance):
#   SERVER_ID=0 SERVER_URLS="http://<ip0>:5000,...,http://<ip4>:5000" bash deploy/setup_ec2.sh

set -euo pipefail

: "${SERVER_ID:?Set SERVER_ID (0-4) before running}"
: "${SERVER_URLS:?Set SERVER_URLS (comma-separated base URLs) before running}"

REPO_DIR="$(cd "$(dirname "$0")/.." && pwd)"
DATA_DIR="$REPO_DIR/data"
PYTHON="${PYTHON:-python3}"

echo "==> Installing system packages"
if command -v dnf &>/dev/null; then
    sudo dnf install -y python3 python3-pip git
elif command -v yum &>/dev/null; then
    sudo yum install -y python3 python3-pip git
elif command -v apt-get &>/dev/null; then
    sudo apt-get update -y && sudo apt-get install -y python3 python3-pip git
fi

echo "==> Installing Python dependencies"
$PYTHON -m pip install --upgrade pip
$PYTHON -m pip install -r "$REPO_DIR/requirements.txt"

echo "==> Creating data directory: $DATA_DIR"
mkdir -p "$DATA_DIR"

echo "==> Writing environment file: /etc/verdictfs.env"
sudo tee /etc/verdictfs.env > /dev/null <<EOF
SERVER_ID=$SERVER_ID
SERVER_URLS=$SERVER_URLS
N=5
M=3
F=1
DATA_DIR=$DATA_DIR
EOF

UVICORN_BIN="$(command -v uvicorn || $PYTHON -m site --user-base)/bin/uvicorn"

echo "==> Installing systemd service"
sudo tee /etc/systemd/system/verdictfs.service > /dev/null <<EOF
[Unit]
Description=VerdictFS Server Node $SERVER_ID
After=network.target

[Service]
User=$USER
WorkingDirectory=$REPO_DIR
EnvironmentFile=/etc/verdictfs.env
ExecStart=$UVICORN_BIN server.main:app --host 0.0.0.0 --port 5000
Restart=on-failure
RestartSec=3
StandardOutput=journal
StandardError=journal

[Install]
WantedBy=multi-user.target
EOF

sudo systemctl daemon-reload
sudo systemctl enable verdictfs
sudo systemctl start verdictfs

echo ""
echo "==> Done. Server $SERVER_ID is running."
echo "    Check status:  sudo systemctl status verdictfs"
echo "    View logs:     sudo journalctl -u verdictfs -f"
