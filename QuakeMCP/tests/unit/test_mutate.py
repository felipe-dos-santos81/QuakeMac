"""Task 2: the shared mutation envelope (lazy lease, sequences, errors)."""
import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..",
                                "src"))

from quakemcp import lifecycle, server
from quakemcp.models import EngineDisconnected


class FakeClient:
    def __init__(self, replies):
        self.replies = replies
        self.sent = []

    def send(self, op, **kw):
        self.sent.append((op, dict(kw)))
        return self.replies.pop(0)

    def send_retrying(self, op, **kw):
        return self.send(op, **kw)

    def close(self):
        pass


class FakeInstance:
    def __init__(self, replies):
        self.replies = replies
        self.calls = []
        self.lease = ""
        self.epoch = 0
        self.next_seq = 0
        self._hb_thread = None

    def client(self):
        self.calls.append(FakeClient(self.replies))
        return self.calls[-1]

    def keepalive(self, lease, epoch):
        self._hb_thread = object()

    def stop_keepalive(self):
        self._hb_thread = None


class DisconnectedClient:
    def send(self, op, **kw):
        raise EngineDisconnected("boom")

    def send_retrying(self, op, **kw):
        raise EngineDisconnected("boom")

    def close(self):
        pass


class SilentClient:
    def send(self, op, **kw):
        return {"ok": True, "result": {}}

    def close(self):
        pass


def test_mutate_lazily_acquires_and_assigns_sequence():
    inst = FakeInstance([
        {"ok": True, "result": {"lease": "l1-1", "epoch": 7}},
        {"ok": True, "result": {"output": "hi"}},
    ])
    out = server._mutate(inst, "exec", text="god")
    assert out == {"output": "hi"}
    acquire_op, _ = inst.calls[0].sent[0]
    exec_op, kw = inst.calls[1].sent[0]
    assert acquire_op == "control"
    assert exec_op == "exec"
    assert kw["lease"] == "l1-1" and kw["epoch"] == "7" and kw["seq"] == "1"
    assert inst.next_seq == 1


def test_mutate_honours_explicit_sequence():
    inst = FakeInstance([{"ok": True, "result": {}}])
    inst.lease = "l1-1"
    inst.epoch = 7
    server._mutate(inst, "key", key="27", down="1", action_seq=9)
    _, kw = inst.calls[0].sent[0]
    assert kw["seq"] == "9" and inst.next_seq == 9


def test_mutate_stale_state_drops_lease_and_beat():
    inst = FakeInstance([{"ok": False, "error": "STALE_STATE",
                          "detail": "lease mismatch"}])
    inst.lease = "l1-1"
    inst.epoch = 7
    inst._hb_thread = object()
    try:
        server._mutate(inst, "key", key="27", down="1")
        assert False, "expected ValueError"
    except ValueError as e:
        assert str(e) == "STALE_STATE: lease mismatch"
    assert inst.lease == "" and inst._hb_thread is None


@pytest.mark.parametrize("detail", ["world generation mismatch",
                                    "control revision mismatch"])
def test_mutate_keeps_lease_on_precondition_mismatch(detail):
    """A world/control generation mismatch is not a lease failure: the
    bridge keeps the lease and its sequence high-water, so the local
    lease, its beat and the sequence counter must all survive. Rewinding
    here spends a sequence the bridge never consumed and every following
    mutation re-acquires the same lease and returns RESULT_EXPIRED."""
    inst = FakeInstance([
        {"ok": False, "error": "STALE_STATE", "detail": detail},
        {"ok": True, "result": {"output": "hi"}},
    ])
    inst.lease = "l1-1"
    inst.epoch = 7
    inst._hb_thread = object()
    try:
        server._mutate(inst, "exec", text="god", world_generation=3)
        assert False, "expected ValueError"
    except ValueError as e:
        assert str(e) == "STALE_STATE: %s" % detail
    assert inst.lease == "l1-1" and inst._hb_thread is not None

    out = server._mutate(inst, "exec", text="god")
    assert out == {"output": "hi"}
    # the rejected call consumed local seq 1 but no engine fence; the
    # retry continues at 2 with no lazy acquire in between
    assert len(inst.calls) == 2
    op, kw = inst.calls[1].sent[0]
    assert op == "exec" and kw["seq"] == "2" and kw["lease"] == "l1-1"


def test_mutate_lazy_acquire_resets_sequence_for_a_new_lease():
    """A genuinely dead lease is cleared, and the lazy re-acquire of a
    different lease id opens a fresh sequence fence."""
    inst = FakeInstance([
        {"ok": False, "error": "STALE_STATE", "detail": "lease mismatch"},
        {"ok": True, "result": {"lease": "l2-9", "epoch": 9}},
        {"ok": True, "result": {"output": "hi"}},
    ])
    inst.lease = "l1-1"
    inst.epoch = 7
    inst.next_seq = 5
    inst._hb_thread = object()
    try:
        server._mutate(inst, "exec", text="god")
        assert False, "expected ValueError"
    except ValueError as e:
        assert str(e) == "STALE_STATE: lease mismatch"
    assert inst.lease == "" and inst._hb_thread is None

    out = server._mutate(inst, "exec", text="god")
    assert out == {"output": "hi"}
    first_op, _ = inst.calls[0].sent[0]
    acquire_op, _ = inst.calls[1].sent[0]
    exec_op, kw = inst.calls[2].sent[0]
    assert first_op == "exec" and acquire_op == "control" and exec_op == "exec"
    assert kw["lease"] == "l2-9" and kw["seq"] == "1"
    assert inst.next_seq == 1


def test_mutate_wraps_engine_disconnect():
    inst = FakeInstance([])
    inst.lease = "l1-1"
    inst.epoch = 7
    inst.client = lambda: DisconnectedClient()
    try:
        server._mutate(inst, "exec", text="god")
        assert False, "expected ValueError"
    except ValueError as e:
        assert str(e) == "ENGINE_DISCONNECTED: boom"


def test_instance_stop_keepalive_clears_thread():
    """A stopped beat must clear the thread handle: _mutate restarts the
    beat from exactly that flag, so a stale handle means a lease that is
    never beaten again."""
    inst = lifecycle.Instance("qtest", -1, 1, "tok", owned=False)
    inst._hb_thread = object()
    inst.stop_keepalive()
    assert inst._hb_thread is None


def test_mutate_restarts_stopped_beat():
    inst = lifecycle.Instance("qtest", -1, 1, "tok", owned=False)
    inst.client = lambda: SilentClient()
    inst.lease = "l1-1"
    inst.epoch = 7
    inst.keepalive(inst.lease, inst.epoch)
    inst.stop_keepalive()
    assert inst._hb_thread is None
    server._mutate(inst, "key", key="27", down="1")
    assert inst._hb_thread is not None
    inst.stop_keepalive()
