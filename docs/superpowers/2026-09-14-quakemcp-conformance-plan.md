# QuakeMCP Conformance Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Implement `docs/superpowers/2026-09-14-quakemcp-conformance-design.md`: uniform lease enforcement, two-slot responsiveness, receipt integrity (`FRAME_EXPIRED`), modal `needs_input`, async cancellation, death-flow fixes, and documentary truth.

**Architecture:** The C bridge in `QuakeMCP/bridge/` gains a two-connection socket layer, one shared precondition/receipt path for every mutation, and modal interception. The Python server in `QuakeMCP/src/quakemcp/` gains an async tool surface over `anyio` threads with a server-assigned lease sequence, a receipt LRU, capabilities reporting and corrected policy. Tests stay layered: unit (no engine), contract (in-process SDK), integration (real `glquake`, skipped without game data).

**Tech Stack:** id-era C (Apple clang, GNU make, SDL3, OpenGL), Python 3.12, `mcp==1.29.0`, `Pillow>=12.0`, pytest 8.x, anyio (SDK dependency).

## Global Constraints

- Work on branch `fix/quakemcp-conformance`; `QuakeWorld/` untouched; every engine edit behind `#ifdef QUAKE_MCP` (macro shims allowed where a call site compiles to a no-op).
- Ground truth is `make`; editor/LSP diagnostics are noise.
- Build gates after every engine task: `make clean && make build-release build-server build-client` and `make clean && make build-release QUAKE_MCP=1` both exit 0. Leave the MCP build in place before integration tests.
- `make clean` between variants: switching `QUAKE_MCP=1` on after a vanilla build does not relink.
- Integration tests skip without `game/id1/pak0.pak`; link `game/` in a worktree (`ln -s ../../game game`) and remove the link before committing. Never commit `game/`, `__pycache__/`, `*.sav`, token files.
- Stage explicit paths only; never `git add -A`. id-era C style: tabs, K&R braces, `/* banner */` comments.
- Integration test module basenames must be unique across `tests/unit/` and `tests/integration/`.
- Wire protocol stays v1; additions are additive. Request `v` must equal 1.
- Python deps are pinned: `mcp==1.29.0`, `Pillow>=12.0`; `anyio` arrives with the SDK.

## File Structure

| File | Responsibility after this round |
|---|---|
| `QuakeMCP/bridge/q_mcp.c` | socket slots, auth, version gate, dispatch, shared preconditions, receipts, lease/action lifecycle, modal interception |
| `QuakeMCP/bridge/q_mcp.h` | `MCP_*` surface incl. `MCP_ModalOpened`/`MCP_ModalClosed`, `MCAP_Cancel` |
| `QuakeMCP/bridge/q_mcp_capture.c` | capture ring; gains cancel + unified slot struct |
| `QuakeMCP/src/quakemcp/server.py` | async tools, `_mutate`, policy, capabilities, receipt handling |
| `QuakeMCP/src/quakemcp/lifecycle.py` | `Instance` lease/seq state, launch defaults, small hygiene fixes |
| `QuakeMCP/src/quakemcp/models.py` | capabilities manifest, gameplay-ready predicate, error codes (unchanged list) |
| `QuakeMCP/tests/unit/*` | envelope/seq, receipt cache, cancellation, policy |
| `QuakeMCP/tests/integration/*` | slots, envelope, stepped default, modal, death flow, observation |
| `QuakeMCP/docs/engine-integration.md` | re-verified hooks, op map, capability matrix |
| `QuakeMCP/README.md`, `QuakeMCP/AGENTS.md` | truth about `#ifdef` and stepped default |
| `Quake`, `Quake/client/menu.c`, `Quake/render/gl_screen.c` | modal hooks only |

---

### Task 1: Two connection slots, uniform lease expiry, EOF invalidation

**Files:**
- Modify: `QuakeMCP/bridge/q_mcp.c` (socket statics, setup/accept/poll/reply/blob/close, lease check)
- Modify: `QuakeMCP/bridge/q_mcp.h` (add `void MCAP_Cancel (void);`)
- Modify: `QuakeMCP/bridge/q_mcp_capture.c` (implement `MCAP_Cancel`)
- Test: `QuakeMCP/tests/integration/test_slots.py` (new)

**Interfaces:**
- Consumes: existing `MCP_Poll`, `MCP_Reply (id, ok, error, result)`, `MCP_SendBlob`, `MCP_CloseClient`.
- Produces: two generic slots; `mcp_reply_fd` reply target; `mcp_act_fd`/`mcp_observe_fd` deferred targets; `MCAP_Cancel()`; uniform `MCP_CheckLease`.

- [ ] **Step 1: Write the failing integration test**

Create `QuakeMCP/tests/integration/test_slots.py`. Copy the launch helper pattern from `test_bridge_ping.py` (raw socket, token file, module-owned port 29886):

```python
"""Two-connection end to end: control traffic while an act reply is deferred."""
import json, os, socket, subprocess, sys, time
import pytest

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", ".."))
PORT = 29886

def _launch():
    exe = os.path.join(ROOT, "Quake", "build-macosx", "glquake")
    if not os.path.exists(os.path.join(ROOT, "game", "id1", "pak0.pak")):
        pytest.skip("game data missing")
    tmp = os.environ.get("TMPDIR", "/tmp")
    before = set(os.listdir(tmp))
    proc = subprocess.Popen(
        [exe, "-basedir", "game", "-mcp_port", str(PORT), "+mcp_enabled", "1"],
        cwd=ROOT, stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    token = None
    deadline = time.time() + 10
    while time.time() < deadline and token is None:
        for name in os.listdir(tmp):
            if name.startswith("quakemcp-") and name.endswith(".token") \
                    and name not in before:
                with open(os.path.join(tmp, name)) as fh:
                    token = fh.read().strip()
        time.sleep(0.1)
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
            _send(a[1], {"v": 1, "auth": token, "id": "2", "op": "act",
                         "lease": lease, "epoch": str(epoch),
                         "seq": "1", "forward": "1", "ticks": "72"})
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
```

- [ ] **Step 2: Run it to verify it fails**

Run: `python3 -m pytest QuakeMCP/tests/integration/test_slots.py -v`
Expected: FAIL — the second connection is closed by the single-slot acceptor, so `_recv(b[1])` raises or returns garbage.

- [ ] **Step 3: Implement the slot layer**

In `q_mcp.c`, replace the single-client statics with two slots, keeping the existing reply signature (call sites stay untouched):

```c
#define MCP_SLOTS	2
typedef struct
{
	int	fd;
	char	line[MCP_LINE_MAX + 1];
	int	len;
} mcp_slot_t;

static mcp_slot_t mcp_slots[MCP_SLOTS] = {
	{ -1, { 0 }, 0 },
	{ -1, { 0 }, 0 },
};
static int	mcp_reply_fd = -1;	// target of the next MCP_Reply
static int	mcp_act_fd = -1;	// connection owning a deferred act
static int	mcp_observe_fd = -1;	// connection owning a deferred observe
```

- `MCP_Send`, `MCP_SendBlob` and `MCP_CloseClient` move to `mcp_reply_fd`. Add `MCP_CloseSlot (int i)` that closes `mcp_slots[i].fd`, resets the slot, and when the closed fd owned the deferred act/observe: `MCP_FinishAct (true)` (its send to the dead fd is a no-op) or `MCAP_Cancel ()`. `MCP_CloseClient ()` becomes `MCP_CloseAll ()` semantics for `MCP_Shutdown`, plus a per-fd variant for error paths.
- `MCP_Send`/`MCP_SendBlob` guard `mcp_reply_fd < 0`.
- The accept loop picks the first free slot; if both are occupied, it reaps a slot whose peer is at EOF (existing `MSG_PEEK` probe) and takes it, else closes the new fd.
- `MCP_PollClient` drains every occupied slot; before each `MCP_HandleLine (slot.line)`, set `mcp_reply_fd = slot.fd`.
- Deferred senders: when arming an act, `mcp_act_fd = mcp_reply_fd`; when deferring an observe, `mcp_observe_fd = mcp_reply_fd`. `MCP_FinishAct` and `MCP_SendObservation` set `mcp_reply_fd` to their saved fd before replying.
- Remove the `if (mcp_act_active) return;` early return in `MCP_CheckLease`; expiry now calls `MCP_ClearControl ()` even mid-action.

In `q_mcp_capture.c`:

```c
/*
==================
MCAP_Cancel

Drop an outstanding request or a captured-but-undelivered slot when the
requesting connection goes away.
==================
*/
void MCAP_Cancel (void)
{
	mcap_req_active = false;
	mcap_pending = -1;
	mcap_pinned = -1;
}
```

Declare it in `q_mcp.h` next to `MCAP_Request`.

- [ ] **Step 4: Run the test to verify it passes**

Run: `make clean && make build-release QUAKE_MCP=1 && python3 -m pytest QuakeMCP/tests/integration/test_slots.py -v`
Expected: PASS.

- [ ] **Step 5: Add the uniform-expiry case to the same file**

```python
def test_lease_expiry_after_silence():
    proc, token = _launch()
    try:
        a = _client(token)
        try:
            _send(a[1], {"v": 1, "auth": token, "id": "1", "op": "control",
                         "sub": "acquire"})
            res = _recv(a[1])["result"]
            time.sleep(2.5)              # no heartbeat
            _send(a[1], {"v": 1, "auth": token, "id": "2", "op": "exec",
                         "text": "god", "lease": res["lease"],
                         "epoch": str(res["epoch"]), "seq": "1"})
            assert _recv(a[1])["error"] == "STALE_STATE"
        finally:
            a[0].close()
    finally:
        proc.kill(); proc.wait()
```

The mid-act expiry is the same expression; its enabling condition (heartbeats
landing while an act reply is deferred) is what the first test proves.

- [ ] **Step 6: Build both variants and commit**

```bash
make clean && make build-release build-server build-client
make clean && make build-release QUAKE_MCP=1
python3 -m pytest QuakeMCP/tests/integration/test_slots.py -v
git add QuakeMCP/bridge/q_mcp.c QuakeMCP/bridge/q_mcp.h QuakeMCP/bridge/q_mcp_capture.c QuakeMCP/tests/integration/test_slots.py
git commit -m "fix: quakemcp two connection slots and uniform lease expiry"
```

---

### Task 2: Python `_mutate`, async tools, cancellation

**Files:**
- Modify: `QuakeMCP/src/quakemcp/lifecycle.py` (`Instance` state; hygiene: `_wait_token`, `log.close`)
- Modify: `QuakeMCP/src/quakemcp/server.py` (envelope helper, async conversion, cancellation)
- Test: `QuakeMCP/tests/unit/test_mutate.py` (new), `QuakeMCP/tests/unit/test_cancel.py` (new)

**Interfaces:**
- Consumes: Task 1 bridge; existing `BridgeClient.send/send_retrying`, `_bridge`, `_bridge_ok`.
- Produces: `_mutate(inst, op, action_id="", lease="", action_seq=0, epoch=0, world_generation=0, control_revision=0, **args) -> dict`; `_offload(fn, *args, **kwargs)`; `_cancel_releases(inst)` async context manager; `Instance.lease`, `Instance.epoch`, `Instance.next_seq`.

- [ ] **Step 1: Write the failing unit tests**

`tests/unit/test_mutate.py` — a fake `Instance` with a fake client:

```python
import os, sys
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", "src"))

from quakemcp import server

class FakeClient:
    def __init__(self, replies):
        self.replies = list(replies)
        self.sent = []
    def send(self, op, **kw):
        self.sent.append((op, dict(kw)))
        return self.replies.pop(0)
    def send_retrying(self, op, **kw):
        return self.send(op, **kw)
    def close(self):
        pass

class FakeInstance:
    def __init__(self, replies):
        self.replies = replies
        self.calls = []
        self.lease = ""
        self.epoch = 0
        self.next_seq = 0
        self._hb_thread = None
    def client(self):
        self.calls.append(FakeClient(self.replies))
        return self.calls[-1]
    def keepalive(self, lease, epoch):
        self._hb_thread = object()
    def stop_keepalive(self):
        self._hb_thread = None

def test_mutate_lazily_acquires_and_assigns_sequence():
    inst = FakeInstance([
        {"ok": True, "result": {"lease": "l1-1", "epoch": 7}},
        {"ok": True, "result": {"output": "hi"}},
    ])
    out = server._mutate(inst, "exec", text="god")
    assert out == {"output": "hi"}
    acquire_op, _ = inst.calls[0].sent[0]
    exec_op, kw = inst.calls[1].sent[0]
    assert acquire_op == "control"
    assert exec_op == "exec"
    assert kw["lease"] == "l1-1" and kw["epoch"] == "7" and kw["seq"] == "1"
    assert inst.next_seq == 1

def test_mutate_honours_explicit_sequence():
    inst = FakeInstance([{"ok": True, "result": {}}])
    inst.lease = "l1-1"; inst.epoch = 7
    server._mutate(inst, "key", key="27", down="1", action_seq=9)
    _, kw = inst.calls[0].sent[0]
    assert kw["seq"] == "9" and inst.next_seq == 9
```

`tests/unit/test_cancel.py`:

```python
import threading, time
import anyio
import os, sys
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", "src"))

from quakemcp import server

class BlockingClient:
    def __init__(self, started, record):
        self.started = started
        self.record = record
    def send(self, op, **kw):
        if op == "release":
            self.record.append("release")
            return {"ok": True, "result": {}}
        self.started.set()
        time.sleep(2)          # bounded: the abandoned worker finishes fast
        return {"ok": True, "result": {}}
    def send_retrying(self, op, **kw):
        return self.send(op, **kw)
    def close(self):
        pass

class Inst:
    def __init__(self, started, record):
        self.started = started
        self.record = record
        self.lease = "l1-1"; self.epoch = 7; self.next_seq = 0
        self._hb_thread = object()
    def client(self):
        return BlockingClient(self.started, self.record)
    def keepalive(self, lease, epoch):
        pass
    def stop_keepalive(self):
        pass

def test_cancelled_tool_sends_emergency_release():
    started = threading.Event()
    record = []
    inst = Inst(started, record)

    async def run():
        async with server._cancel_releases(inst):
            await server._offload(server._mutate, inst, "exec", text="god")

    async def main():
        async with anyio.create_task_group() as tg:
            tg.start_soon(run)
            while not started.is_set():
                await anyio.sleep(0.01)
            tg.cancel_scope.cancel()

    anyio.run(main)
    assert "release" in record
```

Simplify `BlockingClient` to import `time` at the top and drop the dead line.

- [ ] **Step 2: Run to verify failure**

Run: `python3 -m pytest QuakeMCP/tests/unit/test_mutate.py QuakeMCP/tests/unit/test_cancel.py -v`
Expected: FAIL with `AttributeError: module 'quakemcp.server' has no attribute '_mutate'`.

- [ ] **Step 3: Implement `Instance` state and helpers**

`lifecycle.py` — extend `Instance.__init__` and clean the two flagged nits in the same edit:

```python
class Instance:
    def __init__(self, instance_id, pid, port, token, owned,
                 profile_id=None, proc=None):
        ...
        self.lease = ""
        self.epoch = 0
        self.next_seq = 0
```

`quake_control` acquire/release should set/clear these fields; `_mutate` also sets them. `_wait_token (before, timeout=...)` loses its unused `pid` argument (update the one call site). In `launch`, remove the duplicate `log.close()` on the success path (keep the one in the `finally`-style exits already present; ensure exactly one close per path).

`server.py` — add the helper and the async plumbing:

```python
import anyio
import contextlib
import functools

async def _offload(fn, *args, **kwargs):
    return await anyio.to_thread.run_sync(functools.partial(fn, *args, **kwargs))

def _emergency_release(inst):
    """Best-effort priority release; never raises."""
    try:
        _bridge(inst, "release")
    except Exception:
        pass

@contextlib.asynccontextmanager
async def _cancel_releases(inst):
    try:
        yield
    except anyio.get_cancelled_exc_class():
        with anyio.CancelScope(shield=True):
            await anyio.to_thread.run_sync(_emergency_release, inst)
        raise

def _mutate(inst, op, action_id="", lease="", action_seq=0, epoch=0,
            world_generation=0, control_revision=0, **args):
    if not lease or not epoch:
        res = _bridge_ok(inst, "control", sub="acquire")
        lease = res.get("lease", "")
        epoch = res.get("epoch", 0)
        inst.next_seq = 0
    inst.lease, inst.epoch = lease, epoch
    if not getattr(inst, "_hb_thread", None):
        inst.keepalive(lease, epoch)
    if action_seq <= 0:
        inst.next_seq += 1
        action_seq = inst.next_seq
    else:
        inst.next_seq = max(inst.next_seq, action_seq)
    kw = dict(args, lease=lease, epoch=str(epoch), seq=str(action_seq),
              action_id=action_id)
    if world_generation > 0:
        kw["world_generation"] = str(world_generation)
    if control_revision > 0:
        kw["control_revision"] = str(control_revision)
    client = inst.client()
    try:
        if action_id:
            reply = client.send_retrying(op, **kw)
        else:
            reply = client.send(op, **kw)
    finally:
        client.close()
    if reply.get("ok") is not True:
        code = reply.get("error", "ENGINE_DISCONNECTED")
        if code == "STALE_STATE":
            inst.stop_keepalive()
            inst.lease = ""
        raise ValueError("%s: %s" % (code, reply.get("detail", "")))
    return reply.get("result", {})
```

Wrap `EngineDisconnected` exactly like `_bridge` does (raise `ValueError("<code>: <detail>")`).

- [ ] **Step 4: Convert the tools and internal waits to async**

- All `@mcp.tool` functions become `async def`.
- Blocking calls (`_state`, `_tail`, `_bridge_ok`, `_mutate`, `_list_maps`, `_list_saves`) run through `await _offload(...)`.
- `_world_op`, `_save_op`, `_wait_world` become `async def`; replace `time.sleep` with `await anyio.sleep`.
- Mutating tools and the waits wrap their body in `async with _cancel_releases(inst):`.
- `quake_act` uses `_mutate(inst, "act", action_id=..., lease=..., action_seq=..., epoch=..., world_generation=..., control_revision=..., forward=..., ...)` and drops its private acquire/send block.

- [ ] **Step 5: Run the unit suite and the contract tests**

Run: `python3 -m pytest QuakeMCP/tests/unit QuakeMCP/tests/contract -v`
Expected: PASS (existing contract tests exercise the async tools through the SDK).

- [ ] **Step 6: Commit**

```bash
git add QuakeMCP/src/quakemcp/lifecycle.py QuakeMCP/src/quakemcp/server.py QuakeMCP/tests/unit/test_mutate.py QuakeMCP/tests/unit/test_cancel.py
git commit -m "feat: quakemcp async tools with shared mutation envelope"
```

---

### Task 3: Bridge envelope enforcement, receipts, `tail`, `status`, version gate

**Files:**
- Modify: `QuakeMCP/bridge/q_mcp.c`
- Modify: `QuakeMCP/src/quakemcp/server.py` (`_tail` to the `tail` op)
- Test: `QuakeMCP/tests/integration/test_envelope.py` (new); update `QuakeMCP/tests/integration/test_bridge_exec.py`

**Interfaces:**
- Consumes: Task 2's envelope fields on the wire.
- Produces: `MCP_CheckPreconditions(line, id) -> qboolean`; `MCP_LookupReceipt(line, id) -> qboolean`; `MCP_RecordReceipt(op, lease, aid, epoch, hash, result)`; ledger entries gain `op[16]`, `state[16]`; reply payloads may carry `"duplicate":true`; ops `tail`, `status`.

- [ ] **Step 1: Write the failing integration test**

`tests/integration/test_envelope.py` (raw sockets, port 29887). Cases, one test each:

1. `exec` with no lease → `STALE_STATE`.
2. acquire (`seq` starts at 1), `exec {"text":"god","action_id":"e1"}` → ok; same id and text again → ok with `"duplicate":true`; same id different text → `POLICY_DENIED`.
3. `tail` with no auth → `POLICY_DENIED`; with auth → `"output"` present.
4. `status {}` → `"action_id":"e1"` in the result; `status {"action_id":"nope"}` → `INVALID_CONTEXT`.
5. `ping` with `"v": 2` → `UNSUPPORTED_CAPABILITY`.

Also update `test_bridge_exec.py`: after acquiring a lease, pass `lease`, `epoch` and `seq` on every `exec`/`cvar` mutation (a tiny helper keeps it readable).

- [ ] **Step 2: Run to verify failure**

Run: `make clean && make build-release QUAKE_MCP=1 && python3 -m pytest QuakeMCP/tests/integration/test_envelope.py QuakeMCP/tests/integration/test_bridge_exec.py -v`
Expected: FAIL — `exec` without a lease currently succeeds; `tail` is unknown; version 2 is accepted.

- [ ] **Step 3: Implement shared preconditions and the version gate**

In `MCP_HandleLine`, first:

```c
if (MCP_FieldRawInt (line, "v") != 1)
{
	MCP_Reply (id, false, "UNSUPPORTED_CAPABILITY", "protocol version");
	return;
}
```

with:

```c
/*
==================
MCP_FieldRawInt

Read an unquoted numeric top-level field (the wire's "v").
==================
*/
static int MCP_FieldRawInt (char *line, char *name)
{
	char	pat[32];
	char	*p;

	snprintf (pat, sizeof (pat), "\"%s\":", name);
	p = strstr (line, pat);
	if (!p)
		return -1;
	p += strlen (pat);
	while (*p == ' ' || *p == '\t')
		p++;
	return atoi (p);
}
```

Then the shared checks. Extract the act handler's existing lease/epoch/world/control block into:

```c
static qboolean MCP_CheckPreconditions (char *line, char *id)
{
	char leas[64], epocs[32], worlds[32], ctrls[32];

	if (!mcp_lease_active || mcp_lease_id[0] == 0)
	{
		MCP_Reply (id, false, "STALE_STATE", "no lease");
		return false;
	}
	if (!MCP_Field (line, "lease", leas, sizeof (leas))
		|| strcmp (leas, mcp_lease_id) != 0)
	{
		MCP_Reply (id, false, "STALE_STATE", "lease mismatch");
		return false;
	}
	if (!MCP_Field (line, "epoch", epocs, sizeof (epocs))
		|| atoi (epocs) != mcp_epoch)
	{
		MCP_Reply (id, false, "STALE_STATE", "epoch mismatch");
		return false;
	}
	if (MCP_Field (line, "world_generation", worlds, sizeof (worlds))
		&& atoi (worlds) != mcp_world_gen)
	{
		MCP_Reply (id, false, "STALE_STATE",
			"world generation mismatch");
		return false;
	}
	if (MCP_Field (line, "control_revision", ctrls, sizeof (ctrls))
		&& atoi (ctrls) != mcp_control_rev)
	{
		MCP_Reply (id, false, "STALE_STATE",
			"control revision mismatch");
		return false;
	}
	return true;
}
```

Call it from `act`, `exec`, `key`, `control mode`, and `cvar` when `value` is present; act keeps its sequence check after it. `exec` with a missing or empty `text` stays a read (no envelope), so the old tail idiom keeps working until Step 4.

- [ ] **Step 4: Receipts for every mutation**

Extend the ledger entry and add helpers:

```c
#define MCP_LEDGER_SIZE 64
typedef struct
{
	char		op[16];
	char		lease[64];
	char		action_id[128];
	int		epoch;
	unsigned	hash;
	char		state[16];	// "done" | "pending" | "denied"
	char		result[MCP_REPLY_MAX];
} mcp_ledger_t;
```

- `MCP_LookupReceipt (line, id)`: for a non-empty `action_id` and the request's op, find a matching `(op, lease, action_id, epoch)`; on equal hash reply the stored result with `"duplicate":true` prefixed into the result object (build `{"duplicate":true,<result>}` — if `result` is empty use `"\"duplicate\":true"`); on different hash reply `POLICY_DENIED`.
- `MCP_RecordReceipt (op, aid, hash, state, result)`: write the next ring entry.
- `act` records at completion (`MCP_FinishAct`) with `op="act"`, state `done`; synchronous mutations record right after executing, before replying.
- The hash input gains the op name; keep FNV-1a.
- Duplicate lookup runs before `MCP_CheckPreconditions` in every mutation, and after auth/op parsing.
- `seq <= mcp_lease_seq` without a receipt remains `RESULT_EXPIRED`.

- [ ] **Step 5: Add `tail` and `status`, migrate Python**

Bridge:

```c
if (!strcmp (op, "tail"))
{
	MCP_ConsoleTail (tail, sizeof (tail));
	MCP_Escape (esctail, sizeof (esctail), tail);
	snprintf (result, sizeof (result), "\"output\":\"%s\"", esctail);
	MCP_Reply (id, true, NULL, result);
	return;
}
```

`status` (read): with `action_id`, scan the ledger for the newest matching entry (any lease, current epoch) and reply its `result` plus `"state":"<state>"`; without an id, reply the newest entry; no entry → `INVALID_CONTEXT`.

Python: `_tail` becomes `_bridge_ok(inst, "tail")`.

- [ ] **Step 6: Run tests and both gates, then commit**

Run:
```bash
make clean && make build-release build-server build-client
make clean && make build-release QUAKE_MCP=1
python3 -m pytest QuakeMCP/tests/unit QuakeMCP/tests/contract -v
python3 -m pytest QuakeMCP/tests/integration/test_envelope.py QuakeMCP/tests/integration/test_bridge_exec.py -v
```
Expected: all PASS (integration may SKIP without data).

```bash
git add QuakeMCP/bridge/q_mcp.c QuakeMCP/src/quakemcp/server.py QuakeMCP/tests/integration/test_envelope.py QuakeMCP/tests/integration/test_bridge_exec.py
git commit -m "feat: quakemcp uniform mutation envelope and receipts"
```

---

### Task 4: Tool surface — status/capabilities, stepped default, policy corrections

**Files:**
- Modify: `QuakeMCP/src/quakemcp/models.py` (capabilities manifest, gameplay predicate)
- Modify: `QuakeMCP/src/quakemcp/server.py` (envelope params, status, start, console policy, gating)
- Modify: `QuakeMCP/bridge/q_mcp.c` (gameplay gate helper used by `act`)
- Test: `QuakeMCP/tests/unit/test_policy.py` (update), `QuakeMCP/tests/contract/test_tools_list.py` (update), `QuakeMCP/tests/integration/test_lifecycle.py` (extend)

**Interfaces:**
- Consumes: `_mutate`, bridge `status`/envelope.
- Produces: `models.CAPABILITIES`; `models.gameplay_ready(state) -> bool`; console entries `(count, validator, class)` with classes `client`/`gameplay`; `quake_status(action_id="")` extended result; `quake_start` stepped default.

- [ ] **Step 1: Write the failing tests**

Unit `test_policy.py` additions:

```python
def test_console_policy_removals():
    for command in ("save", "load", "connect", "disconnect"):
        try:
            server._console_line(command, ["x"]) 
            assert False, command
        except ValueError as e:
            assert str(e).startswith("POLICY_DENIED")

def test_gameplay_command_needs_ready_state():
    state = {"signon": 4, "movemessages": 3, "dead": False,
             "intermission": False, "health": 100, "map": "start"}
    assert models.gameplay_ready(state) is True
    assert models.gameplay_ready({**state, "movemessages": 0}) is False
    assert models.gameplay_ready({**state, "dead": True}) is False
```

Contract: `quake_status` on a fake bridge returns `capabilities` with keys `tools`, `actions`, `weapons`, `modes`, `telemetry`; mutating tools expose `lease`, `action_id`, `action_seq`, `epoch`, `world_generation`, `control_revision` in their input schema.

Integration (`test_lifecycle.py`): after `quake_start`, `quake_state(instance)["mode"] == "stepped"`.

- [ ] **Step 2: Run to verify failure**

Run: `python3 -m pytest QuakeMCP/tests/unit/test_policy.py QuakeMCP/tests/contract/test_tools_list.py -v`
Expected: FAIL — `save` is still allowed; `capabilities` missing.

- [ ] **Step 3: Implement models + status + start + policy**

- `models.py`: add

```python
CAPABILITIES = {
    "tools": [...13 names...],
    "actions": {"axes": ["forward", "strafe", "vertical"],
                "run": True, "attack": True, "jump": ["none", "tap", "hold"],
                "weapons": list(range(1, 9))},
    "ui": {"contexts": ["game", "console", "message", "menu"]},
    "settings": sorted(CONFIG_CVARS),
    "frames": {"formats": ["png", "jpeg"], "longest_edge": 1280,
               "crop": True},
    "modes": ["stepped", "realtime"],
    "telemetry": ["hud", "pixels_only"],
    "console": sorted(CONSOLE_COMMANDS),
    "game": [...operations...],
}

def gameplay_ready(state):
    return bool(state.get("map")) and state.get("signon") == 4 \
        and state.get("movemessages", 0) > 2 \
        and not state.get("dead") and not state.get("intermission")
```

(`CONFIG_CVARS`, `CONFIG_ACCESS_PREFIX`, `CONFIG_ACCESS_WRITE`, `CONSOLE_COMMANDS`, `KEY_CODES`, `UI_CONTEXTS` move from `server.py` into `models.py`; `server.py` imports them. One definition, no import cycle: `models.py` stays engine-I/O-free.)

- `quake_status`: merge `ping` + `state`, add `capabilities=CAPABILITIES` and `action` (from `_bridge_ok(inst, "status", action_id=...)` when given, else `_bridge_ok(inst, "status")`).
- `quake_state` and `quake_status` delegate their round trips to `_bridge_ok`; the hand-rolled try/finally copies disappear.
- `quake_start`: after registration, `_mutate(inst, "control", sub="mode", mode="stepped")` inside a try; on `CONTROL_BUSY`/`STALE_STATE` report `mode: "realtime"` with `reason`; on success `mode: "stepped"`.
- Console policy: `(count, validator, cls)` tuples; remove `save`, `load`, `connect`, `disconnect`; gameplay class gated by `models.gameplay_ready(await `_state`)` except `pause 0`; `_console_arg` becomes a validator table folded into the entries.
- Envelope params on `quake_console`, `quake_config` (set), `quake_ui`, `quake_game`, `quake_control` (mode), all routed through `_mutate`.
- Bridge `act` gate: before starting input, `if (!respawn && !MCP_GameplayReady ())` → `NOT_READY`; implement `MCP_GameplayReady` from `sv.active`, `cls.signon == 4`, `cl.movemessages > 2`, `cl.stats[STAT_HEALTH] > 0`, `!cl.intermission`, `!sv.paused`; read the optional `respawn` act field for the §7 path.

- [ ] **Step 4: Run unit + contract, then integration**

Run:
```bash
python3 -m pytest QuakeMCP/tests/unit QuakeMCP/tests/contract -v
make clean && make build-release QUAKE_MCP=1
python3 -m pytest QuakeMCP/tests/integration/test_lifecycle.py -v
```
Expected: PASS/ SKIP cleanly.

- [ ] **Step 5: Commit**

```bash
git add QuakeMCP/src/quakemcp/models.py QuakeMCP/src/quakemcp/server.py QuakeMCP/bridge/q_mcp.c QuakeMCP/tests/unit/test_policy.py QuakeMCP/tests/contract/test_tools_list.py QuakeMCP/tests/integration/test_lifecycle.py
git commit -m "feat: quakemcp status capabilities stepped default and policy"
```

---

### Task 5: Observation integrity — act reporting, receipt LRU, `FRAME_EXPIRED`

**Files:**
- Modify: `QuakeMCP/bridge/q_mcp_input.c` (report applied deltas; store last applied)
- Modify: `QuakeMCP/bridge/q_mcp.c` (`act` result adds applied/weapon fields; `duplicate` marker already added in Task 3)
- Modify: `QuakeMCP/src/quakemcp/server.py` (receipt LRU, duplicate handling, `FRAME_EXPIRED`)
- Modify: `QuakeMCP/src/quakemcp/lifecycle.py` (`Instance.receipts`)
- Test: `QuakeMCP/tests/unit/test_receipts.py` (new), `QuakeMCP/tests/integration/test_act.py` (extend)

**Interfaces:**
- Consumes: Task 3 `"duplicate":true` replies; Task 4 envelope.
- Produces: `Instance.receipts` (OrderedDict) + `RECEIPT_LIMIT`, `RECEIPT_BYTES`; `server._receipt_store(inst, action_id, encoded, report, structured)`; `server._receipt_get(inst, action_id) -> dict | None`; act result fields `yaw_applied_deg`, `pitch_applied_deg`, `weapon_requested`, `weapon_active`.

- [ ] **Step 1: Write the failing tests**

Unit `test_receipts.py`: store an observation, read it back byte-identical; push entries past `RECEIPT_LIMIT` and assert the oldest is gone; a consumed cache miss followed by a duplicate reply raises `FRAME_EXPIRED`.

Integration `test_act.py` additions:

```python
def test_act_reports_effective_view_and_weapon():
    # after acquiring a lease and loading a map
    res = client_act(pitch_delta_deg=200, weapon_id=2, ticks=1)
    assert res["pitch_applied_deg"] <= 80
    assert res["weapon_requested"] == 2
    assert "weapon_active" in res
```

- [ ] **Step 2: Run to verify failure**

Run: `python3 -m pytest QuakeMCP/tests/unit/test_receipts.py -v`
Expected: FAIL — no `_receipt_store`.

- [ ] **Step 3: Implement**

Bridge (`q_mcp_input.c`): keep `mcp_yaw_applied`/`mcp_pitch_applied` from `MCP_Move`'s clamp (record `requested - excess`), expose them via `MCP_InputStats (float *yaw, float *pitch)`; `q_mcp.c` `MCP_FinishAct` adds:

```c
snprintf (result, sizeof (result),
	"\"completed_ticks\":%d,\"elapsed_ms\":%d,\"interrupted\":%s,"
	"\"action_id\":\"%s\",\"yaw_applied_deg\":%.2f,"
	"\"pitch_applied_deg\":%.2f,\"weapon_requested\":%d,"
	"\"weapon_active\":%d",
	...,
	mcp_act_impulse, cl.stats[STAT_ACTIVEWEAPON]);
```

Python: `Instance.receipts = collections.OrderedDict()`; store `{"encoded": bytes, "report": dict, "structured": dict}` keyed by `action_id`, evicting oldest until `len <= 16` and total bytes `<= 8 MiB`. In `quake_act`:

```python
res = await _offload(_mutate, inst, "act", ...)
if res.get("duplicate") is True and action_id:
    cached = _receipt_get(inst, action_id)
    if cached is None:
        raise ValueError("FRAME_EXPIRED: %s result frame evicted" % action_id)
    return _replay(cached)
encoded, report, structured = await _offload(_encode_observation, inst, instance, 0, 2000)
structured.update(completed_fields)
if action_id:
    _receipt_store(inst, action_id, encoded, report, structured)
```

`_replay` rebuilds the same `CallToolResult` shape from cache.

- [ ] **Step 4: Run tests**

Run:
```bash
python3 -m pytest QuakeMCP/tests/unit -v
make clean && make build-release QUAKE_MCP=1
python3 -m pytest QuakeMCP/tests/integration/test_act.py -v
```
Expected: PASS / SKIP.

- [ ] **Step 5: Commit**

```bash
git add QuakeMCP/bridge/q_mcp_input.c QuakeMCP/bridge/q_mcp.c QuakeMCP/src/quakemcp/server.py QuakeMCP/src/quakemcp/lifecycle.py QuakeMCP/tests/unit/test_receipts.py QuakeMCP/tests/integration/test_act.py
git commit -m "feat: quakemcp act reporting and receipt retention"
```

---

### Task 6: Modal `needs_input`

**Files:**
- Modify: `QuakeMCP/bridge/q_mcp.c` (modal state, intercepted key op, escape injection, receipt state)
- Modify: `QuakeMCP/bridge/q_mcp.h` (`MCP_ModalOpened`, `MCP_ModalClosed`)
- Modify: `Quake/render/gl_screen.c` (two hooks inside `SCR_ModalMessage`)
- Modify: `QuakeMCP/src/quakemcp/server.py` (`quake_ui` needs_input result)
- Test: `QuakeMCP/tests/integration/test_modal.py` (new)

**Interfaces:**
- Consumes: Task 3 ledger states; Task 1 reply-fd discipline.
- Produces: `int MCP_ModalOpened (char *text)` (nonzero when the modal was entered from an MCP key op; replies `needs_input` to it); `void MCP_ModalClosed (qboolean confirmed)`.

- [ ] **Step 1: Write the failing integration test**

`test_modal.py`: use the in-process session pattern from `test_lifecycle.py`
(`create_connected_server_and_client_session(mcp)`, module port 29888, skip
without game data). Sequence: `quake_start`, `quake_game(new_game)`,
`quake_ui(key="escape")` to raise the menu, then activate the menu path to the
new-game confirmation (top item, then New Game) with
`quake_ui(key="enter", action_id="modal1")`. Assert that activating call
returns `needs_input` with an image block and
`structuredContent["modal_text"]`, and that `quake_state` still reports the
menu context; answer with `quake_ui(key="escape")`; assert
`quake_status(instance, action_id="modal1")` reports the action as denied and
the UI later returns to the menu/game without a stuck modal. Read
`quake_state` between keys and pin the observed key sequence in a comment; if
the menu layout in this build differs, adjust the sequence rather than
weakening the assertions. Because the tools are async, wrap the body in
`asyncio.run` exactly as `test_lifecycle.py` does.

- [ ] **Step 2: Run to verify failure**

Run: `make clean && make build-release QUAKE_MCP=1 && python3 -m pytest QuakeMCP/tests/integration/test_modal.py -v`
Expected: FAIL / hangs until the test's timeout — the key op does not reply while the modal waits.

- [ ] **Step 3: Implement the bridge side**

State: `mcp_in_key` (set around `Key_Event` in the `key` op), `mcp_modal_fd`, `mcp_modal_id`, `mcp_modal_aid`, `mcp_modal_replied`.

In `SCR_ModalMessage`, after the first `SCR_UpdateScreen`:

```c
#ifdef QUAKE_MCP
	if (MCP_ModalOpened (text))
		scr_drawdialog = true;	// keep the dialog visible on demand
#endif
```

and in the wait loop:

```c
	do
	{
		key_count = -1;
		Sys_SendKeyEvents ();
#ifdef QUAKE_MCP
		MCP_Poll ();
		if (MCAP_Busy ())
			SCR_UpdateScreen ();
#endif
	} while (...);
#ifdef QUAKE_MCP
	MCP_ModalClosed (key_lastpress == 'y');
#endif
```

`MCP_ModalOpened`: when `mcp_in_key` is set, save the pending op's fd/id/action id, reply `needs_input` with the escaped text and `"action_id"`, record a `pending` receipt, set `mcp_modal_replied`, return 1. Otherwise return 0 (human modal).
`MCP_ModalClosed`: update the pending receipt to `done`/`denied` with the confirmation result; clear the modal flags.
`MCP_ClearControl` (release/expiry): when a modal is pending, `Key_Event (K_ESCAPE, true); Key_Event (K_ESCAPE, false);` and mark the receipt `denied`.
The `key` op skips its own reply when `mcp_modal_replied` was set for its id.

- [ ] **Step 4: Python side**

`quake_ui` checks the result for `needs_input`; when present, fetch an observation and return `CallToolResult` with the dialog image plus structured `{"needs_input": True, "modal_text": ..., "action_id": ..., "instance": ...}`. Answering is an ordinary `quake_ui` call.

- [ ] **Step 5: Run, verify no stuck modal, commit**

Run:
```bash
make clean && make build-release build-server build-client
make clean && make build-release QUAKE_MCP=1
python3 -m pytest QuakeMCP/tests/integration/test_modal.py -v
python3 -m pytest QuakeMCP/tests/unit QuakeMCP/tests/contract -v
```

```bash
git add QuakeMCP/bridge/q_mcp.c QuakeMCP/bridge/q_mcp.h Quake/render/gl_screen.c QuakeMCP/src/quakemcp/server.py QuakeMCP/tests/integration/test_modal.py
git commit -m "feat: quakemcp modal needs_input contract"
```

---

### Task 7: Death flow — console flush, respawn, intermission

**Files:**
- Modify: `QuakeMCP/src/quakemcp/server.py` (console flush window, `quake_game respawn`)
- Modify: `QuakeMCP/tests/integration/test_lifecycle.py` or a new `test_death.py`
- Update: `QuakeMCP/docs/engine-integration.md` (death/respawn/intermission verification result)

**Interfaces:**
- Consumes: Task 4 gameplay gate + `respawn` relax flag; Task 2 async waits.
- Produces: `_console_flush(inst)` async helper; `quake_game respawn` result `{respawned: bool, waited_ms: int}`.

- [ ] **Step 1: Record the investigation (no code yet)**

Run the built bridge in realtime and stepped, then `exec kill` with a valid lease; capture console tail and `state` before/after each. Write the observed mechanism into `docs/engine-integration.md` (one short table: mode, command, observed health/dead, tail). Expected: realtime kills; stepped queues the forwarded command. If the observation differs, implement what is observed and update the spec's §7 note.

- [ ] **Step 2: Write the failing tests**

`test_death.py`: (a) stepped session, `quake_console("kill")` → within 5 s `state.dead` is true; (b) `quake_game("respawn")` → within 10 s `state.dead` is false and health > 0; (c) intermission: `quake_game("load_map", map_id="end")` → `state.intermission` true (skip with a recorded message if `end` is not in `_list_maps`).

- [ ] **Step 3: Implement**

- `_console_flush(inst)`: when `(await _state(inst))["mode"] == "stepped"` and the command class is `gameplay`, set mode realtime, `await anyio.sleep(0.25)`, restore stepped; run after `exec` returns, inside the console tool.
- `quake_game respawn`: verify `state.dead`; send an act through `_mutate` with `attack=1, ticks=2, respawn=1` (bridge relaxes the health/intermission gate for this flag only), then wait for `dead == false` with a realtime flush if the session is stepped; `NOT_READY` if not dead on entry.

- [ ] **Step 4: Run and commit**

```bash
make clean && make build-release QUAKE_MCP=1
python3 -m pytest QuakeMCP/tests/integration/test_death.py -v
git add QuakeMCP/src/quakemcp/server.py QuakeMCP/tests/integration/test_death.py QuakeMCP/docs/engine-integration.md
git commit -m "fix: quakemcp forwarded console commands and respawn evidence"
```

---

### Task 8: Hygiene and documentary truth

**Files:**
- Modify: `Quake/host.c` (revert two whitespace hunks)
- Modify: `QuakeMCP/bridge/q_mcp.c` (`MCP_CheckAction` collapse)
- Modify: `QuakeMCP/bridge/q_mcp.h`, `QuakeMCP/bridge/q_mcp_capture.c` (unify `mcap_slot_t`/`mcap_snapshot_t`)
- Modify: `QuakeMCP/docs/engine-integration.md` (re-verified line numbers, ops, matrix)
- Modify: `QuakeMCP/README.md`, `QuakeMCP/AGENTS.md`, `docs/superpowers/2026-08-29-quake-apple-silicon.md`

- [ ] **Step 1: Engine whitespace + small C cleanups**

`git diff 0c8a6fc...HEAD -- Quake/host.c` identifies `host.c:245` and `host.c:661` (trailing-tab removals). Restore the tabs. Collapse `MCP_CheckAction`:

```c
	if ((mcp_act_mode == MCP_ACT_TICKS
			&& mcp_act_completed >= mcp_act_ticks)
		|| (mcp_act_mode == MCP_ACT_DURATION
			&& now - mcp_act_start >= mcp_act_duration))
		MCP_FinishAct (false);
	else if (now - mcp_act_start >= MCP_ACT_CAP)
		MCP_FinishAct (true);
```

Unify the capture structs: `MCAP_Poll` fills an `mcap_snapshot_t` directly from one `static mcap_snapshot_t mcap_ring[2]` and the copy loop disappears.

- [ ] **Step 2: Re-verify docs against the tree**

Re-run the greps from `engine-integration.md`'s hook table (`rg -n` for each symbol), update every line number and the "re-verified" sentence with the actual method/date, add the new ops (`tail`, `status`) and the envelope to the op map, and re-state `FRAME_EXPIRED` as implemented.

- [ ] **Step 3: Truth in README/AGENTS and the prior ledger entry**

- `QuakeMCP/README.md`: quick session drops the explicit `mode("stepped")` (now default); the `#ifdef` sentence becomes "every hook is guarded by `#ifdef QUAKE_MCP` or a macro shim that compiles it out of vanilla builds".
- `QuakeMCP/AGENTS.md`: same wording; update the traps section for the new protocol (envelope fields, `tail`, two connections) and the new tests.
- `docs/superpowers/2026-08-29-quake-apple-silicon.md`: replace "and the Task 10 fixes" with the hashes `5746b0e, 7a72b19`.

- [ ] **Step 4: Gates and commit**

```bash
make clean && make build-release build-server build-client
make clean && make build-release QUAKE_MCP=1
git add Quake/host.c QuakeMCP/bridge/q_mcp.c QuakeMCP/bridge/q_mcp.h QuakeMCP/bridge/q_mcp_capture.c QuakeMCP/docs/engine-integration.md QuakeMCP/README.md QuakeMCP/AGENTS.md docs/superpowers/2026-08-29-quake-apple-silicon.md
git commit -m "docs: quakemcp conformance truth and hygiene"
```

---

### Task 9: Acceptance matrix, full verification, ledger

**Files:**
- Modify: `QuakeMCP/docs/acceptance-results.md`
- Modify: `docs/superpowers/2026-08-29-quake-apple-silicon.md` (Fixes Ledger entry)
- Test: the whole suite plus the SIGKILL smoke and the autonomous loop

- [ ] **Step 1: Full verification set**

```bash
make clean && make build-release build-server build-client
python3 -m pytest QuakeMCP/tests/unit QuakeMCP/tests/contract -v
make clean && make build-release QUAKE_MCP=1
python3 -m pytest QuakeMCP/tests/integration -v
```
Then the 3-binary SIGKILL smoke (launch each, SIGKILL, count `Received signal` == 0) and one autonomous observe-act loop through the real MCP stdio server.

- [ ] **Step 2: Update `acceptance-results.md`**

New/updated rows with dated evidence: cancellation (client cancel -> release, no synthetic input), death/respawn, intermission (or FAIL with cause), modal `needs_input` (no stuck modal), retry/`FRAME_EXPIRED`, capabilities/status, stepped default, lease enforcement. Vision-with-a-model stays recorded as environment-blocked.

- [ ] **Step 3: Fixes Ledger entry**

Append a `### QuakeMCP conformance round (fix)` entry with every commit hash, the spec/plan path, a short description, and `Validation:` lines for both build gates and the test suite.

- [ ] **Step 4: Commit**

```bash
git add QuakeMCP/docs/acceptance-results.md docs/superpowers/2026-08-29-quake-apple-silicon.md
git commit -m "docs: quakemcp conformance acceptance and ledger"
```
