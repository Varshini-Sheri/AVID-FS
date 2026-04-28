"""
fs/object_store.py

Object store for VerdictFS.
Currently in-memory; swap the backend without changing the interface.

Used by:
  - server/routes.py  (store/retrieve fragments)
  - client/client.py  (assemble retrieved fragments)
"""

import hashlib
import logging
from dataclasses import dataclass, field

logger = logging.getLogger(__name__)


@dataclass
class StoredObject:
    key: str
    data: bytes
    metadata: dict = field(default_factory=dict)

    @property
    def checksum(self) -> str:
        return hashlib.sha256(self.data).hexdigest()


class ObjectStore:
    """
    Simple in-memory key-value store.
    Thread-safe for asyncio (single-threaded event loop).
    """

    def __init__(self) -> None:
        self._store: dict[str, StoredObject] = {}

    def put(self, key: str, data: bytes, metadata: dict | None = None) -> None:
        self._store[key] = StoredObject(key=key, data=data, metadata=metadata or {})
        logger.debug("stored key=%s  size=%d bytes", key, len(data))

    def get(self, key: str) -> bytes | None:
        obj = self._store.get(key)
        return obj.data if obj else None

    def exists(self, key: str) -> bool:
        return key in self._store

    def delete(self, key: str) -> bool:
        if key in self._store:
            del self._store[key]
            return True
        return False

    def list_keys(self) -> list[str]:
        return list(self._store.keys())

    def stats(self) -> dict:
        total_bytes = sum(len(o.data) for o in self._store.values())
        return {"object_count": len(self._store), "total_bytes": total_bytes}
