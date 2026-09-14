"""Task 8 contract tests: console/config/UI policy and lifecycle refusal.

No engine: a fake bridge answers the ops the tools send, registered as a
lifecycle instance so the tools resolve it normally.
"""
import asyncio
import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..",
                                "src"))

from mcp.shared.memory import create_connected_server_and_client_session

from quakemcp import lifecycle
from quakemcp.server import mcp


class FakeBridge:
    def __init__(self, replies=None):
        self.replies = dict(replies or {})
        self.calls = []

    def send(self, op, **kw):
        self.calls.append((op, kw))
        if op in self.replies:
            reply = self.replies[op]
            return reply(op, kw) if callable(reply) else reply
        return {"v": 1, "id": kw.get("id", "x"), "ok": True, "result": {}}

    def close(self):
        pass


def _call(name, args):
    async def go():
        async with create_connected_server_and_client_session(mcp) as s:
            await s.initialize()
            return await s.call_tool(name, args)
    return asyncio.run(go())


def _text(result):
    return "".join(getattr(c, "text", "") for c in result.content)


@pytest.fixture
def fake(monkeypatch):
    bridge = FakeBridge()
    monkeypatch.setattr(lifecycle.Instance, "client",
                        lambda self: bridge)
    return bridge


def _register(monkeypatch, instance_id="qtest", owned=True, basedir=None):
    inst = lifecycle.Instance(instance_id, -1, 1, "tok", owned=owned,
                              profile_id="local" if owned else None)
    if basedir is not None:
        monkeypatch.setattr(lifecycle.Instance, "basedir",
                            lambda self: str(basedir))
    lifecycle._register(inst)
    return inst


def test_console_rejects_unlisted_command(fake, monkeypatch):
    _register(monkeypatch)
    try:
        r = _call("quake_console", {"instance": "qtest", "command": "exec",
                                    "args": ["quit"]})
        assert r.isError
        assert "POLICY_DENIED" in _text(r)
        assert all(op != "exec" or kw.get("text") != "quit"
                   for op, kw in fake.calls)
    finally:
        lifecycle.forget("qtest")


def test_console_typed_args(fake, monkeypatch):
    _register(monkeypatch)
    try:
        r = _call("quake_console", {"instance": "qtest", "command": "skill",
                                    "args": ["9"]})
        assert r.isError and "INVALID_CONTEXT" in _text(r)
        r = _call("quake_console", {"instance": "qtest", "command": "map",
                                    "args": ["../../etc/passwd"]})
        assert r.isError and "POLICY_DENIED" in _text(r)
        r = _call("quake_console", {"instance": "qtest", "command": "status",
                                    "args": []})
        assert not r.isError
        assert any(op == "exec" and kw.get("text") == "status"
                   for op, kw in fake.calls)
    finally:
        lifecycle.forget("qtest")


def test_map_traversal_denied(fake, monkeypatch):
    _register(monkeypatch)
    try:
        r = _call("quake_game", {"instance": "qtest",
                                 "operation": "load_map",
                                 "map_id": "../../etc"})
        assert r.isError and "POLICY_DENIED" in _text(r)
    finally:
        lifecycle.forget("qtest")


def test_save_overwrite_guard(fake, monkeypatch, tmp_path):
    _register(monkeypatch, basedir=tmp_path)
    (tmp_path / "id1").mkdir()
    (tmp_path / "id1" / "mcp0.sav").write_bytes(b"x")
    try:
        r = _call("quake_game", {"instance": "qtest", "operation": "save",
                                 "slot": "mcp0"})
        assert r.isError and "INVALID_CONTEXT" in _text(r)
        assert "overwrite" in _text(r)
        r = _call("quake_game", {"instance": "qtest", "operation": "list_saves"})
        assert not r.isError and "mcp0" in _text(r)
    finally:
        lifecycle.forget("qtest")


def test_respawn_unsupported(fake, monkeypatch):
    _register(monkeypatch)
    try:
        r = _call("quake_game", {"instance": "qtest",
                                 "operation": "respawn"})
        assert r.isError and "UNSUPPORTED_CAPABILITY" in _text(r)
    finally:
        lifecycle.forget("qtest")


def test_config_policy(fake, monkeypatch):
    fake.replies["cvar"] = {"v": 1, "id": "x", "ok": True,
                            "result": {"value": "1"}}
    _register(monkeypatch)
    try:
        r = _call("quake_config", {"instance": "qtest", "operation": "get",
                                   "name": "nope"})
        assert r.isError and "INVALID_CONTEXT" in _text(r)
        r = _call("quake_config", {"instance": "qtest", "operation": "set",
                                   "name": "access_doubleclick_command",
                                   "value": "quit"})
        assert r.isError and "POLICY_DENIED" in _text(r)
        r = _call("quake_config", {"instance": "qtest", "operation": "get",
                                   "name": "access_mouseonly"})
        assert not r.isError and "1" in _text(r)
        r = _call("quake_config", {"instance": "qtest", "operation": "set",
                                   "name": "sensitivity", "value": "5"})
        assert not r.isError
        assert any(op == "cvar" and kw.get("name") == "sensitivity"
                   and kw.get("value") == "5" for op, kw in fake.calls)
    finally:
        lifecycle.forget("qtest")


def test_ui_key_pair_and_text_policy(fake, monkeypatch):
    fake.replies["state"] = {"v": 1, "id": "x", "ok": True,
                             "result": {"ui": 1}}
    _register(monkeypatch)
    try:
        r = _call("quake_ui", {"instance": "qtest", "key": "escape"})
        assert not r.isError
        keys = [(kw["key"], kw["down"]) for op, kw in fake.calls
                if op == "key"]
        assert keys == [("27", "1"), ("27", "0")]
        r = _call("quake_ui", {"instance": "qtest", "text": "hi"})
        assert not r.isError
        typed = [(kw["key"], kw["down"]) for op, kw in fake.calls
                 if op == "key"][2:]
        assert typed == [("104", "1"), ("104", "0"),
                         ("105", "1"), ("105", "0")]
        fake.replies["state"] = {"v": 1, "id": "x", "ok": True,
                                 "result": {"ui": 0}}
        r = _call("quake_ui", {"instance": "qtest", "text": "hi"})
        assert r.isError and "INVALID_CONTEXT" in _text(r)
    finally:
        lifecycle.forget("qtest")


def test_stop_refuses_attached(monkeypatch):
    _register(monkeypatch, instance_id="qatt", owned=False)
    try:
        r = _call("quake_stop", {"instance": "qatt"})
        assert r.isError and "POLICY_DENIED" in _text(r)
    finally:
        lifecycle.forget("qatt")


def test_release_without_lease_is_idempotent(fake, monkeypatch):
    _register(monkeypatch)
    try:
        r1 = _call("quake_release", {"instance": "qtest", "reason": "test"})
        r2 = _call("quake_release", {"instance": "qtest"})
        assert not r1.isError and not r2.isError
        assert [op for op, _ in fake.calls].count("release") == 2
    finally:
        lifecycle.forget("qtest")
