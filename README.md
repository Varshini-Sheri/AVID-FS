# VerdictFS — Fault-Tolerant Distributed File System

**VerdictFS** implements a distributed object store with cryptographic integrity guarantees, based on the AVID-FP protocol described in:

> Hendricks, Ganger, and Reiter. *"Verifying Distributed Erasure-Coded Data."* PODC 2007.

Data is split across **5 storage servers** using Reed-Solomon erasure coding over GF(2⁸), protected by homomorphic fingerprinting. Any 3 servers can reconstruct a stored object, and Byzantine tampering by up to 1 server is detected before data is returned to the client.

---

## Architecture Overview

```
Client CLI
    │
    │  PUT: HTTP POST /disperse  (fragment_i + fpcc → server_i)
    │
    ├──► server0 :5001 ──┐
    ├──► server1 :5002    │  AVID-FP echo/ready rounds
    ├──► server2 :5003    │  (servers talk to each other via /echo, /ready)
    ├──► server3 :5004    │
    └──► server4 :5005 ──┘
    │
    │  GET: HTTP GET /retrieve (any m=3 servers reconstruct object)
```

**Parameters:** n=5 total servers, m=3 reconstruction threshold, f=1 Byzantine fault tolerance.

---

## Project Structure

```
verdictfs/
├── requirements.txt
├── docker-compose.yml
├── prometheus.yml               ← Prometheus scrape config
├── corrupt_server.py            ← Byzantine fault injection tool
├── demo.sh                      ← Automated fault-tolerance demo script
│
├── deploy/
│   ├── start_server.sh          ← EC2 node startup script (manual / nohup)
│   ├── setup_ec2.sh             ← One-time EC2 instance setup + systemd registration
│   └── demo_ec2.sh              ← EC2-adapted demo (SSH-based server management)
│
├── core/
│   ├── gf.py                    ← GF(2⁸) finite field arithmetic
│   ├── fingerprint.py           ← Homomorphic fingerprinting
│   ├── rs.py                    ← Custom Reed-Solomon over gf.py
│   └── fpcc.py                  ← Fingerprinted cross-checksum
│
├── protocol/
│   ├── messages.py              ← Pydantic message models
│   └── avid_fp.py               ← AVID-FP disperse/retrieve logic
│
├── server/
│   ├── main.py                  ← Uvicorn entry point
│   ├── routes.py                ← FastAPI endpoints
│   ├── state.py                 ← Per-key echo/ready sets + async locks
│   ├── metrics.py               ← Prometheus instrumentation
│   └── Dockerfile
│
├── fs/
│   ├── object_store.py          ← In-memory key/value store
│   ├── chunker.py               ← Large file → chunk blocks
│   └── metadata.py              ← File → {fpcc, chunk info} mapping
│
├── client/
│   ├── client.py                ← Async dispersal / retrieval
│   └── cli.py                   ← `python -m client.cli` entry point
│
└── tests/
    ├── test_core.py             ← GF(2⁸), fingerprint, RS, and FPCC unit tests
    ├── test_integration.py      ← AVID-FP encode/decode round-trip tests
    ├── test_server_smoke.py     ← Server endpoint smoke tests
    └── test_byzantine.py        ← Byzantine fault simulation tests
```

---

## Prerequisites

| Tool | Minimum Version | Check |
|------|----------------|-------|
| Python | 3.11 | `python --version` |
| Docker | 24.x | `docker --version` |
| Docker Compose | 2.x (plugin) | `docker compose version` |

No other system dependencies are needed — everything runs inside Docker containers.

---

## Quick Start

### 1. Enter the project directory

```bash
cd verdictfs
```

### 2. Install Python client dependencies (for the CLI)

```bash
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

### 3. Start the 5-server cluster

```bash
docker compose up -d
```

Wait ~10 seconds for all servers to report `Application startup complete`.

```bash
# Verify all 5 servers are healthy
for port in 5001 5002 5003 5004 5005; do
  echo -n "server @ :$port  → "
  curl -s http://localhost:$port/health | python -c "import sys,json; d=json.load(sys.stdin); print(d['status'])"
done
```

Expected output:
```
server @ :5001  → ok
server @ :5002  → ok
server @ :5003  → ok
server @ :5004  → ok
server @ :5005  → ok
```

### 4. Store and retrieve a file

```bash
# Create a sample file
echo "Hello from VerdictFS!" > hello.txt

# Store across all 5 servers
python -m client.cli put hello.txt

# Retrieve  (key + output file are positional arguments)
python -m client.cli get hello.txt retrieved.txt

# Verify byte-for-byte match
cmp -s hello.txt retrieved.txt && echo "✓ Files match"
```

### 5. Check server status for a stored object

```bash
python -m client.cli status hello.txt
```

---

## CLI Reference

```
python -m client.cli put    <file>
python -m client.cli get    <key>  <output_file>
python -m client.cli status <key>
```

| Command | Description |
|---------|-------------|
| `put <file>` | Disperse file across all 5 servers via AVID-FP |
| `get <key> <output_file>` | Retrieve and reconstruct from any 3 servers, write to output\_file |
| `status <key>` | Show per-server stored/echo/ready counts |

---

## Byzantine Fault Injection

`corrupt_server.py` injects faults into a running server. It exposes three modes:

| Mode | Command | Behaviour |
|------|---------|-----------|
| **lie** (Byzantine) | `--lie <id>` | Flips fragment bytes but keeps `stored=True`. Server stays in the retrieval pool and returns garbage. FPCC verification catches and discards it. This is the real Byzantine attack. |
| **corrupt** (honest opt-out) | *(default, no flag)* | Flips bytes and sets `stored=False`. Server removes itself from retrieval honestly — not a Byzantine fault. |
| **reset** | `--reset <id> [id …]` | Wipes all state for the key on the specified server(s). |

```bash
# Byzantine attack: server3 lies (stored=True, corrupted fragment)
python -m corrupt_server --lie 3 --key test1.txt

# Honest corruption: server3 opts out of retrieval (stored=False)
python -m corrupt_server 3 --key test1.txt

# Reset state on all servers for a key
python -m corrupt_server --reset 0 1 2 3 4 --key test1.txt
```

`--key` accepts a bare filename (e.g. `test1.txt`) — the script automatically targets `chunk/0`. For multi-chunk files pass the full key (e.g. `test1.txt/chunk/1`). If `--key` is omitted it defaults to `testfile.txt/chunk/0`.

The `--lie` mode is the strongest form of fault the protocol is designed to detect: the server passes health checks, participates in echo/ready rounds, and only returns a poisoned fragment on retrieval.

---

## Fault Tolerance Demos

### Demo 1 — Byzantine fault **before** PUT (pre-store corruption)

This is the strongest correctness scenario: a server is already adversarial when the
client disperses the file. Because the AVID-FP echo/ready protocol only requires n−f=4
honest responses, the disperse still completes successfully. On retrieval, the FPCC
fingerprint check catches the tampered fragment before it is used in reconstruction.

```bash
# 1. Start the cluster
docker compose up -d && sleep 10

# 2. Corrupt server3 BEFORE storing anything
python -m corrupt_server --lie 3 --key test1.txt

# 3. PUT the file  (4 honest servers satisfy echo/ready thresholds; disperse succeeds)
python -m client.cli put test1.txt

# 4. GET  (FPCC rejects server3's fragment; reconstructs from servers 0, 1, 2, 4)
python -m client.cli get test1.txt recovered_prebyz.txt

cmp -s test1.txt recovered_prebyz.txt && echo "✓ Correct data returned despite pre-store Byzantine server"
```

### Demo 2 — Byzantine fault **after** PUT (post-store corruption)

A server is compromised after a successful disperse. On retrieval the FPCC detects the
tampered fragment and the client reconstructs from the remaining honest servers.

```bash
# 1. Store first (all servers honest)
python -m client.cli put test1.txt

# 2. Corrupt server3 after the fact
python -m corrupt_server --lie 3 --key test1.txt

# 3. GET  (server3 rejected by FPCC; reconstructed from 4 clean servers)
python -m client.cli get test1.txt recovered_postbyz.txt

cmp -s test1.txt recovered_postbyz.txt && echo "✓ Correct data returned despite post-store Byzantine server"
```

### Demo 3 — Crash fault (erasure coding)

Stop a server entirely. The remaining 4 fragments are enough to reconstruct (need only m=3).
Docker container names follow the Compose convention `verdictfs-server<N>-1`.

```bash
# Stop server2
docker stop verdictfs-server2-1

# Retrieve with only 4/5 servers available
python -m client.cli get test1.txt recovered_crash.txt

cmp -s test1.txt recovered_crash.txt && echo "✓ Retrieved with 1 server offline"

# Bring it back
docker start verdictfs-server2-1
```

### Demo 4 — 1 Byzantine + 1 offline (combined faults, should succeed)

With f=1, the system tolerates one Byzantine server. Combined with one crash, there are
still 3 clean fragments — exactly the reconstruction threshold.

```bash
# Take server2 offline
docker stop verdictfs-server2-1

# Corrupt server3
python -m corrupt_server --lie 3 --key test1.txt

# GET: server3 rejected by FPCC, server2 offline → servers 0, 1, 4 suffice
python -m client.cli get test1.txt recovered_combined.txt

cmp -s test1.txt recovered_combined.txt && echo "✓ Reconstructed from 3 clean fragments (servers 0, 1, 4)"

# Restore
docker start verdictfs-server2-1
```

### Demo 5 — 2 Byzantine + 1 offline (expected failure)

Exceeds the protocol's tolerance. Only 2 verified fragments remain — below the
reconstruction threshold of m=3. The client should report an error and refuse to return data.

```bash
# Take server2 offline
docker stop verdictfs-server2-1

# Corrupt server0 and server3
python -m corrupt_server --lie 0 --key test1.txt
python -m corrupt_server --lie 3 --key test1.txt

# Attempt retrieval — expected to FAIL (this is correct protocol behaviour)
python -m client.cli get test1.txt recovered_fail.txt
# Expected: error — only 2 verified fragments available, need 3
```

### Demo 6 — Full cluster restart

```bash
docker compose down
docker compose up -d
sleep 10

python -m client.cli get test1.txt recovered_restart.txt
cmp -s test1.txt recovered_restart.txt && echo "✓ Clean retrieval after full restart"
```

---

## Full Automated Demo

The included script runs all the above scenarios in sequence with colour-coded pass/fail output:

```bash
chmod +x demo.sh
./demo.sh
```

---

## Prometheus Metrics

All servers expose Prometheus metrics at `/metrics`. A Prometheus instance is included in the compose stack.

```bash
# View raw metrics from server0
curl -s http://localhost:5001/metrics | grep verdictfs

# Open Prometheus UI
open http://localhost:9090
```

| Metric | Description |
|--------|-------------|
| `verdictfs_dispersals_total` | Total disperse calls received |
| `verdictfs_echo_messages_total` | Echo messages processed |
| `verdictfs_ready_messages_total` | Ready messages processed |
| `verdictfs_stored_objects_total` | Objects committed to storage |
| `verdictfs_retrieve_duration_seconds` | Retrieval latency histogram |
| `verdictfs_verify_duration_seconds` | Fingerprint verification latency |

---

## Running the Test Suite

```bash
# All unit tests (no Docker required)
python -m pytest tests/ -v

# Individual suites
python -m pytest tests/test_core.py -v          # GF(2⁸), fingerprint, RS, and FPCC unit tests
python -m pytest tests/test_integration.py -v   # AVID-FP encode/decode round-trip
python -m pytest tests/test_server_smoke.py -v  # Server endpoint smoke tests
python -m pytest tests/test_byzantine.py -v     # Byzantine fault simulation
```

---

## Stopping the Cluster

```bash
docker compose down
```

To also remove persisted volumes:

```bash
docker compose down -v
```

---

