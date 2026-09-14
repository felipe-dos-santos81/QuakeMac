"""Task 3: exec/cvar mutations under the lease envelope. Skips without
game data."""
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
PORT = 29877
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


def test_bridge_exec():
    if not os.path.exists(PAK):
        pytest.skip("game data absent: %s" % PAK)
    if not os.path.exists(EXE):
        pytest.skip("binary missing: %s" % EXE)

    before = set(glob.glob(os.path.join(TOKEN_DIR, "quakemcp-*.token")))
    # -nosound: CoreAudio device open stalls S_Init on this host; the
    # bridge does not touch the sound system
    child = subprocess.Popen(
        [EXE, "-nosound", "-basedir", os.path.join(REPO, "game"),
         "-mcp_port", str(PORT), "+mcp_enabled", "1"],
        stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        cwd=REPO)
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

            # every mutation carries the lease envelope; this raw client
            # has no server-side keepalive, so it beats between calls
            acquired = op(id="a0", op="control", sub="acquire")
            assert acquired["ok"] is True, acquired
            lease = acquired["result"]["lease"]
            epoch = acquired["result"]["epoch"]
            seq = [0]

            def beat():
                reply = op(id="hb", op="hb", lease=lease, epoch=str(epoch))
                assert reply["ok"] is True, reply

            def mutate(**kw):
                seq[0] += 1
                return op(lease=lease, epoch=str(epoch), seq=str(seq[0]),
                          **kw)

            # exec runs on the next _Host_Frame via Cbuf_Execute, so the
            # first reply predates execution: poll until output lands.
            # NOTE: brief says exec "help" shows tail "commands:" — wrong:
            # help opens the help MENU (M_Menu_Help_f, menu.c) and prints
            # nothing. "version" (Host_Version_f) prints "Version x.xx".
            beat()
            mutate(id="e1", op="exec", text="version")
            tail = ""
            deadline = time.time() + 15
            while time.time() < deadline:
                beat()
                reply = mutate(id="e2", op="exec", text="echo POLL_MARKER")
                assert reply["ok"] is True, reply
                tail = reply["result"]["output"]
                if "Version" in tail:
                    break
                time.sleep(0.25)
            assert "Version" in tail, "version output missing: %r" % tail

            reply = op(id="c1", op="cvar", name="host_maxfps")
            assert reply["ok"] is True, reply
            assert reply["result"] == {"value": "72"}, reply

            try:
                reply = mutate(id="c2", op="cvar", name="host_maxfps",
                               value="80")
                assert reply["ok"] is True, reply
                assert reply["result"] == {"value": "80"}, reply
                reply = op(id="c3", op="cvar", name="host_maxfps")
                assert reply["result"] == {"value": "80"}, reply
            finally:
                mutate(id="c4", op="cvar", name="host_maxfps", value="72")
            reply = op(id="c5", op="cvar", name="host_maxfps")
            assert reply["result"] == {"value": "72"}, reply

            reply = op(id="c6", op="cvar", name="no_such_var")
            assert reply["ok"] is False, reply
            assert reply["error"] == "INVALID_CONTEXT", reply

            reply = op(id="e3", op="exec", text="x" * 5000)
            assert reply["ok"] is False, reply
            assert reply["error"] == "POLICY_DENIED", reply

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
