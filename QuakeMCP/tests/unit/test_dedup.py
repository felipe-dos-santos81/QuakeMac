"""Task 9: receipt-ring semantics mirrored from the C bridge.

The engine copy is authoritative; these pin the behavior a retry sees
and the order property that a duplicate lookup precedes preconditions.
"""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..",
                                "src"))

from quakemcp.models import Ledger


def test_duplicate_same_arguments_returns_receipt():
    led = Ledger()
    assert led.check("l1-1", 1, "a1", 0xabc) == ("new", None)
    led.record("l1-1", 1, "a1", 0xabc, '{"completed_ticks":5}')
    assert led.check("l1-1", 1, "a1", 0xabc) == (
        "duplicate", '{"completed_ticks":5}')


def test_duplicate_different_arguments_is_conflict():
    led = Ledger()
    led.record("l1-1", 1, "a1", 0xabc, "receipt")
    assert led.check("l1-1", 1, "a1", 0xdef) == ("conflict", None)
    # the original receipt is untouched and still retrievable
    assert led.check("l1-1", 1, "a1", 0xabc) == ("duplicate", "receipt")


def test_no_action_id_is_never_deduplicated():
    led = Ledger()
    led.record("l1-1", 1, "", 0xabc, "receipt")
    assert led.check("l1-1", 1, "", 0xabc) == ("new", None)


def test_identity_is_scoped_by_lease_and_epoch():
    led = Ledger()
    led.record("l1-1", 1, "a1", 7, "receipt")
    assert led.check("l2-1", 1, "a1", 7) == ("new", None)
    assert led.check("l1-1", 2, "a1", 7) == ("new", None)


def test_duplicate_lookup_precedes_preconditions():
    """A retry gets its receipt even though its lease is long gone: the
    engine checks the ring before lease, epoch, world and control."""
    led = Ledger()
    led.record("l1-1", 1, "a1", 7, "receipt")
    assert led.check("l1-1", 1, "a1", 7) == ("duplicate", "receipt")


def test_eviction_expires_consumed_sequences():
    led = Ledger(capacity=2)
    for seq, action_id in enumerate(("a1", "a2", "a3"), start=1):
        assert led.check("l1-1", 1, action_id, seq) == ("new", None)
        led.record("l1-1", 1, action_id, seq, "r%d" % seq)
        led.consume(seq)
    # a1 fell out of the ring, so its sequence can only expire
    assert led.check("l1-1", 1, "a1", 1) == ("new", None)
    assert led.expired(2) is True
    assert led.expired(4) is False
