"""
fs/chunker.py

Splits files into fixed-size chunks for dispersal, reassembles them on retrieval.
Chunk keys follow the pattern: "{object_key}/chunk/{index}"
"""

import math

DEFAULT_CHUNK_SIZE = 1024 * 1024  # 1 MB


def split(data: bytes, chunk_size: int = DEFAULT_CHUNK_SIZE) -> list[bytes]:
    """Split data into chunks of at most chunk_size bytes."""
    if not data:
        return [b""]
    n = math.ceil(len(data) / chunk_size)
    return [data[i * chunk_size:(i + 1) * chunk_size] for i in range(n)]


def join(chunks: list[bytes]) -> bytes:
    """Reassemble chunks in order."""
    return b"".join(chunks)


def chunk_key(object_key: str, index: int) -> str:
    """Derive the store key for a specific chunk."""
    return f"{object_key}/chunk/{index}"


def parse_chunk_key(key: str) -> tuple[str, int] | None:
    """Reverse of chunk_key. Returns (object_key, index) or None."""
    parts = key.split("/chunk/")
    if len(parts) != 2:
        return None
    try:
        return parts[0], int(parts[1])
    except ValueError:
        return None
