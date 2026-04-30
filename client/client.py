"""
client/client.py

Client-side dispersal and retrieval for VerdictFS.
Pass verbose=True to get a full breakdown of encoding, FPCC, and per-server results.
"""

import asyncio
import base64
import logging
from collections import Counter
from dataclasses import dataclass, field

import httpx

from protocol.messages import DisperseRequest, Fragment, FPCC
from protocol.avid_fp import encode_fragment, decode_fragments, build_fpcc, verify_fragment, compute_fpcc_digest
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


@dataclass
class ServerRetrieveResult:
    server: int
    reachable: bool
    had_fragment: bool        # server said stored=True
    fpcc_verified: bool       # fragment passed verify_fragment
    used: bool                # fragment was included in the decode
    error: str = ""


@dataclass
class GetResult:
    data: bytes
    key: str
    server_results: list[ServerRetrieveResult] = field(default_factory=list)


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

    async def get(self, key: str, chunk_count: int) -> GetResult:
        """
        key: base object key (e.g. "testfile.txt") OR a full chunk key.
        If key already contains "/chunk/", treat it as a single chunk key directly.
        Otherwise, generate chunk keys internally.
        """
        from fs.chunker import parse_chunk_key
        chunks = []
        all_server_results: list[ServerRetrieveResult] = []
        if parse_chunk_key(key) is not None:
            data, srv = await self._retrieve_chunk(key)
            chunks.append(data)
            all_server_results.extend(srv)
        else:
            for i in range(chunk_count):
                ckey = chunk_key(key, i)
                data, srv = await self._retrieve_chunk(ckey)
                chunks.append(data)
                all_server_results.extend(srv)
        return GetResult(data=join(chunks), key=key, server_results=all_server_results)

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
            if isinstance(r, BaseException):
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
                        fragment=Fragment(index=i, data=fragments[i], original_len=len(data)),
                        fpcc=fpcc,
                    ).model_dump(mode="json"),
                )
                for i, url in enumerate(self.server_urls)
            ]
            results = await asyncio.gather(*tasks, return_exceptions=True)

        for i, r in enumerate(results):
            if isinstance(r, BaseException):
                chunk_result.server_responses.append(
                    {"server": i, "status": "unreachable", "error": str(r)}
                )
            else:
                chunk_result.server_responses.append(
                    {"server": i, "status": r.status_code, "body": r.json()}
                )

        failed = sum(1 for r in results if isinstance(r, BaseException))
        if failed > self.n - self.m:
            raise RuntimeError(f"Too many servers unreachable: {failed}/{self.n}")

        return chunk_result

    async def _retrieve_chunk(
        self, key: str
    ) -> tuple[bytes, list[ServerRetrieveResult]]:
        f = (self.n - self.m) // 2
        srv_results: list[ServerRetrieveResult] = []

        async with httpx.AsyncClient(timeout=DEFAULT_TIMEOUT) as client:
            raw_results = await asyncio.gather(
                *[client.get(f"{url}/retrieve/{key}") for url in self.server_urls],
                return_exceptions=True,
            )

        # Parse every response into a structured record.
        records = []
        for i, result in enumerate(raw_results):
            if isinstance(result, BaseException):
                logger.warning("retrieve from server %d failed: %s", i, result)
                srv_results.append(ServerRetrieveResult(
                    server=i, reachable=False, had_fragment=False,
                    fpcc_verified=False, used=False, error=str(result),
                ))
                continue
            if result.status_code != 200:
                srv_results.append(ServerRetrieveResult(
                    server=i, reachable=True, had_fragment=False,
                    fpcc_verified=False, used=False,
                ))
                continue
            body = result.json()
            if not body.get("stored") or not body.get("fragment"):
                srv_results.append(ServerRetrieveResult(
                    server=i, reachable=True, had_fragment=False,
                    fpcc_verified=False, used=False,
                ))
                continue

            frag_data = body["fragment"]
            raw = (
                base64.b64decode(frag_data["data"])
                if isinstance(frag_data["data"], str)
                else bytes(frag_data["data"])
            )
            fpcc_obj = FPCC(**body["fpcc"]) if body.get("fpcc") else None
            digest   = compute_fpcc_digest(fpcc_obj) if fpcc_obj else None

            records.append({
                "server":       i,
                "frag_index":   frag_data["index"],
                "frag_data":    raw,
                "original_len": frag_data.get("original_len", 0),
                "fpcc":         fpcc_obj,
                "digest":       digest,
            })

        # Paper Figure 4.2 lines 407-408:
        # wait for f+1 servers that agree on the same FPCC digest before
        # trusting any fragments.
        digest_counts = Counter(r["digest"] for r in records if r["digest"] is not None)
        chosen_digest = next(
            (d for d, cnt in digest_counts.most_common() if cnt >= f + 1), None
        )
        if chosen_digest is None:
            raise RuntimeError(
                f"Cannot find f+1={f+1} servers agreeing on an FPCC for key={key}"
            )

        chosen_fpcc = next(r["fpcc"] for r in records if r["digest"] == chosen_digest)

        # Paper Figure 4.2 lines 400-405:
        # verify every candidate fragment, collect results for the caller.
        fragments: list[tuple[int, bytes]] = []
        original_len: int | None = None
        used_indices: set[int] = set()

        for r in records:
            if r["digest"] != chosen_digest:
                srv_results.append(ServerRetrieveResult(
                    server=r["server"], reachable=True, had_fragment=True,
                    fpcc_verified=False, used=False,
                ))
                continue

            frag = Fragment(index=r["frag_index"], data=r["frag_data"],
                            original_len=r["original_len"])
            verified = verify_fragment(frag, chosen_fpcc, r["frag_index"], self.n, self.m)

            if verified:
                if len(fragments) < self.m:
                    fragments.append((r["frag_index"], r["frag_data"]))
                    used_indices.add(r["server"])
                    if original_len is None and r["original_len"] > 0:
                        original_len = r["original_len"]
            else:
                logger.warning(
                    "server %d failed FPCC verification for key=%s — discarding",
                    r["server"], key,
                )

            srv_results.append(ServerRetrieveResult(
                server=r["server"], reachable=True, had_fragment=True,
                fpcc_verified=verified, used=(r["server"] in used_indices),
            ))

        if len(fragments) < self.m:
            raise RuntimeError(
                f"Not enough verified fragments for key={key}: "
                f"got {len(fragments)}, need {self.m}"
            )

        return decode_fragments(fragments, self.n, self.m, original_len), srv_results