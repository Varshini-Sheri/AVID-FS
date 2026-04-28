"""
tests/test_core.py

Unit tests for GF(2^8), fingerprinting, FPCC, and end-to-end verification.
Run with: pytest tests/test_core.py -v
"""

import hashlib
import pytest
from core.gf import gf_add, gf_mul, gf_inv, gf_pow, gf_eval
from core.fingerprint import fingerprint, multi_fingerprint, fingerprint_from_seed, FINGERPRINT_LEN
from core.fpcc import build_fpcc, derive_seed, expected_fingerprint
from protocol.avid_fp import encode_fragment, decode_fragments, build_fpcc as avid_build_fpcc, verify_fragment
from protocol.messages import Fragment, FPCC


# ---------------------------------------------------------------------------
# GF(2^8) field axioms
# ---------------------------------------------------------------------------

class TestGF:
    def test_add_is_xor(self):
        for a in [0, 1, 127, 255]:
            for b in [0, 1, 128, 255]:
                assert gf_add(a, b) == (a ^ b)

    def test_add_identity(self):
        for a in range(256):
            assert gf_add(a, 0) == a

    def test_add_self_is_zero(self):
        for a in range(256):
            assert gf_add(a, a) == 0

    def test_mul_by_zero(self):
        for a in range(256):
            assert gf_mul(a, 0) == 0
            assert gf_mul(0, a) == 0

    def test_mul_by_one(self):
        for a in range(256):
            assert gf_mul(a, 1) == a

    def test_mul_commutativity(self):
        assert gf_mul(3, 5) == gf_mul(5, 3)
        assert gf_mul(0x53, 0xCA) == gf_mul(0xCA, 0x53)

    def test_mul_associativity(self):
        a, b, c = 7, 11, 13
        assert gf_mul(gf_mul(a, b), c) == gf_mul(a, gf_mul(b, c))

    def test_distributivity(self):
        a, b, c = 0x57, 0x83, 0x42
        assert gf_mul(a, gf_add(b, c)) == gf_add(gf_mul(a, b), gf_mul(a, c))

    def test_inverse(self):
        for a in range(1, 256):
            assert gf_mul(a, gf_inv(a)) == 1

    def test_inverse_zero_raises(self):
        with pytest.raises(ZeroDivisionError):
            gf_inv(0)

    def test_pow(self):
        assert gf_pow(2, 0) == 1
        assert gf_pow(2, 1) == 2
        assert gf_pow(2, 8) == gf_mul(gf_mul(gf_mul(2, 2), gf_mul(2, 2)), gf_mul(gf_mul(2, 2), gf_mul(2, 2)))

    def test_eval_constant(self):
        # Polynomial = [c] → evaluates to c for any s
        for c in [0, 1, 42, 255]:
            assert gf_eval([c], 7) == c

    def test_eval_linear(self):
        # p(x) = a + b*x  → p(s) = a XOR (b*s)
        a, b, s = 3, 5, 7
        assert gf_eval([a, b], s) == gf_add(a, gf_mul(b, s))


# ---------------------------------------------------------------------------
# Fingerprinting — homomorphic property
# ---------------------------------------------------------------------------

class TestFingerprint:
    def test_fingerprint_single_byte(self):
        # fp(s, [b]) = b  (constant polynomial)
        assert fingerprint(5, bytes([42])) == 42

    def test_homomorphic_property(self):
        """
        fp(s, D1 XOR D2) == fp(s, D1) XOR fp(s, D2)
        This is the key property AVID-FP relies on.
        """
        import os
        d1 = os.urandom(64)
        d2 = os.urandom(64)
        d_xor = bytes(a ^ b for a, b in zip(d1, d2))
        s = 17

        fp1   = fingerprint(s, d1)
        fp2   = fingerprint(s, d2)
        fp_xor = fingerprint(s, d_xor)

        assert fp_xor == (fp1 ^ fp2)

    def test_multi_fingerprint_length(self):
        fp = multi_fingerprint([1, 2, 3], b"hello world")
        assert len(fp) == 3

    def test_fingerprint_from_seed_length(self):
        fp = fingerprint_from_seed(b"test_seed", b"some data")
        assert len(fp) == FINGERPRINT_LEN

    def test_fingerprint_deterministic(self):
        data = b"deterministic test"
        seed = b"fixed_seed"
        fp1 = fingerprint_from_seed(seed, data)
        fp2 = fingerprint_from_seed(seed, data)
        assert fp1 == fp2

    def test_fingerprint_sensitive_to_data(self):
        seed = b"seed"
        fp1 = fingerprint_from_seed(seed, b"data_a")
        fp2 = fingerprint_from_seed(seed, b"data_b")
        assert fp1 != fp2


# ---------------------------------------------------------------------------
# FPCC construction
# ---------------------------------------------------------------------------

class TestFPCC:
    def _make_fragments(self, n=5, m=3, data=b"hello world" * 100):
        return encode_fragment(data, n, m)

    def test_build_fpcc_structure(self):
        frags = self._make_fragments()
        fpcc = build_fpcc(frags, n=5, m=3)
        assert len(fpcc.cc) == 5
        assert len(fpcc.fp) == 3
        for c in fpcc.cc:
            assert len(c) == 32   # SHA-256
        for f in fpcc.fp:
            assert len(f) == FINGERPRINT_LEN

    def test_cc_matches_hashes(self):
        frags = self._make_fragments()
        fpcc = build_fpcc(frags, n=5, m=3)
        for i, frag in enumerate(frags):
            assert fpcc.cc[i] == hashlib.sha256(frag).digest()

    def test_fp_matches_fingerprints(self):
        """fp[i] == fingerprint_from_seed(seed, fragment[i]) for i < m."""
        frags = self._make_fragments()
        fpcc = build_fpcc(frags, n=5, m=3)
        seed = derive_seed(fpcc.cc)
        for i in range(3):
            expected = fingerprint_from_seed(seed, frags[i])
            assert fpcc.fp[i] == expected

    def test_expected_fingerprint_systematic(self):
        """For i < m, expected_fingerprint returns fp[i] directly."""
        frags = self._make_fragments()
        fpcc = build_fpcc(frags, n=5, m=3)
        seed = derive_seed(fpcc.cc)
        for i in range(3):
            exp = expected_fingerprint(fpcc, i, 5, 3)
            actual = fingerprint_from_seed(seed, frags[i])
            assert exp == actual

    def test_expected_fingerprint_parity(self):
        """For i >= m, expected_fingerprint matches actual fingerprint of parity fragment."""
        frags = self._make_fragments()
        fpcc = build_fpcc(frags, n=5, m=3)
        seed = derive_seed(fpcc.cc)
        for i in range(3, 5):
            exp = expected_fingerprint(fpcc, i, 5, 3)
            actual = fingerprint_from_seed(seed, frags[i])
            assert exp == actual, f"Parity fingerprint mismatch at index {i}"


# ---------------------------------------------------------------------------
# End-to-end: verify_fragment
# ---------------------------------------------------------------------------

class TestVerifyFragment:
    def _setup(self, n=5, m=3, data=b"the quick brown fox" * 50):
        fragments = encode_fragment(data, n, m)
        fpcc = avid_build_fpcc(fragments, n, m)
        return fragments, fpcc

    def test_all_fragments_verify(self):
        frags, fpcc = self._setup()
        for i, frag_data in enumerate(frags):
            frag = Fragment(index=i, data=frag_data)
            assert verify_fragment(frag, fpcc, i, 5, 3), f"Fragment {i} should verify"

    def test_corrupted_fragment_fails(self):
        frags, fpcc = self._setup()
        # Flip a byte in fragment 0
        corrupted = bytearray(frags[0])
        corrupted[0] ^= 0xFF
        frag = Fragment(index=0, data=bytes(corrupted))
        assert not verify_fragment(frag, fpcc, 0, 5, 3)

    def test_wrong_index_fails(self):
        frags, fpcc = self._setup()
        # Send fragment 1's data but claim it's fragment 0
        frag = Fragment(index=0, data=frags[1])
        assert not verify_fragment(frag, fpcc, 0, 5, 3)

    def test_roundtrip(self):
        """Encode → build FPCC → verify all → decode → original data."""
        original = b"roundtrip test data " * 200
        n, m = 5, 3
        frags = encode_fragment(original, n, m)
        fpcc = avid_build_fpcc(frags, n, m)

        for i, f in enumerate(frags):
            assert verify_fragment(Fragment(index=i, data=f), fpcc, i, n, m)

        # Reconstruct from any 3 fragments
        recovered = decode_fragments([(0, frags[0]), (2, frags[2]), (4, frags[4])], n, m, len(original))
        assert recovered == original
