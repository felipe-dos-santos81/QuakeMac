"""Task 7: real-engine image observation, frame correlation, scene
change across an action. Skips without game data.

The bridge delivers raw bottom-up RGB (flip/encode happen in
vision.py), so this test checks geometry and content change, not PNG
bytes.
"""
import glob
import hashlib
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
PORT = 29880
TOKEN_DIR = os.environ.get("TMPDIR", "/tmp")


def _read_line(f, timeout):
    line = b""
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            chunk = f.readline()
        except Exception as e:
            raise AssertionError("read error %r; partial=%r"
                                 % (e, line[:160]))
        if chunk:
            line += chunk
            if line.endswith(b"\n"):
                return line
        else:
            time.sleep(0.05)
    raise AssertionError("no reply line from bridge; partial=%r"
                         % (line[:160],))


def send_recv(f, obj, timeout=15):
    """Line reply plus the framed blob it declares, if any."""
    f.write((json.dumps(obj) + "\n").encode())
    f.flush()
    reply = json.loads(_read_line(f, timeout).decode())
    n = int(reply.get("result", {}).get("blob_bytes", 0) or 0)
    buf = b""
    while len(buf) < n:
        chunk = f.read(min(65536, n - len(buf)))
        if not chunk:
            break
        buf += chunk
    assert len(buf) == n, "short blob: %d/%d" % (len(buf), n)
    return reply, buf


def test_bridge_observe():
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

            def observe(after="0", timeout="3000"):
                reply, blob = op(id="ob", op="observe", after_frame=after,
                                 timeout_ms=timeout)
                assert reply["ok"] is True, reply
                res = reply["result"]
                assert len(blob) == res["src_w"] * res["src_h"] * 3, res
                return res, blob

            op(id="s0", op="exec", text="access_mouseonly 0")

            # a frame before any map: the renderer always composes
            res, blob = observe()
            assert res["src_w"] > 0 and res["src_h"] > 0, res
            assert res["ui"] in (0, 1, 2, 3), res
            assert res["viewport"][2] == res["src_w"], res
            assert res["viewport"][3] == res["src_h"], res
            assert res["hud_rect"][1] + res["hud_rect"][3] == res["src_h"], res
            assert res["frame"] > 0, res
            assert isinstance(res["capture_age_ms"], int), res

            op(id="s1", op="exec", text="map start")
            deadline = time.time() + 30
            while time.time() < deadline:
                reply, _ = op(id="st", op="state")
                assert reply["ok"] is True, reply
                if reply["result"]["signon"] == 4:
                    break
                time.sleep(0.25)
            else:
                pytest.fail("map never reached gameplay-ready")

            res1, blob1 = observe()
            assert res1["map"] == "start", res1
            hash1 = hashlib.sha1(blob1).hexdigest()
            frame1 = res1["frame"]

            # frame correlation: a state read right after the observation
            # is the same or a newer poll of the same still scene
            reply, _ = op(id="st2", op="state")
            assert reply["ok"] is True, reply
            delta = reply["result"]["frame"] - frame1
            assert 0 <= delta <= 5, (reply["result"], frame1)

            reply, _ = op(id="c1", op="control", sub="acquire")
            assert reply["ok"] is True, reply
            lease = reply["result"]["lease"]
            epoch = reply["result"]["epoch"]

            reply, _ = op(id="ac", op="act", lease=lease, epoch=str(epoch),
                          seq="1", action_id="look", forward="1", strafe="0",
                          vertical="0", yaw="30", pitch="0", attack="0",
                          jump="none", impulse="0", run="0", ticks="12")
            assert reply["ok"] is True, reply
            assert reply["result"]["completed_ticks"] == 12, reply

            res2, blob2 = observe()
            assert res2["frame"] > frame1, (res2, frame1)
            hash2 = hashlib.sha1(blob2).hexdigest()
            assert hash2 != hash1, "scene pixels did not change"

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
