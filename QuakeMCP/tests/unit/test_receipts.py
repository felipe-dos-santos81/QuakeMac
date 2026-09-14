"""Task 5: per-instance observation receipts and FRAME_EXPIRED.

The bridge ledger dedupes mutations; this cache remembers the delivered
frame so a duplicate action_id replays the exact observation instead of
re-shooting the engine. These pin the cache, its bounds and the expired
path a caller hits when the frame is gone.
"""
import base64
import json
import os
import sys

import anyio
import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..",
                                "src"))

from quakemcp import lifecycle, server


def test_receipt_round_trip_is_byte_identical():
    inst = lifecycle.Instance("qtest", -1, 1, "tok", owned=False)
    encoded = b"\x89PNG\r\n\x1a\npayload"
    report = {"encoding": "png"}
    structured = {"frame": 7, "instance": "qtest", "completed_ticks": 1}
    server._receipt_store(inst, "a1", encoded, report, structured)

    cached = server._receipt_get(inst, "a1")
    assert cached is not None
    assert cached["encoded"] == encoded
    assert cached["report"] == report
    assert cached["structured"] == structured

    # replay rebuilds the live CallToolResult shape: the image block
    # carries the recorded bytes byte-for-byte
    result = server._replay(cached)
    assert result.isError is False
    assert result.content[0].type == "image"
    assert result.content[0].mimeType == "image/png"
    assert base64.b64decode(result.content[0].data) == encoded
    assert result.structuredContent == structured


def test_receipt_miss_is_none():
    inst = lifecycle.Instance("qtest", -1, 1, "tok", owned=False)
    assert server._receipt_get(inst, "never") is None


def test_receipt_limit_evicts_oldest(monkeypatch):
    monkeypatch.setattr(server, "RECEIPT_LIMIT", 2)
    monkeypatch.setattr(server, "RECEIPT_BYTES", 1 << 30)
    inst = lifecycle.Instance("qtest", -1, 1, "tok", owned=False)
    for i in range(3):
        server._receipt_store(inst, "a%d" % i, b"frame%d" % i,
                              {"encoding": "png"}, {"frame": i})
    assert server._receipt_get(inst, "a0") is None
    assert server._receipt_get(inst, "a1") is not None
    assert server._receipt_get(inst, "a2") is not None
    assert len(inst.receipts) == 2


def test_receipt_bytes_evict_oldest(monkeypatch):
    """An entry's evictable bytes are its encoded image plus the
    serialized structured payload; the oldest entry goes first."""
    monkeypatch.setattr(server, "RECEIPT_LIMIT", 16)
    inst = lifecycle.Instance("qtest", -1, 1, "tok", owned=False)
    encoded = b"x" * 100
    structured = {"pad": "y" * 100}
    one = len(encoded) + len(json.dumps(structured))
    monkeypatch.setattr(server, "RECEIPT_BYTES", 2 * one - 1)
    for i in range(2):
        server._receipt_store(inst, "a%d" % i, encoded, {"encoding": "png"},
                              structured)
    # the second entry alone fits; holding both would not
    assert server._receipt_get(inst, "a0") is None
    assert server._receipt_get(inst, "a1") is not None


def test_duplicate_with_cached_frame_replays(monkeypatch):
    """A duplicate reply never re-observes: the recorded frame is the
    only honest answer for that action id."""
    inst = lifecycle.Instance("qtest", -1, 1, "tok", owned=False)
    encoded = b"\x89PNG-recorded"
    structured = {"frame": 3, "instance": "qtest", "completed_ticks": 1}
    server._receipt_store(inst, "a1", encoded, {"encoding": "png"},
                          structured)
    monkeypatch.setattr(server.lifecycle, "get", lambda iid: inst)
    monkeypatch.setattr(server, "_mutate",
                        lambda *a, **kw: {"duplicate": True})

    def no_observe(*a, **kw):
        raise AssertionError("re-observed a recorded frame")

    monkeypatch.setattr(server, "_encode_observation", no_observe)

    async def run():
        return await server.quake_act("qtest", ticks=1, action_id="a1")

    result = anyio.run(run)
    assert base64.b64decode(result.content[0].data) == encoded
    assert result.structuredContent["frame"] == 3


def test_duplicate_without_cached_frame_raises_frame_expired(monkeypatch):
    """A consumed duplicate whose frame is gone (evicted, or captured
    before a server restart) is FRAME_EXPIRED, never a re-shoot."""
    inst = lifecycle.Instance("qtest", -1, 1, "tok", owned=False)
    monkeypatch.setattr(server.lifecycle, "get", lambda iid: inst)
    monkeypatch.setattr(server, "_mutate",
                        lambda *a, **kw: {"duplicate": True,
                                          "completed_ticks": 1})

    def no_observe(*a, **kw):
        raise AssertionError("re-observed an expired result frame")

    monkeypatch.setattr(server, "_encode_observation", no_observe)

    async def run():
        await server.quake_act("qtest", ticks=1, action_id="lost")

    with pytest.raises(ValueError) as err:
        anyio.run(run)
    assert str(err.value).startswith("FRAME_EXPIRED")
    assert "lost" in str(err.value)
