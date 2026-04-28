"""
core/fingerprint.py

Homomorphic evaluation fingerprinting over GF(2^8).

The fingerprint of a byte string D at point s is:
  fp(s, D) = D[0] + D[1]*s + D[2]*s^2 + ... (Horner's rule, in GF(2^8))
  = a single byte (GF(2^8) element)

To get FINGERPRINT_LEN bytes of security we evaluate at FINGERPRINT_LEN
independent random points s_0, s_1, ..., s_{L-1}.

Homomorphic property (per-position):
  fp_j(D1 XOR D2) = fp_j(D1) XOR fp_j(D2)

This means fingerprints of erasure-coded fragments are themselves
erasure-coded the same way — the key property AVID-FP relies on.
"""

import hashlib
from core.gf import gf_eval

FINGERPRINT_LEN = 16   # 128-bit fingerprint (16 evaluation points)


def _derive_eval_points(seed: bytes, count: int) -> list[int]:
    """
    Derive `count` distinct GF(2^8) evaluation points from a seed.
    Uses SHA-256 expansion + rejection sampling for uniformity.
    """
    points: list[int] = []
    seen: set[int] = set()
    counter = 0
    while len(points) < count:
        h = hashlib.sha256(seed + counter.to_bytes(4, "big")).digest()
        for byte in h:
            val = byte % 254 + 2   # map to [2, 255] — avoids 0 and 1 (trivial points)
            if val not in seen:
                seen.add(val)
                points.append(val)
                if len(points) == count:
                    break
        counter += 1
    return points


def fingerprint(s: int, data: bytes) -> int:
    """
    Evaluate the polynomial with coefficients=data at point s in GF(2^8).
    Returns a single byte (int in [0, 255]).
    """
    poly = list(data)
    return gf_eval(poly, s)


def multi_fingerprint(eval_points: list[int], data: bytes) -> bytes:
    """
    Compute fingerprints at each point in eval_points.
    Returns FINGERPRINT_LEN bytes.
    """
    return bytes(fingerprint(s, data) for s in eval_points)


def fingerprint_from_seed(seed: bytes, data: bytes) -> bytes:
    """
    High-level API: derive eval points from seed, return FINGERPRINT_LEN-byte fingerprint.
    This is what fpcc.py calls.
    """
    eval_points = _derive_eval_points(seed, FINGERPRINT_LEN)
    return multi_fingerprint(eval_points, data)
