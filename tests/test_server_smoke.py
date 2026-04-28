"""
Basic smoke tests you can run before your partner's code is ready.
These test your infrastructure layer in isolation.
"""
import pytest
from server.state import ServerState


@pytest.mark.asyncio
async def test_state_create_and_get():
    state = ServerState(server_id=0, n=5, m=3, f=1)
    ks = await state.get_or_create("mykey")
    assert ks is not None
    assert not ks.stored
    assert not ks.verified


@pytest.mark.asyncio
async def test_state_thresholds():
    state = ServerState(server_id=0, n=5, m=3, f=1)
    assert state.echo_threshold == 4    # n - f = 4
    assert state.ready_threshold == 2   # f + 1 = 2
    assert state.store_threshold == 3   # 2f + 1 = 3


def test_chunker_roundtrip():
    from fs.chunker import split, join
    data = b"hello world" * 1000
    chunks = split(data, chunk_size=100)
    assert join(chunks) == data


def test_chunker_keys():
    from fs.chunker import chunk_key, parse_chunk_key
    key = chunk_key("myfile.txt", 3)
    assert parse_chunk_key(key) == ("myfile.txt", 3)
