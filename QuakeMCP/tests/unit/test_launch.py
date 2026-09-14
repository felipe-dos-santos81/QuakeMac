"""Task 10: the launch contract that keeps the control channel intact."""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..",
                                "src"))

from quakemcp import lifecycle


def test_launch_does_not_inherit_stdio(monkeypatch, tmp_path):
    """The engine child must never hold the server's stdio: under the
    stdio transport that pipe is the MCP control channel, and an
    inherited stdin both steals requests and closes the session."""
    calls = {}

    class FakeProc:
        pid = 4242

    class FakeClient:
        def __init__(self, *a, **kw):
            pass

        def send(self, op, **kw):
            return {"ok": True}

        def close(self):
            pass

    def fake_popen(args, **kw):
        calls["args"] = list(args)
        calls.update(kw)
        return FakeProc()

    monkeypatch.setattr(lifecycle.subprocess, "Popen", fake_popen)
    monkeypatch.setattr(lifecycle, "BridgeClient", FakeClient)
    monkeypatch.setattr(lifecycle, "_wait_token",
                        lambda pid, before: "tok")
    monkeypatch.setattr(lifecycle.os.path, "exists", lambda p: True)
    monkeypatch.setattr(lifecycle.tempfile, "gettempdir",
                        lambda: str(tmp_path))

    inst = lifecycle.launch("local", port=9999)
    assert calls["stdin"] is lifecycle.subprocess.DEVNULL
    assert calls["stdout"] is calls["stderr"]
    assert calls["cwd"] == lifecycle._repo_root()
    assert calls["args"][-4:] == ["-mcp_port", "9999", "+mcp_enabled", "1"]
    assert inst.owned is True and inst.pid == 4242
    lifecycle.forget(inst.instance_id)
