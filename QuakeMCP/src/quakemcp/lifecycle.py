"""Process supervision: launch/attach/stop glquake instances.

Profiles are a hardcoded table — never a shell command. Child
stdout/stderr go to the platform temp dir, never the repo. The token
is read from the bridge's private file, never CLI or logs.
"""
import glob
import os
import subprocess
import tempfile
import time

from .engine import BridgeClient
from .models import EngineDisconnected, QuakeMCPError

TOKEN_WAIT_SECS = 10.0
PING_RETRIES = 3


def _repo_root():
    here = os.path.abspath(__file__)
    # src/quakemcp/lifecycle.py -> QuakeMCP/ -> repo root
    return os.path.dirname(os.path.dirname(os.path.dirname(
        os.path.dirname(here))))


PROFILES = {
    "local": {
        "exe": os.path.join("Quake", "build-macosx", "glquake"),
        "args": ["-basedir", "game"],
    },
}


class Instance:
    def __init__(self, instance_id, pid, port, token, owned,
                 profile_id=None, proc=None):
        self.instance_id = instance_id
        self.pid = pid
        self.port = port
        self.token = token
        self.owned = owned
        self.profile_id = profile_id
        self._proc = proc

    def client(self):
        return BridgeClient("127.0.0.1", self.port, self.token)

    def stop(self):
        if not self.owned:
            raise QuakeMCPError("POLICY_DENIED",
                                "attached instance: refuse to terminate")
        if self._proc is not None and self._proc.poll() is None:
            self._proc.terminate()
            try:
                self._proc.wait(timeout=5)
            except subprocess.TimeoutExpired:
                self._proc.kill()
                self._proc.wait()
        return {"stopped": True, "instance": self.instance_id}


_instances = {}
_next_id = [1]


def _register(instance):
    _instances[instance.instance_id] = instance
    return instance


def get(instance_id):
    return _instances.get(instance_id)


def forget(instance_id):
    _instances.pop(instance_id, None)


def _token_dir():
    return os.environ.get("TMPDIR", "/tmp")


def _wait_token(pid, before, timeout=TOKEN_WAIT_SECS):
    deadline = time.time() + timeout
    while time.time() < deadline:
        for path in glob.glob(os.path.join(_token_dir(),
                                           "quakemcp-*.token")):
            if path in before:
                continue
            with open(path) as fh:
                token = fh.read().strip()
            if token:
                return token
        time.sleep(0.25)
    raise EngineDisconnected("no token file in %.0fs" % timeout)


def launch(profile_id, port=28900, extra_args=()):
    """Spawn an owned glquake child and wait for bridge_ready."""
    profile = PROFILES.get(profile_id)
    if profile is None:
        raise QuakeMCPError("INVALID_CONTEXT",
                            "unknown profile: %r" % (profile_id,))
    root = _repo_root()
    exe = os.path.join(root, profile["exe"])
    if not os.path.exists(exe):
        raise EngineDisconnected("binary missing: %s" % exe)
    log_dir = os.path.join(tempfile.gettempdir(), "quakemcp-logs")
    os.makedirs(log_dir, exist_ok=True)
    ts = time.strftime("%Y%m%d-%H%M%S")
    log_path = os.path.join(log_dir, "%s-%d.log" % (ts, os.getpid()))
    log = open(log_path, "ab")
    before = set(glob.glob(os.path.join(_token_dir(), "quakemcp-*.token")))
    args = [exe] + list(profile["args"]) + ["-mcp_port", str(port),
                                            "+mcp_enabled", "1"] + list(extra_args)
    try:
        proc = subprocess.Popen(args, stdout=log, stderr=log, cwd=root)
    except OSError as e:
        log.close()
        raise EngineDisconnected("spawn: %s" % e)
    try:
        token = _wait_token(proc.pid, before)
    except EngineDisconnected:
        if proc.poll() is None:
            proc.kill()
            proc.wait()
        log.close()
        raise
    instance_id = "q%d" % _next_id[0]
    _next_id[0] += 1
    inst = Instance(instance_id, proc.pid, port, token, owned=True,
                    profile_id=profile_id, proc=proc)
    # ping to confirm bridge_ready
    last = None
    for _ in range(PING_RETRIES):
        try:
            client = inst.client()
            try:
                reply = client.send("ping")
            finally:
                client.close()
            if reply.get("ok") is True:
                _register(inst)
                log.close()
                return inst
            last = "bad ping reply: %r" % (reply,)
        except EngineDisconnected as e:
            last = str(e)
        time.sleep(0.5)
    if proc.poll() is None:
        proc.kill()
        proc.wait()
    log.close()
    raise EngineDisconnected(last or "ping failed")


def attach(instance_id, port, token):
    """Attach to an authorized instrumented instance (owned=False)."""
    client = BridgeClient("127.0.0.1", port, token)
    try:
        reply = client.send("ping")
    finally:
        client.close()
    if reply.get("ok") is not True:
        raise EngineDisconnected("attach ping: %r" % (reply,))
    inst = Instance(instance_id, -1, port, token, owned=False)
    return _register(inst)
