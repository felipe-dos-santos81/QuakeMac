"""Process supervision: launch/attach/stop glquake instances.

Profiles are a hardcoded table — never a shell command. Child
stdout/stderr go to the platform temp dir, never the repo. The token
is read from the bridge's private file, never CLI or logs.
"""
import collections
import glob
import os
import subprocess
import tempfile
import threading
import time

from .engine import BridgeClient
from .models import EngineDisconnected, QuakeMCPError

# Token wait covers a plain (no -nosound) start: the CoreAudio device
# open on this host can stall S_Init for ~15 s before MCP_Init writes
# the token. 10 s failed the lifecycle launch; 30 s is behavior-neutral
# for users (only a longer failure wait).
TOKEN_WAIT_SECS = 30.0
PING_RETRIES = 3
HEARTBEAT_SECS = 0.5


def _repo_root():
    here = os.path.abspath(__file__)
    # src/quakemcp/lifecycle.py -> QuakeMCP/ -> repo root
    return os.path.dirname(os.path.dirname(os.path.dirname(
        os.path.dirname(here))))


PROFILES = {
    "local": {
        "exe": os.path.join("Quake", "build-macosx", "glquake"),
        "args": ["-basedir", "game"],
        "basedir": "game",
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
        self._hb_stop = None
        self._hb_thread = None
        self.lease = ""
        self.epoch = 0
        self.next_seq = 0
        # delivered act observations keyed by action_id (Task 5); the
        # bridge ledger replays a duplicate's metadata, this replays its
        # frame
        self.receipts = collections.OrderedDict()

    def client(self):
        return BridgeClient("127.0.0.1", self.port, self.token)

    def basedir(self):
        """Game data root for owned profiles; None when attach cannot know it."""
        profile = PROFILES.get(self.profile_id)
        if not profile or not profile.get("basedir"):
            return None
        return os.path.join(_repo_root(), profile["basedir"])

    def keepalive(self, lease, epoch):
        """Beat the controller lease from the server side.

        The bridge expires a lease after 2 s of silence; the design calls
        for a beat every 500 ms. A tool call can legitimately outlast
        that window (world loads, image encoding), so the beat runs on a
        daemon thread with its own short-lived connection.
        """
        self.stop_keepalive()
        stop = threading.Event()
        self._hb_stop = stop

        def beat():
            while not stop.wait(HEARTBEAT_SECS):
                if not self._heartbeat_round(lease, epoch):
                    return

        self._hb_thread = threading.Thread(target=beat, daemon=True)
        self._hb_thread.start()

    def adopt_lease(self, lease, epoch):
        """Record an acquire reply as the local lease and keep it beating.

        A redundant acquire returns the same lease and the bridge keeps
        its sequence high-water; rewinding here would spend a sequence
        the engine never consumed and the next mutation would be
        RESULT_EXPIRED. Only a new lease id opens a fresh fence.
        """
        if lease != self.lease:
            self.next_seq = 0
        self.lease = lease
        self.epoch = epoch
        if not self._hb_thread:
            self.keepalive(lease, epoch)

    def _heartbeat_round(self, lease, epoch):
        """One beat. False when the bridge says the lease is gone.

        A STALE_STATE reply means human takeover, expiry or revocation:
        drop the local lease so status stays truthful and the next
        mutation lazily acquires a fresh one. Only a reply for the
        generation this round beats for drops; a late reply from a
        superseded beat stops this thread without touching the newer
        lease. Transport errors are transient; keep beating.
        """
        try:
            client = self.client()
        except (EngineDisconnected, OSError):
            return True
        try:
            reply = client.send("hb", lease=lease, epoch=str(epoch))
        except (EngineDisconnected, OSError):
            return True
        finally:
            client.close()
        if reply.get("ok") is not True \
                and reply.get("error") == "STALE_STATE":
            self.drop_lease_for(lease, epoch)
            return False
        return True

    def stop_keepalive(self):
        if self._hb_stop is not None:
            self._hb_stop.set()
            self._hb_stop = None
        self._hb_thread = None

    def clear_lease(self):
        """Drop the local lease copy and its beat.

        One path for release, cancellation, heartbeat loss and any other
        STALE_STATE that revokes the lease; the next mutation lazily
        acquires a fresh one.
        """
        self.stop_keepalive()
        self.lease = ""
        self.epoch = 0
        self.next_seq = 0

    def drop_lease_for(self, lease, epoch):
        """Clear the local lease only when it is still the named generation.

        Stale replies arrive late from both the heartbeat and mutations;
        a reply for a superseded lease/beat must not clear a lease
        another caller has since adopted.
        """
        if self.lease == lease and self.epoch == epoch:
            self.clear_lease()

    def stop(self):
        self.stop_keepalive()
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


def _wait_token(before, timeout=TOKEN_WAIT_SECS):
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
        # stdin must not be inherited: over stdio transport it is the
        # control channel, and a game child holding it steals requests
        # or EOFs the session
        proc = subprocess.Popen(args, stdin=subprocess.DEVNULL,
                                stdout=log, stderr=log, cwd=root)
    except OSError as e:
        log.close()
        raise EngineDisconnected("spawn: %s" % e)
    log.close()
    try:
        token = _wait_token(before)
    except EngineDisconnected:
        if proc.poll() is None:
            proc.kill()
            proc.wait()
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
                return inst
            last = "bad ping reply: %r" % (reply,)
        except EngineDisconnected as e:
            last = str(e)
        time.sleep(0.5)
    if proc.poll() is None:
        proc.kill()
        proc.wait()
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
