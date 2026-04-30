#!/usr/bin/env bash
# =============================================================================
#  VerdictFS  —  Fault-Tolerance Demo Script
#  1. PUT baseline
#  2. GET baseline
#  3. Crash server2 offline
#  4. GET with 1 offline
#  5. Corrupt server3  →  1 Byzantine + 1 offline  (succeeds)
#  6. Corrupt server0  →  2 Byzantine + 1 offline  (expected FAILURE)
#  7. docker compose down  (full cluster restart)
#  8. docker compose up
#  9. GET  →  clean recovery
# =============================================================================

set -euo pipefail

# ── Config ────────────────────────────────────────────────────────────────────
FILE="test1.txt"
KEY="test1.txt"
PYTHON="python"
CLI="$PYTHON -m client.cli"
CORRUPT="$PYTHON -m corrupt_server"
OFFLINE_SERVER="verdictfs-server2-1"
SLEEP_BETWEEN=2

# ── Colours ───────────────────────────────────────────────────────────────────
RED='\033[0;31m'; GREEN='\033[0;32m'; YELLOW='\033[1;33m'
CYAN='\033[0;36m'; BOLD='\033[1m'; RESET='\033[0m'

banner()  { echo -e "\n${CYAN}${BOLD}══════════════════════════════════════════════════════${RESET}"
            echo -e "${CYAN}${BOLD}  $1${RESET}"
            echo -e "${CYAN}${BOLD}══════════════════════════════════════════════════════${RESET}\n"; }
ok()      { echo -e "${GREEN}✓ $1${RESET}"; }
warn()    { echo -e "${YELLOW}⚠ $1${RESET}"; }
info()    { echo -e "  $1"; }
fail_ok() { echo -e "${RED}✗ $1  (expected — this is correct behaviour)${RESET}"; }

pause() { sleep "$SLEEP_BETWEEN"; }

verify_files_match() {
    if cmp -s "$1" "$2"; then
        ok "Files match byte-for-byte: $1 == $2"
    else
        echo -e "${RED}✗ Files differ: $1 vs $2${RESET}"; exit 1
    fi
}

# ── Pre-flight ────────────────────────────────────────────────────────────────
banner "0  Pre-flight"

if [ ! -f "$FILE" ]; then
    info "Creating sample file: $FILE"
    python -c "
import random, string
random.seed(42)
lines = [''.join(random.choices(string.ascii_letters + ' ', k=72)) for _ in range(74)]
print('\n'.join(lines))
" > "$FILE"
fi

info "File: $FILE"
info "Bringing all servers up..."
docker compose up -d
pause
ok "All 5 servers running"

# ── Step 1: Baseline PUT ──────────────────────────────────────────────────────
banner "1  PUT — store file across all 5 servers"

$CLI put "$FILE"
ok "Put complete"
pause

# ── Step 2: Baseline GET ──────────────────────────────────────────────────────
banner "2  GET — all 5 servers healthy (baseline)"

$CLI get "$KEY" received_baseline.txt
verify_files_match "$FILE" received_baseline.txt
pause

# ── Step 3: Take server2 offline ─────────────────────────────────────────────
banner "3  CRASH FAULT — taking $OFFLINE_SERVER offline"

docker stop "$OFFLINE_SERVER"
warn "$OFFLINE_SERVER is now offline"
pause

# ── Step 4: GET with 1 server offline ────────────────────────────────────────
banner "4  GET — 1 offline  (4/5 fragments, need 3)"

$CLI get "$KEY" received_1offline.txt
verify_files_match "$FILE" received_1offline.txt
ok "Reconstruction succeeded with 4 fragments"
pause

# ── Step 5: 1 Byzantine + 1 offline  →  should still succeed ─────────────────
banner "5  BYZANTINE — corrupt server3  (1 Byzantine + 1 offline, need 3)"

$CORRUPT --lie 3 --key "$KEY"
warn "server3 is serving corrupted data but claiming stored=True"
warn "$OFFLINE_SERVER still offline  →  3 clean fragments remain (server0, server1, server4)"
pause

$CLI get "$KEY" received_1byz.txt
verify_files_match "$FILE" received_1byz.txt
ok "FPCC caught server3 — reconstructed from 3 clean fragments"
pause

# ── Step 6: 2 Byzantine + 1 offline  →  expected FAILURE ─────────────────────
banner "6  BYZANTINE — corrupt server0  (2 Byzantine + 1 offline, expected FAILURE)"

$CORRUPT --lie 0 --key "$KEY"
warn "server0 is now also Byzantine"
warn "Verified fragments: only server1 + server4 remain  →  2 < 3 needed"
pause

set +e
$CLI get "$KEY" received_2byz.txt
exit_code=$?
set -e

if [ $exit_code -ne 0 ]; then
    fail_ok "GET correctly failed — only 2 verified fragments available, need 3"
else
    echo -e "${RED}✗ GET should have failed but succeeded — check FPCC logic!${RESET}"; exit 1
fi
pause

# ── Step 7: Full cluster restart ─────────────────────────────────────────────
banner "7  RESTART — docker compose down (full cluster teardown)"

docker compose down
warn "All containers stopped and removed"
pause

# ── Step 8: Bring everything back up ─────────────────────────────────────────
banner "8  RESTART — docker compose up"

docker compose up -d
info "Waiting for all servers to become ready..."
sleep 10
ok "All 5 servers back online"

# ── Step 9: GET after full restart ───────────────────────────────────────────
banner "9  GET — after full cluster restart"

$CLI get "$KEY" received_recovered.txt
verify_files_match "$FILE" received_recovered.txt
ok "File retrieved cleanly after full restart"

# ── Summary ───────────────────────────────────────────────────────────────────
banner "Demo complete — summary"

echo -e "  ${BOLD}Test case                                Result${RESET}"
echo    "  ───────────────────────────────────────────────────"
echo -e "  Baseline put + get                       ${GREEN}PASS${RESET}"
echo -e "  1 server offline (crash fault)           ${GREEN}PASS${RESET}  (4/5 fragments)"
echo -e "  1 Byzantine + 1 offline                  ${GREEN}PASS${RESET}  (FPCC excluded server3)"
echo -e "  2 Byzantine + 1 offline                  ${RED}FAIL${RESET}  (correct — below threshold)"
echo -e "  Full restart  →  clean retrieval         ${GREEN}PASS${RESET}"
echo ""