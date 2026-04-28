"""
client/client.py

Client-side dispersal and retrieval for VerdictFS.
Pass verbose=True to get a full breakdown of encoding, FPCC, and per-server results.
"""

import asyncio
import logging
from dataclasses import dataclass, field

import httpx

from protocol.messages import DisperseRequest, Fragment
from protocol.avid_fp import encode_fragment, decode_fragments, build_fpcc
from fs.chunker import split, join, chunk_key

logger = logging.getLogger(__name__)
DEFAULT_TIMEOUT = 10.0


@dataclass
class ChunkResult:
    chunk_index: int
    key: str
    original_size: int
    fragment_size: int
    fpcc_cc: list[str]        # hex-truncated hashes
    fpcc_fp: list[str]        # hex fingerprints
    server_responses: list[dict] = field(default_factory=list)


@dataclass
class PutResult:
    key: str
    total_size: int
    chunk_count: int
    chunks: list[ChunkResult] = field(default_factory=list)


class VerdictFSClient:
    def __init__(self, server_urls: list[str], n: int, m: int, verbose: bool = False):
        self.server_urls = server_urls
        self.n = n
        self.m = m
        self.verbose = verbose

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    async def put(self, key: str, data: bytes) -> PutResult:
        chunks = split(data)
        logger.info("put key=%s  size=%d  chunks=%d", key, len(data), len(chunks))

        result = PutResult(key=key, total_size=len(data), chunk_count=len(chunks))
        for i, chunk in enumerate(chunks):
            ckey = chunk_key(key, i)
            chunk_result = await self._disperse_chunk(ckey, chunk, i)
            result.chunks.append(chunk_result)

        return result

    async def get(self, key: str, chunk_count: int) -> bytes:
        chunks = []
        for i in range(chunk_count):
            ckey = chunk_key(key, i)
            chunk = await self._retrieve_chunk(ckey)
            chunks.append(chunk)
        return join(chunks)

    async def wait_for_status(self, key: str, timeout: float = 5.0) -> list[dict]:
        """Poll all servers for status of a key. Used by CLI after put."""
        await asyncio.sleep(1.5)  # give echo/ready rounds time to complete
        async with httpx.AsyncClient(timeout=timeout) as client:
            tasks = [
                client.get(f"{url}/status/{key}")
                for url in self.server_urls
            ]
            results = await asyncio.gather(*tasks, return_exceptions=True)

        statuses = []
        for i, r in enumerate(results):
            if isinstance(r, Exception):
                statuses.append({"server": i, "error": str(r)})
            elif r.status_code == 404:
                statuses.append({"server": i, "stored": False, "echo_count": 0,
                                  "ready_count": 0, "verified": False})
            else:
                s = r.json()
                s["server"] = i
                statuses.append(s)
        return statuses

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    async def _disperse_chunk(self, key: str, data: bytes, chunk_index: int) -> ChunkResult:
        fragments = encode_fragment(data, self.n, self.m)
        fpcc = build_fpcc(fragments, self.n, self.m)

        chunk_result = ChunkResult(
            chunk_index=chunk_index,
            key=key,
            original_size=len(data),
            fragment_size=len(fragments[0]),
            fpcc_cc=[h.hex()[:16] + "..." for h in fpcc.cc],
            fpcc_fp=[f.hex() for f in fpcc.fp],
        )

        async with httpx.AsyncClient(timeout=DEFAULT_TIMEOUT) as client:
            tasks = [
                client.post(
                    f"{url}/disperse",
                    json=DisperseRequest(
                        key=key,
                        fragment=Fragment(index=i, data=fragments[i]),
                        fpcc=fpcc,
                    ).model_dump(mode="json"),
                )
                for i, url in enumerate(self.server_urls)
            ]
            results = await asyncio.gather(*tasks, return_exceptions=True)

        for i, r in enumerate(results):
            if isinstance(r, Exception):
                chunk_result.server_responses.append(
                    {"server": i, "status": "unreachable", "error": str(r)}
                )
            else:
                chunk_result.server_responses.append(
                    {"server": i, "status": r.status_code, "body": r.json()}
                )

        failed = sum(1 for r in results if isinstance(r, Exception))
        if failed > self.n - self.m:
            raise RuntimeError(f"Too many servers unreachable: {failed}/{self.n}")

        return chunk_result

    async def _retrieve_chunk(self, key: str) -> bytes:
        async with httpx.AsyncClient(timeout=DEFAULT_TIMEOUT) as client:
            results = await asyncio.gather(
                *[client.get(f"{url}/retrieve/{key}") for url in self.server_urls],
                return_exceptions=True,
            )

        fragments: list[tuple[int, bytes]] = []
        for i, result in enumerate(results):
            if isinstance(result, Exception):
                logger.warning("retrieve from server %d failed: %s", i, result)
                continue
            body = result.json()
            if body.get("stored") and body.get("fragment"):
                frag = body["fragment"]
                from protocol.messages import Fragment as Frag
                import base64
                raw = base64.b64decode(frag["data"]) if isinstance(frag["data"], str) else bytes(frag["data"])
                fragments.append((frag["index"], raw))
            if len(fragments) >= self.m:
                break

        if len(fragments) < self.m:
            raise RuntimeError(
                f"Not enough fragments to reconstruct: got {len(fragments)}, need {self.m}"
            )

        return decode_fragments(fragments, self.n, self.m)