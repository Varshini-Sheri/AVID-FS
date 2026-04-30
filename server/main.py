"""
server/main.py

FastAPI application entrypoint for a VerdictFS server node.
Configuration is entirely via environment variables — set in docker-compose.yml.

Required env vars:
  SERVER_ID   — integer index of this server (0-based)
  SERVER_URLS — comma-separated list of ALL server base URLs (including self)
  N           — total number of servers
  M           — reconstruction threshold (erasure coding k)
  F           — max faulty servers

Example (5-server cluster, this is server 0):
  SERVER_ID=0
  SERVER_URLS=http://server0:5000,http://server1:5000,...,http://server4:5000
  N=5  M=3  F=1
"""

import logging
import os
from contextlib import asynccontextmanager

from fastapi import FastAPI

from server.state import ServerState
from server.routes import init_routes, router
from fs.object_store import ObjectStore

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s  %(levelname)-8s  %(name)s  %(message)s",
)
logger = logging.getLogger(__name__)


def _load_config() -> dict:
    return {
        "server_id": int(os.environ["SERVER_ID"]),
        "n": int(os.environ["N"]),
        "m": int(os.environ["M"]),
        "f": int(os.environ["F"]),
        "server_urls": [u.strip() for u in os.environ["SERVER_URLS"].split(",")],
    }


@asynccontextmanager
async def lifespan(app: FastAPI):
    cfg = _load_config()
    logger.info(
        "Starting VerdictFS server id=%d  n=%d  m=%d  f=%d",
        cfg["server_id"], cfg["n"], cfg["m"], cfg["f"],
    )

    state = ServerState(
        server_id=cfg["server_id"],
        n=cfg["n"],
        m=cfg["m"],
        f=cfg["f"],
    )

    store = ObjectStore(data_dir=os.environ.get("DATA_DIR", "/app/data"))

    # Peers = all servers except self
    self_url = cfg["server_urls"][cfg["server_id"]]
    peers = [url for url in cfg["server_urls"] if url != self_url]

    init_routes(state, peers, store)
    logger.info("Peers: %s", peers)

    yield   # server runs here

    logger.info("Shutting down server id=%d", cfg["server_id"])


app = FastAPI(title="VerdictFS", version="0.1.0", lifespan=lifespan)
app.include_router(router)
