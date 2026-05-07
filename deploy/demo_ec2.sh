#!/usr/bin/env bash
# deploy/demo_ec2.sh
#
# EC2-adapted version of demo.sh.
# Replaces docker compose / docker stop commands with SSH calls to each instance.
#
# Configure the five variables below before running, then:
#   bash deploy/demo_ec2.sh
#
# Requirements on your local machine:
#   - SSH key with access to all 5 instances
#   - SERVER_URLS exported (or set below)
#   - Python client dependencies installed (pip install -r requirements.txt)

set -euo pipefail

# ── Configure these ───────────────────────────────────────────────────────────
IP0="<ec2-ip-server0>"
IP1="<ec2-ip-server1>"
IP2="<ec2-ip-server2>"
IP3="<ec2-ip-server3>"
IP4="<ec2-ip-server4>"
SSH_KEY="~/.ssh/verdictfs-key.pem"   # path to your EC2 key pair
SSH_USER="ec2-user"                   # Amazon Linux default; use "ubuntu" on Ubuntu AMIs
# ─────────────────────────────────────────────────────────────────────────────

export SERVER_URLS="http://$IP0:5000,http://$IP1:5000,http://$IP2:5000,http://$IP3:5000,http://$IP4:5000"
IPS=("$IP0" "$IP1" "$IP2" "$IP3" "$IP4")

FILE="test1.txt"
KEY="test1.txt"
PYTHON="python3"
CLI="$PYTHON -m client.cli"
CORRUPT="$PYTHON -m corrupt_server"
SLEEP_BETWEEN=3

RED='\033[0;31m'; GREEN='\033[0;32m'; YELLOW='\033[1;33m'
CYAN='\033[0;36m'; BOLD='\033[1m'; RESET='\033[0m'

banner()  { echo -e "\n${CYAN}${BOLD}══════════════════════════════════════════════════════${RESET}"
            echo -e "${CYAN}${BOLD}  $1${RESET}"
            echo -e "${CYAN}${BOLD}══════════════════════════════════════════════════════${RESET}\n"; }
ok()      { echo -e "${GREEN}✓ $1${RESET}"; }
warn()    { echo -e "${YELLOW}⚠ $1${RESET}"; }
info()    { echo -e "  $1"; }
fail_ok() { echo -e "${RED}✗ $1  (expected — correct behaviour)${RESET}"; }

ssh_cmd() {
    local id=$1; shift
    ssh -i "$SSH_KEY" -o StrictHostKeyChecking=no "$SSH_USER@${IPS[$id]}" "$@"
}

restart_server() {
    local id=$1
    info "Restarting server$id on ${IPS[$id]}..."
    ssh_cmd "$id" "sudo systemctl restart verdictfs"
    sleep 5
    ok "server$id restarted"
}

stop_server() {
    local id=$1
    warn "Stopping server$id on ${IPS[$id]}..."
    ssh_cmd "$id" "sudo systemctl stop verdictfs"
}

start_server() {
    local id=$1
    info "Starting server$id on ${IPS[$id]}..."
    ssh_cmd "$id" "sudo systemctl start verdictfs"
    sleep 3
}

verify_files_match() {
    if cmp -s "$1" "$2"; then
        ok "Files match byte-for-byte: $1 == $2"
    else
        echo -e "${RED}✗ Files differ: $1 vs $2${RESET}"; exit 1
    fi
}

check_health() {
    banner "0  Pre-flight — checking all 5 servers"
    for i in 0 1 2 3 4; do
        status=$(curl -s --max-time 3 "http://${IPS[$i]}:5000/health" | python3 -c "import sys,json; print(json.load(sys.stdin)['status'])" 2>/dev/null || echo "unreachable")
        if [ "$status" = "ok" ]; then
            ok "server$i @ ${IPS[$i]}:5000  →  ok"
        else
            echo -e "${RED}✗ server$i @ ${IPS[$i]}:5000  →  $status${RESET}"
            echo "   Start it with:  ssh -i $SSH_KEY $SSH_USER@${IPS[$i]} 'sudo systemctl start verdictfs'"
            exit 1
        fi
    done
}

# ── Pre-flight ────────────────────────────────────────────────────────────────
check_health

if [ ! -f "$FILE" ]; then
    info "Creating sample file: $FILE"
    python3 -c "
import random, string
random.seed(42)
lines = [''.join(random.choices(string.ascii_letters + ' ', k=72)) for _ in range(74)]
print('\n'.join(lines))
" > "$FILE"
fi

# ── Step 1: Byzantine fault BEFORE PUT ───────────────────────────────────────
banner "1  BYZANTINE BEFORE PUT — lie on server3 before dispersal"
$CORRUPT --lie 3 --key "$KEY"
warn "server3 is now Byzantine"
sleep "$SLEEP_BETWEEN"

$CLI put "$FILE"
ok "PUT succeeded (4 honest servers satisfied echo/ready thresholds)"
sleep "$SLEEP_BETWEEN"

$CLI get "$KEY" received_prebyz.txt
verify_files_match "$FILE" received_prebyz.txt
ok "FPCC detected and excluded server3"
sleep "$SLEEP_BETWEEN"

# ── Step 2: Reset server3 ────────────────────────────────────────────────────
banner "2  RESET — restart server3 to clear corruption"
restart_server 3
sleep "$SLEEP_BETWEEN"

# ── Step 3: Baseline PUT ──────────────────────────────────────────────────────
banner "3  PUT — all 5 servers honest"
$CLI put "$FILE"
ok "Put complete"
sleep "$SLEEP_BETWEEN"

# ── Step 4: Baseline GET ──────────────────────────────────────────────────────
banner "4  GET — baseline (all healthy)"
$CLI get "$KEY" received_baseline.txt
verify_files_match "$FILE" received_baseline.txt
sleep "$SLEEP_BETWEEN"

# ── Step 5: Take server2 offline ─────────────────────────────────────────────
banner "5  CRASH FAULT — stopping server2"
stop_server 2
sleep "$SLEEP_BETWEEN"

# ── Step 6: GET with 1 offline ───────────────────────────────────────────────
banner "6  GET — 1 offline (4/5 fragments, need 3)"
$CLI get "$KEY" received_1offline.txt
verify_files_match "$FILE" received_1offline.txt
ok "Reconstruction succeeded with 4 fragments"
sleep "$SLEEP_BETWEEN"

# ── Step 7: 1 Byzantine + 1 offline ─────────────────────────────────────────
banner "7  BYZANTINE — corrupt server3  (1 Byzantine + 1 offline)"
$CORRUPT --lie 3 --key "$KEY"
warn "server3 lying; server2 offline  →  3 clean fragments remain (0, 1, 4)"
sleep "$SLEEP_BETWEEN"

$CLI get "$KEY" received_1byz.txt
verify_files_match "$FILE" received_1byz.txt
ok "FPCC caught server3 — reconstructed from servers 0, 1, 4"
sleep "$SLEEP_BETWEEN"

# ── Step 8: 2 Byzantine + 1 offline  →  expected FAILURE ─────────────────────
banner "8  BYZANTINE — corrupt server0  (2 Byzantine + 1 offline, expected FAILURE)"
$CORRUPT --lie 0 --key "$KEY"
warn "server0 also Byzantine; only server1 + server4 clean  →  2 < 3 needed"
sleep "$SLEEP_BETWEEN"

set +e
$CLI get "$KEY" received_2byz.txt
exit_code=$?
set -e

if [ $exit_code -ne 0 ]; then
    fail_ok "GET correctly failed — only 2 verified fragments, need 3"
else
    echo -e "${RED}✗ GET should have failed — check FPCC logic!${RESET}"; exit 1
fi
sleep "$SLEEP_BETWEEN"

# ── Step 9/10: Full restart ───────────────────────────────────────────────────
banner "9  RESTART — stopping all 5 servers"
for i in 0 1 2 3 4; do stop_server $i; done
sleep 3

banner "10  RESTART — starting all 5 servers"
for i in 0 1 2 3 4; do start_server $i; done
sleep 8
ok "All 5 servers back online"

# ── Step 11: GET after full restart ───────────────────────────────────────────
banner "11  GET — after full cluster restart"
$CLI get "$KEY" received_recovered.txt
verify_files_match "$FILE" received_recovered.txt
ok "File retrieved cleanly after full restart"

# ── Summary ───────────────────────────────────────────────────────────────────
banner "Demo complete"
echo -e "  ${BOLD}Test case                                Result${RESET}"
echo    "  ───────────────────────────────────────────────────"
echo -e "  Byzantine BEFORE put                     ${GREEN}PASS${RESET}"
echo -e "  Baseline put + get                       ${GREEN}PASS${RESET}"
echo -e "  1 server offline (crash fault)           ${GREEN}PASS${RESET}"
echo -e "  1 Byzantine + 1 offline                  ${GREEN}PASS${RESET}"
echo -e "  2 Byzantine + 1 offline                  ${RED}FAIL${RESET}  (correct — below threshold)"
echo -e "  Full restart → clean retrieval           ${GREEN}PASS${RESET}"
echo ""
