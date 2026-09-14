# QuakeMCP Review-Fixes Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Implement `docs/superpowers/2026-09-14-quakemcp-review-fixes-design.md`: the fourteen findings of the two-axis review — impulse-merge invariant, `exec` envelope bypass, vision error codes, capability keys, the human takeover stop control, the six refactors, and the acceptance/documentary restorations.

**Architecture:** The C bridge gains a shared mutation-begin helper, a shared JSON member scanner, and a small `q_mcp_ui.c` module (takeover banner + left-click interception). The Python server gets one lease-drop path (`Instance.clear_lease`), one observation-result shape, heartbeat-loss detection, and a single-source state schema. The impulse fix keeps the engine's `in_impulse` untouched and composes the serialized byte locally.

**Tech Stack:** id-era C (Apple clang, GNU make, SDL3, OpenGL), Python 3.12, `mcp==1.29.0`, `Pillow>=12.0`, pytest 8.x, anyio (SDK dependency).

## Global Constraints

- Work on branch `fix/quakemcp-review-findings` from `main` (`fd5dcbd`); `QuakeWorld/` untouched; every engine edit behind `#ifdef QUAKE_MCP` (macro shims allowed where a call site compiles to a no-op).
- Ground truth is `make`; editor/LSP diagnostics are noise.
- Build gates after every engine task: `make clean && make build-release build-server build-client` and `make clean && make build-release QUAKE_MCP=1` both exit 0. Leave the MCP build in place before integration tests.
- `make clean` between variants: switching `QUAKE_MCP=1` on after a vanilla build does not relink.
- Integration tests skip without `game/id1/pak0.pak`; link `game/` in a worktree (`ln -s ../../game game`) and remove the link before committing. Never commit `game/`, `__pycache__/`, `*.sav`, token files.
- Stage explicit paths only; never `git add -A`. id-era C style: tabs, K&R braces, `/* banner */` comments.
- Integration test module basenames must be unique across `tests/unit/` and `tests/integration/`; fixed ports stay in 29876–29889.
- Wire protocol stays v1; no new error codes, tools, or schema shape changes.
- Python deps are pinned: `mcp==1.29.0`, `Pillow>=12.0`.
- The review-round design doc is committed at `fd5dcbd`; do not restate its decisions, implement them.

## File Structure

| File | Responsibility after this round |
|---|---|
| `Quake/client/cl_input.c` | impulse byte composed locally; `in_impulse` never assigned by MCP |
| `QuakeMCP/bridge/q_mcp.c` | shared `MCP_BeginMutation`, shared JSON member scanner, `MCP_LeaseHeld`/`MCP_HumanTakeover`, `exec` text required |
| `QuakeMCP/bridge/q_mcp_ui.c` (new) | takeover banner draw + left-click interception |
| `QuakeMCP/bridge/q_mcp.h` | new `MCP_*` declarations |
| `Quake/render/gl_screen.c` | `MCP_UiDraw()` before `MCAP_Frame()` |
| `Quake/platform/in_sdl.c` | `MCP_UiMouseClick()` before the access funnel |
| `Makefile` | `q_mcp_ui.o` in `QUAKE_MCP_OBJS` |
| `QuakeMCP/src/quakemcp/lifecycle.py` | `clear_lease()`, heartbeat-loss detection |
| `QuakeMCP/src/quakemcp/server.py` | `clear_lease` call sites, `_observation_result`, reuse skips |
| `QuakeMCP/src/quakemcp/models.py` | capabilities `ui.keys`, single-source state schema |
| `QuakeMCP/src/quakemcp/vision.py` | code-prefixed errors |
| `QuakeMCP/tests/unit/test_lease.py` (new) | lease drop + heartbeat loss |
| `QuakeMCP/tests/unit/test_vision.py`, `test_models.py` | code prefixes, schema consistency |
| `QuakeMCP/tests/contract/test_policy.py` | capabilities keys |
| `QuakeMCP/tests/integration/test_envelope.py` | `exec` text required, parser regressions |
| `QuakeMCP/docs/engine-integration.md` | impulse wording, new hooks, re-verified line numbers |
| `QuakeMCP/README.md`, `QuakeMCP/AGENTS.md` | takeover + `exec` truth |
| `docs/superpowers/2026-09-14-quakemcp-conformance-plan.md` | Task 6 drift note |
| `QuakeMCP/docs/acceptance-results.md` | restored matrix rows, takeover protocol |
| `docs/superpowers/2026-08-29-quake-apple-silicon.md` | Fixes Ledger entry |

---

### Task 1: Impulse merge never writes `in_impulse`

**Files:**
- Modify: `Quake/client/cl_input.c:376-387`

**Interfaces:**
- Consumes: `MCP_Buttons ()`, `MCP_Impulse ()` (`q_mcp_input.c`; `MCP_Impulse` clears on read, so it must only be called when its value will be sent).
- Produces: unchanged wire semantics; `in_impulse` untouched by MCP.

No failing test exists for this: the human/MCP impulse collision cannot be driven deterministically without a test-only seam, and the plan forbids adding one. The assertion is the invariant (`QuakeMCP/AGENTS.md` "never write `in_impulse`"); the regression net is `test_act.py`'s existing weapon-switch assertions.

- [ ] **Step 1: Replace the merge block**

Current (`cl_input.c:376-387`):

```c
#ifdef QUAKE_MCP
	bits |= MCP_Buttons ();
	{
		int	im = MCP_Impulse ();
		if (im)
			in_impulse = im;
	}
#endif
    MSG_WriteByte (&buf, bits);

    MSG_WriteByte (&buf, in_impulse);
	in_impulse = 0;
```

New:

```c
#ifdef QUAKE_MCP
	bits |= MCP_Buttons ();
#endif
    MSG_WriteByte (&buf, bits);

#ifdef QUAKE_MCP
	// a pending human impulse owns its frame; the MCP one-shot stays
	// latched (MCP_Impulse clears on read) and goes out next send
	MSG_WriteByte (&buf, in_impulse ? in_impulse : MCP_Impulse ());
#else
    MSG_WriteByte (&buf, in_impulse);
#endif
	in_impulse = 0;
```

- [ ] **Step 2: Build both gates**

Run: `make clean && make build-release build-server build-client`
Expected: exit 0.

Run: `make clean && make build-release QUAKE_MCP=1`
Expected: exit 0.

- [ ] **Step 3: Run the regression suite**

Run: `python3 -m pytest QuakeMCP/tests/integration/test_act.py -v`
Expected: PASS (weapon `requested != active` assertions intact).

- [ ] **Step 4: Commit**

```bash
git add Quake/client/cl_input.c
git commit -m "fix: quakemcp never write in_impulse in the merge"
```

---

### Task 2: `exec` without text is `INVALID_CONTEXT`, not a read

**Files:**
- Modify: `QuakeMCP/bridge/q_mcp.c:1547-1562`
- Test: `QuakeMCP/tests/integration/test_envelope.py`

**Interfaces:**
- Produces: `exec` with missing or empty `text` replies `INVALID_CONTEXT: exec needs text; use tail to read the console` before the envelope; `tail` is the only console read.

- [ ] **Step 1: Write the failing test**

Append to `QuakeMCP/tests/integration/test_envelope.py`:

```python
def test_exec_text_is_required(bridge):
    lease, epoch = bridge.acquire()
    env = {"lease": lease, "epoch": str(epoch)}

    empty = bridge.op(id="1", op="exec", text="", seq="1", **env)
    assert empty["ok"] is False, empty
    assert empty["error"] == "INVALID_CONTEXT", empty

    missing = bridge.op(id="2", op="exec", seq="2", **env)
    assert missing["ok"] is False, missing
    assert missing["error"] == "INVALID_CONTEXT", missing

    # validation precedes the envelope: neither rejection spent seq 1
    ok = bridge.op(id="3", op="exec", text="god", seq="1", **env)
    assert ok["ok"] is True, ok
```

- [ ] **Step 2: Run it to verify it fails**

Run: `make clean && make build-release QUAKE_MCP=1 && python3 -m pytest QuakeMCP/tests/integration/test_envelope.py::test_exec_text_is_required -v`
Expected: FAIL — the empty/missing forms currently answer an envelope-free `tail` read with `ok: true`.

- [ ] **Step 3: Delete the read branch**

In `q_mcp.c`, replace the empty-text block:

```c
		if (r == 0 || text[0] == 0)
		{
			// no text mutates nothing: this stays a read until the
			// tail op replaces the old exec text="" idiom
			MCP_FormatTail (result, sizeof (result));
			MCP_Reply (id, true, NULL, result);
			return;
		}
```

with:

```c
		if (r == 0 || text[0] == 0)
		{
			MCP_Reply (id, false, "INVALID_CONTEXT",
				"exec needs text; use tail to read the console");
			return;
		}
```

- [ ] **Step 4: Run the test to verify it passes**

Run: `python3 -m pytest QuakeMCP/tests/integration/test_envelope.py -v`
Expected: PASS (all envelope tests, including the new one).

- [ ] **Step 5: Commit**

```bash
git add QuakeMCP/bridge/q_mcp.c QuakeMCP/tests/integration/test_envelope.py
git commit -m "fix: quakemcp exec without text is invalid, not a read"
```

---

### Task 3: Vision errors carry policy codes

**Files:**
- Modify: `QuakeMCP/src/quakemcp/vision.py` (raises at lines 42, 54, 57, 64, 73, 75, 77, 101)
- Test: `QuakeMCP/tests/unit/test_vision.py`

**Interfaces:**
- Produces: every `vision` error message starts with `"<CODE>: "`: malformed input → `INVALID_CONTEXT`; HUD-drop refusal and the size cap → `POLICY_DENIED`. `BadCrop`/`ImageTooLarge` classes and message bodies unchanged.

- [ ] **Step 1: Write the failing test**

Append to `QuakeMCP/tests/unit/test_vision.py`:

```python
def test_error_messages_carry_codes():
    raw = _gradient(8, 8)
    with pytest.raises(vision.BadCrop) as e:
        vision.encode_frame(raw, 8, 8, crop=[0, 0, 9, 1])
    assert str(e.value).startswith("INVALID_CONTEXT: "), e.value

    with pytest.raises(vision.BadCrop) as e:
        vision.encode_frame(raw, 8, 8, crop=[0, 0, 8, 4],
                            hud_rect=[0, 6, 8, 2])
    assert str(e.value).startswith("POLICY_DENIED: "), e.value

    with pytest.raises(ValueError) as e:
        vision.encode_frame(raw, 8, 8, fmt="gif")
    assert str(e.value).startswith("INVALID_CONTEXT: "), e.value

    with pytest.raises(vision.ImageTooLarge) as e:
        vision.encode_frame(os.urandom(1024 * 1024 * 3), 1024, 1024)
    assert str(e.value).startswith("POLICY_DENIED: "), e.value
```

- [ ] **Step 2: Run it to verify it fails**

Run: `python3 -m pytest QuakeMCP/tests/unit/test_vision.py::test_error_messages_carry_codes -v`
Expected: FAIL — messages currently carry no code prefix.

- [ ] **Step 3: Prefix the raises**

Apply exactly these message changes in `vision.py`:

| Line | New message |
|---|---|
| `apply_telemetry` | `"INVALID_CONTEXT: telemetry must be hud\|pixels_only"` |
| crop shape | `"INVALID_CONTEXT: crop must be four integers x,y,w,h"` |
| crop bounds | `"INVALID_CONTEXT: crop %r outside the %dx%d frame"` |
| HUD drop | `"POLICY_DENIED: crop removes the HUD strip; pass allow_hud_crop=True to crop it out explicitly"` |
| rgb length | `"INVALID_CONTEXT: rgb must be exactly %d bytes"` |
| format | `"INVALID_CONTEXT: unsupported encoding %r"` |
| longest_edge | `"INVALID_CONTEXT: longest_edge must be positive"` |
| size cap | `"POLICY_DENIED: encoded %s frame is %d bytes (cap %d): reduce longest_edge, use jpeg, or crop"` |

- [ ] **Step 4: Run the suite**

Run: `python3 -m pytest QuakeMCP/tests/unit/test_vision.py -v`
Expected: PASS (existing substring assertions keep passing).

- [ ] **Step 5: Commit**

```bash
git add QuakeMCP/src/quakemcp/vision.py QuakeMCP/tests/unit/test_vision.py
git commit -m "fix: quakemcp vision errors carry policy codes"
```

---

### Task 4: Capabilities advertise the UI keys

**Files:**
- Modify: `QuakeMCP/src/quakemcp/models.py:266`
- Test: `QuakeMCP/tests/contract/test_policy.py` (`test_status_reports_capabilities`)

**Interfaces:**
- Produces: `CAPABILITIES["ui"] == {"contexts": [...], "keys": sorted(KEY_CODES)}`.

- [ ] **Step 1: Write the failing assertion**

In `test_status_reports_capabilities`, after the existing `caps["actions"]["weapons"]` assertion, add:

```python
        assert caps["ui"]["keys"] == sorted(models.KEY_CODES)
        assert "escape" in caps["ui"]["keys"]
```

- [ ] **Step 2: Run it to verify it fails**

Run: `python3 -m pytest "QuakeMCP/tests/contract/test_policy.py::test_status_reports_capabilities" -v`
Expected: FAIL with `KeyError: 'keys'`.

- [ ] **Step 3: Add the keys**

In `models.py`, replace the `ui` line of `CAPABILITIES`:

```python
    "ui": {"contexts": ["game", "console", "message", "menu"]},
```

with:

```python
    "ui": {"contexts": ["game", "console", "message", "menu"],
           "keys": sorted(KEY_CODES)},
```

- [ ] **Step 4: Run the contract suite**

Run: `python3 -m pytest QuakeMCP/tests/contract -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add QuakeMCP/src/quakemcp/models.py QuakeMCP/tests/contract/test_policy.py
git commit -m "feat: quakemcp advertise ui keys in capabilities"
```

---

### Task 5: One `clear_lease` for every lease drop

**Files:**
- Modify: `QuakeMCP/src/quakemcp/lifecycle.py` (add method to `Instance`, after `stop_keepalive` at `:101-105`)
- Modify: `QuakeMCP/src/quakemcp/server.py` (`:131-134`, `:197-200`, `:1069-1073`, `:1100-1103`)
- Test: `QuakeMCP/tests/unit/test_lease.py` (new)

**Interfaces:**
- Produces: `Instance.clear_lease()` — stops the beat, then `lease=""`, `epoch=0`, `next_seq=0`. Consumed by Task 6 and by `server.py`.

- [ ] **Step 1: Write the failing test**

Create `QuakeMCP/tests/unit/test_lease.py`:

```python
"""Unit tests: local lease lifecycle (drop path, heartbeat loss)."""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..",
                                "src"))

from quakemcp import lifecycle


def test_clear_lease_resets_and_stops_beat(monkeypatch):
    inst = lifecycle.Instance("qtest", -1, 1, "tok", owned=False)
    stopped = []
    monkeypatch.setattr(inst, "stop_keepalive",
                        lambda: stopped.append(True))
    inst.lease, inst.epoch, inst.next_seq = "l1-1", 7, 4
    inst.clear_lease()
    assert (inst.lease, inst.epoch, inst.next_seq) == ("", 0, 0)
    assert stopped, "the beat must stop with the lease"
```

- [ ] **Step 2: Run it to verify it fails**

Run: `python3 -m pytest QuakeMCP/tests/unit/test_lease.py -v`
Expected: FAIL with `AttributeError: 'Instance' object has no attribute 'clear_lease'`.

- [ ] **Step 3: Add the method**

In `lifecycle.py`, after `stop_keepalive`:

```python
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
```

- [ ] **Step 4: Replace the four inlined copies in `server.py`**

`_cancel_releases` (`:128-134`) — replace the three assignments after the
comment with:

```python
            # the release revoked the engine-side lease: drop the local
            # copy and the beat, exactly like quake_release does, so the
            # next mutation lazily acquires instead of trusting a dead id
            inst.clear_lease()
```

`_mutate` stale handling (`:197-200`) — replace:

```python
        if code == "STALE_STATE" and detail not in PRECONDITION_MISMATCHES:
            inst.stop_keepalive()
            inst.lease = ""
```

with:

```python
        if code == "STALE_STATE" and detail not in PRECONDITION_MISMATCHES:
            inst.clear_lease()
```

`quake_control` release branch (`:1069-1073`) — replace the four lines with `inst.clear_lease()`.

`quake_release` (`:1100-1103`) — replace the four lines with `inst.clear_lease()`.

- [ ] **Step 5: Run the tests**

Run: `python3 -m pytest QuakeMCP/tests/unit QuakeMCP/tests/contract -v`
Expected: PASS (the cancel/release policy tests assert the same end state).

- [ ] **Step 6: Commit**

```bash
git add QuakeMCP/src/quakemcp/lifecycle.py QuakeMCP/src/quakemcp/server.py QuakeMCP/tests/unit/test_lease.py
git commit -m "refactor: quakemcp one clear_lease for every lease drop"
```

---

### Task 6: Heartbeat loss drops the local lease

**Files:**
- Modify: `QuakeMCP/src/quakemcp/lifecycle.py` (`keepalive` at `:73-99`)
- Test: `QuakeMCP/tests/unit/test_lease.py`

**Interfaces:**
- Consumes: `Instance.clear_lease()` (Task 5).
- Produces: `Instance._heartbeat_round(lease, epoch) -> bool` — one beat; `False` after a `STALE_STATE` reply for the generation this round beats for, which also clears the lease (a superseded round stops without touching the newer lease). Transport errors keep beating (`True`).

- [ ] **Step 1: Write the failing test**

Append to `test_lease.py` (and add `import threading` to its imports):

```python
class _Bridge:
    def __init__(self, reply):
        self.reply = reply

    def send(self, op, **kw):
        return self.reply

    def close(self):
        pass


def test_beat_clears_lease_on_stale_state(monkeypatch):
    inst = lifecycle.Instance("qtest", -1, 1, "tok", owned=False)
    inst.lease, inst.epoch, inst.next_seq = "l1-1", 3, 2
    monkeypatch.setattr(lifecycle.Instance, "client", lambda self: _Bridge(
        {"ok": False, "error": "STALE_STATE", "detail": "no lease"}))
    assert inst._heartbeat_round("l1-1", 3) is False
    assert (inst.lease, inst.epoch, inst.next_seq) == ("", 0, 0)


def test_beat_keeps_beating_on_ok(monkeypatch):
    inst = lifecycle.Instance("qtest", -1, 1, "tok", owned=False)
    monkeypatch.setattr(lifecycle.Instance, "client", lambda self: _Bridge(
        {"ok": True, "result": {}}))
    inst.lease, inst.epoch = "l1-1", 3
    assert inst._heartbeat_round("l1-1", 3) is True
    assert (inst.lease, inst.epoch) == ("l1-1", 3)


def test_beat_ignores_stale_reply_for_superseded_generation(monkeypatch):
    inst = lifecycle.Instance("qtest", -1, 1, "tok", owned=False)
    inst.lease, inst.epoch, inst.next_seq = "l2-7", 4, 5
    stop = threading.Event()
    inst._hb_stop = stop
    monkeypatch.setattr(lifecycle.Instance, "client", lambda self: _Bridge(
        {"ok": False, "error": "STALE_STATE"}))
    assert inst._heartbeat_round("l1-1", 3) is False
    assert (inst.lease, inst.epoch, inst.next_seq) == ("l2-7", 4, 5)
    assert inst._hb_stop is stop
    assert not stop.is_set()
```

- [ ] **Step 2: Run it to verify it fails**

Run: `python3 -m pytest QuakeMCP/tests/unit/test_lease.py -v`
Expected: FAIL with `AttributeError: 'Instance' object has no attribute '_heartbeat_round'`.

- [ ] **Step 3: Split the beat loop**

In `keepalive` (`lifecycle.py:85-96`), replace the `beat()` body:

```python
        def beat():
            while not stop.wait(HEARTBEAT_SECS):
                try:
                    client = self.client()
                except (EngineDisconnected, OSError):
                    continue
                try:
                    client.send("hb", lease=lease, epoch=str(epoch))
                except (EngineDisconnected, OSError):
                    pass
                finally:
                    client.close()
```

with:

```python
        def beat():
            while not stop.wait(HEARTBEAT_SECS):
                if not self._heartbeat_round(lease, epoch):
                    return
```

and add the method after `keepalive` (before `stop_keepalive`):

```python
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
            if self.lease == lease and self.epoch == epoch:
                self.clear_lease()
            return False
        return True
```

- [ ] **Step 4: Run the tests**

Run: `python3 -m pytest QuakeMCP/tests/unit/test_lease.py -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add QuakeMCP/src/quakemcp/lifecycle.py QuakeMCP/tests/unit/test_lease.py
git commit -m "fix: quakemcp heartbeat loss drops the local lease"
```

---

### Task 7: One observation-result shape

**Files:**
- Modify: `QuakeMCP/src/quakemcp/server.py` (`_replay` `:520-533`, `quake_observe` `:576-580`, `quake_act` `:700-704`, `quake_ui` modal `:1028-1032`)

**Interfaces:**
- Produces: `_observation_result(encoded, report, structured) -> CallToolResult` — the single image-block + caption + structured-content builder, used by all four sites.

- [ ] **Step 1: Add the helper**

Above `_replay` in `server.py`:

```python
def _observation_result(encoded, report, structured):
    """The one CallToolResult shape every observation delivers.

    The image block carries the recorded bytes; structured content stays
    the caller's dictionary (already telemetry-filtered where policy
    applies).
    """
    return CallToolResult(
        content=[_image_content(encoded, report["encoding"]),
                 _caption(structured)],
        structuredContent=structured,
        isError=False)
```

- [ ] **Step 2: Route the four sites through it**

`_replay`: replace the whole `return CallToolResult(...)` block with:

```python
    return _observation_result(cached["encoded"], cached["report"],
                               structured)
```

`quake_observe`: replace the trailing `return CallToolResult(...)` with:

```python
    return _observation_result(encoded, report, structured)
```

`quake_act`: replace the trailing `return CallToolResult(...)` with:

```python
    return _observation_result(encoded, report, structured)
```

`quake_ui` modal branch: replace the `return CallToolResult(...)` with:

```python
            return _observation_result(encoded, report, structured)
```

- [ ] **Step 3: Run the suites**

Run: `python3 -m pytest QuakeMCP/tests/unit QuakeMCP/tests/contract -v`
Expected: PASS (`test_receipts.py` pins the replay shape and bytes).

- [ ] **Step 4: Commit**

```bash
git add QuakeMCP/src/quakemcp/server.py
git commit -m "refactor: quakemcp one observation result shape"
```

---

### Task 8: Reuse `_instance` and collapse duplicate excepts

**Files:**
- Modify: `QuakeMCP/src/quakemcp/server.py` (`quake_act` `:631-634`, `quake_stop` `:1110-1113`, `quake_start` `:378-381`, `quake_attach` `:410-413`)

**Interfaces:**
- Consumes: `_instance(instance)` (`:65-72`, raises `ValueError ENGINE_DISCONNECTED`); `EngineDisconnected` subclasses `QuakeMCPError` (`models.py:301`).

- [ ] **Step 1: Replace the re-inlined lookups**

`quake_act`:

```python
    inst = lifecycle.get(instance)
    if inst is None:
        raise ValueError("ENGINE_DISCONNECTED: unknown instance %r"
                         % (instance,))
```

becomes:

```python
    inst = _instance(instance)
```

`quake_stop`:

```python
    inst = lifecycle.get(instance)
    if inst is None:
        raise ValueError("ENGINE_DISCONNECTED: unknown instance %r"
                         % (instance,))
```

becomes:

```python
    inst = _instance(instance)
```

- [ ] **Step 2: Collapse the except pairs**

In `quake_start` and `quake_attach`, delete:

```python
    except EngineDisconnected as e:
        raise ValueError("%s: %s" % (e.code, e.detail))
```

keeping the following `except QuakeMCPError as e:` block (the subclass
already lands there). Do not touch `_bridge`, which raises
`EngineDisconnected` itself and must keep catching it.

- [ ] **Step 3: Run the suites**

Run: `python3 -m pytest QuakeMCP/tests/unit QuakeMCP/tests/contract -v`
Expected: PASS (lifecycle refusal tests cover both paths).

- [ ] **Step 4: Commit**

```bash
git add QuakeMCP/src/quakemcp/server.py
git commit -m "refactor: quakemcp reuse _instance and collapse excepts"
```

---

### Task 9: Single-source the state schema

**Files:**
- Modify: `QuakeMCP/src/quakemcp/models.py:28-46` (`REQUIRED_STATE_KEYS`)
- Test: `QuakeMCP/tests/unit/test_models.py`

**Interfaces:**
- Produces: `REQUIRED_STATE_KEYS` derived from `STATE_KEYS`; a unit test pinning both tuples against the `StateRequired`/`StateOut` annotations.

- [ ] **Step 1: Write the failing test**

Append to `test_models.py` (extend the import to include the needed names):

```python
def test_state_schema_sources_agree():
    from quakemcp import models

    # the observation tuple is the bridge snapshot minus angles, plus
    # the server-side instance id
    assert models.REQUIRED_STATE_KEYS == ("instance",) + tuple(
        k for k in models.STATE_KEYS if k != "angles")

    survival = set(models.StateRequired.__annotations__)
    hud = {"health", "ammo", "dead"}
    assert survival == (set(models.REQUIRED_STATE_KEYS) - hud) | {"angles"}
    assert set(models.StateOut.__annotations__) == survival | hud
```

- [ ] **Step 2: Run it to verify it fails**

Run: `python3 -m pytest QuakeMCP/tests/unit/test_models.py::test_state_schema_sources_agree -v`
Expected: FAIL — `REQUIRED_STATE_KEYS` is a hand-written literal whose order does not match the derivation.

- [ ] **Step 3: Derive the tuple**

In `models.py`, replace the literal `REQUIRED_STATE_KEYS` block (`:28-46`) with:

```python
# The observation's required keys: the bridge snapshot minus the angles
# holdback, plus the server-side instance id. Deriving from STATE_KEYS
# keeps the three schema statements in step (unit-tested below).
REQUIRED_STATE_KEYS = ("instance",) + tuple(
    k for k in STATE_KEYS if k != "angles")
```

Move the block below `STATE_KEYS` (`:53-71`) so the reference is defined;
keep `REQUIRED_IDENTITY_KEYS` where it is.

- [ ] **Step 4: Run the suite**

Run: `python3 -m pytest QuakeMCP/tests/unit/test_models.py QuakeMCP/tests/unit/test_receipts.py -v`
Expected: PASS (if any test asserted the old order, reorder its expectation to the derived tuple — do not change the derivation).

- [ ] **Step 5: Commit**

```bash
git add QuakeMCP/src/quakemcp/models.py QuakeMCP/tests/unit/test_models.py
git commit -m "refactor: quakemcp single-source the state schema"
```

---

### Task 10: One mutation-begin helper in the bridge

**Files:**
- Modify: `QuakeMCP/bridge/q_mcp.c` (helper + `exec` `:1563-1569`, `cvar` `:1592-1599`, `key` `:1624-1630`, `control mode` `:1710-1716`, `act` `:1898-1915`)

**Interfaces:**
- Consumes: `MCP_HashRequest`, `MCP_LookupReceipt`, `MCP_CheckPreconditions`.
- Produces: `MCP_BeginMutation(op, line, id, &hash)` — hash, duplicate lookup, preconditions; `false` means a reply was already sent.

- [ ] **Step 1: Add the helper**

Insert after `MCP_CheckSequence`'s definition (locate with `grep -n "static qboolean MCP_CheckSequence" QuakeMCP/bridge/q_mcp.c`):

```c
/*
==================
MCP_BeginMutation

The shared envelope preamble for every mutation: canonical hash, known
duplicate, then preconditions. Returns false when a reply has already
been sent. `act` checks CONTROL_BUSY between this and MCP_CheckSequence;
every caller checks its sequence next.
==================
*/
static qboolean MCP_BeginMutation (char *op, char *line, char *id,
	unsigned *hash)
{
	*hash = MCP_HashRequest (op, line);
	if (MCP_LookupReceipt (line, id))
		return false;
	if (!MCP_CheckPreconditions (line, id))
		return false;
	return true;
}
```

- [ ] **Step 2: Replace the four verbatim preambles**

Each of these blocks:

```c
		hash = MCP_HashRequest (op, line);
		if (MCP_LookupReceipt (line, id))
			return;
		if (!MCP_CheckPreconditions (line, id))
			return;
		if (!MCP_CheckSequence (line, id, &seq))
			return;
```

becomes:

```c
		if (!MCP_BeginMutation (op, line, id, &hash))
			return;
		if (!MCP_CheckSequence (line, id, &seq))
			return;
```

at `exec`, `cvar` (inside `if (hasval)`), `key`, and `control`'s `mode` sub-op.

- [ ] **Step 3: Rewrite the `act` preamble**

Replace:

```c
		hash = MCP_HashRequest (op, line);

		// known duplicate: the same op + lease + action_id + arguments
		// returns its recorded receipt; different arguments under the
		// same id are a conflict, never a second execution. Acts with
		// no action_id carry no identity and are never deduplicated.
		if (MCP_LookupReceipt (line, id))
			return;
		if (!MCP_CheckPreconditions (line, id))
			return;
		if (mcp_act_active)
```

with:

```c
		// known duplicate: the same op + lease + action_id + arguments
		// returns its recorded receipt; different arguments under the
		// same id are a conflict, never a second execution. Acts with
		// no action_id carry no identity and are never deduplicated.
		if (!MCP_BeginMutation (op, line, id, &hash))
			return;
		if (mcp_act_active)
```

(the comment now documents the helper's contract; the
`MCP_CheckSequence` call after the busy check stays put).

- [ ] **Step 4: Build and run the envelope suites**

Run: `make clean && make build-release QUAKE_MCP=1`
Expected: exit 0.

Run: `python3 -m pytest QuakeMCP/tests/integration/test_envelope.py QuakeMCP/tests/integration/test_bridge_exec.py -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add QuakeMCP/bridge/q_mcp.c
git commit -m "refactor: quakemcp one mutation begin helper"
```

---

### Task 11: Shared JSON member scanner

**Files:**
- Modify: `QuakeMCP/bridge/q_mcp.c` (`MCP_Field` `:433-529`, `MCP_HashRequest` `:1152-1285`)
- Test: `QuakeMCP/tests/integration/test_envelope.py`

**Interfaces:**
- Produces: `MCP_NextMember(char **pp, char **keystart, int *keylen, char **val)` and `MCP_SkipValue(char **pp)`; `MCP_Field`/`MCP_HashRequest` built on them with unchanged contracts (Field: 1 fit, -1 truncated, 0 absent; Hash: same canonical hash for the same request bytes).

- [ ] **Step 1: Write the failing regression test**

Append to `test_envelope.py`:

```python
def test_parser_value_does_not_impersonate_a_key(bridge):
    """A value equal to a later key name must not divert the lookup."""
    lease, epoch = bridge.acquire()
    reply = bridge.op(id="1", op="exec", pad="text", zz="9",
                      text="echo MARKER", seq="1", lease=lease,
                      epoch=str(epoch))
    assert reply["ok"] is True, reply
    assert "MARKER" in reply["result"]["output"], reply


def test_parser_escaped_quotes_still_decode(bridge):
    lease, epoch = bridge.acquire()
    reply = bridge.op(id="1", op="exec", text='echo "A B"', seq="1",
                      lease=lease, epoch=str(epoch))
    assert reply["ok"] is True, reply
    assert "A B" in reply["result"]["output"], reply
```

- [ ] **Step 2: Run them to verify the first fails**

Run: `make clean && make build-release QUAKE_MCP=1 && python3 -m pytest QuakeMCP/tests/integration/test_envelope.py::test_parser_value_does_not_impersonate_a_key -v`
Expected: FAIL — with the current parser, `"text"` as `pad`'s value is matched as a key, the lookup walks to `"zz"`, and the console runs `9`, so `MARKER` is absent. The escaped-quotes test passes before and after (parity guard).

- [ ] **Step 3: Add the scanner**

Insert above `MCP_Field`:

```c
/*
==================
MCP_NextMember

Advance to the next top-level member of a flat JSON object. On success
returns true, setting *keystart/*keylen to the raw key bytes between the
quotes and *val past the colon plus whitespace; *pp ends at *val.
Strings inside nested values never count as keys. Returns false at the
end of the object.
==================
*/
static qboolean MCP_NextMember (char **pp, char **keystart, int *keylen,
	char **val)
{
	char	*p = *pp;
	int	depth = 0;

	if (*p == '{')
		p++;
	while (*p)
	{
		while (*p == ' ' || *p == '\t')
			p++;
		if (*p != '"')
		{
			if (*p == '{' || *p == '[')
				depth++;
			else if (*p == '}' || *p == ']')
			{
				if (depth == 0)
					break;
				depth--;
			}
			p++;
			continue;
		}
		if (depth > 0)
		{	// a string inside a nested value: skip it
			p++;
			while (*p && *p != '"')
			{
				if (*p == '\\' && p[1])
					p++;
				p++;
			}
			if (*p == '"')
				p++;
			continue;
		}
		*keystart = p + 1;
		p = *keystart;
		while (*p && *p != '"')
		{
			if (*p == '\\' && p[1])
				p++;
			p++;
		}
		if (*p != '"')
			break;
		*keylen = (int)(p - *keystart);
		p++;
		p = strchr (p, ':');
		if (!p)
			break;
		p++;
		while (*p == ' ' || *p == '\t')
			p++;
		*val = p;
		*pp = p;
		return true;
	}
	*pp = p;
	return false;
}

/*
==================
MCP_SkipValue

Advance *pp past the current member's value to its comma or the closing
brace (left on the delimiter). Quoted values honor escapes; raw values
walk nested braces so a comma inside them is not a boundary.
==================
*/
static void MCP_SkipValue (char **pp)
{
	char	*p = *pp;
	int	depth = 0;

	if (*p == '"')
	{
		p++;
		while (*p && *p != '"')
		{
			if (*p == '\\' && p[1])
				p++;
			p++;
		}
		if (*p == '"')
			p++;
		*pp = p;
		return;
	}
	while (*p)
	{
		if (*p == '{' || *p == '[')
			depth++;
		else if (*p == '}' || *p == ']')
		{
			if (depth == 0)
				break;
			depth--;
		}
		else if (*p == ',' && depth == 0)
			break;
		p++;
	}
	*pp = p;
}
```

- [ ] **Step 4: Rebuild `MCP_Field` on the scanner**

Replace the body of `MCP_Field` (`:433-529`) with:

```c
static int MCP_Field (char *line, char *name, char *out, int outsize)
{
	char	*p, *ks, *v;
	int	klen, n, full, i;

	p = line;
	while (MCP_NextMember (&p, &ks, &klen, &v))
	{
		if (klen == (int)strlen (name) && !strncmp (ks, name, klen))
		{
			if (*v != '"')
				return 0;
			p = v + 1;
			n = 0;
			full = 0;
			while (*p && *p != '"')
			{
				if (*p == '\\' && p[1])
				{
					p++;
					full++;
					if (*p == 'u')
					{ // \uXXXX -> '?', skip hex digits
						if (n + 1 < outsize)
							out[n++] = '?';
						for (i = 0; i < 4 && p[1]; i++)
							p++;
						p++;
						continue;
					}
					if (n + 1 >= outsize)
					{
						p++;
						continue;
					}
					switch (*p)
					{
					case 'n': out[n++] = '\n'; break;
					case 'r': out[n++] = '\r'; break;
					case 't': out[n++] = '\t'; break;
					case 'b': out[n++] = '\b'; break;
					case 'f': out[n++] = '\f'; break;
					default: out[n++] = *p; break;
					}
					p++;
					continue;
				}
				full++;
				if (n + 1 < outsize)
					out[n++] = *p;
				p++;
			}
			out[n] = 0;
			return (full <= outsize - 1) ? 1 : -1;
		}
		MCP_SkipValue (&p);
	}
	out[0] = 0;
	return 0;
}
```

Keep the existing banner comment (contract: 1 fit, -1 truncated, 0 absent,
`\uXXXX` → `?`).

- [ ] **Step 5: Rebuild `MCP_HashRequest` on the scanner**

Replace its loop body (`:1158-1284` from `p = line;` through the closing
brace) with:

```c
	p = line;
	while (MCP_NextMember (&p, &ks, &klen, &v))
	{
		// the hash sees the key with escapes resolved, as before
		n = 0;
		for (q = ks; q < ks + klen; q++)
		{
			if (*q == '\\' && q + 1 < ks + klen)
				q++;
			if (n + 1 < (int)sizeof (key))
				key[n++] = *q;
		}
		key[n] = 0;
		skip = false;
		for (i = 0; envelope[i]; i++)
		{
			if (!strcmp (key, envelope[i]))
			{
				skip = true;
				break;
			}
		}
		if (!skip)
		{
			for (q = key; *q; q++)
			{
				h ^= (unsigned char)*q;
				h *= 16777619u;
			}
			h ^= '=';
			h *= 16777619u;
		}
		p = v;
		if (*p == '"')
		{
		// quoted value: hash the wire bytes, escapes included
			for (p++; *p && *p != '"'; p++)
			{
				if (!skip)
				{
					h ^= (unsigned char)*p;
					h *= 16777619u;
				}
				if (*p == '\\' && p[1])
				{
					p++;
					if (!skip)
					{
						h ^= (unsigned char)*p;
						h *= 16777619u;
					}
				}
			}
			if (*p == '"')
				p++;
		}
		else
		{
		// raw value (number, bool, null, nested): hash to the member end
			int d = 0;

			while (*p)
			{
				if (*p == '{' || *p == '[')
					d++;
				else if (*p == '}' || *p == ']')
				{
					if (d == 0)
						break;
					d--;
				}
				else if (*p == ',' && d == 0)
					break;
				if (!skip)
				{
					h ^= (unsigned char)*p;
					h *= 16777619u;
				}
				p++;
			}
		}
	}
	return h;
}
```

Update the declaration line to `char	key[64], *p, *ks, *v, *q;` and
`int	klen, i, n, skip;` (the old loop-local `depth` and `q = p + 1` key
extraction go away).

- [ ] **Step 6: Build and run the envelope suites**

Run: `make clean && make build-release QUAKE_MCP=1`
Expected: exit 0.

Run: `python3 -m pytest QuakeMCP/tests/integration/test_envelope.py QuakeMCP/tests/integration/test_bridge_exec.py -v`
Expected: PASS, including the new regression test and the escape parity test.

- [ ] **Step 7: Commit**

```bash
git add QuakeMCP/bridge/q_mcp.c QuakeMCP/tests/integration/test_envelope.py
git commit -m "refactor: quakemcp shared json member scanner"
```

---

### Task 12: Human takeover stop control

**Files:**
- Create: `QuakeMCP/bridge/q_mcp_ui.c`
- Modify: `QuakeMCP/bridge/q_mcp.h`, `QuakeMCP/bridge/q_mcp.c` (accessors + revoke path, place next to `MCP_LeaseHeld`'s users), `Makefile:122-124`, `Quake/render/gl_screen.c:947-949`, `Quake/platform/in_sdl.c:15-20,178-181`

**Interfaces:**
- Consumes: `MCP_ClearControl()` (`q_mcp.c:1100`, static), `Draw_FillAlpha`/`Draw_StringAlpha` (`render/draw.h`).
- Produces: `int MCP_LeaseHeld(void)`, `void MCP_HumanTakeover(void)`, `void MCP_UiDraw(void)`, `int MCP_UiMouseClick(int button, qboolean down)`.

Real window clicks cannot be injected by the test harness; the control's
drawing and click path are verified by the Task 14 manual protocol. This
task's automated gate is: both builds green.

- [ ] **Step 1: Declare the surface**

Append to `q_mcp.h` before `#endif`:

```c
// Human takeover (q_mcp.c, q_mcp_ui.c): the visible stop control. While
// a lease is held the banner draws and a physical left click revokes the
// lease through MCP_ClearControl. MCP_UiMouseClick returns nonzero when
// it swallowed the event; MCP_HumanTakeover is the shared revoke path.
int MCP_LeaseHeld (void);
void MCP_HumanTakeover (void);
void MCP_UiDraw (void);
int MCP_UiMouseClick (int button, qboolean down);
```

- [ ] **Step 2: Add the bridge accessors**

In `q_mcp.c`, immediately after `MCP_ClearControl` (`:1121`):

```c
/*
==================
MCP_LeaseHeld

True while a controller lease is live; the stop control draws and
intercepts only then.
==================
*/
int MCP_LeaseHeld (void)
{
	return mcp_lease_active && mcp_lease_id[0] != 0;
}

/*
==================
MCP_HumanTakeover

The human pressed the visible stop control: revoke the lease exactly
like an MCP release, so a running act ends interrupted, input is
neutralized and a pending modal is denied. The session keeps its
execution mode.
==================
*/
void MCP_HumanTakeover (void)
{
	MCP_ClearControl ();
}
```

- [ ] **Step 3: Create the UI module**

Create `QuakeMCP/bridge/q_mcp_ui.c`:

```c
/*
Copyright (C) 2026 Id Software-derived QuakeMCP contributors

This program is free software; you can redistribute it and/or
modify it under the terms of the GNU General Public License
as published by the Free Software Foundation; either version 2
of the License, or (at your option) any later version.
*/

// q_mcp_ui.c -- MCP takeover control: visible banner + left click.
//
// While a controller lease is held, glquake draws a top-center banner
// and the first physical left-click revokes the lease (human takeover).
// There is no cursor or hit test: any left-click is the stop control,
// which makes recovery work in game, console and menu contexts alike.
// Without a lease nothing is drawn and no event is touched.

#include "quakedef.h"
#include "q_mcp.h"

#define MCP_STOP_TEXT	"MCP CONTROL - LEFT-CLICK TO STOP"
#define MCP_STOP_PAD	4
#define MCP_STOP_MARGIN	8

static qboolean	mcp_stop_swallow;	// matching left-up after takeover

/*
==================
MCP_UiRect

Screen-space rect of the stop banner (top-center, 8x8 font metrics).
False while there is nothing to draw.
==================
*/
static qboolean MCP_UiRect (int *x, int *y, int *w, int *h)
{
	int	textw;

	if (!MCP_LeaseHeld () || vid.width <= 0 || vid.height <= 0)
		return false;
	textw = strlen (MCP_STOP_TEXT) * 8;
	*w = textw + 2 * MCP_STOP_PAD;
	*h = 8 + 2 * MCP_STOP_PAD;
	*x = (vid.width - *w) / 2;
	*y = MCP_STOP_MARGIN;
	return true;
}

/*
==================
MCP_UiDraw

Called at the end of SCR_UpdateScreen, so the banner is part of the
frame the bridge captures.
==================
*/
void MCP_UiDraw (void)
{
	int	x, y, w, h;

	if (!MCP_UiRect (&x, &y, &w, &h))
		return;
	Draw_FillAlpha (x, y, w, h, 12, 0.65f);
	Draw_FillAlpha (x + 1, y + 1, w - 2, h - 2, 0, 0.65f);
	Draw_StringAlpha (x + MCP_STOP_PAD, y + MCP_STOP_PAD,
		MCP_STOP_TEXT, 0.9f);
}

/*
==================
MCP_UiMouseClick

One physical mouse button event, before the access/game funnel. While a
lease is held the left button takes over and the click (and its
matching release) is swallowed; otherwise the event passes through
untouched.
==================
*/
int MCP_UiMouseClick (int button, qboolean down)
{
	if (button != 0)
		return 0;
	if (down)
	{
		if (!MCP_LeaseHeld ())
			return 0;
		mcp_stop_swallow = true;
		MCP_HumanTakeover ();
		return 1;
	}
	if (mcp_stop_swallow)
	{
		mcp_stop_swallow = false;
		return 1;
	}
	return 0;
}
```

- [ ] **Step 4: Add the object to the build**

In `Makefile:122-124`, extend `QUAKE_MCP_OBJS`:

```make
QUAKE_MCP_OBJS = $(QUAKE_BUILDDIR)/mcp/q_mcp.o \
	$(QUAKE_BUILDDIR)/mcp/q_mcp_input.o \
	$(QUAKE_BUILDDIR)/mcp/q_mcp_ui.o \
	$(QUAKE_BUILDDIR)/mcp/q_mcp_capture.o
```

- [ ] **Step 5: Hook the draw**

In `gl_screen.c`, extend the existing MCP block (`:947-949`):

```c
#ifdef QUAKE_MCP
	MCP_UiDraw ();
	MCAP_Frame ();
#endif
```

- [ ] **Step 6: Hook the click**

In `in_sdl.c`, add the include after `cl_access.h` (`:18`):

```c
#ifdef QUAKE_MCP
#include "q_mcp.h"
#endif
```

and in the mouse-button case (`:178-181`):

```c
			if (b >= 0)
			{
#ifdef QUAKE_MCP
				if (MCP_UiMouseClick (b, event.button.down))
					break;
#endif
				Access_ButtonEvent(K_MOUSE1 + b, event.button.down,
				                   (unsigned int)SDL_GetTicks());
			}
```

- [ ] **Step 7: Build both gates**

Run: `make clean && make build-release build-server build-client`
Expected: exit 0 (no MCP symbols referenced).

Run: `make clean && make build-release QUAKE_MCP=1`
Expected: exit 0.

- [ ] **Step 8: Run the integration suite**

Run: `python3 -m pytest QuakeMCP/tests/integration -v`
Expected: PASS (19 pre-existing tests; takeover is manual until Task 14).

- [ ] **Step 9: Commit**

```bash
git add QuakeMCP/bridge/q_mcp_ui.c QuakeMCP/bridge/q_mcp.h QuakeMCP/bridge/q_mcp.c Makefile Quake/render/gl_screen.c Quake/platform/in_sdl.c
git commit -m "feat: quakemcp human takeover stop control"
```

---

### Task 13: Documentary truth pass

**Files:**
- Modify: `QuakeMCP/docs/engine-integration.md` (impulse rows `:32`, `:168`; hook table; op map), `QuakeMCP/AGENTS.md` (Protocol "Reads" bullet; Traps; Architecture hook table), `QuakeMCP/README.md` (protocol line; tools note), `docs/superpowers/2026-09-14-quakemcp-conformance-plan.md:334` (Task 6 drift note)

**Interfaces:**
- Consumes: the shipped behavior from Tasks 1–12.

- [ ] **Step 1: Re-verify hook line numbers**

Run the greps listed in the hook table's method note (`grep -n` for
`MCP_Poll`, `MCP_FreezeSim`, `MCP_NoteTick`, `MCP_Move`, `MCP_Buttons`,
`MCP_Impulse`, `MCP_NoteWorldSpawn`, `MCAP_Frame`) and update every line
number plus the "re-verified" sentence (method + date `2026-09-14`).

- [ ] **Step 2: Fix the impulse wording**

In `engine-integration.md`:

- row `:32`: `... in_impulse` written then cleared; MCP bits/impulse merged at :377-382` → ``the engine writes and clears `in_impulse`; MCP never assigns it — the pending MCP impulse merges at :377-386 only when no human impulse is pending that frame``;
- row `:168`: keep `in_attack`/`in_jump`/`in_impulse` are never written; add ``a pending human impulse wins its frame, a pending MCP impulse is deferred to the next serialization``.

- [ ] **Step 3: Add the takeover hooks**

In `engine-integration.md`'s hook table and `AGENTS.md`'s Architecture
table, add:

| Hook | File | Role |
|---|---|---|
| `MCP_UiDraw` | `Quake/render/gl_screen.c` | takeover banner, drawn while a lease is held |
| `MCP_UiMouseClick` | `Quake/platform/in_sdl.c` | left-click takeover before the access funnel |

- [ ] **Step 4: Correct the `exec` wording**

In `AGENTS.md` Protocol → Reads, drop "the old `exec text=""` idiom stays
a read"; state: `tail` is the console read; `exec` always requires text
(missing/empty → `INVALID_CONTEXT`). In `README.md`'s protocol summary,
add one line: while a lease is held a visible top-center banner offers a
left-click human takeover.

- [ ] **Step 5: Record the plan drift**

In `2026-09-14-quakemcp-conformance-plan.md:334`, append to the
`No new hooks` sentence: `SHIPPED DRIFT (2026-09-14): the round added
MCP_NoteWorldSpawn in SV_SpawnServer instead, because a sv.name poll
cannot see same-map same-time reloads; recorded in the Fixes Ledger.`

- [ ] **Step 6: Commit**

```bash
git add QuakeMCP/docs/engine-integration.md QuakeMCP/AGENTS.md QuakeMCP/README.md docs/superpowers/2026-09-14-quakemcp-conformance-plan.md
git commit -m "docs: quakemcp review-fixes truth pass"
```

---

### Task 14: Acceptance, matrix, ledger

**Files:**
- Modify: `QuakeMCP/docs/acceptance-results.md`
- Modify: `docs/superpowers/2026-08-29-quake-apple-silicon.md` (append Fixes Ledger entry)

**Interfaces:**
- Consumes: all prior tasks; game data at `game/`.

- [ ] **Step 1: Run the full verification set**

```bash
make clean && make build-release build-server build-client
python3 -m pytest QuakeMCP/tests/unit QuakeMCP/tests/contract -v
make clean && make build-release QUAKE_MCP=1
python3 -m pytest QuakeMCP/tests/integration -v
```

Expected: both builds exit 0; 0 skips in integration (game data present);
unit/contract count grows by the new tests (record the exact numbers).

SIGKILL smoke, per the standing protocol: launch each vanilla binary with
game data, SIGKILL it, and count `Received signal` lines in its output —
must be 0 for `glquake`, `qwsv`, `glqwcl`.

- [ ] **Step 2: Run the manual takeover protocol**

Document this exact sequence in the acceptance file and record the
observed results:

1. `make build-release QUAKE_MCP=1`, launch `Quake/build-macosx/glquake
   -basedir game -mcp_port 29890 +mcp_enabled 1` (a free port: the test
   modules own 29876–29889 and nothing else runs here).
2. Connect with the stdio server (or raw socket), `control acquire`,
   start an `act` with `ticks=72` so a reply is deferred.
3. Confirm the top-center banner `MCP CONTROL - LEFT-CLICK TO STOP` is
   visible in the window while the lease is held.
4. Click once inside the game window. Expected: the act reply returns
   `interrupted: true`; a subsequent `hb` is `STALE_STATE`; `state` shows
   neutral input (no held MCP buttons); `control acquire` succeeds again.
5. No click is delivered to the game (menus/console unaffected).

- [ ] **Step 3: Rewrite the acceptance matrix**

Rewrite `acceptance-results.md` for this round, re-mapping rows to the
09-13 acceptance gates (design §9, plan Task 10) and restoring the two
dropped rows with current evidence:

| Restored gate | Evidence |
|---|---|
| Full UI reachability | `test_modal.py` (menu enter/escape, modal image), `test_lifecycle.py` (save/load UI paths); options-dialog depth stays PARTIAL if untested |
| Failure cleanup | `test_slots.py` (heartbeat loss, EOF), SIGKILL smoke (engine abort), Task 14 manual takeover protocol (physical takeover), `test_lease.py` (local lease drop) |

Keep every honest negative unchanged: intermission FAIL with cause,
vision ENVIRONMENT-BLOCKED, water PARTIAL, cancellation PARTIAL. Record
the impulse collision policy as reasoned-not-tested.

- [ ] **Step 4: Append the Fixes Ledger entry**

Append to `2026-08-29-quake-apple-silicon.md` a round entry in the
existing voice (what shipped; the S7 accept-with-rationale for the
envelope data clump; the plan Task 6 drift; gates, suite counts, smoke,
manual takeover result; deferred items unchanged).

- [ ] **Step 5: Commit**

```bash
git add QuakeMCP/docs/acceptance-results.md docs/superpowers/2026-08-29-quake-apple-silicon.md
git commit -m "docs: quakemcp review-fixes acceptance and ledger"
```

---

## Self-Review Notes

- **Spec coverage:** S1→T1, S2→T3, S3→T5, S4→T7, S5→T10, S6→T8, S7→T14
  ledger + design rationale (no code), S8→T9, S9→T11; P1→T12, P2→T4,
  P3→T14, P4→T13, P5→T2. Every design section maps to a task.
- **Placeholder scan:** none; each step carries the code or exact command.
- **Type consistency:** `clear_lease()` (T5) consumed by T6 and `server.py`;
  `_observation_result(encoded, report, structured)` (T7) used at four
  sites; `MCP_BeginMutation(op, line, id, &hash)` (T10) used at five
  sites; `MCP_UiMouseClick(int button, qboolean down)` (T12) matches the
  `in_sdl.c` call site.
