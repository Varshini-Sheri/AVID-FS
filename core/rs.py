"""
core/rs.py

Systematic (m, n) Reed-Solomon erasure code over our GF(2^8).

Because both encode_fragment and FPCC fingerprint verification use this same
encoder, the homomorphic linearity property holds:
  fp(s, encode_i(data)) == encode_i(fp(s, data[0..m-1]))

Using a systematic Vandermonde-based generator matrix.
Evaluation points: alpha_j = j+1 for j in [0, n-1].
  - Systematic rows (j < m): identity (because Lagrange interpolation through
    the first m evaluation points gives back the original symbols)
  - Actually we use a simpler construction: augmented Vandermonde matrix

For an academic project we use a simple but correct approach:
  - Encode each byte position independently across the m input chunks
  - For systematic output i < m: output[i] = input[i]  (systematic property)
  - For parity output i >= m: output[i] = sum_j(G[i][j] * input[j]) in GF(2^8)

The generator matrix G is constructed so the first m rows form the identity.
We use evaluation points 1..n in GF(2^8) with a Cauchy-like matrix approach.

For simplicity we implement a non-systematic Vandermonde encoder and then
convert it to systematic form, but the fastest practical approach for this
project is the following explicit systematic construction.
"""

from core.gf import gf_mul, gf_add, gf_pow, gf_inv


# ---------------------------------------------------------------------------
# Generator matrix construction
# ---------------------------------------------------------------------------

def _make_vandermonde_row(alpha: int, m: int) -> list[int]:
    """Row of Vandermonde matrix: [alpha^0, alpha^1, ..., alpha^(m-1)]."""
    return [gf_pow(alpha, j) for j in range(m)]


def _gf_mat_mul_row_vec(row: list[int], vec: list[int]) -> int:
    """Dot product of a GF row and a GF vector."""
    result = 0
    for a, b in zip(row, vec):
        result = gf_add(result, gf_mul(a, b))
    return result


def _make_systematic_generator(n: int, m: int) -> list[list[int]]:
    """
    Build the n x m systematic generator matrix G such that:
      output[i] = G[i] · input   for input of length m
      G[0..m-1] = identity       (systematic property)

    We use evaluation points alpha_i = i+1 (i=0..n-1) in a Vandermonde matrix,
    then row-reduce to make the first m rows identity.
    """
    # Full Vandermonde: V[i] = [alpha_i^0, ..., alpha_i^(m-1)], alpha_i = i+1
    V = [_make_vandermonde_row(i + 1, m) for i in range(n)]

    # We need to transform V so the first m rows become identity.
    # Multiply on the right by V_top^{-1}, where V_top = V[0..m-1].
    # Result: G[i] = V[i] · V_top^{-1}
    V_top = [row[:] for row in V[:m]]
    inv_top = _gf_matrix_inverse(V_top, m)

    G = []
    for row in V:
        new_row = [_gf_mat_mul_row_vec(row, [inv_top[r][c] for r in range(m)]) for c in range(m)]
        G.append(new_row)

    return G


def _gf_matrix_inverse(M: list[list[int]], n: int) -> list[list[int]]:
    """Gaussian elimination to invert n×n GF(2^8) matrix."""
    # Augment M with identity
    aug = [row[:] + [1 if i == j else 0 for j in range(n)] for i, row in enumerate(M)]

    for col in range(n):
        # Find pivot
        pivot = None
        for row in range(col, n):
            if aug[row][col] != 0:
                pivot = row
                break
        if pivot is None:
            raise ValueError(f"Matrix is singular (column {col})")
        aug[col], aug[pivot] = aug[pivot], aug[col]

        # Scale pivot row
        scale = gf_inv(aug[col][col])
        aug[col] = [gf_mul(x, scale) for x in aug[col]]

        # Eliminate column
        for row in range(n):
            if row != col and aug[row][col] != 0:
                factor = aug[row][col]
                aug[row] = [gf_add(aug[row][k], gf_mul(factor, aug[col][k])) for k in range(2 * n)]

    return [row[n:] for row in aug]


# Cache generator matrices to avoid recomputation
_GENERATOR_CACHE: dict[tuple[int, int], list[list[int]]] = {}


def get_generator(n: int, m: int) -> list[list[int]]:
    key = (n, m)
    if key not in _GENERATOR_CACHE:
        _GENERATOR_CACHE[key] = _make_systematic_generator(n, m)
    return _GENERATOR_CACHE[key]


# ---------------------------------------------------------------------------
# Encode / decode
# ---------------------------------------------------------------------------

def encode(data: bytes, n: int, m: int) -> list[bytes]:
    """
    Encode data into n fragments, m of which suffice for reconstruction.
    Returns a list of n byte strings (one per server), all equal length.

    data: arbitrary bytes (will be zero-padded to be divisible by m)
    """
    # Pad to multiple of m
    pad_len = (-len(data)) % m
    padded = data + b'\x00' * pad_len
    chunk_size = len(padded) // m

    # Split into m equal input chunks
    input_chunks = [padded[j * chunk_size:(j + 1) * chunk_size] for j in range(m)]

    G = get_generator(n, m)

    # Output chunk i = G[i] · input_chunks  (byte-by-byte)
    output_chunks = [bytearray(chunk_size) for _ in range(n)]
    for i in range(n):
        for byte_pos in range(chunk_size):
            val = 0
            for j in range(m):
                val = gf_add(val, gf_mul(G[i][j], input_chunks[j][byte_pos]))
            output_chunks[i][byte_pos] = val

    return [bytes(c) for c in output_chunks]


def decode(fragments: list[tuple[int, bytes]], n: int, m: int, original_len: int | None = None) -> bytes:
    """
    Reconstruct data from any m of n (index, data) pairs.
    """
    assert len(fragments) >= m
    fragments = fragments[:m]
    indices = [idx for idx, _ in fragments]
    chunks  = [list(data) for _, data in fragments]
    chunk_size = len(chunks[0])

    # Extract the m×m submatrix corresponding to received indices
    G = get_generator(n, m)
    G_sub = [G[i] for i in indices]

    # Invert G_sub to recover input_chunks
    G_sub_inv = _gf_matrix_inverse([row[:] for row in G_sub], m)

    recovered = [bytearray(chunk_size) for _ in range(m)]
    for j in range(m):
        for byte_pos in range(chunk_size):
            val = 0
            for k in range(m):
                val = gf_add(val, gf_mul(G_sub_inv[j][k], chunks[k][byte_pos]))
            recovered[j][byte_pos] = val

    data = b''.join(bytes(c) for c in recovered)
    return data[:original_len] if original_len is not None else data


def encode_fingerprints_at(fp_list: list[bytes], target_index: int, n: int, m: int) -> bytes:
    """
    Given m fingerprints (each FINGERPRINT_LEN bytes), return the fingerprint
    that server target_index should have.

    This works byte-by-byte using the same generator matrix as encode(),
    so the homomorphic property holds.
    """
    G = get_generator(n, m)
    row = G[target_index]
    fp_len = len(fp_list[0])
    result = bytearray(fp_len)
    for byte_pos in range(fp_len):
        val = 0
        for j in range(m):
            val = gf_add(val, gf_mul(row[j], fp_list[j][byte_pos]))
        result[byte_pos] = val
    return bytes(result)
