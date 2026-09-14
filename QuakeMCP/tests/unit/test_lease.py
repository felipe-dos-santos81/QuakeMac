"""Unit tests: local lease lifecycle (drop path, heartbeat loss)."""
import os
import sys

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
