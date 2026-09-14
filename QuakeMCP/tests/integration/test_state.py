"""Task 6: state snapshot, movement observation, world generation. Skips
without game data."""
import glob
import json
import os
import socket
import subprocess
import time

import pytest

REPO = os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(
    os.path.abspath(__file__)))))
EXE = os.path.join(REPO, "Quake", "build-macosx", "glquake")
PAK = os.path.join(REPO, "game", "id1", "pak0.pak")
PORT = 29879
TOKEN_DIR = os.environ.get("TMPDIR", "/tmp")

STATE_KEYS = ("epoch", "world_gen", "control_rev", "frame", "time", "map",
              "mode", "pos", "angles", "health", "ammo", "ui", "loading",
              "dead", "intermission", "signon", "movemessages")


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


def test_bridge_state():
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
    token_path = None
    try:
        deadline = time.time() + 30
        while time.time() < deadline:
            for path in glob.glob(os.path.join(TOKEN_DIR,
                                               "quakemcp-*.token")):
                if path in before:
                    continue
                with open(path) as fh:
                    token = fh.read().strip()
                token_path = path
                break
            if token:
                break
            if child.poll() is not None:
                pytest.fail("glquake exited early, code %s"
                            % child.returncode)
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
                st = reply["result"]
                for k in STATE_KEYS:
                    assert k in st, "state missing %s: %r" % (k, st)
                return st

            op(id="s0", op="exec", text="access_mouseonly 0")
            op(id="s1", op="exec", text="map start")

            # gameplay_ready: signon complete, input no longer suppressed,
            # loading plaque gone
            deadline = time.time() + 30
            while time.time() < deadline:
                st = state()
                if (st["signon"] == 4 and st["movemessages"] > 2
                        and not st["loading"]):
                    break
                time.sleep(0.25)
            else:
                pytest.fail("never reached gameplay_ready: %r" % (st,))

            assert st["map"] == "start", st
            assert st["dead"] is False, st
            assert st["intermission"] is False, st
            assert st["time"] >= 1.0, st
            pos0 = tuple(st["pos"])
            frame0 = st["frame"]
            gen0 = st["world_gen"]

            reply = op(id="a1", op="control", sub="acquire")
            assert reply["ok"] is True, reply
            lease = reply["result"]["lease"]
            epoch = reply["result"]["epoch"]

            reply = op(id="ac", op="act", lease=lease, epoch=str(epoch),
                       seq="1", action_id="walk", forward="1", strafe="0",
                       vertical="0", yaw="0", pitch="0", attack="0",
                       jump="none", impulse="0", run="0", ticks="24")
            assert reply["ok"] is True, reply
            assert reply["result"]["completed_ticks"] == 24, reply
            assert reply["result"]["interrupted"] is False, reply

            st2 = state()
            assert st2["frame"] > frame0, (st2, frame0)
            pos1 = tuple(st2["pos"])
            moved = sum((a - b) ** 2 for a, b in zip(pos1, pos0)) ** 0.5
            assert moved > 1.0, "player did not move: %r -> %r" % (pos0, pos1)

            # a same-map reload is a new world generation
            gen_before = st2["world_gen"]
            op(id="s2", op="exec", text="map start")
            deadline = time.time() + 30
            while time.time() < deadline:
                st3 = state()
                if st3["world_gen"] > gen_before and st3["signon"] == 4:
                    break
                time.sleep(0.25)
            else:
                pytest.fail("world_gen did not bump on reload: %r" % (st3,))

        assert token_path is not None and os.path.exists(token_path)
    finally:
        child.kill()
        child.wait()
        for path in glob.glob(os.path.join(
                TOKEN_DIR, "quakemcp-%d.token" % child.pid)):
            os.unlink(path)
        assert glob.glob(os.path.join(
            TOKEN_DIR, "quakemcp-%d.token" % child.pid)) == [], \
            "token file not cleaned up"
