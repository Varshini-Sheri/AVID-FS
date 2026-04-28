"""
tests/test_integration.py

Integration tests: in-process FastAPI servers via ASGI transport.
Full AVID-FP protocol: disperse → echo/ready rounds → retrieve.

Run with: pytest tests/test_integration.py -v
"""

import asyncio
import pytest
from httpx import AsyncClient, ASGITransport
from fastapi import FastAPI

from server.state import ServerState
from server.routes import init_routes, router as _router
from protocol.avid_fp import encode_fragment, build_fpcc, compute_fpcc_digest
from protocol.messages import Fragment, FPCC, DisperseRequest, EchoMessage, ReadyMessage

N = 5
M = 3
F = 1


def _make_server_app(server_id: int) -> tuple[FastAPI, ServerState]:
    """Create a fresh FastAPI app + state for one server. Peers wired separately."""
    app = FastAPI()
    app.include_router(_router)
    state = ServerState(server_id=server_id, n=N, m=M, f=F)
    return app, state


def _make_client(app: FastAPI, server_id: int) -> AsyncClient:
    return AsyncClient(
        transport=ASGITransport(app=app),
        base_url=f"http://server{server_id}",
    )


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

class TestCluster:

    @pytest.mark.asyncio
    async def test_health_all_servers(self):
        """All 5 servers respond to /health with their server_id."""
        for sid in range(N):
            app, state = _make_server_app(sid)
            init_routes(state, [])
            async with _make_client(app, sid) as client:
                resp = await client.get("/health")
                assert resp.status_code == 200
                assert resp.json()["server_id"] == sid

    @pytest.mark.asyncio
    async def test_disperse_valid_fragment_accepted(self):
        """Server accepts a valid fragment+fpcc pair."""
        app, state = _make_server_app(0)
        init_routes(state, [])  # no peers — isolated test

        data = b"hello verdictfs" * 50
        frags = encode_fragment(data, N, M)
        fpcc  = build_fpcc(frags, N, M)

        req = DisperseRequest(
            key="valid_key",
            fragment=Fragment(index=0, data=frags[0]),
            fpcc=fpcc,
        )

        async with _make_client(app, 0) as client:
            resp = await client.post("/disperse", json=req.model_dump(mode="json"))
            assert resp.status_code == 200
            assert resp.json()["status"] == "accepted"

    @pytest.mark.asyncio
    async def test_corrupted_fragment_rejected(self):
        """Server rejects a fragment whose hash doesn't match fpcc.cc."""
        app, state = _make_server_app(0)
        init_routes(state, [])

        data = b"legitimate data" * 50
        frags = encode_fragment(data, N, M)
        fpcc  = build_fpcc(frags, N, M)

        corrupted = bytearray(frags[0])
        corrupted[5] ^= 0xFF

        req = DisperseRequest(
            key="bad_key",
            fragment=Fragment(index=0, data=bytes(corrupted)),
            fpcc=fpcc,
        )

        async with _make_client(app, 0) as client:
            resp = await client.post("/disperse", json=req.model_dump(mode="json"))
            assert resp.status_code == 400

    @pytest.mark.asyncio
    async def test_echo_messages_counted(self):
        """Echo messages from different senders accumulate in echo_set."""
        app, state = _make_server_app(1)
        init_routes(state, [])

        data = b"echo test" * 100
        frags = encode_fragment(data, N, M)
        fpcc  = build_fpcc(frags, N, M)
        digest = compute_fpcc_digest(fpcc)

        async with _make_client(app, 1) as client:
            for sender in [0, 2, 3]:
                msg = EchoMessage(sender_id=sender, key="echo_key", fpcc_digest=digest)
                resp = await client.post("/echo", json=msg.model_dump(mode="json"))
                assert resp.status_code == 200

            resp = await client.get("/status/echo_key")
            assert resp.json()["echo_count"] == 3

    @pytest.mark.asyncio
    async def test_store_after_2f_plus_1_readys(self):
        """
        Server stores a fragment after:
          (a) /disperse — fragment verified
          (b) 2f+1 = 3 /ready messages received
        """
        app, state = _make_server_app(0)
        init_routes(state, [])

        data = b"store test" * 80
        frags = encode_fragment(data, N, M)
        fpcc  = build_fpcc(frags, N, M)
        digest = compute_fpcc_digest(fpcc)
        key = "store_key"

        async with _make_client(app, 0) as client:
            # Step 1: disperse (verifies fragment)
            req = DisperseRequest(key=key, fragment=Fragment(index=0, data=frags[0]), fpcc=fpcc)
            await client.post("/disperse", json=req.model_dump(mode="json"))

            # Step 2: 2f+1 = 3 ready messages
            for sender in [1, 2, 3]:
                msg = ReadyMessage(sender_id=sender, key=key, fpcc_digest=digest)
                await client.post("/ready", json=msg.model_dump(mode="json"))

            await asyncio.sleep(0.05)  # let background tasks settle

            resp = await client.get(f"/status/{key}")
            assert resp.json()["stored"], "Should be stored after 2f+1 ready messages"

    @pytest.mark.asyncio
    async def test_not_stored_with_insufficient_readys(self):
        """Server does NOT store with fewer than 2f+1 = 3 ready messages."""
        app, state = _make_server_app(0)
        init_routes(state, [])

        data = b"insufficient ready" * 80
        frags = encode_fragment(data, N, M)
        fpcc  = build_fpcc(frags, N, M)
        digest = compute_fpcc_digest(fpcc)
        key = "not_stored_key"

        async with _make_client(app, 0) as client:
            req = DisperseRequest(key=key, fragment=Fragment(index=0, data=frags[0]), fpcc=fpcc)
            await client.post("/disperse", json=req.model_dump(mode="json"))

            # Only 2 ready messages (need 3)
            for sender in [1, 2]:
                msg = ReadyMessage(sender_id=sender, key=key, fpcc_digest=digest)
                await client.post("/ready", json=msg.model_dump(mode="json"))

            resp = await client.get(f"/status/{key}")
            assert not resp.json()["stored"], "Should NOT be stored with only 2 readys"

    @pytest.mark.asyncio
    async def test_retrieve_stored_fragment(self):
        """After storing, /retrieve/{key} returns the fragment."""
        app, state = _make_server_app(0)
        init_routes(state, [])

        data = b"retrieve me" * 80
        frags = encode_fragment(data, N, M)
        fpcc  = build_fpcc(frags, N, M)
        digest = compute_fpcc_digest(fpcc)
        key = "retrieve_key"

        async with _make_client(app, 0) as client:
            req = DisperseRequest(key=key, fragment=Fragment(index=0, data=frags[0]), fpcc=fpcc)
            await client.post("/disperse", json=req.model_dump(mode="json"))

            for sender in [1, 2, 3]:
                msg = ReadyMessage(sender_id=sender, key=key, fpcc_digest=digest)
                await client.post("/ready", json=msg.model_dump(mode="json"))

            await asyncio.sleep(0.05)

            resp = await client.get(f"/retrieve/{key}")
            body = resp.json()
            assert body["stored"]
            assert body["key"] == key


class TestEndToEnd:

    @pytest.mark.asyncio
    async def test_encode_verify_decode_roundtrip(self):
        """Full encode → build_fpcc → verify all → decode cycle."""
        from protocol.avid_fp import decode_fragments, verify_fragment

        original = b"roundtrip data " * 100
        frags = encode_fragment(original, N, M)
        fpcc  = build_fpcc(frags, N, M)

        for i, f in enumerate(frags):
            assert verify_fragment(Fragment(index=i, data=f), fpcc, i, N, M)

        # Reconstruct from fragments {0, 2, 4}
        recovered = decode_fragments(
            [(0, frags[0]), (2, frags[2]), (4, frags[4])], N, M, len(original)
        )
        assert recovered == original

    @pytest.mark.asyncio
    async def test_all_subsets_of_m_reconstruct(self):
        """Every subset of size m recovers the original data."""
        from itertools import combinations
        from protocol.avid_fp import decode_fragments

        original = b"subset recovery " * 200
        frags = encode_fragment(original, N, M)

        for indices in combinations(range(N), M):
            subset = [(i, frags[i]) for i in indices]
            recovered = decode_fragments(subset, N, M, len(original))
            assert recovered == original, f"Failed for subset {indices}"
