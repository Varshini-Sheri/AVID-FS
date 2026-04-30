"""
server/routes.py

FastAPI route handlers for the VerdictFS server node.
Implements the server-side AVID-FP state machine.
"""

import asyncio
import logging
import os
import time

import httpx
from fastapi import APIRouter, HTTPException, BackgroundTasks
from prometheus_client import generate_latest, CONTENT_TYPE_LATEST
from starlette.responses import Response

from protocol.messages import (
    DisperseRequest,
    EchoMessage,
    ReadyMessage,
    RetrieveResponse,
    StatusResponse,
    HealthResponse,
)
from protocol.avid_fp import verify_fragment, compute_fpcc_digest
from server.state import ServerState
from server import metrics

logger = logging.getLogger(__name__)
router = APIRouter()

# ---------------------------------------------------------------------------
# Module-level state — injected by main.py on startup
# ---------------------------------------------------------------------------
_state: ServerState | None = None
_peers: list[str] = []   # list of peer base URLs, e.g. ["http://server2:5000", ...]


def init_routes(state: ServerState, peers: list[str]) -> None:
    global _state, _peers
    _state = state
    _peers = peers


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

async def _broadcast(path: str, payload: dict, timeout: float = 5.0) -> None:
    """
    Fire-and-forget broadcast to all peers concurrently.
    Failures are logged but not raised — Byzantine fault tolerance
    means we expect some servers to be unresponsive.
    """
    async with httpx.AsyncClient(timeout=timeout) as client:
        tasks = [
            client.post(f"{peer}{path}", json=payload)
            for peer in _peers
        ]
        results = await asyncio.gather(*tasks, return_exceptions=True)
    for peer, result in zip(_peers, results):
        if isinstance(result, Exception):
            logger.warning("broadcast to %s%s failed: %s", peer, path, result)


async def _try_store(key: str, ks) -> None:
    """
    Check store threshold and commit if reached. Must be called under ks.lock.
    """
    if not ks.stored and len(ks.ready_set) >= _state.store_threshold:
        # TODO: write fragment to persistent storage (fs/object_store.py)
        ks.stored = True
        metrics.stored_objects_total.inc()
        logger.info("server %d stored key=%s", _state.server_id, key)


async def _maybe_send_ready(key: str, ks, fpcc_digest: bytes) -> None:
    """
    Send READY if we haven't yet and conditions are met. Under ks.lock.
    """
    if ks.ready_sent:
        return

    enough_echos = len(ks.echo_set) >= _state.echo_threshold
    enough_readys = len(ks.ready_set) >= _state.ready_threshold

    if ks.verified and (enough_echos or enough_readys):
        ks.ready_sent = True
        msg = ReadyMessage(
            sender_id=_state.server_id,
            key=key,
            fpcc_digest=fpcc_digest,
        )
        asyncio.create_task(
            _broadcast("/ready", msg.model_dump())
        )


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------

@router.get("/health", response_model=HealthResponse)
async def health() -> HealthResponse:
    return HealthResponse(server_id=_state.server_id)


@router.get("/metrics")
async def prometheus_metrics() -> Response:
    return Response(
        content=generate_latest(),
        media_type=CONTENT_TYPE_LATEST,
    )


@router.post("/disperse")
async def disperse(req: DisperseRequest) -> dict:
    """
    Phase 1: Client sends us our fragment and the fpcc.
    We verify, then broadcast ECHO to all peers.
    """
    metrics.concurrent_dispersals.inc()
    metrics.dispersals_total.labels(status="received").inc()

    ks = await _state.get_or_create(req.key)

    async with ks.lock:
        if ks.stored:
            metrics.concurrent_dispersals.dec()
            return {"status": "already_stored", "key": req.key}

        ks.fragment = req.fragment
        ks.fpcc = req.fpcc
        fpcc_digest = compute_fpcc_digest(req.fpcc)
        ks.fpcc_digest = fpcc_digest

        # Verify fragment against fpcc
        t0 = time.perf_counter()
        ok = verify_fragment(req.fragment, req.fpcc, _state.server_id, _state.n, _state.m)
        metrics.verify_duration_seconds.observe(time.perf_counter() - t0)

        if not ok:
            metrics.dispersals_total.labels(status="invalid").inc()
            metrics.concurrent_dispersals.dec()
            raise HTTPException(status_code=400, detail="Fragment verification failed")

        ks.verified = True

        # Broadcast ECHO to all peers
        if not ks.echoed:
            ks.echoed = True
            echo_msg = EchoMessage(
                sender_id=_state.server_id,
                key=req.key,
                fpcc_digest=fpcc_digest,
            )
            t1 = time.perf_counter()
            asyncio.create_task(
                _broadcast("/echo", echo_msg.model_dump())
            )
            metrics.echo_broadcast_duration_seconds.observe(time.perf_counter() - t1)

    metrics.concurrent_dispersals.dec()
    return {"status": "accepted", "key": req.key}


@router.post("/echo")
async def echo(msg: EchoMessage) -> dict:
    """
    Phase 2: Receive an ECHO from a peer.
    Count echoes; send READY when threshold is reached.
    """
    metrics.echo_messages_total.labels(from_server=str(msg.sender_id)).inc()

    ks = await _state.get_or_create(msg.key)

    async with ks.lock:
        ks.echo_set.add(msg.sender_id)
        if ks.fpcc_digest is None:
            ks.fpcc_digest = msg.fpcc_digest  # may arrive before /disperse completes
        await _maybe_send_ready(msg.key, ks, msg.fpcc_digest)

    return {"status": "ok"}


@router.post("/ready")
async def ready(msg: ReadyMessage) -> dict:
    """
    Phase 3: Receive a READY from a peer.
    Count readys; re-broadcast READY if f+1 received (amplification).
    Store once 2f+1 readys received.
    """
    metrics.ready_messages_total.labels(from_server=str(msg.sender_id)).inc()

    ks = await _state.get_or_create(msg.key)

    async with ks.lock:
        ks.ready_set.add(msg.sender_id)
        fpcc_digest = ks.fpcc_digest or msg.fpcc_digest
        await _maybe_send_ready(msg.key, ks, fpcc_digest)
        await _try_store(msg.key, ks)

    return {"status": "ok"}


@router.get("/retrieve/{key:path}", response_model=RetrieveResponse)
async def retrieve(key: str) -> RetrieveResponse:
    """Return this server's stored fragment for the given key."""
    t0 = time.perf_counter()
    ks = await _state.get(key)
    metrics.retrieve_duration_seconds.observe(time.perf_counter() - t0)

    if ks is None or not ks.stored or ks.fragment is None:
        return RetrieveResponse(key=key, fragment=None, stored=False)

    return RetrieveResponse(key=key, fragment=ks.fragment, fpcc=ks.fpcc, stored=True)


@router.post("/lie/{key:path}")
async def lie(key: str) -> dict:
    """
    DEBUG ONLY — Byzantine mode: corrupt the fragment but keep stored=True.
    Unlike /corrupt, this server still responds to /retrieve with stored=True
    but hands back garbage bytes — simulating a server that actively lies.
    This is the attack that FPCC verification on retrieval is designed to catch.
    """
    ks = await _state.get(key)
    if ks is None or not ks.stored or ks.fragment is None:
        raise HTTPException(status_code=404, detail="Key not found or not stored")

    bad = bytearray(ks.fragment.data)
    for i in range(0, len(bad), 4):
        bad[i] ^= 0b10101010
    from protocol.messages import Fragment as _Frag
    ks.fragment = _Frag(index=ks.fragment.index, data=bytes(bad))
    # stored=True intentionally kept — server lies but stays in the retrieval pool
    logger.warning("DEBUG: server %d is now lying for key=%s", _state.server_id, key)
    return {"status": "lying", "server": _state.server_id, "key": key}


@router.post("/corrupt/{key:path}")
async def corrupt(key: str) -> dict:
    """
    DEBUG ONLY — flip bytes in the stored fragment to simulate a Byzantine server.
    Call this after dispersal to corrupt what this server has stored.
    """
    ks = await _state.get(key)
    if ks is None or not ks.stored or ks.fragment is None:
        raise HTTPException(status_code=404, detail="Key not found or not stored")

    bad = bytearray(ks.fragment.data)
    bad[0] ^= 0xFF
    bad[1] ^= 0xFF
    from protocol.messages import Fragment as _Frag
    ks.fragment = _Frag(index=ks.fragment.index, data=bytes(bad))
    ks.stored   = False   # mark as unstored so /retrieve returns stored=False
    ks.verified = False   # mark as unverified
    logger.warning("DEBUG: corrupted fragment for key=%s on server %d", key, _state.server_id)
    return {"status": "corrupted", "server": _state.server_id, "key": key}


@router.post("/reset/{key:path}")
async def reset_key(key: str) -> dict:
    """DEBUG ONLY — wipe state for a key so it can be re-dispersed."""
    if key in _state._keys:
        del _state._keys[key]
        return {"status": "reset", "key": key}
    raise HTTPException(status_code=404, detail="Key not found")


@router.get("/status/{key:path}", response_model=StatusResponse)
async def status(key: str) -> StatusResponse:
    """Debug endpoint — inspect current state for a key."""
    ks = await _state.get(key)
    if ks is None:
        raise HTTPException(status_code=404, detail="Key not found")
    return StatusResponse(
        key=key,
        stored=ks.stored,
        echo_count=len(ks.echo_set),
        ready_count=len(ks.ready_set),
        verified=ks.verified,
    )