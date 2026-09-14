"""Task 8 contract tests: console/config/UI policy and lifecycle refusal.

No engine: a fake bridge answers the ops the tools send, registered as a
lifecycle instance so the tools resolve it normally. The conformance
round (Task 4) added the gameplay gate, the status/capabilities surface
and the redundant-acquire sequence rule.
"""
import asyncio
import json
import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..",
                                "src"))

from mcp.shared.memory import create_connected_server_and_client_session

from quakemcp import lifecycle, models, server
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
    # the mutation envelope starts a beat; policy tests must not leak
    # real heartbeat threads onto the shared fake connection
    monkeypatch.setattr(lifecycle.Instance, "keepalive",
                        lambda self, lease, epoch: None)
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


def test_console_policy_removals():
    for command in ("save", "load", "connect", "disconnect"):
        try:
            server._console_line(command, ["x"])
            assert False, command
        except ValueError as e:
            assert str(e).startswith("POLICY_DENIED")


def test_gameplay_command_needs_ready_state():
    state = {"signon": 4, "movemessages": 3, "dead": False,
             "intermission": False, "health": 100, "map": "start"}
    assert models.gameplay_ready(state) is True
    assert models.gameplay_ready({**state, "movemessages": 0}) is False
    assert models.gameplay_ready({**state, "dead": True}) is False


def test_gameplay_console_gated_until_ready(fake, monkeypatch):
    _register(monkeypatch)
    try:
        r = _call("quake_console", {"instance": "qtest", "command": "god"})
        assert r.isError and "NOT_READY" in _text(r)
        assert not any(op == "exec" for op, _ in fake.calls)
        # pause 0 resumes a paused game: the one gameplay exemption
        r = _call("quake_console", {"instance": "qtest", "command": "pause",
                                    "args": ["0"]})
        assert not r.isError, _text(r)
        assert any(op == "exec" and kw.get("text") == "pause 0"
                   for op, kw in fake.calls)
    finally:
        lifecycle.forget("qtest")


def test_status_reports_capabilities(fake, monkeypatch):
    fake.replies["ping"] = {"v": 1, "id": "x", "ok": True,
                            "result": {"ready": True}}
    _register(monkeypatch)
    try:
        r = _call("quake_status", {"instance": "qtest"})
        assert not r.isError, _text(r)
        out = json.loads(_text(r))
        assert out["bridge_ready"] is True
        assert out["gameplay_ready"] is False
        caps = out["capabilities"]
        for key in ("tools", "actions", "ui", "settings", "frames",
                    "modes", "telemetry", "console", "game"):
            assert key in caps, key
        assert len(caps["tools"]) == 13
        assert caps["actions"]["weapons"] == list(range(1, 9))
        assert "save" not in caps["console"]
    finally:
        lifecycle.forget("qtest")


def test_status_action_query(fake, monkeypatch):
    fake.replies["status"] = {"v": 1, "id": "x", "ok": False,
                              "error": "INVALID_CONTEXT",
                              "detail": "no receipt"}
    _register(monkeypatch)
    try:
        # a fresh instance has no receipts: not an error, just empty
        r = _call("quake_status", {"instance": "qtest"})
        assert not r.isError, _text(r)
        assert json.loads(_text(r))["action"] is None
        # an explicitly named action that matches nothing is the error
        r = _call("quake_status", {"instance": "qtest", "action_id": "nope"})
        assert r.isError and "INVALID_CONTEXT" in _text(r)
    finally:
        lifecycle.forget("qtest")


def test_status_returns_receipt(fake, monkeypatch):
    fake.replies["status"] = {"v": 1, "id": "x", "ok": True,
                              "result": {"action_id": "e1",
                                         "state": "done"}}
    _register(monkeypatch)
    try:
        r = _call("quake_status", {"instance": "qtest", "action_id": "e1"})
        assert not r.isError, _text(r)
        assert json.loads(_text(r))["action"]["state"] == "done"
    finally:
        lifecycle.forget("qtest")


def test_redundant_acquire_keeps_sequence(fake, monkeypatch):
    """The bridge keeps its high-water while the same lease lives; the
    server must not rewind its own sequence counter on that path or the
    next mutation is RESULT_EXPIRED."""
    fake.replies["control"] = lambda op, kw: {
        "v": 1, "id": kw.get("id", "x"), "ok": True,
        "result": {"lease": "l1-7", "epoch": 7, "control_rev": 2}}
    _register(monkeypatch)
    try:
        r = _call("quake_control", {"instance": "qtest"})
        assert not r.isError, _text(r)
        inst = lifecycle.get("qtest")
        assert inst.lease == "l1-7" and inst.next_seq == 0
        inst.next_seq = 5
        r = _call("quake_control", {"instance": "qtest"})
        assert not r.isError, _text(r)
        assert inst.next_seq == 5, "same lease: the fence must survive"
    finally:
        lifecycle.forget("qtest")
