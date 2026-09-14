"""Task 3: uniform mutation envelope, receipts, tail/status, version gate.

Raw sockets against the real bridge; skips without game data. Each test
launches its own instance on the module port.
"""
import json
import os
import socket
import subprocess
import time

import pytest

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", ".."))
EXE = os.path.join(ROOT, "Quake", "build-macosx", "glquake")
PAK = os.path.join(ROOT, "game", "id1", "pak0.pak")
PORT = 29887
TOKEN_DIR = os.environ.get("TMPDIR", "/tmp")


def _launch():
    if not os.path.exists(PAK):
        pytest.skip("game data missing")
    if not os.path.exists(EXE):
        pytest.skip("binary missing: %s" % EXE)
    before = set(os.listdir(TOKEN_DIR))
    # -nosound: CoreAudio device open can stall S_Init for a minute or
    # more on this host; the bridge does not touch the sound system
    proc = subprocess.Popen(
        [EXE, "-nosound", "-basedir", "game", "-mcp_port", str(PORT),
         "+mcp_enabled", "1"],
        cwd=ROOT, stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    token = None
    deadline = time.time() + 30
    while time.time() < deadline and token is None:
        for name in os.listdir(TOKEN_DIR):
            if name.startswith("quakemcp-") and name.endswith(".token") \
                    and name not in before:
                with open(os.path.join(TOKEN_DIR, name)) as fh:
                    token = fh.read().strip()
        if token:
            break
        if proc.poll() is not None:
            pytest.fail("glquake exited early, code %s" % proc.returncode)
        time.sleep(0.1)
    if not token:
        proc.kill()
        proc.wait()
    assert token, "no token"
    return proc, token


class Bridge:
    """One authenticated raw connection to the bridge."""

    def __init__(self, token, timeout=10):
        self.token = token
        self.sock = socket.create_connection(("127.0.0.1", PORT),
                                             timeout=timeout)
        self.fh = self.sock.makefile("rwb")

    def close(self):
        self.fh.close()
        self.sock.close()

    def op(self, **kw):
        kw.setdefault("v", 1)
        kw.setdefault("auth", self.token)
        self.fh.write((json.dumps(kw) + "\n").encode())
        self.fh.flush()
        line = b""
        deadline = time.time() + 10
        while time.time() < deadline:
            chunk = self.fh.readline()
            if chunk:
                line += chunk
                if line.endswith(b"\n"):
                    break
        assert line.endswith(b"\n"), "no reply line from bridge"
        return json.loads(line.decode())

    def acquire(self):
        reply = self.op(id="acq", op="control", sub="acquire")
        assert reply["ok"] is True, reply
        return reply["result"]["lease"], reply["result"]["epoch"]


@pytest.fixture()
def bridge():
    proc, token = _launch()
    conn = None
    try:
        deadline = time.time() + 15
        last = None
        while time.time() < deadline:
            try:
                conn = Bridge(token)
                break
            except OSError as e:
                last = e
                time.sleep(0.25)
        assert conn is not None, "cannot connect to bridge: %s" % last
        yield conn
    finally:
        if conn is not None:
            conn.close()
        proc.kill()
        proc.wait()
        path = os.path.join(TOKEN_DIR, "quakemcp-%d.token" % proc.pid)
        if os.path.exists(path):
            os.unlink(path)


def _poll_output(bridge, needle, timeout=5):
    """The exec reply predates Cbuf_Execute; poll the ring for its output."""
    deadline = time.time() + timeout
    output = ""
    while time.time() < deadline:
        reply = bridge.op(id="tail", op="tail")
        assert reply["ok"] is True, reply
        output = reply["result"]["output"]
        if needle in output:
            break
        time.sleep(0.05)
    return output


def test_exec_without_lease_is_stale(bridge):
    reply = bridge.op(id="1", op="exec", text="god", seq="1")
    assert reply["ok"] is False, reply
    assert reply["error"] == "STALE_STATE", reply


def test_receipts_dedupe_and_conflict(bridge):
    lease, epoch = bridge.acquire()
    env = {"lease": lease, "epoch": str(epoch)}

    first = bridge.op(id="2", op="exec", text="god", action_id="e1",
                      seq="1", **env)
    assert first["ok"] is True, first

    again = bridge.op(id="3", op="exec", text="god", action_id="e1",
                      seq="2", **env)
    assert again["ok"] is True, again
    assert again["result"].get("duplicate") is True, again

    conflict = bridge.op(id="4", op="exec", text="noclip", action_id="e1",
                         seq="3", **env)
    assert conflict["ok"] is False, conflict
    assert conflict["error"] == "POLICY_DENIED", conflict

    # a spent sequence with no surviving receipt never executes again
    replay = bridge.op(id="5", op="exec", text="god", action_id="e2",
                       seq="1", **env)
    assert replay["ok"] is False, replay
    assert replay["error"] == "RESULT_EXPIRED", replay


def test_tail_is_a_read(bridge):
    denied = bridge.op(id="6", op="tail", auth="wrong")
    assert denied["ok"] is False, denied
    assert denied["error"] == "POLICY_DENIED", denied

    reply = bridge.op(id="7", op="tail")
    assert reply["ok"] is True, reply
    assert "output" in reply["result"], reply


def test_status_reports_receipts(bridge):
    lease, epoch = bridge.acquire()
    reply = bridge.op(id="8", op="exec", text="god", action_id="e1",
                      seq="1", lease=lease, epoch=str(epoch))
    assert reply["ok"] is True, reply

    latest = bridge.op(id="9", op="status")
    assert latest["ok"] is True, latest
    assert latest["result"]["action_id"] == "e1", latest
    assert latest["result"]["state"] == "done", latest

    named = bridge.op(id="9b", op="status", action_id="e1")
    assert named["ok"] is True, named
    assert named["result"]["action_id"] == "e1", named

    missing = bridge.op(id="10", op="status", action_id="nope")
    assert missing["ok"] is False, missing
    assert missing["error"] == "INVALID_CONTEXT", missing


def test_protocol_version_gate(bridge):
    reply = bridge.op(id="11", op="ping", v=2)
    assert reply["ok"] is False, reply
    assert reply["error"] == "UNSUPPORTED_CAPABILITY", reply


def test_exec_text_is_required(bridge):
    lease, epoch = bridge.acquire()
    env = {"lease": lease, "epoch": str(epoch)}

    empty = bridge.op(id="1", op="exec", text="", seq="1", **env)
    assert empty["ok"] is False, empty
    assert empty["error"] == "INVALID_CONTEXT", empty

    missing = bridge.op(id="2", op="exec", seq="2", **env)
    assert missing["ok"] is False, missing
    assert missing["error"] == "INVALID_CONTEXT", missing

    # validation precedes the envelope: neither rejection spent seq 1
    ok = bridge.op(id="3", op="exec", text="god", seq="1", **env)
    assert ok["ok"] is True, ok


def test_parser_value_does_not_impersonate_a_key(bridge):
    """A value equal to a later key name must not divert the lookup."""
    lease, epoch = bridge.acquire()
    reply = bridge.op(id="1", op="exec", pad="text", zz="9",
                      text="echo MARKER", seq="1", lease=lease,
                      epoch=str(epoch))
    assert reply["ok"] is True, reply
    output = _poll_output(bridge, "MARKER")
    assert "MARKER" in output, output


def test_parser_escaped_quotes_still_decode(bridge):
    lease, epoch = bridge.acquire()
    reply = bridge.op(id="1", op="exec", text='echo "A B"', seq="1",
                      lease=lease, epoch=str(epoch))
    assert reply["ok"] is True, reply
    output = _poll_output(bridge, "A B")
    assert "A B" in output, output
