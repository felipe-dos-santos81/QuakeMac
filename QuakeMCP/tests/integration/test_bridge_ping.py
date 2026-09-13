"""Task 2: bridge ping over token TCP. Skips without game data."""
import glob
import json
import os
import socket
import subprocess
import sys
import time

import pytest

REPO = os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(
    os.path.abspath(__file__)))))
EXE = os.path.join(REPO, "Quake", "build-macosx", "glquake")
PAK = os.path.join(REPO, "game", "id1", "pak0.pak")
PORT = 29876
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


def test_bridge_ping():
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
            for path in glob.glob(os.path.join(TOKEN_DIR, "quakemcp-*.token")):
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

        ping_deadline = time.time() + 15
        reply = None
        last_err = None
        while time.time() < ping_deadline:
            try:
                s = socket.create_connection(("127.0.0.1", PORT), timeout=5)
                break
            except OSError as e:
                last_err = e
                time.sleep(0.25)
        else:
            pytest.fail("cannot connect to bridge: %s" % last_err)
        with s:
            f = s.makefile("rwb")
            reply = send_recv(f, {"v": 1, "auth": token,
                                  "id": "1", "op": "ping"})
            assert reply["ok"] is True, reply
            assert reply["result"] == {"ready": True}, reply

            reply = send_recv(f, {"v": 1, "auth": token,
                                  "id": "2", "op": "bogus_op"})
            assert reply["ok"] is False, reply
            assert reply["error"] == "UNSUPPORTED_CAPABILITY", reply

        assert token_path is not None and os.path.exists(token_path)
    finally:
        child.kill()
        child.wait()
        # SIGKILL skips MCP_Shutdown: unlink the stale token so reruns
        # start clean, then assert it is gone.
        for path in glob.glob(os.path.join(TOKEN_DIR, "quakemcp-%d.token" % child.pid)):
            os.unlink(path)
        assert glob.glob(os.path.join(TOKEN_DIR, "quakemcp-%d.token" % child.pid)) == [], \
            "token file not cleaned up"
