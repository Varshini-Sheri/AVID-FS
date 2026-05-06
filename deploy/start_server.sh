#!/usr/bin/env bash
# deploy/start_server.sh
#
# Run on each EC2 instance to start a VerdictFS server node.
#
# Required environment variables:
#   SERVER_ID    — integer index of this server (0–4)
#   SERVER_URLS  — comma-separated list of all 5 server base URLs
#                  e.g. "http://1.2.3.4:5000,http://1.2.3.5:5000,..."
#
# Optional:
#   N (default 5)  M (default 3)  F (default 1)
#   DATA_DIR (default ~/verdictfs/data)
#   REPO_DIR (default ~/verdictfs)

set -euo pipefail

: "${SERVER_ID:?Set SERVER_ID (0-4) before running}"
: "${SERVER_URLS:?Set SERVER_URLS (comma-separated URLs) before running}"

REPO_DIR="${REPO_DIR:-$HOME/verdictfs}"
DATA_DIR="${DATA_DIR:-$REPO_DIR/data}"
N="${N:-5}"
M="${M:-3}"
F="${F:-1}"

mkdir -p "$DATA_DIR"
cd "$REPO_DIR"

export SERVER_ID SERVER_URLS N M F DATA_DIR

exec uvicorn server.main:app --host 0.0.0.0 --port 5000
