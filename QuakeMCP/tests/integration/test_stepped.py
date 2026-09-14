"""Task 9: stepped mode, fixed-step acts, receipts and preconditions.
Skips without game data."""
import asyncio
import glob
import json
import os
import socket
import subprocess
import sys
import time

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..",
                                "src"))

from mcp.shared.memory import create_connected_server_and_client_session

from quakemcp.server import mcp

REPO = os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(
    os.path.abspath(__file__)))))
EXE = os.path.join(REPO, "Quake", "build-macosx", "glquake")
PAK = os.path.join(REPO, "game", "id1", "pak0.pak")
PORT = 29881
TOKEN_DIR = os.environ.get("TMPDIR", "/tmp")


def send_recv(f, obj, timeout=10):
    f.write((json.dumps(obj) + "\n").encode())
    f.flush()
    line = b""
    deadline = time.time() + timeout
    while time.time() < deadline:
        chunk = f.readline()
        if chunk:
            line += chunk
            if line.endswith(b"\n"):
                break
        else:
            time.sleep(0.05)
    assert line.endswith(b"\n"), "no reply line from bridge"
    return json.loads(line.decode())


def _act(op, lease, epoch, seq, ticks, action_id="", **extra):
    fields = dict(id="x", op="act", lease=lease, epoch=str(epoch),
                  seq=str(seq), action_id=action_id, forward="0",
                  strafe="0", vertical="0", yaw="0", pitch="0",
                  attack="0", jump="none", impulse="0", run="0")
    fields.update(extra)
    if ticks:
        fields["ticks"] = str(ticks)
    return op(**fields)


def test_stepped_mode():
    if not os.path.exists(PAK):
        pytest.skip("game data absent: %s" % PAK)
    if not os.path.exists(EXE):
        pytest.skip("binary missing: %s" % EXE)

    child = subprocess.Popen(
        [EXE, "-basedir", os.path.join(REPO, "game"),
         "-mcp_port", str(PORT), "+mcp_enabled", "1"],
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        cwd=REPO)
    before = set(glob.glob(os.path.join(TOKEN_DIR, "quakemcp-*.token")))
    token = None
    try:
        deadline = time.time() + 30
        while time.time() < deadline:
            for path in glob.glob(os.path.join(TOKEN_DIR,
                                               "quakemcp-*.token")):
                if path in before:
                    continue
                with open(path) as fh:
                    token = fh.read().strip()
                break
            if token:
                break
            if child.poll() is not None:
                pytest.fail("glquake exited early, code %s" % child.returncode)
            time.sleep(0.25)
        assert token, "no token file appeared in 30s"

        s = None
        deadline = time.time() + 15
        while time.time() < deadline:
            try:
                s = socket.create_connection(("127.0.0.1", PORT), timeout=20)
                break
            except OSError:
                time.sleep(0.25)
        assert s is not None, "cannot connect to bridge"
        with s:
            f = s.makefile("rwb")

            def op(**kw):
                kw.setdefault("v", 1)
                kw.setdefault("auth", token)
                return send_recv(f, kw)

            def state():
                reply = op(id="st", op="state")
                assert reply["ok"] is True, reply
                return reply["result"]

            # every mutation runs under the controller lease; this raw
            # socket has no server-side keepalive, so the lease must be
            # beaten while a world loads
            reply = op(id="a0", op="control", sub="acquire")
            assert reply["ok"] is True, reply
            lease = reply["result"]["lease"]
            epoch = reply["result"]["epoch"]
            seq = [0]

            def next_seq():
                seq[0] += 1
                return str(seq[0])

            def beat():
                reply = op(id="hb", op="hb", lease=lease, epoch=str(epoch))
                assert reply["ok"] is True, reply

            def mutate(**kw):
                return op(lease=lease, epoch=str(epoch), seq=next_seq(), **kw)

            # a loaded world first: stepped mode opts out of the normal
            # clock afterwards, and world loads need the clock
            mutate(id="s0", op="exec", text="access_mouseonly 0")
            mutate(id="s1", op="exec", text="map start")
            deadline = time.time() + 30
            while time.time() < deadline:
                beat()
                reply = mutate(id="w", op="exec", text="status")
                assert reply["ok"] is True, reply
                if "players" in reply["result"]["output"]:
                    break
                time.sleep(0.25)
            else:
                pytest.fail("map never reached gameplay-ready")

            # a beat must name the live lease, never a stale id
            reply = op(id="hb0", op="hb", lease="l0-0", epoch="0")
            assert reply["ok"] is False, reply
            assert reply["error"] == "STALE_STATE", reply

            reply = mutate(id="m1", op="control", sub="mode", mode="stepped")
            assert reply["ok"] is True, reply
            assert reply["result"]["mode"] == "stepped", reply

            # idle stepped session: the clock stops but polling and
            # rendering keep the endpoint observable
            a = state()
            time.sleep(1.0)
            b = state()
            assert b["frame"] == a["frame"], (a, b)
            assert b["time"] == a["time"], (a, b)
            assert b["movemessages"] == a["movemessages"], (a, b)

            # fixed-step act: exactly ten simulation steps, and the
            # freeze never pays back the idle wall time as catch-up
            reply = _act(op, lease, epoch, next_seq(), 10, action_id="a1")
            assert reply["ok"] is True, reply
            assert reply["result"]["completed_ticks"] == 10, reply
            assert reply["result"]["interrupted"] is False, reply

            reply = op(id="hb", op="hb", lease=lease,
                        epoch=str(epoch))
            assert reply["ok"] is True, reply
            c = state()
            assert c["frame"] == a["frame"] + 10, (a, c)
            assert 0.0 < c["time"] - a["time"] < 0.6, (a, c)
            assert c["movemessages"] > a["movemessages"], (a, c)

            # repeat of the same (lease, action_id, arguments) returns
            # the recorded receipt and advances nothing
            reply = _act(op, lease, epoch, next_seq(), 10, action_id="a1")
            assert reply["ok"] is True, reply
            assert reply["result"]["completed_ticks"] == 10, reply
            d = state()
            assert d["frame"] == c["frame"], (c, d)

            # same id with different arguments is a conflict
            reply = _act(op, lease, epoch, next_seq(), 12, action_id="a1")
            assert reply["ok"] is False, reply
            assert reply["error"] == "POLICY_DENIED", reply

            reply = op(id="hb", op="hb", lease=lease,
                        epoch=str(epoch))
            assert reply["ok"] is True, reply
            # stale world generation is refused before execution
            reply = _act(op, lease, epoch, next_seq(), 4, action_id="a2",
                         world_generation=str(a["world_gen"] + 999))
            assert reply["ok"] is False, reply
            assert reply["error"] == "STALE_STATE", reply

            # stepped mode counts steps; a wall-clock budget would
            # silently degrade into real-time stepping
            reply = _act(op, lease, epoch, next_seq(), 0, action_id="a3",
                         duration_ms="100")
            assert reply["ok"] is False, reply
            assert reply["error"] == "UNSUPPORTED_CAPABILITY", reply

            # back to realtime: the clock runs again
            reply = mutate(id="m2", op="control", sub="mode", mode="realtime")
            assert reply["ok"] is True, reply
            assert reply["result"]["mode"] == "realtime", reply
            reply = op(id="hb", op="hb", lease=lease,
                        epoch=str(epoch))
            assert reply["ok"] is True, reply
            e = state()
            time.sleep(0.4)
            g = state()
            assert g["frame"] > e["frame"], (e, g)

    finally:
        child.kill()
        child.wait()
        for path in glob.glob(os.path.join(
                TOKEN_DIR, "quakemcp-%d.token" % child.pid)):
            os.unlink(path)
        assert glob.glob(os.path.join(
            TOKEN_DIR, "quakemcp-%d.token" % child.pid)) == [], \
            "token file not cleaned up"


def test_stepped_tools():
    """The tool surface: mode selection, fixed steps, receipts and
    preconditions end to end through the MCP server."""
    if not os.path.exists(PAK):
        pytest.skip("game data absent: %s" % PAK)
    if not os.path.exists(EXE):
        pytest.skip("binary missing: %s" % EXE)

    async def run():
        async with create_connected_server_and_client_session(mcp) as s:
            await s.initialize()

            def text(result):
                return "".join(getattr(c, "text", "") for c in result.content)

            r = await s.call_tool("quake_start", {"profile": "local",
                                                  "port": 29882})
            assert not r.isError, text(r)
            inst = json.loads(text(r))["instance"]
            try:
                r = await s.call_tool("quake_game", {
                    "instance": inst, "operation": "new_game"})
                assert not r.isError, text(r)
                assert json.loads(text(r))["gameplay_ready"] is True

                r = await s.call_tool("quake_control", {
                    "instance": inst, "operation": "mode",
                    "mode": "stepped"})
                assert not r.isError, text(r)
                assert json.loads(text(r))["mode"] == "stepped"

                r = await s.call_tool("quake_control", {
                    "instance": inst, "operation": "acquire"})
                assert not r.isError, text(r)
                ctl = json.loads(text(r))
                lease, epoch = ctl["lease"], ctl["epoch"]

                # the server beats the caller's lease: a quiet stretch
                # longer than the bridge's 2 s expiry keeps control
                time.sleep(2.5)

                r = await s.call_tool("quake_state", {"instance": inst})
                a = json.loads(text(r))

                # action_seq stays unset: the server assigns the next
                # sequence after the earlier mutations
                act = {"instance": inst, "ticks": 8, "action_id": "tool1",
                       "lease": lease, "epoch": epoch}
                r = await s.call_tool("quake_act", dict(act))
                assert not r.isError, text(r)
                assert r.structuredContent["completed_ticks"] == 8
                assert r.content and r.content[0].type == "image"
                assert r.structuredContent["interrupted"] is False

                r = await s.call_tool("quake_state", {"instance": inst})
                b = json.loads(text(r))
                assert b["frame"] == a["frame"] + 8

                # identical retry: the recorded receipt, no new steps
                r = await s.call_tool("quake_act", dict(act))
                assert not r.isError, text(r)
                assert r.structuredContent["completed_ticks"] == 8
                r = await s.call_tool("quake_state", {"instance": inst})
                c = json.loads(text(r))
                assert c["frame"] == b["frame"]

                # stale world generation is refused before execution
                r = await s.call_tool("quake_act", {
                    "instance": inst, "ticks": 4, "action_id": "tool2",
                    "lease": lease, "epoch": epoch,
                    "world_generation": a["world_gen"] + 999})
                assert r.isError and "STALE_STATE" in text(r), text(r)

                # stepped sessions count steps, not wall time
                r = await s.call_tool("quake_act", {
                    "instance": inst, "duration_ms": 100,
                    "action_id": "tool3", "lease": lease, "epoch": epoch})
                assert r.isError and "UNSUPPORTED_CAPABILITY" in text(r), \
                    text(r)

                r = await s.call_tool("quake_control", {
                    "instance": inst, "operation": "mode",
                    "mode": "realtime"})
                assert not r.isError, text(r)
                assert json.loads(text(r))["mode"] == "realtime"
            finally:
                await s.call_tool("quake_stop", {"instance": inst})

    asyncio.run(run())
