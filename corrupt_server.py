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
  python corrupt_server.py 1                    corrupt server1 (honest opt-out)
  python corrupt_server.py --lie 1              make server1 lie (Byzantine attack)
  python corrupt_server.py --reset 0 1 2 3 4   reset all servers

End-to-end Byzantine demo with Docker:
  1.  docker-compose up --build
  2.  python -m client.cli put testfile.txt
  3.  python corrupt_server.py --lie 0
  4.  python -m client.cli get testfile.txt/chunk/0 recovered.txt
        -> server0's fragment is caught by FPCC verification and discarded
        -> file is reconstructed correctly from the remaining honest servers
"""

import sys
import httpx

SERVER_URLS = [f"http://localhost:{5000+i}" for i in range(5)]
KEY = "testfile.txt/chunk/0"


def corrupt(server_ids: list[int]):
    for i in server_ids:
        try:
            r = httpx.post(f"{SERVER_URLS[i]}/corrupt/{KEY}")
            if r.status_code == 200:
                print(f"  server{i}:  corrupted (stored=False — server opts out of retrieval)")
            elif r.status_code == 404:
                print(f"  server{i}:  nothing stored yet (run put first)")
            else:
                print(f"  server{i}:  {r.status_code} {r.json()}")
        except Exception as e:
            print(f"  server{i}:  offline ({e})")


def lie(server_ids: list[int]):
    for i in server_ids:
        try:
            r = httpx.post(f"{SERVER_URLS[i]}/lie/{KEY}")
            if r.status_code == 200:
                print(f"  server{i}:  lying (stored=True but fragment is corrupted — Byzantine attack)")
            elif r.status_code == 404:
                print(f"  server{i}:  nothing stored yet (run put first)")
            else:
                print(f"  server{i}:  {r.status_code} {r.json()}")
        except Exception as e:
            print(f"  server{i}:  offline ({e})")


def reset(server_ids: list[int]):
    for i in server_ids:
        try:
            r = httpx.post(f"{SERVER_URLS[i]}/reset/{KEY}")
            if r.status_code == 200:
                print(f"  server{i}:  state reset")
            else:
                print(f"  server{i}:  {r.status_code} {r.json()}")
        except Exception as e:
            print(f"  server{i}:  offline ({e})")


args = sys.argv[1:]

if not args:
    print(__doc__)
    sys.exit(0)

if args[0] == "--reset":
    ids = [int(x) for x in args[1:]]
    print(f"\nResetting server(s): {ids}\n")
    reset(ids)
elif args[0] == "--lie":
    ids = [int(x) for x in args[1:]]
    print(f"\nMaking server(s) lie (Byzantine — stored=True, garbage data): {ids}\n")
    lie(ids)
else:
    ids = [int(x) for x in args]
    print(f"\nCorrupting server(s) (honest opt-out — stored=False): {ids}\n")
    corrupt(ids)

print()
