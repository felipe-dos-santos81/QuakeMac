"""Task 2: a cancelled mutation issues a best-effort emergency release."""
import os
import sys
import threading
import time

import anyio

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..",
                                "src"))

from quakemcp import lifecycle, server


class BlockingClient:
    def __init__(self, started, record):
        self.started = started
        self.record = record

    def send(self, op, **kw):
        if op == "release":
            self.record.append("release")
            return {"ok": True, "result": {}}
        self.started.set()
        time.sleep(2)          # bounded: the abandoned worker finishes fast
        return {"ok": True, "result": {}}

    def send_retrying(self, op, **kw):
        return self.send(op, **kw)

    def close(self):
        pass


class Inst(lifecycle.Instance):
    def __init__(self, started, record):
        super().__init__("qfake", -1, 0, "tok", owned=False)
        self.started = started
        self.record = record
        self.lease = "l1-1"
        self.epoch = 7

    def client(self):
        return BlockingClient(self.started, self.record)

    def keepalive(self, lease, epoch):
        pass


def test_cancelled_tool_sends_emergency_release():
    started = threading.Event()
    record = []
    inst = Inst(started, record)

    async def run():
        async with server._cancel_releases(inst):
            await server._offload(server._mutate, inst, "exec", text="god")

    async def main():
        async with anyio.create_task_group() as tg:
            tg.start_soon(run)
            while not started.is_set():
                await anyio.sleep(0.01)
            tg.cancel_scope.cancel()

    anyio.run(main)
    assert "release" in record
    # the emergency path must leave the instance as clean as quake_release
    # does, or the next mutation trusts a revoked lease and fails once
    assert inst.lease == "" and inst.epoch == 0 and inst.next_seq == 0
    assert inst._hb_thread is None
