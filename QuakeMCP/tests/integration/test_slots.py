"""Task 1: two connection slots and uniform lease expiry. Skips
without game data."""
import json
import os
import socket
import subprocess
import time

import pytest

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", ".."))
PORT = 29886


def _launch():
    exe = os.path.join(ROOT, "Quake", "build-macosx", "glquake")
    if not os.path.exists(os.path.join(ROOT, "game", "id1", "pak0.pak")):
        pytest.skip("game data missing")
    if not os.path.exists(exe):
        pytest.skip("binary missing: %s" % exe)
    tmp = os.environ.get("TMPDIR", "/tmp")
    before = set(os.listdir(tmp))
    # -nosound: CoreAudio device open can stall S_Init for a minute or
    # more on this host; the bridge does not touch the sound system
    proc = subprocess.Popen(
        [exe, "-nosound", "-basedir", "game", "-mcp_port", str(PORT),
         "+mcp_enabled", "1"],
        cwd=ROOT, stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    token = None
    deadline = time.time() + 30
    while time.time() < deadline and token is None:
        for name in os.listdir(tmp):
            if name.startswith("quakemcp-") and name.endswith(".token") \
                    and name not in before:
                with open(os.path.join(tmp, name)) as fh:
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


def _client(token):
    sock = socket.create_connection(("127.0.0.1", PORT), timeout=10)
    fh = sock.makefile("rwb")
    return sock, fh


def _send(fh, obj):
    fh.write((json.dumps(obj) + "\n").encode()); fh.flush()


def _recv(fh):
    return json.loads(fh.readline().decode())


def test_control_connection_during_deferred_act():
    proc, token = _launch()
    try:
        a = _client(token)
        b = _client(token)
        try:
            _send(a[1], {"v": 1, "auth": token, "id": "1", "op": "control",
                         "sub": "acquire"})
            res = _recv(a[1])["result"]
            lease, epoch = res["lease"], res["epoch"]
            # act defers its reply; keep the request connection open
            # slot/lease mechanics, not admission: respawn relaxes the gate
            _send(a[1], {"v": 1, "auth": token, "id": "2", "op": "act",
                         "lease": lease, "epoch": str(epoch),
                         "seq": "1", "forward": "1", "ticks": "72",
                         "respawn": "1"})
            # the control connection must still answer while the act runs
            _send(b[1], {"v": 1, "auth": token, "id": "3", "op": "state"})
            assert _recv(b[1])["ok"] is True
            _send(b[1], {"v": 1, "auth": token, "id": "4", "op": "hb",
                         "lease": lease, "epoch": str(epoch)})
            assert _recv(b[1])["ok"] is True, "heartbeat lands mid-act"
            _send(b[1], {"v": 1, "auth": token, "id": "5", "op": "release"})
            assert _recv(b[1])["ok"] is True
            reply = _recv(a[1])  # the deferred act reply
            assert reply["result"]["interrupted"] is True
        finally:
            a[0].close(); b[0].close()
    finally:
        proc.kill(); proc.wait()


def test_eof_mid_act_finishes_action():
    proc, token = _launch()
    try:
        a = _client(token)
        _send(a[1], {"v": 1, "auth": token, "id": "1", "op": "control",
                     "sub": "acquire"})
        res = _recv(a[1])["result"]
        lease, epoch = res["lease"], res["epoch"]
        # arm a deferred act, then drop the connection that asked for it
        # slot/lease mechanics, not admission: respawn relaxes the gate
        _send(a[1], {"v": 1, "auth": token, "id": "2", "op": "act",
                     "lease": lease, "epoch": str(epoch),
                     "seq": "1", "forward": "1", "ticks": "72",
                     "respawn": "1", "action_id": "eof1"})
        a[1].close()
        a[0].close()
        # EOF finishes the act and records the receipt; by the time the
        # lease is gone the retry must still answer from the ledger
        time.sleep(2.7)
        b = _client(token)
        try:
            # the retry repeats every hashed argument, respawn included
            _send(b[1], {"v": 1, "auth": token, "id": "3", "op": "act",
                         "lease": lease, "epoch": str(epoch),
                         "seq": "2", "forward": "1", "ticks": "72",
                         "respawn": "1", "action_id": "eof1"})
            reply = _recv(b[1])
            assert reply["ok"] is True, reply
            assert reply["result"]["interrupted"] is True, reply
        finally:
            b[0].close()
    finally:
        proc.kill(); proc.wait()


def test_lease_expiry_after_silence():
    proc, token = _launch()
    try:
        a = _client(token)
        try:
            _send(a[1], {"v": 1, "auth": token, "id": "1", "op": "control",
                         "sub": "acquire"})
            res = _recv(a[1])["result"]
            time.sleep(2.5)              # no heartbeat
            # hb names and checks the lease, so a dead lease must surface
            # here as STALE_STATE
            _send(a[1], {"v": 1, "auth": token, "id": "2", "op": "hb",
                         "lease": res["lease"], "epoch": str(res["epoch"])})
            assert _recv(a[1])["error"] == "STALE_STATE"
        finally:
            a[0].close()
    finally:
        proc.kill(); proc.wait()
