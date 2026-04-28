"""
protocol/avid_fp.py

AVID-FP protocol helpers — the seam between the core math layer and
the server/client HTTP infrastructure.

Public API:
  verify_fragment(fragment, fpcc, server_id, n, m) -> bool
  compute_fpcc_digest(fpcc)                         -> bytes
  encode_fragment(data, n, m)                       -> list[bytes]
  decode_fragments(fragments, n, m)                 -> bytes
  build_fpcc(fragments, n, m)                       -> FPCC
"""

import hashlib
import math
from protocol.messages import Fragment, FPCC


# ---------------------------------------------------------------------------
# Verification (server calls this on /disperse)
# ---------------------------------------------------------------------------

def verify_fragment(
    fragment: Fragment,
    fpcc: FPCC,
    server_id: int,
    n: int,
    m: int,
) -> bool:
    """
    Verify that fragment is consistent with fpcc.
    Implements Definition 3.3 from Hendricks et al., PODC 2007.

    Check (a): SHA-256(fragment.data) == fpcc.cc[fragment.index]
    Check (b): fingerprint(seed, fragment.data) == expected_fingerprint(fpcc, i, n, m)
    """
    from core.fpcc import derive_seed, expected_fingerprint
    from core.fingerprint import fingerprint_from_seed

    i = fragment.index
    data = fragment.data

    # Check (a) — collision-resistant hash
    if hashlib.sha256(data).digest() != fpcc.cc[i]:
        return False

    # Check (b) — homomorphic fingerprint
    seed = derive_seed(fpcc.cc)
    actual_fp   = fingerprint_from_seed(seed, data)
    expected_fp = expected_fingerprint(fpcc, i, n, m)

    return actual_fp == expected_fp


# ---------------------------------------------------------------------------
# FPCC digest for echo/ready messages
# ---------------------------------------------------------------------------

def compute_fpcc_digest(fpcc: FPCC) -> bytes:
    """
    Short digest of the FPCC for use in echo/ready messages.
    SHA-256(cc[0] || cc[1] || ... || fp[0] || fp[1] || ...)
    Both partners must use this — never inline the hash.
    """
    h = hashlib.sha256()
    for c in fpcc.cc:
        h.update(c)
    for f in fpcc.fp:
        h.update(f)
    return h.digest()


# ---------------------------------------------------------------------------
# Erasure coding (client calls these)
# ---------------------------------------------------------------------------

def encode_fragment(data: bytes, n: int, m: int) -> list[bytes]:
    """
    Erasure-encode data into n fragments using our GF(2^8) RS encoder.
    Any m fragments suffice for reconstruction.
    Returns a list of n byte strings, one per server.
    """
    from core.rs import encode as rs_encode
    return rs_encode(data, n, m)


def decode_fragments(
    fragments: list[tuple[int, bytes]],
    n: int,
    m: int,
    original_len: int | None = None,
) -> bytes:
    """
    Reconstruct original data from any m of n (index, data) pairs.
    original_len: if provided, strips zero-padding.
    """
    from core.rs import decode as rs_decode
    return rs_decode(fragments, n, m, original_len)


# ---------------------------------------------------------------------------
# FPCC construction (client calls this after encoding)
# ---------------------------------------------------------------------------

def build_fpcc(fragments: list[bytes], n: int, m: int) -> FPCC:
    """Build an FPCC from n already-encoded fragments."""
    from core.fpcc import build_fpcc as _build
    return _build(fragments, n, m)
