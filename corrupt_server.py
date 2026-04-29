"""
corrupt_server.py — corrupt or reset a server's stored fragment

Usage:
  python corrupt_server.py 1                        corrupt server1
  python corrupt_server.py 1 2 3                    corrupt server1, server2, server3
  python corrupt_server.py --reset 1                reset server1 (wipe its state)
  python corrupt_server.py --reset 0 1 2 3 4        reset all servers

Examples for demo:
  python corrupt_server.py 1
  python corrupt_server.py 2 3
  python corrupt_server.py --reset 0 1 2 3 4
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
                print(f"  server{i}:  ✓ fragment corrupted")
            elif r.status_code == 404:
                print(f"  server{i}:  ✗ nothing stored yet (run put first)")
            else:
                print(f"  server{i}:  ✗ {r.status_code} {r.json()}")
        except Exception as e:
            print(f"  server{i}:  ✗ offline ({e})")


def reset(server_ids: list[int]):
    for i in server_ids:
        try:
            r = httpx.post(f"{SERVER_URLS[i]}/reset/{KEY}")
            if r.status_code == 200:
                print(f"  server{i}:  ✓ state reset")
            else:
                print(f"  server{i}:  ✗ {r.status_code} {r.json()}")
        except Exception as e:
            print(f"  server{i}:  ✗ offline ({e})")


args = sys.argv[1:]

if not args:
    print(__doc__)
    sys.exit(0)

if args[0] == "--reset":
    ids = [int(x) for x in args[1:]]
    print(f"\nResetting server(s): {ids}\n")
    reset(ids)
else:
    ids = [int(x) for x in args]
    print(f"\nCorrupting fragment on server(s): {ids}\n")
    corrupt(ids)

print()