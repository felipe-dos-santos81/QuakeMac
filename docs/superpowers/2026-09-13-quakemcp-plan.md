# QuakeMCP Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build `QuakeMCP/` (Python MCP stdio server + in-engine C bridge) so an MCP client can drive `glquake` through bounded actions and receive real rendered frames.

**Architecture:** Out-of-process Python server owns schemas, supervision and image encoding; a small C bridge compiled into `glquake` behind `-DQUAKE_MCP` (default off) owns input arbitration and snapshots on the main thread. Control flows over loopback TCP as line-JSON; frames are length-framed raw bytes encoded to PNG/JPEG in Python.

**Tech Stack:** id-era C (Apple clang, GNU make, SDL3, OpenGL), Python 3.12, `mcp==1.29.0`, Pillow 12.3.0, pytest 8.4.2.

## Global Constraints

- `glquake` (`Quake/` tree) only. `QuakeWorld/` untouched.
- Bridge disabled by default (`mcp_enabled 0`); a normal build opens no endpoint and needs no Python.
- Loopback-only bind (`127.0.0.1`), single client, per-instance random token in a `0600` file. No token on CLI or in logs.
- `game/` is user territory: never commit paks, saves, screenshots or token files there or anywhere.
- `access_mouseonly 0` behavior stays byte-identical to vanilla; `Access_Frame` untouched.
- id-era C style: tabs, K&R braces, `/* banner */` comments.
- Stage explicit paths only (`git add <path>`). No `game/` paths in any commit.
- Integration tests needing game data SKIP with a message when `game/id1/pak0.pak` is absent.
- Pins: `mcp==1.29.0` in `QuakeMCP/pyproject.toml`; record any newer verified pin in the task commit message.

## File Structure

```
QuakeMCP/
  pyproject.toml            # pins mcp==1.29.0, Pillow; pytest config
  src/quakemcp/
    server.py               # FastMCP app, 12 tool registrations, stdio transport
    models.py               # lease/action/error types, JSON validation (no engine I/O)
    engine.py               # TCP client: line-JSON control + framed binary reads
    lifecycle.py            # spawn/attach/stop supervision, token file handling
    vision.py               # raw RGB -> PNG/JPEG @ longest-edge cap, transform report
  bridge/
    q_mcp.h                 # MCP_Init/Poll/Shutdown, MCP_Move, MCP_ConsoleTail, MCP_FrameId
    q_mcp.c                 # socket, token, queue, watchdog, dispatch to Cbuf_/Cvar_
    q_mcp_input.c           # MCP_* button state merged into usercmd in MCP_Move
    q_mcp_capture.c         # glReadPixels snapshot into ring (main thread only)
  tests/unit/               # stdlib-free pytest, no engine, no sockets (fake engine.py peer)
  tests/contract/           # in-process MCP client (installed SDK) vs server
  tests/integration/        # real glquake; skipped without game data
  docs/engine-integration.md
  docs/acceptance-results.md
```

Engine hook calls (each one line, guarded by `#ifdef QUAKE_MCP` except the `CL_SendCmd` merge which checks `mcp_enabled` at runtime):
- `Quake/host.c` `_Host_Frame`: `MCP_Poll();` immediately before `if (!Host_FilterTime (time))`.
- `Quake/render/gl_screen.c` `SCR_ModalMessage` loop: `MCP_Poll();` beside `Sys_SendKeyEvents ();`.
- `Quake/client/cl_main.c` `CL_SendCmd`: `MCP_Move (&cmd);` between `IN_Move (&cmd);` and `CL_SendMove (&cmd);`.
- Root `Makefile`: one pattern rule + `mkdir` target for `QuakeMCP/bridge/*.c` into `$(QUAKE_BUILDDIR)/mcp/`, objects added to a `QUAKE_MCP_OBJS` list linked only when `QUAKE_MCP=1`; `-DQUAKE_MCP` appended to a `QUAKE_MCP_CFLAGS` used by that rule.

Wire protocol v1 (frozen by Task 2, extended only additively later):
- Control: ASCII line, `\n`-terminated, max 64 KiB, JSON `{"v":1,"auth":"<hex>","id":"<client-msg-id>","op":"<name>",...}`. Reply line: `{"v":1,"id":"...","ok":true,"result":{...}}` or `{"v":1,"id":"...","ok":false,"error":"CODE","detail":"..."}`.
- Frames: reply line `{"v":1,"id":"...","ok":true,"result":{"blob":N,"w":W,"h":H,"frame":F}}` followed by exactly N raw RGB bytes.

---

### Task 1: Hook inventory doc

**Files:**
- Create: `QuakeMCP/docs/engine-integration.md`
- Test: `git status --short` (only the new file)

**Interfaces:**
- Consumes: nothing.
- Produces: verified hook table used by Tasks 2, 3, 5, 7 (exact symbols/paths below).

- [ ] **Step 1: Re-run the hook greps and confirm line numbers**

Run:
```bash
rg -n "CL_BaseMove|CL_SendMove|in_impulse" Quake/client/cl_input.c | head -n 20
rg -n "_Host_Frame|Host_FilterTime|Sys_SendKeyEvents|Host_ServerFrame" Quake/host.c | head -n 20
rg -n "SCR_ModalMessage|SCR_UpdateScreen|glReadPixels" Quake/render/gl_screen.c | head -n 20
rg -n "Host_Map_f|Host_Savegame_f|Host_Loadgame_f|Cmd_AddCommand" Quake/host_cmd.c | head -n 25
rg -n "access_mouseonly" Quake/client/cl_access.c | head -n 5
```
Expected: hits matching the spec §3 table (`CL_SendMove` ~332, `_Host_Frame` ~644, `SCR_ModalMessage` ~739, `Host_Map_f` ~256). If a number moved, use the new number everywhere below.

- [ ] **Step 2: Write `QuakeMCP/docs/engine-integration.md`**

Content (fill line numbers from Step 1): fork commit (`git rev-parse --short HEAD`), renderer (OpenGL, `gl_vidsdl.c`), platform pump (`in_sdl.c`), the three hook sites with file:line, `host_framecount` as frame id, `con_text` ring as console-tail source, `cl.movemessages` startup drop, `host_maxfps` default 72, `access_mouseonly` default 1, op map (`map`/`restart`/`changelevel`/`save`/`load`/`kill`/`pause` exist; `list_maps`/`list_saves` do not), death/respawn flow marked UNVERIFIED for Task 8.

- [ ] **Step 3: Commit**

```bash
git add QuakeMCP/docs/engine-integration.md
git commit -m "docs: quakemcp engine hook inventory"
```

---

### Task 2: Bridge skeleton with token TCP and poll hooks

**Files:**
- Create: `QuakeMCP/bridge/q_mcp.h`, `QuakeMCP/bridge/q_mcp.c`
- Modify: `Makefile` (pattern rule + mkdir + `QUAKE_MCP_OBJS`), `Quake/host.c` (1 line), `Quake/render/gl_screen.c` (1 line)
- Test: `QuakeMCP/tests/integration/test_bridge_ping.py` (socket ping; skips cleanly only if binary missing — binary is always built here)

**Interfaces:**
- Consumes: Task 1 hook sites.
- Produces (for Tasks 3, 5, 7):
  - `void MCP_Init (void); void MCP_Poll (void); void MCP_Shutdown (void);`
  - `unsigned MCP_FrameId (void);` (returns `host_framecount`)
  - Wire §"Wire protocol v1" frozen above; new op `ping` -> `{"ok":true,"result":{"ready":true}}`.

- [ ] **Step 1: Write `QuakeMCP/bridge/q_mcp.h`**

```c
#ifndef Q_MCP_H
#define Q_MCP_H

void MCP_Init (void);
void MCP_Poll (void);
void MCP_Shutdown (void);
unsigned MCP_FrameId (void);

#endif
```

- [ ] **Step 2: Write `QuakeMCP/bridge/q_mcp.c` (skeleton: socket + token + `ping`)**

Requirements (all in this file, ~150 lines, tabs, K&R): `mcp_enabled` (`"0"`) and `mcp_port` (`"28900"`) cvars registered in `MCP_Init`; `-mcp_port` CLI override via `COM_CheckParm`; TCP bind `127.0.0.1`, single client, non-blocking accept; 32-byte token from `/dev/urandom` written hex to `$TMPDIR/quakemcp-<pid>.token` mode `0600`; `MCP_Poll` accepts/polls (50 ms budget, 64 KiB line cap, larger -> drop + count); `ping` op replies `ready:true`; unknown op -> `ok:false,error:UNSUPPORTED_CAPABILITY`; `MCP_Shutdown` closes fds and unlinks token. No game-state writes in this task.

- [ ] **Step 3: Wire the `Makefile`**

Add (next to `QUAKE_PLATFORM_OBJS`):
```make
QUAKE_MCP_OBJS = $(QUAKE_BUILDDIR)/mcp/q_mcp.o $(QUAKE_BUILDDIR)/mcp/q_mcp_input.o $(QUAKE_BUILDDIR)/mcp/q_mcp_capture.o
$(QUAKE_BUILDDIR)/mcp:
	mkdir -p $(QUAKE_BUILDDIR)/mcp
$(QUAKE_BUILDDIR)/mcp/%.o: QuakeMCP/bridge/%.c | $(QUAKE_BUILDDIR)/mcp
	$(CC) $(CFLAGS) -DQUAKE_MCP -o $@ -c $<
```
And append `$(QUAKE_MCP_OBJS)` to the `$(QUAKE_BUILDDIR)/glquake:` link line prerequisites only (guard with `ifdef QUAKE_MCP`). Missing `.o` files for the not-yet-written `q_mcp_input.c`/`q_mcp_capture.c` break the link, so for THIS task list only `q_mcp.o` in `QUAKE_MCP_OBJS` and extend the list in Tasks 5/7.

- [ ] **Step 4: Add the two one-line hooks**

In `Quake/host.c` `_Host_Frame`, before `if (!Host_FilterTime (time))`:
```c
#ifdef QUAKE_MCP
	MCP_Poll ();
#endif
```
In `Quake/render/gl_screen.c` `SCR_ModalMessage`, beside `Sys_SendKeyEvents ();`:
```c
#ifdef QUAKE_MCP
	MCP_Poll ();
#endif
```
Include `q_mcp.h` in both files under `#ifdef QUAKE_MCP`.

- [ ] **Step 5: Build with and without the flag**

Run:
```bash
make clean && make build-release build-server build-client
make clean && make build-release QUAKE_MCP=1
```
Expected: both exit 0. (`build-server`/`build-client` never see the bridge.)

- [ ] **Step 6: Write the ping test**

`QuakeMCP/tests/integration/test_bridge_ping.py`: launches `Quake/build-macosx/glquake -basedir game -mcp_port 29876 +mcp_enabled 1` (skip via `pytest.skip` if `game/id1/pak0.pak` absent), waits for `/tmp/quakemcp-<pid>.token`, sends `{"v":1,"auth":token,"id":"1","op":"ping"}\n`, asserts reply `ok:true,ready:true`, then sends unknown op and asserts `UNSUPPORTED_CAPABILITY`, kills child, asserts token file gone.

Run: `python3 -m pytest QuakeMCP/tests/integration/test_bridge_ping.py -v`
Expected: PASS (or SKIP with game-data message).

- [ ] **Step 7: Commit**

```bash
git add QuakeMCP/bridge/q_mcp.h QuakeMCP/bridge/q_mcp.c Makefile Quake/host.c Quake/render/gl_screen.c QuakeMCP/tests/integration/test_bridge_ping.py
git commit -m "feat: quakemcp bridge skeleton with token tcp and poll hooks"
```

---

### Task 3: Console/cvar dispatch + console tail

**Files:**
- Modify: `QuakeMCP/bridge/q_mcp.c` (add `exec`, `cvar` ops)
- Create: `QuakeMCP/bridge/q_mcp.h` (add `void MCP_ConsoleTail (char *out, int outsize);`)
- Test: extend `QuakeMCP/tests/integration/test_bridge_ping.py` -> new file `QuakeMCP/tests/integration/test_bridge_exec.py`

**Interfaces:**
- Consumes: Task 2 socket/queue/auth.
- Produces (for Task 4): ops `exec {text}` -> `{"output":<tail>}`, `cvar {name}` / `cvar {name,value}` -> `{"value":<effective>}` or `Cvar_FindVar` miss -> `INVALID_CONTEXT`.

- [ ] **Step 1: Implement `exec`/`cvar` in `q_mcp.c`**

`exec`: `Cbuf_AddText` (append `\n` if missing); reply carries last 8 console lines. `cvar` get/set via `Cvar_FindVar`/`Cvar_Set`, reply with effective string. Reject `exec` text over 4 KiB with `POLICY_DENIED`. Tail reader `MCP_ConsoleTail` walks the existing `con_text` ring (`Quake/client/console.c`, externs `con_text`, `con_totallines`, `con_current`, `con_linewidth`); no console changes.

- [ ] **Step 2: Write `QuakeMCP/tests/integration/test_bridge_exec.py`**

Same launch helper as Task 2 (copy the ~30-line helper into this file; do not import across test files). Cases: `exec "help"` returns tail containing `commands:`; `cvar {"name":"host_maxfps"}` returns `"72"`; `cvar {"name":"host_maxfps","value":"80"}` then get returns `"80"` and restores `"72"` in teardown; `cvar {"name":"no_such_var"}` -> `INVALID_CONTEXT`; oversized exec -> `POLICY_DENIED`. Skip without game data.

Run: `python3 -m pytest QuakeMCP/tests/integration/test_bridge_exec.py -v`
Expected: PASS (or SKIP).

- [ ] **Step 3: Rebuild + commit**

```bash
make build-release QUAKE_MCP=1
git add QuakeMCP/bridge/q_mcp.c QuakeMCP/bridge/q_mcp.h QuakeMCP/tests/integration/test_bridge_exec.py
git commit -m "feat: quakemcp exec and cvar ops with console tail"
```

---

### Task 4: Python package, stdio server, lifecycle tools

**Files:**
- Create: `QuakeMCP/pyproject.toml`, `QuakeMCP/src/quakemcp/models.py`, `engine.py`, `lifecycle.py`, `server.py`
- Test: `QuakeMCP/tests/unit/test_models.py`, `QuakeMCP/tests/contract/test_tools_list.py`

**Interfaces:**
- Consumes: Task 2-3 wire protocol.
- Produces (for Tasks 5-8): `BridgeClient.send(op, **kw) -> dict`, `launch(profile) -> Instance`, `Instance.{pid,port,token,stop}()`, error-code enum; server exposes `quake_status`, `quake_start`, `quake_attach`, `quake_stop` (other 8 tools added in later tasks; unregistered tools must NOT appear in `tools/list`).

- [ ] **Step 1: Verify the installed SDK's API (do not copy from memory)**

Run:
```bash
python3 -c "import mcp.server.fastmcp, inspect; print([n for n in dir(mcp.server.fastmcp.FastMCP) if 'tool' in n.lower()])"
python3 -c "import mcp.types; print([n for n in dir(mcp.types) if 'Image' in n])"
```
Expected: names like `tool` decorator and an `Image`/`ImageContent` type. Write `server.py` against exactly what this prints for `mcp==1.29.0`.

- [ ] **Step 2: Write `pyproject.toml`**

```toml
[build-system]
requires = ["setuptools>=61"]
[project]
name = "quakemcp"
version = "0.1.0"
requires-python = ">=3.12"
dependencies = ["mcp==1.29.0", "Pillow>=12.0"]
[tool.pytest.ini_options]
testpaths = ["QuakeMCP/tests/unit", "QuakeMCP/tests/contract"]
```
(`tests/integration` runs only on explicit path request.)

- [ ] **Step 3: Write `models.py`**

Frozen error enum: `NOT_READY CONTROL_BUSY STALE_STATE UNSUPPORTED_CAPABILITY INVALID_CONTEXT ACTION_TIMEOUT ACTION_INTERRUPTED ENGINE_DISCONNECTED FRAME_TIMEOUT FRAME_EXPIRED RENDER_UNAVAILABLE RESULT_EXPIRED POLICY_DENIED`. Dataclasses: `Lease(id, seq, epoch)`, `Observation` (identity/timing/image/state/telemetry groups per spec §7 as plain dicts with required keys), `validate_line(obj)` rejecting unknown `op`, oversize payloads and missing `auth`. Pure functions only.

- [ ] **Step 4: Write `engine.py` + `lifecycle.py`**

`BridgeClient(host, port, token)`: `send` writes one JSON line, reads one reply line (5 s timeout -> raise `EngineDisconnected`); `read_blob(n)` reads exactly N bytes. `lifecycle.launch(profile_id)`: profile table hardcoded to `{"local": {"exe": "Quake/build-macosx/glquake", "args": ["-basedir", "game"]}}` — never a shell command; spawns child with stdout/stderr to `QuakeMCP/logs/<ts>.log` (git-ignored? logs go to `/tmp/quakemcp-logs/`, never the repo), waits for token file (10 s -> `NOT_READY`), sends `ping`. `attach(instance_id, token)`: `ping` only, marks `owned=False`; `stop()` refuses when `owned=False`.

- [ ] **Step 5: Write `server.py` (4 tools only)**

FastMCP app `quakemcp`, stdio transport, logs to stderr only. Tools: `quake_status`, `quake_start`, `quake_attach`, `quake_stop` with strict input schemas (extra fields rejected). Mutating annotations: `destructiveHint` set on stop; all four carry `readOnlyHint:false` except status `true`. (Annotations are hints only; enforcement lives in `lifecycle.py`.)

- [ ] **Step 6: Write unit + contract tests**

`tests/unit/test_models.py`: unknown op rejected; oversize rejected; error enum contains all 13 codes; `Observation` requires identity keys (missing -> `ValueError`).
`tests/contract/test_tools_list.py`: in-process SDK client -> `tools/list` returns exactly the 4 tools; `quake_status` with no instance returns `ENGINE_DISCONNECTED` as tool error (`isError`), not a protocol error.

Run: `python3 -m pytest QuakeMCP/tests/unit QuakeMCP/tests/contract -v`
Expected: PASS.

- [ ] **Step 7: Commit**

```bash
git add QuakeMCP/pyproject.toml QuakeMCP/src/quakemcp/ QuakeMCP/tests/unit QuakeMCP/tests/contract
git commit -m "feat: quakemcp server skeleton with lifecycle tools"
```

---

### Task 5: Input injection, bounded actions, watchdog

**Files:**
- Create: `QuakeMCP/bridge/q_mcp_input.c`, extend `q_mcp.c` (`act`, `ui`, `control`, `release`, heartbeat)
- Modify: `QuakeMCP/bridge/q_mcp.h` (add `int MCP_Move (usercmd_t *cmd);` — `usercmd_t` comes from `Quake/client/client.h` via `quakedef.h`, already included at both hook sites), `Quake/client/cl_main.c` (1-line `MCP_Move` hook), `QuakeMCP/src/quakemcp/server.py` (register `quake_act`), `Makefile` (`QUAKE_MCP_OBJS` += `q_mcp_input.o`)
- Test: `QuakeMCP/tests/integration/test_act.py`

**Interfaces:**
- Consumes: Tasks 2-4.
- Produces (for Tasks 6, 9): `int MCP_Move (usercmd_t *cmd);` (0 = no MCP input, 1 = merged); ops `act`, `ui`, `control`, `release`; heartbeat expiry 2 s clears MCP state.

- [ ] **Step 1: Write `q_mcp_input.c`**

Dedicated `mcp_buttons_t` (forward/strafe/vertical -1..1 fixed-point int, yaw/pitch deltas, attack, jump mode, impulse, weapon) plus human/MCP separation: MCP state never touches `in_attack`/`in_jump`. `MCP_Move` scales -1..1 through `cl_forwardspeed`/`cl_sidespeed`/`cl_upspeed`, applies yaw/pitch deltas to `cl.viewangles` once per action start (clamped pitch -90..90), ORs attack/jump bits into `cmd->buttons`, sets `cmd->impulse` one-shot. Jump `tap` = set bit for exactly one `MCP_Move` call then auto-clear.

- [ ] **Step 2: Extend `q_mcp.c` with action lifecycle**

`act {ticks|duration_ms, ...inputs, lease, action_id, seq, epoch}`: neutral-start, engine monotonic deadline (`Sys_DoubleTime`), hard 5 s wall cap, completion reply carries `{completed_ticks, elapsed_ms, interrupted}`. `control {acquire|release|detach|mode}`: acquire requires no physical buttons held: sample `in_attack.state|in_jump.state` == 0 else `CONTROL_BUSY`. `release` is queue-jumping: serviced first in `MCP_Poll`, clears MCP state, idempotent. Heartbeat op bumps timestamp; `MCP_Poll` expires lease after 2 s wall-clock (uses `Sys_DoubleTime`, independent of paused sim).

- [ ] **Step 3: Hook `CL_SendCmd`**

In `Quake/client/cl_main.c` after `IN_Move (&cmd);`:
```c
#ifdef QUAKE_MCP
	MCP_Move (&cmd);
#endif
```

- [ ] **Step 4: Register `quake_act` (state-only observation; Task 7 adds pixels)**

`server.py` gains `quake_act` with the §5 input schema (mutually exclusive `ticks` 1..72 / `duration_ms` 1..1000, lease/action/seq/epoch/world/control preconditions). It returns text + `structuredContent` (Task 6 keys); the `image` block is appended in Task 7 — record this ordering in a code comment so the interim shape is intentional, not a gap.

- [ ] **Step 5: Write `test_act.py` (real engine)**

Launch, `exec access_mouseonly 0` first (deterministic input path; default-1 behavior is covered in Task 10 regression), `exec map start`, wait `gameplay_ready` (poll `cvar cl.signon`? No such cvar — poll bridge `state` op added in Task 6... ordering problem). Fix: this task's test uses `exec` + screenshot-less poll: send `exec "status"` and read tail until `connected`? Simpler: after `map start`, sleep-free poll `exec "echo READY_$n"` round-trip plus fixed 72-tick act; assert reply `completed_ticks == 72` and no `interrupted`. Movement assertion: record `cl.origin`? Needs state op. Minimal for THIS task: tick-exact completion + `release` idempotency + heartbeat expiry (act, wait 2.5 s, next act with old lease -> lease error). Position-change assertions move to Task 6 where `state` exists. Skip without game data.

Run: `python3 -m pytest QuakeMCP/tests/integration/test_act.py -v`
Expected: PASS (or SKIP).

- [ ] **Step 6: Rebuild all + commit**

```bash
make clean && make build-release build-server build-client && make build-release QUAKE_MCP=1
git add QuakeMCP/bridge/q_mcp_input.c QuakeMCP/bridge/q_mcp.c QuakeMCP/bridge/q_mcp.h Quake/client/cl_main.c QuakeMCP/src/quakemcp/server.py Makefile QuakeMCP/tests/integration/test_act.py
git commit -m "feat: quakemcp bounded actions with watchdog"
```

---

### Task 6: State snapshot and observation metadata

**Files:**
- Modify: `QuakeMCP/bridge/q_mcp.c` (add `state` op), `QuakeMCP/src/quakemcp/server.py` (register `quake_state`), `models.py` (freeze `Observation` keys)
- Test: `QuakeMCP/tests/integration/test_state.py`

**Interfaces:**
- Consumes: Tasks 2-5.
- Produces (for Task 7): `state {}` -> `{instance, epoch, world_gen, control_rev, frame:MCP_FrameId(), time:host_time, map:sv.name, pos:cl.entities[cl.viewentity] origin/angles, health, ammo, ui:key_dest, loading, dead, intermission, signon, movemessages}`. Counter snapshot: state and pixels share `frame` (pixels arrive in Task 7).

- [ ] **Step 1: Implement `state` in `q_mcp.c`**

Read-only. `world_gen`/`control_rev`/`epoch` counters static in `q_mcp.c` (epoch set at `MCP_Init` from `getpid()`; `world_gen` bumped by hooking? No new hooks: bump when `sv.name` string changes between polls — detect in `MCP_Poll`, cheap `strcmp`). SHIPPED DRIFT (2026-09-14): the round added MCP_NoteWorldSpawn in SV_SpawnServer instead, because a sv.name poll cannot see same-map same-time reloads; recorded in the Fixes Ledger. Dead flag: `cl.stats[STAT_HEALTH] <= 0`. Intermission: `cl.intermission`. UI: `key_dest` int. All values from one poll pass (single snapshot, no re-reads).

- [ ] **Step 2: Register `quake_state` + freeze `Observation`**

`server.py` adds read-only `quake_state` returning `structuredContent` exactly the `state` keys. `models.py` gains `REQUIRED_STATE_KEYS` tuple; constructor raises `ValueError` on missing key.

- [ ] **Step 3: Write `test_state.py`**

Launch, `map start`, wait `signon == 4` (SIGNONS) via `state` poll loop (timeout 30 s, no sleeps over 250 ms); assert `movemessages > 2`; record pos; `act` 24 ticks forward; `state` again; assert position changed and `frame` increased; `map start` again bumps `world_gen`. Skip without game data.

Run: `python3 -m pytest QuakeMCP/tests/integration/test_state.py -v`
Expected: PASS (or SKIP).

- [ ] **Step 4: Rebuild + commit**

```bash
make build-release QUAKE_MCP=1
git add QuakeMCP/bridge/q_mcp.c QuakeMCP/src/quakemcp/server.py QuakeMCP/src/quakemcp/models.py QuakeMCP/tests/integration/test_state.py
git commit -m "feat: quakemcp state snapshot and quake_state"
```

---

### Task 7: Vision capture and `quake_observe`

**Files:**
- Create: `QuakeMCP/bridge/q_mcp_capture.c`, `QuakeMCP/src/quakemcp/vision.py`
- Modify: `QuakeMCP/bridge/q_mcp.h` (add `void MCAP_Begin (void);`), `QuakeMCP/bridge/q_mcp.c` (`observe` op), `server.py` (register `quake_observe`, upgrade `quake_act` with image block, add `telemetry:"hud"|"pixels_only"` to both), `Makefile` (`QUAKE_MCP_OBJS` += `q_mcp_capture.o`)
- Test: `QuakeMCP/tests/unit/test_vision.py`, `QuakeMCP/tests/integration/test_observe.py`

**Interfaces:**
- Consumes: Tasks 2, 4, 6 (`frame` correlation).
- Produces: `observe {after_frame?, timeout_ms?, w?}` -> framed RGB blob; Python returns MCP `image` block (PNG default, JPEG optional) + transform report `{src_w,src_h,out_w,out_h,encoding,frame_hash}`. Limits: longest edge default 1280, no upscale, 2 MiB cap else size error with alternatives.

- [ ] **Step 1: Write `q_mcp_capture.c`**

`MCAP_Begin()` (called from `observe` op, main thread only): `glReadPixels(glx, gly, glwidth, glheight, GL_RGB, GL_UNSIGNED_BYTE, ring_slot)` into a 2-slot ring; tag with `MCP_FrameId()`. Vertical flip + BGR swap done in Python (`vision.py`), not C. Over-2-MiB-source guard before alloc: `(glwidth*glheight*3)` cap 32 MiB, else `RENDER_UNAVAILABLE`. Never called from the socket thread (there is none — `MCP_Poll` is main-thread).

- [ ] **Step 2: Write `vision.py`**

```python
def encode_frame(rgb: bytes, w: int, h: int, longest_edge: int = 1280, fmt: str = "png") -> tuple[bytes, dict]:
```
Pillow: `Image.frombytes("RGB", (w, h), rgb).transpose(FLIP_TOP_BOTTOM)`, aspect-preserving `thumbnail`, PNG or JPEG(85); byte cap 2 MiB -> raise `ImageTooLarge(supported alternatives in message)`; returns `(encoded, {"src_w":w,"src_h":h,"out_w":..,"out_h":..,"encoding":fmt,"frame_hash":sha1 hex})`. Crop helper takes source-pixel rect, rejects out-of-bounds and any crop removing the HUD strip without explicit flag (never silent).

- [ ] **Step 3: Wire `observe` + `quake_observe`, upgrade `quake_act`, add `pixels_only`**

`q_mcp.c` `observe`: optional `after_frame` waits (bounded by `timeout_ms`, default 1000, polls inside `MCP_Poll` — never blocks the frame); stale/evicted -> `FRAME_EXPIRED`; no frame in window -> `FRAME_TIMEOUT`. `server.py` returns `[ImageContent, text]` + `structuredContent` (Task 6 keys + image group). `quake_act` now appends the post-action frame's image block (remove the Task 5 interim comment). Both tools accept `telemetry:"hud"` (default, authoritative HUD values from the same snapshot) or `"pixels_only"` (gameplay telemetry and derived flags stripped; transport/timing/renderer/control keys retained). Verify constructor names against Step-1-style introspection of installed `mcp.types` before finalizing.

- [ ] **Step 4: Write tests**

`tests/unit/test_vision.py`: synthetic 64x32 RGB gradient -> PNG decodes to same pixels post-flip; resize math exact for 1280 cap; oversize raises with alternatives message; bad crop rejected; `pixels_only` output contains no `health`/`ammo` keys while `hud` output does.
`tests/integration/test_observe.py`: launch, menu frame via `observe` (no map needed — asserts PNG + `ui == key_menu`? menu key_dest is 3; assert key present); `map start`, `observe` asserts dims, hash differs across a 12-tick act (scene change), `frame` matches a `state` taken in the same poll window. Skip without game data.

Run:
```bash
python3 -m pytest QuakeMCP/tests/unit/test_vision.py QuakeMCP/tests/contract -v
python3 -m pytest QuakeMCP/tests/integration/test_observe.py -v
```
Expected: PASS (or SKIP for integration).

- [ ] **Step 5: Rebuild all + commit**

```bash
make clean && make build-release build-server build-client && make build-release QUAKE_MCP=1
git add QuakeMCP/bridge/q_mcp_capture.c QuakeMCP/src/quakemcp/vision.py QuakeMCP/bridge/q_mcp.c QuakeMCP/bridge/q_mcp.h QuakeMCP/src/quakemcp/server.py Makefile QuakeMCP/tests/unit/test_vision.py QuakeMCP/tests/integration/test_observe.py
git commit -m "feat: quakemcp vision capture and quake_observe"
```

---

### Task 8: Lifecycle ops, config allowlist, guarded console

**Files:**
- Modify: `QuakeMCP/bridge/q_mcp.c` (allowlisted console path), `server.py` (register `quake_game`, `quake_config`, `quake_console`, `quake_ui`, `quake_control`, `quake_release`)
- Test: `QuakeMCP/tests/contract/test_policy.py`, `QuakeMCP/tests/integration/test_lifecycle.py`

**Interfaces:**
- Consumes: Tasks 2-6.
- Produces: all 12 tools registered; `tools/list` complete. `quake_console` allowlist: `{"status","version","skill","god","noclip","give","impulse","kill","pause","save","load","map","restart","changelevel","connect","disconnect","quit","screenshot","toggleconsole"}` + any `access_*` cvar read; everything else -> `POLICY_DENIED`. `quake_config` allowlist of cvar names from capability matrix; unknown -> `INVALID_CONTEXT`.

- [ ] **Step 1: Implement server-side gating (no new engine code)**

`quake_game`: maps ops to `exec` strings (`new_game` -> `map start`; `restart` -> `restart`; `load_map {id}` validated against `list_maps`; `save {slot}` requires `overwrite:true` if slot file exists; `load {slot}`); completion = `world_gen` bump (or `frame` advance + tail match on `Wrote` for save). `list_maps`/`list_saves` scan `<basedir>/id1` (*.bsp, *.sav) with traversal rejection (`..`, `/`, `\0` -> `POLICY_DENIED`). `quake_config`: get/set through `cvar` op, allowlist above. `quake_console`: allowlist above, typed args (e.g. `skill` int 0-3). `quake_ui`: sends `Key_Event` pairs via new tiny bridge op `key {key,down}` (add to `q_mcp.c`, routes to existing `Key_Event`, honors `key_dest`); text only when `key_dest == key_console` (chat/console field) else `INVALID_CONTEXT`. `quake_control`/`quake_release` wrap Task 5 ops.

- [ ] **Step 2: Contract policy tests (no engine)**

`tests/contract/test_policy.py`: drive `server.py` tools against a fake `BridgeClient` (monkeypatched): disallowed console command -> `POLICY_DENIED` tool error; `../../etc` map id -> `POLICY_DENIED`; `stop()` on attached instance refused; `release` works with no lease held (idempotent). Uses in-process SDK client like Task 4.

Run: `python3 -m pytest QuakeMCP/tests/contract -v`
Expected: PASS.

- [ ] **Step 3: Integration lifecycle test**

`tests/integration/test_lifecycle.py`: `quake_start local` -> status `bridge_ready`; `quake_game new_game` -> `gameplay_ready` with `movemessages > 2`; `save` slot `mcp0` (no overwrite flag, fresh) then `load mcp0`; `list_saves` contains it; cleanup deletes `game/id1/mcp0.sav` in teardown (test artifact, never committed); `quake_stop` ends child; second `stop` is clean. Skip without game data.

Run: `python3 -m pytest QuakeMCP/tests/integration/test_lifecycle.py -v`
Expected: PASS (or SKIP).

- [ ] **Step 4: Rebuild + commit**

```bash
make build-release QUAKE_MCP=1
git add QuakeMCP/src/quakemcp/server.py QuakeMCP/bridge/q_mcp.c QuakeMCP/tests/contract/test_policy.py QuakeMCP/tests/integration/test_lifecycle.py
git commit -m "feat: quakemcp lifecycle config and guarded console tools"
```

---

### Task 9: Stepped mode, epochs, dedup ledger

**Files:**
- Modify: `QuakeMCP/bridge/q_mcp.c` (step counting, result ledger), `QuakeMCP/src/quakemcp/models.py` + `engine.py` (preconditions, retry)
- Test: `QuakeMCP/tests/unit/test_dedup.py`, `QuakeMCP/tests/integration/test_stepped.py`

**Interfaces:**
- Consumes: Tasks 5-6.
- Produces: stepped `act {ticks:1..72}` advances exactly N simulation steps (freeze-while-idle via `host_timescale 0`? NO — new minimal mechanism: bridge sets a `mcp_freeze` flag consumed in `_Host_Frame` to skip `CL_SendCmd`+`Host_ServerFrame` while still running poll/render/watchdog; document deviation); `STALE_STATE` on epoch/world/control mismatch; duplicate `(lease, action_id)` + same hash -> cached receipt, different hash -> `POLICY_DENIED`; evicted -> `RESULT_EXPIRED`.

- [ ] **Step 1: Implement freeze + step counting in the bridge**

`mcp_freeze` static: when set and mode==stepped and no action running, `_Host_Frame` (edit the guarded block in `Quake/host.c`) skips `CL_SendCmd ()` / `Host_ServerFrame ()` / `host_time +=` but still runs `MCP_Poll`, `SCR_UpdateScreen`, watchdog. `act {ticks:N}` clears freeze, counts N `_Host_Frame` passes with `!Host_FilterTime`-gated? Steps = completed `Host_ServerFrame` runs (single-player: server+client in lockstep). Reply `completed_ticks`. Wall-clock `oldtime` reset on resume (mirror `sys_unix.c` clamp: set `oldtime = newtime` via flag consumed in outer loop? Outer loop edit is platform code — instead clamp inside: after resume, first `MCP_Poll` sets a `mcp_resync` flag; `_Host_Frame` hook consumes it by zeroing `host_frametime` for one pass. Document in code comment.)

- [ ] **Step 2: Ledger + preconditions**

Bounded ring (64 entries): `{lease, action_id, hash, epoch, result}`. Check order: auth -> known-duplicate (return receipt) -> lease/seq/epoch/world/control preconditions (`STALE_STATE`) -> execute. `RESULT_EXPIRED` when seq <= high-water but evicted.

- [ ] **Step 3: Write tests**

`tests/unit/test_dedup.py`: pure-ledger tests against a `models.Ledger` class (implement ledger in `models.py`, bridge mirrors semantics): duplicate-same returns receipt; duplicate-different raises; expired raises; precondition order verified by call log.
`tests/integration/test_stepped.py`: `control {mode:stepped}`; `act {ticks:10}` -> `completed_ticks == 10`; `state` twice with no act -> same `frame` and sim time (frozen); `act` with stale `world_gen` -> `STALE_STATE`. Skip without game data.

Run:
```bash
python3 -m pytest QuakeMCP/tests/unit/test_dedup.py QuakeMCP/tests/contract -v
python3 -m pytest QuakeMCP/tests/integration/test_stepped.py -v
```
Expected: PASS (or SKIP for integration).

- [ ] **Step 4: Rebuild all + commit**

```bash
make clean && make build-release build-server build-client && make build-release QUAKE_MCP=1
git add QuakeMCP/bridge/q_mcp.c Quake/host.c QuakeMCP/src/quakemcp/models.py QuakeMCP/src/quakemcp/engine.py QuakeMCP/tests/unit/test_dedup.py QuakeMCP/tests/integration/test_stepped.py
git commit -m "feat: quakemcp stepped mode with epochs and dedup ledger"
```

---

### Task 10: Acceptance matrix, capability matrix, ledger entry

**Files:**
- Create: `QuakeMCP/docs/acceptance-results.md`
- Modify: `QuakeMCP/docs/engine-integration.md` (capability matrix appendix), `docs/superpowers/2026-08-29-quake-apple-silicon.md` (Fixes Ledger entry)
- Test: full suite + smoke protocol below

**Interfaces:**
- Consumes: Tasks 1-9.
- Produces: every spec §9 gate has a dated evidence row or an explicit FAIL with follow-up. Done only when the observe-act-observe loop plays without manual intervention and a human can reclaim control while responsive.

- [ ] **Step 1: Run the full verification set**

```bash
make clean && make build-release build-server build-client
python3 -m pytest QuakeMCP/tests/unit QuakeMCP/tests/contract -v
python3 -m pytest QuakeMCP/tests/integration -v
```
Then the 3-binary SIGKILL smoke: launch each binary, SIGKILL, count `Received signal` lines == 0. Then a 60-second autonomous loop script (start, new game, 10x observe+act, release, stop) driven through the real MCP stdio server — record wall time, frames received, actions completed.

- [ ] **Step 2: Write `acceptance-results.md`**

One row per §9 gate (integration inventory, MCP interop, gameplay input incl. simultaneous move/turn/fire + water movement, UI reachability incl. death/respawn + intermission, action/frame alignment, timing exactness, failure cleanup incl. heartbeat loss + takeover, retry/concurrency, image fidelity incl. orientation/palette/HUD/dialogs, save safety, vision usability with named model, regression incl. MCP-disabled build + `access_mouseonly` sanity + capture latency numbers). Failing rows stay FAIL with cause, never inflated.

- [ ] **Step 3: Capability matrix + ledger**

Append capability matrix to `engine-integration.md` (actions, weapons via `impulse`, UI inputs, settings, frame formats, modes, telemetry policy as measured). Append one Fixes Ledger entry to `docs/superpowers/2026-08-29-quake-apple-silicon.md` per repo convention.

- [ ] **Step 4: Commit**

```bash
git add QuakeMCP/docs/acceptance-results.md QuakeMCP/docs/engine-integration.md docs/superpowers/2026-08-29-quake-apple-silicon.md
git commit -m "docs: quakemcp acceptance results and capability matrix"
```
