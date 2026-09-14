"""Task 5: bounded actions, release idempotency, lease expiry. Skips
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
PORT = 29878
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


def _act(op, lease, epoch, seq, ticks):
    """Minimal zero-input ticks act; returns the reply."""
    return op(id="x", op="act", lease=lease, epoch=str(epoch),
              seq=str(seq), action_id="", forward="0", strafe="0",
              vertical="0", yaw="0", pitch="0", attack="0",
              jump="none", impulse="0", run="0", ticks=str(ticks))


def test_bridge_act():
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
                pytest.fail("glquake exited early, code %s" % child.returncode)
            time.sleep(0.25)
        assert token, "no token file appeared in 30s"

        s = None
        deadline = time.time() + 15
        while time.time() < deadline:
            try:
                s = socket.create_connection(("127.0.0.1", PORT), timeout=5)
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

            # deterministic input path + a loaded world (ticks only count
            # once cls.signon == SIGNONS, so a map is required)
            op(id="s0", op="exec", text="access_mouseonly 0")
            op(id="s1", op="exec", text="map start")

            deadline = time.time() + 30
            while time.time() < deadline:
                reply = op(id="w", op="exec", text="status")
                assert reply["ok"] is True, reply
                if "players" in reply["result"]["output"]:
                    break
                time.sleep(0.25)
            else:
                pytest.fail("map never reached gameplay-ready")

            # acquire the controller lease
            reply = op(id="a1", op="control", sub="acquire")
            assert reply["ok"] is True, reply
            lease = reply["result"]["lease"]
            epoch = reply["result"]["epoch"]

            # 72-tick act: exact completion, no interruption
            reply = _act(op, lease, epoch, 1, 72)
            assert reply["ok"] is True, reply
            assert reply["result"]["completed_ticks"] == 72, reply
            assert reply["result"]["interrupted"] is False, reply

            # release is idempotent
            r1 = op(id="r1", op="release")
            assert r1["ok"] is True, r1
            r2 = op(id="r2", op="release")
            assert r2["ok"] is True, r2

            # heartbeat expiry: acquire again, act, let it go silent,
            # then the next act on the stale lease must be refused
            reply = op(id="a2", op="control", sub="acquire")
            assert reply["ok"] is True, reply
            lease2 = reply["result"]["lease"]
            epoch2 = reply["result"]["epoch"]
            reply = _act(op, lease2, epoch2, 1, 5)
            assert reply["ok"] is True, reply

            time.sleep(2.6)  # no hb: lease expires after 2 s wall clock

            reply = _act(op, lease2, epoch2, 2, 5)
            assert reply["ok"] is False, reply
            assert reply["error"] == "STALE_STATE", reply

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