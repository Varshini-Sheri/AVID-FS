"""
corrupt_server.py — corrupt, lie, or reset a server's stored fragment

Modes:
  (default)  /corrupt  — flip bytes AND set stored=False
                         Server opts out of retrieval honestly.
                         Client skips it. No real attack.

  --lie      /lie      — flip bytes but KEEP stored=True
                         Server stays in the retrieval pool and hands back garbage.
                         This is the real Byzantine attack.
                         FPCC verification on retrieval catches and discards it.

  --reset    /reset    — wipe all state for the key

Usage:
  python -m corrupt_server 1                           corrupt server1 (honest opt-out)
  python -m corrupt_server --lie 1                     make server1 lie (Byzantine attack)
  python -m corrupt_server --reset 0 1 2 3 4           reset all servers
  python -m corrupt_server --lie 0 --key test1.txt     target a specific file
  python -m corrupt_server --lie 0 --key myfile/chunk/1  target a specific chunk

If --key is omitted, defaults to testfile.txt/chunk/0.
"""

import os
import sys
import httpx

_default_urls = ",".join(f"http://localhost:{5000+i}" for i in range(5))
SERVER_URLS = [u.strip() for u in os.environ.get("SERVER_URLS", _default_urls).split(",")]
DEFAULT_KEY  = "testfile.txt/chunk/0"


def _build_key(raw: str) -> str:
    """If the user gave a bare filename (no /chunk/), append /chunk/0."""
    if "/chunk/" not in raw:
        return f"{raw}/chunk/0"
    return raw


def corrupt(server_ids: list[int], key: str):
    for i in server_ids:
        try:
            r = httpx.post(f"{SERVER_URLS[i]}/corrupt/{key}")
            if r.status_code == 200:
                print(f"  server{i}:  corrupted (stored=False — server opts out of retrieval)")
            elif r.status_code == 404:
                print(f"  server{i}:  nothing stored yet for key '{key}' (run put first)")
            else:
                print(f"  server{i}:  {r.status_code} {r.json()}")
        except Exception as e:
            print(f"  server{i}:  offline ({e})")


def lie(server_ids: list[int], key: str):
    for i in server_ids:
        try:
            r = httpx.post(f"{SERVER_URLS[i]}/lie/{key}")
            if r.status_code == 200:
                print(f"  server{i}:  lying (stored=True but fragment is corrupted — Byzantine attack)")
            elif r.status_code == 404:
                print(f"  server{i}:  nothing stored yet for key '{key}' (run put first)")
            else:
                print(f"  server{i}:  {r.status_code} {r.json()}")
        except Exception as e:
            print(f"  server{i}:  offline ({e})")


def reset(server_ids: list[int], key: str):
    for i in server_ids:
        try:
            r = httpx.post(f"{SERVER_URLS[i]}/reset/{key}")
            if r.status_code == 200:
                print(f"  server{i}:  state reset")
            else:
                print(f"  server{i}:  {r.status_code} {r.json()}")
        except Exception as e:
            print(f"  server{i}:  offline ({e})")


# ---------------------------------------------------------------------------
# Argument parsing
# ---------------------------------------------------------------------------

args = sys.argv[1:]

if not args:
    print(__doc__)
    sys.exit(0)

# Extract --key VALUE if present (works regardless of position)
key = DEFAULT_KEY
if "--key" in args:
    ki = args.index("--key")
    if ki + 1 >= len(args):
        print("ERROR: --key requires a value, e.g. --key test1.txt/chunk/0")
        sys.exit(1)
    key = _build_key(args[ki + 1])
    args = args[:ki] + args[ki + 2:]

if not args:
    print(__doc__)
    sys.exit(0)

if args[0] == "--reset":
    ids = [int(x) for x in args[1:]]
    print(f"\nResetting server(s) {ids}  key={key}\n")
    reset(ids, key)
elif args[0] == "--lie":
    ids = [int(x) for x in args[1:]]
    print(f"\nMaking server(s) lie (Byzantine — stored=True, garbage data): {ids}  key={key}\n")
    lie(ids, key)
else:
    ids = [int(x) for x in args]
    print(f"\nCorrupting server(s) (honest opt-out — stored=False): {ids}  key={key}\n")
    corrupt(ids, key)

print()
