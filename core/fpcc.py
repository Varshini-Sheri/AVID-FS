"""
core/fpcc.py

Fingerprinted Cross-Checksum (FPCC) construction.
Implements Definition 3.2 from Hendricks et al., PODC 2007.

Given n erasure-coded fragments:
  1. cc[i]  = SHA-256(fragment[i])           for i in [0, n)
  2. seed   = SHA-256(cc[0] || ... || cc[n-1])   (random oracle)
  3. fp[i]  = fingerprint_from_seed(seed, fragment[i])   for i in [0, m)

Verification at server i (Definition 3.3):
  (a) SHA-256(fragment[i]) == cc[i]
  (b) fingerprint_from_seed(seed, fragment[i]) == encode_i(fp[0..m-1])
      where encode_i is the i-th row of the systematic erasure code matrix


"""

import hashlib
from protocol.messages import FPCC
from core.fingerprint import fingerprint_from_seed, FINGERPRINT_LEN


# ---------------------------------------------------------------------------
# FPCC construction (called by the client before dispersal)
# ---------------------------------------------------------------------------

def build_fpcc(fragments: list[bytes], n: int, m: int) -> FPCC:
    """
    Build an FPCC from a list of n already-erasure-coded fragments.

    fragments: list of n byte strings (output of encode_fragment)
    n: total number of servers
    m: reconstruction threshold (= number of systematic fragments)

    Returns an FPCC ready to send alongside each fragment.
    """
    assert len(fragments) == n, f"Expected {n} fragments, got {len(fragments)}"
    assert m <= n

    # Step 1: collision-resistant hashes
    cc = [hashlib.sha256(f).digest() for f in fragments]

    # Step 2: random oracle seed from all hashes
    h = hashlib.sha256()
    for c in cc:
        h.update(c)
    seed = h.digest()

    # Step 3: fingerprints of the first m (systematic) fragments only
    fp = [fingerprint_from_seed(seed, fragments[i]) for i in range(m)]

    return FPCC(cc=cc, fp=fp)


# ---------------------------------------------------------------------------
# Seed derivation (shared between build and verify)
# ---------------------------------------------------------------------------

def derive_seed(cc: list[bytes]) -> bytes:
    """Recompute the random oracle seed from a list of fragment hashes."""
    h = hashlib.sha256()
    for c in cc:
        h.update(c)
    return h.digest()


# ---------------------------------------------------------------------------
# FPCC verification helpers (used by protocol/avid_fp.py)
# ---------------------------------------------------------------------------

def expected_fingerprint(fpcc: FPCC, fragment_index: int, n: int, m: int) -> bytes:
    """
    Return the expected FINGERPRINT_LEN-byte fingerprint for fragment at index i.

    For i < m (systematic):  fp[i] directly.
    For i >= m (parity):     erasure-encode fp[0..m-1] to get fp[i].
    """
    i = fragment_index

    if i < m:
        return fpcc.fp[i]

    # Parity fragment: apply the same erasure code to the fingerprint columns.
    # Each column is one byte across all m fingerprints → encode m bytes → take byte i.
    return _encode_fingerprints_at(fpcc.fp, i, n, m)


def _encode_fingerprints_at(fp: list[bytes], target_index: int, n: int, m: int) -> bytes:
    """
    Given fp[0..m-1] (each FINGERPRINT_LEN bytes), return the fingerprint
    that server `target_index` should have, using our GF(2^8) RS encoder.

    Same linear code as encode_fragment → homomorphic property holds.
    """
    from core.rs import encode_fingerprints_at
    return encode_fingerprints_at(fp, target_index, n, m)
