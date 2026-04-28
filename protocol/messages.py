"""
protocol/messages.py

Pydantic message models for VerdictFS.
bytes fields are base64-encoded when serialized to JSON so they travel
cleanly over HTTP. FastAPI/Pydantic handles encode/decode automatically.
"""

import base64
from pydantic import BaseModel, ConfigDict, field_serializer, field_validator


def _b64_encode_list(v: list[bytes]) -> list[str]:
    return [base64.b64encode(b).decode() for b in v]

def _b64_decode_list(v: list) -> list[bytes]:
    if v and isinstance(v[0], str):
        return [base64.b64decode(s) for s in v]
    return v

def _b64_encode(v: bytes) -> str:
    return base64.b64encode(v).decode()

def _b64_decode(v) -> bytes:
    if isinstance(v, str):
        return base64.b64decode(v)
    return v


# ---------------------------------------------------------------------------
# Core data structures
# ---------------------------------------------------------------------------

class FPCC(BaseModel):
    """
    Fingerprinted Cross-Checksum.
    cc[i] = SHA-256(fragment_i)   for i in [0, n)
    fp[i] = fingerprint(seed, fragment_i)  for i in [0, m)
    """
    cc: list[bytes]   # len == n
    fp: list[bytes]   # len == m

    @field_validator("cc", "fp", mode="before")
    @classmethod
    def decode_bytes_list(cls, v):
        return _b64_decode_list(v)

    @field_serializer("cc", "fp")
    def encode_bytes_list(self, v: list[bytes]) -> list[str]:
        return _b64_encode_list(v)


class Fragment(BaseModel):
    """A single erasure-coded fragment assigned to one server."""
    index: int
    data: bytes

    @field_validator("data", mode="before")
    @classmethod
    def decode_data(cls, v):
        return _b64_decode(v)

    @field_serializer("data")
    def encode_data(self, v: bytes) -> str:
        return _b64_encode(v)


# ---------------------------------------------------------------------------
# Client → Server messages
# ---------------------------------------------------------------------------

class DisperseRequest(BaseModel):
    key: str
    fragment: Fragment
    fpcc: FPCC


class RetrieveResponse(BaseModel):
    key: str
    fragment: Fragment | None = None
    stored: bool


# ---------------------------------------------------------------------------
# Server ↔ Server messages
# ---------------------------------------------------------------------------

class EchoMessage(BaseModel):
    sender_id: int
    key: str
    fpcc_digest: bytes

    @field_validator("fpcc_digest", mode="before")
    @classmethod
    def decode_digest(cls, v):
        return _b64_decode(v)

    @field_serializer("fpcc_digest")
    def encode_digest(self, v: bytes) -> str:
        return _b64_encode(v)


class ReadyMessage(BaseModel):
    sender_id: int
    key: str
    fpcc_digest: bytes

    @field_validator("fpcc_digest", mode="before")
    @classmethod
    def decode_digest(cls, v):
        return _b64_decode(v)

    @field_serializer("fpcc_digest")
    def encode_digest(self, v: bytes) -> str:
        return _b64_encode(v)


# ---------------------------------------------------------------------------
# Server state responses
# ---------------------------------------------------------------------------

class StatusResponse(BaseModel):
    key: str
    stored: bool
    echo_count: int
    ready_count: int
    verified: bool


class HealthResponse(BaseModel):
    server_id: int
    status: str = "ok"
    version: str = "0.1.0"
