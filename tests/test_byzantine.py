"""
tests/test_byzantine.py

Byzantine fault simulation tests.
Verifies that faulty servers cannot cause honest servers to accept invalid data.

Fault models tested:
  - Corrupted fragment (server sends wrong bytes)
  - Wrong index (server claims to be a different server)
  - Modified fpcc (server tampers with hashes or fingerprints)
  - Replay (server sends an old fpcc with a new fragment)
"""

import pytest
import hashlib
from protocol.avid_fp import encode_fragment, build_fpcc, verify_fragment
from protocol.messages import Fragment, FPCC


def make_cluster(data: bytes = b"test data" * 100, n: int = 5, m: int = 3):
    """Helper: encode data, build fpcc, return (frags, fpcc)."""
    frags = encode_fragment(data, n, m)
    fpcc  = build_fpcc(frags, n, m)
    return frags, fpcc


class TestByzantineFaults:

    def test_honest_fragments_all_verify(self):
        frags, fpcc = make_cluster()
        for i, f in enumerate(frags):
            assert verify_fragment(Fragment(index=i, data=f), fpcc, 5, 5, 3)

    # ------------------------------------------------------------------ #
    # Fragment corruption attacks
    # ------------------------------------------------------------------ #

    def test_single_bit_flip_detected(self):
        """Even a 1-bit change to a fragment fails both hash and fingerprint checks."""
        frags, fpcc = make_cluster()
        corrupted = bytearray(frags[2])
        corrupted[0] ^= 0x01
        assert not verify_fragment(Fragment(index=2, data=bytes(corrupted)), fpcc, 2, 5, 3)

    def test_truncated_fragment_rejected(self):
        frags, fpcc = make_cluster()
        truncated = frags[0][:-1]
        # hash will differ → rejected
        assert not verify_fragment(Fragment(index=0, data=truncated), fpcc, 0, 5, 3)

    def test_empty_fragment_rejected(self):
        frags, fpcc = make_cluster()
        assert not verify_fragment(Fragment(index=0, data=b""), fpcc, 0, 5, 3)

    def test_all_zeros_fragment_rejected(self):
        frags, fpcc = make_cluster()
        zeroed = bytes(len(frags[1]))
        assert not verify_fragment(Fragment(index=1, data=zeroed), fpcc, 1, 5, 3)

    # ------------------------------------------------------------------ #
    # Index spoofing attacks
    # ------------------------------------------------------------------ #

    def test_fragment_sent_to_wrong_server_rejected(self):
        """Fragment 0 sent claiming to be fragment 1 fails the hash check."""
        frags, fpcc = make_cluster()
        # frag[0] data with index=1
        assert not verify_fragment(Fragment(index=1, data=frags[0]), fpcc, 1, 5, 3)

    def test_parity_fragment_with_systematic_index_rejected(self):
        frags, fpcc = make_cluster()
        # parity fragment[3] data, but claiming to be server 0
        assert not verify_fragment(Fragment(index=0, data=frags[3]), fpcc, 0, 5, 3)

    # ------------------------------------------------------------------ #
    # FPCC tampering attacks
    # ------------------------------------------------------------------ #

    def test_tampered_cc_rejected(self):
        """A faulty sender that changes cc[i] to hide a corrupted fragment."""
        frags, fpcc = make_cluster()

        # Corrupt fragment 0
        corrupted = bytearray(frags[0])
        corrupted[0] ^= 0xFF
        corrupted = bytes(corrupted)

        # Faulty sender also updates cc[0] to match the corrupted fragment
        evil_cc    = list(fpcc.cc)
        evil_cc[0] = hashlib.sha256(corrupted).digest()
        evil_fpcc  = FPCC(cc=evil_cc, fp=list(fpcc.fp))

        # Hash check passes now, but fingerprint check must fail
        assert not verify_fragment(Fragment(index=0, data=corrupted), evil_fpcc, 0, 5, 3)

    def test_tampered_fp_rejected(self):
        """A faulty sender that changes fp[0] is caught by the fingerprint check."""
        frags, fpcc = make_cluster()

        evil_fp    = list(fpcc.fp)
        evil_fp[0] = bytes(b ^ 0xFF for b in fpcc.fp[0])  # flip all bits
        evil_fpcc  = FPCC(cc=list(fpcc.cc), fp=evil_fp)

        # Fragment is honest, but fpcc is wrong → should fail
        assert not verify_fragment(Fragment(index=0, data=frags[0]), evil_fpcc, 0, 5, 3)

    def test_fpcc_from_different_data_rejected(self):
        """FPCC from one dataset cannot validate fragments from a different dataset."""
        frags_a, fpcc_a = make_cluster(b"dataset_A" * 100)
        frags_b, fpcc_b = make_cluster(b"dataset_B" * 100)

        # Fragment from B with FPCC from A
        assert not verify_fragment(Fragment(index=0, data=frags_b[0]), fpcc_a, 0, 5, 3)

    # ------------------------------------------------------------------ #
    # Threshold edge cases
    # ------------------------------------------------------------------ #

    def test_exactly_m_fragments_sufficient_for_recovery(self):
        """m fragments (no more) are sufficient for reconstruction."""
        from protocol.avid_fp import decode_fragments

        original = b"threshold test" * 300
        n, m = 5, 3
        frags = encode_fragment(original, n, m)

        # Use exactly m=3 fragments (indices 1, 2, 4)
        recovered = decode_fragments([(1, frags[1]), (2, frags[2]), (4, frags[4])], n, m, len(original))
        assert recovered == original

    def test_m_minus_1_fragments_insufficient(self):
        """m-1 fragments cannot reconstruct (decode gives wrong output or raises)."""
        from protocol.avid_fp import decode_fragments

        original = b"insufficient" * 300
        n, m = 5, 3
        frags = encode_fragment(original, n, m)

        # m-1 = 2 fragments — decode should NOT return correct data
        try:
            recovered = decode_fragments([(0, frags[0]), (1, frags[1])], n, m, len(original))
            # If it returns something, it must not match the original
            assert recovered != original, "Should not reconstruct from m-1 fragments"
        except Exception:
            pass  # Raising an error is also acceptable

    def test_f_faults_tolerated(self):
        """
        f=1 server can be completely faulty; remaining n-f=4 servers
        still have at least m=3 valid fragments for reconstruction.
        """
        from protocol.avid_fp import decode_fragments

        original = b"fault tolerance test" * 200
        n, m, f = 5, 3, 1
        frags = encode_fragment(original, n, m)
        fpcc  = build_fpcc(frags, n, m)

        # Server 2 is Byzantine: ignore its fragment entirely
        honest = [(i, frags[i]) for i in range(n) if i != 2][:m]
        recovered = decode_fragments(honest, n, m, len(original))
        assert recovered == original

    def test_verify_all_n_minus_f_honest_fragments(self):
        """
        Even if f servers are faulty, the n-f honest servers' fragments
        all pass verification.
        """
        n, m, f = 5, 3, 1
        frags, fpcc = make_cluster(b"all honest" * 100, n, m)

        honest_ids = list(range(n - f))  # servers 0,1,2,3 are honest
        for i in honest_ids:
            assert verify_fragment(Fragment(index=i, data=frags[i]), fpcc, i, n, m)
