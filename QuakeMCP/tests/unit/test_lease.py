"""Unit tests: local lease lifecycle (drop path, heartbeat loss)."""
import os
import sys
import threading

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..",
                                "src"))

from quakemcp import lifecycle


def test_clear_lease_resets_and_stops_beat(monkeypatch):
    inst = lifecycle.Instance("qtest", -1, 1, "tok", owned=False)
    stopped = []
    monkeypatch.setattr(inst, "stop_keepalive",
                        lambda: stopped.append(True))
    inst.lease, inst.epoch, inst.next_seq = "l1-1", 7, 4
    inst.clear_lease()
    assert (inst.lease, inst.epoch, inst.next_seq) == ("", 0, 0)
    assert stopped, "the beat must stop with the lease"


class _Bridge:
    def __init__(self, reply):
        self.reply = reply

    def send(self, op, **kw):
        return self.reply

    def close(self):
        pass


def test_beat_clears_lease_on_stale_state(monkeypatch):
    inst = lifecycle.Instance("qtest", -1, 1, "tok", owned=False)
    inst.lease, inst.epoch, inst.next_seq = "l1-1", 3, 2
    monkeypatch.setattr(lifecycle.Instance, "client", lambda self: _Bridge(
        {"ok": False, "error": "STALE_STATE", "detail": "no lease"}))
    assert inst._heartbeat_round("l1-1", 3) is False
    assert (inst.lease, inst.epoch, inst.next_seq) == ("", 0, 0)


def test_beat_keeps_beating_on_ok(monkeypatch):
    inst = lifecycle.Instance("qtest", -1, 1, "tok", owned=False)
    monkeypatch.setattr(lifecycle.Instance, "client", lambda self: _Bridge(
        {"ok": True, "result": {}}))
    inst.lease, inst.epoch = "l1-1", 3
    assert inst._heartbeat_round("l1-1", 3) is True
    assert (inst.lease, inst.epoch) == ("l1-1", 3)


def test_beat_ignores_stale_reply_for_superseded_generation(monkeypatch):
    inst = lifecycle.Instance("qtest", -1, 1, "tok", owned=False)
    inst.lease, inst.epoch, inst.next_seq = "l2-7", 4, 5
    stop = threading.Event()
    inst._hb_stop = stop
    monkeypatch.setattr(lifecycle.Instance, "client", lambda self: _Bridge(
        {"ok": False, "error": "STALE_STATE"}))
    assert inst._heartbeat_round("l1-1", 3) is False
    assert (inst.lease, inst.epoch, inst.next_seq) == ("l2-7", 4, 5)
    assert inst._hb_stop is stop
    assert not stop.is_set()
