"""
server/state.py

Per-key server state for the AVID-FP protocol.
Uses per-key asyncio locks to avoid false contention across concurrent dispersals.
"""

import asyncio
from dataclasses import dataclass, field
from protocol.messages import Fragment, FPCC


@dataclass
class KeyState:
    """All state a server tracks for a single object key."""
    fragment: Fragment | None = None
    fpcc: FPCC | None = None
    fpcc_digest: bytes | None = None

    verified: bool = False      # fragment passed verify_fragment()
    echoed: bool = False        # we have broadcast ECHO for this key
    ready_sent: bool = False    # we have broadcast READY for this key
    stored: bool = False        # we have committed the fragment to disk

    echo_set: set[int] = field(default_factory=set)   # sender_ids received
    ready_set: set[int] = field(default_factory=set)  # sender_ids received

    lock: asyncio.Lock = field(default_factory=asyncio.Lock)


class ServerState:
    """
    Global state store for this server instance.
    Access to individual keys is protected by per-key locks.
    """

    def __init__(self, server_id: int, n: int, m: int, f: int):
        self.server_id = server_id
        self.n = n   # total servers
        self.m = m   # reconstruction threshold
        self.f = f   # max faulty servers

        self._keys: dict[str, KeyState] = {}
        self._global_lock = asyncio.Lock()

    async def get_or_create(self, key: str) -> KeyState:
        """Get existing state for key, or create fresh state."""
        async with self._global_lock:
            if key not in self._keys:
                self._keys[key] = KeyState()
            return self._keys[key]

    async def get(self, key: str) -> KeyState | None:
        return self._keys.get(key)

    def all_keys(self) -> list[str]:
        return list(self._keys.keys())

    @property
    def echo_threshold(self) -> int:
        """Need n - f echo messages before sending ready."""
        return self.n - self.f

    @property
    def ready_threshold(self) -> int:
        """Need f + 1 ready messages to re-broadcast ready (if not yet sent)."""
        return self.f + 1

    @property
    def store_threshold(self) -> int:
        """Need 2f + 1 ready messages to store."""
        return 2 * self.f + 1
