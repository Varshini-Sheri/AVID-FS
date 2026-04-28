"""
core/gf.py

GF(2^8) finite field arithmetic.
Irreducible polynomial: x^8 + x^4 + x^3 + x + 1 (0x11B, AES polynomial).
Generator: 0x03 (= x+1), which has order 255 in GF(2^8) under 0x11B.

Operations:
  gf_add(a, b)     -> XOR
  gf_mul(a, b)     -> multiply via exp/log tables (O(1))
  gf_inv(a)        -> multiplicative inverse
  gf_pow(a, n)     -> a^n
  gf_eval(poly, s) -> evaluate polynomial at s using Horner's rule

All inputs/outputs are integers in [0, 255].
"""

_MODULUS   = 0x11B   # x^8 + x^4 + x^3 + x + 1
_GENERATOR = 0x03    # x + 1  — primitive root of order 255


def _gf_mul_raw(a: int, b: int) -> int:
    """Bitwise GF(2^8) multiply — O(8). Used only to build the tables."""
    result = 0
    while b:
        if b & 1:
            result ^= a
        b >>= 1
        a = (a << 1) ^ (_MODULUS if a & 0x80 else 0)
    return result & 0xFF


def _build_tables() -> tuple[list[int], list[int]]:
    exp = [0] * 512
    log = [0] * 256
    x = 1
    for i in range(255):
        exp[i] = x
        log[x] = i
        x = _gf_mul_raw(x, _GENERATOR)   # x = GENERATOR^(i+1)
    # double the exp table so we can index up to 508 without %
    for i in range(255, 512):
        exp[i] = exp[i - 255]
    return exp, log


_EXP, _LOG = _build_tables()


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def gf_add(a: int, b: int) -> int:
    return a ^ b

def gf_sub(a: int, b: int) -> int:
    return a ^ b

def gf_mul(a: int, b: int) -> int:
    if a == 0 or b == 0:
        return 0
    return _EXP[_LOG[a] + _LOG[b]]

def gf_inv(a: int) -> int:
    if a == 0:
        raise ZeroDivisionError("0 has no inverse in GF(2^8)")
    return _EXP[255 - _LOG[a]]

def gf_div(a: int, b: int) -> int:
    return gf_mul(a, gf_inv(b))

def gf_pow(a: int, n: int) -> int:
    if n == 0:
        return 1
    if a == 0:
        return 0
    return _EXP[(_LOG[a] * n) % 255]

def gf_eval(poly: list[int], s: int) -> int:
    """
    Evaluate polynomial at point s via Horner's rule.
    poly[0] is the constant term: poly[0] + poly[1]*s + poly[2]*s^2 + ...
    """
    result = 0
    for coeff in reversed(poly):
        result = gf_add(gf_mul(result, s), coeff)
    return result
