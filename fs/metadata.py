"""
fs/metadata.py

Per-object metadata: fpcc digest, chunk count, original size, etc.
Stored in-memory; extend to a persistent backend later.
"""

from dataclasses import dataclass, field
from protocol.messages import FPCC


@dataclass
class ObjectMetadata:
    key: str
    original_size: int
    chunk_count: int
    chunk_size: int
    fpcc_per_chunk: list[FPCC] = field(default_factory=list)
    checksum: str = ""           # SHA-256 of reassembled data


class MetadataStore:
    def __init__(self) -> None:
        self._store: dict[str, ObjectMetadata] = {}

    def put(self, meta: ObjectMetadata) -> None:
        self._store[meta.key] = meta

    def get(self, key: str) -> ObjectMetadata | None:
        return self._store.get(key)

    def delete(self, key: str) -> bool:
        if key in self._store:
            del self._store[key]
            return True
        return False

    def list_keys(self) -> list[str]:
        return list(self._store.keys())
