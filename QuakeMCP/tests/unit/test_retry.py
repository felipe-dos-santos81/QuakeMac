"""Task 9: connection-level retry for deduplicated operations."""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..",
                                "src"))

import pytest

from quakemcp.engine import BridgeClient
from quakemcp.models import EngineDisconnected


def _client(monkeypatch, replies):
    """A BridgeClient with no socket, scripted send results."""
    client = object.__new__(BridgeClient)
    client.host, client.port, client.token = "127.0.0.1", 1, "t"
    sent = []

    def fake_send(op, **kw):
        sent.append(op)
        out = replies.pop(0)
        if isinstance(out, Exception):
            raise out
        return out

    reconnected = []
    monkeypatch.setattr(client, "send", fake_send)
    monkeypatch.setattr(client, "_reconnect",
                        lambda: reconnected.append(1))
    return client, sent, reconnected


def test_send_retrying_reconnects_once_on_drop(monkeypatch):
    client, sent, reconnected = _client(
        monkeypatch, [EngineDisconnected("reset"), {"ok": True}])
    assert client.send_retrying("act", lease="l", action_id="a") == \
        {"ok": True}
    assert sent == ["act", "act"]
    assert reconnected == [1]


def test_send_retrying_gives_up_without_action_id(monkeypatch):
    # without an action_id the caller must not retry (no dedup), and
    # the helper still surfaces the latest failure
    client, sent, reconnected = _client(
        monkeypatch, [EngineDisconnected("reset")])
    with pytest.raises(EngineDisconnected):
        client.send_retrying("act", lease="l", attempts=1)
    assert sent == ["act"]
    assert reconnected == []
