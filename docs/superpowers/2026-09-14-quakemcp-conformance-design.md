# QuakeMCP conformance round: lease enforcement, observation integrity and dialogs

**Date:** 2026-09-14
**Status:** Proposed; refines `2026-09-13-quakemcp-design.md`, implements review findings
**Basis:** two-axis review of `0c8a6fc...7581bc3` (Standards 10 findings, Spec 11
findings), each re-verified against the code and the pinned MCP SDK.

## 1. Scope

Fix every review finding that is fixable in this repository, in one round:

- uniform lease/precondition enforcement for every mutation, not just `act`;
- stepped-by-default for owned sessions;
- `quake_status` capabilities and action query;
- act effective view deltas and weapon reporting;
- receipt-based retry frames and `FRAME_EXPIRED`;
- the modal `needs_input` contract;
- MCP request cancellation;
- death/respawn and intermission verification;
- hygiene and documentary truth.

**Excluded:** exercising vision with a named model — no vision model is available
in this environment. Acceptance keeps that gate recorded as environment-blocked
with cause.

**Not in scope:** new tools, new transports, server-side perception providers,
QuakeWorld.

Two items surfaced while writing this specification (neither was a review
finding); both are load-bearing for cancellation and are included:

1. the endpoint serves one client, so `release`/heartbeat cannot be serviced
   while an action reply is deferred — §3.4 bounds this with concurrent
   request slots;
2. `exec` doubles as the console-tail read — replaced by a read-only `tail` op
   so `exec` can be uniformly lease-protected (§3.1).

## 2. Decisions

| Decision | Choice |
|---|---|
| Scope | one round, all code-fixable findings |
| Lease envelope | uniform wire fields, one shared bridge helper, lazily acquired, server-assigned sequence |
| Stepped default | `quake_start` sets stepped; a held-button start falls back to realtime and says so |
| Retry frames | receipt-based retention; evicted entry -> `FRAME_EXPIRED` |
| Cancellation | async tools + SDK cancellation -> best-effort emergency release |
| Vision gate | environment-blocked; recorded, not fixed |

## 3. Protocol and lease enforcement

### 3.1 Wire classes

Protocol stays v1; all additions are additive. A request's `v` must equal 1,
checked after auth, else `UNSUPPORTED_CAPABILITY`.

| Class | Ops | Envelope |
|---|---|---|
| Mutation | `act`, `exec`, `key`, `control mode`, `cvar` with `value` | `lease`, `epoch`, `seq`, optional `action_id`, `world_generation`, `control_revision` |
| Read | `ping`, `state`, `observe`, new `tail`, `cvar` without `value` | none |
| Lifecycle | `control acquire/release/detach`, `release` | own validated rules, unchanged |

New ops:

- `tail` — return the last console lines. Read-only; replaces the
  `exec text=""` idiom so `exec` is always a mutation.
- `status {action_id?}` — read-only receipt lookup in the bridge ledger;
  without an id, report the most recent receipt. Backs `quake_status`'s
  action query and survives MCP-server restarts.

### 3.2 Bridge enforcement

- One `MCP_CheckPreconditions()` used by every mutation: live lease, lease id,
  epoch, then optional world/control preconditions. Reads never call it.
- One receipt path used by every mutation. A duplicate is looked up before any
  precondition (design §6 order); the canonical hash includes the op name and
  its arguments; the stored receipt is the reply's `result` JSON; dedup hits
  reply with `"duplicate":true` so Python can never mistake a receipt for a
  fresh execution. `seq <= high-water` without a surviving receipt remains
  `RESULT_EXPIRED`. Empty `action_id` is never deduplicated.
- Deferred replies (`act`, `observe`) keep their current framing. `act` stores
  its receipt at completion, as today.

### 3.3 Python enforcement

One `_mutate(inst, op, action_id=..., lease=..., action_seq=..., epoch=...,
world_generation=..., control_revision=..., **args)` used by every mutating
tool:

- lazily acquires a lease when the caller supplied none, registering the
  keepalive beat;
- assigns `seq` from `Instance.next_seq` when the caller omitted it (explicit
  values are honored, so a caller-managed sequence still works);
- uses `send_retrying` whenever `action_id` is set.

Reads bypass `_mutate` and stay responsive.

### 3.4 Concurrent request slots (amends the plan's "single client")

The endpoint accepts and drains a small bounded set of concurrent request
connections (proposed 4, each with its own line buffer; extra connections are
closed as today). Rationale: design §6 requires emergency release to be
serviced within ~100 ms while the engine is responsive, and cancellation must
cancel a running action — both are impossible when the in-flight request owns
the only connection. Bounded slots also let heartbeats land during deferred
replies and long polls.

Consequences:

- `MCP_CheckLease` no longer early-returns during an action; the 2 s expiry is
  uniform, and a dead controller's action is interrupted at expiry instead of
  at the 5 s cap.
- The server's 500 ms keepalive keeps a legitimate action's lease alive;
  `hb` never fails merely because a reply is deferred.
- Mutations stay serialized by the lease, not by the socket model.
- Connection count is a security-relevant surface: loopback bind, token auth
  per connection, a fixed cap, and a fixed line-size bound per slot.

## 4. Tool surface

Mutating tools (`quake_console`, `quake_config` set, `quake_ui`,
`quake_game`, `quake_control` mode) accept the same optional flat envelope
parameters as `quake_act`: `action_id`, `lease`, `action_seq`, `epoch`,
`world_generation`, `control_revision`. Omitting them is the normal case.

`quake_status` becomes the §4 status report:

- connection/instance: `bridge_ready`, `gameplay_ready`, instance, pid, owned,
  epoch, world generation and control revision;
- controller: lease held, execution mode; UI context, loading/dead/intermission;
- capabilities: a static manifest from `models.py` (tools, action axes,
  weapons, UI keys and contexts, settings, frame formats and resize/crop,
  execution modes, telemetry policy, console commands, game operations);
- action: `quake_status(action_id=...)` queries the bridge ledger, or the
  latest receipt when omitted.

Policy corrections:

- `save` and `load` leave the console allowlist; `quake_game` owns them and
  its overwrite guard, closing the bypass.
- `connect` and `disconnect` leave the allowlist (design §8 forbids remote
  connection commands).
- `_console_arg`'s validator switch folds into the `CONSOLE_COMMANDS` entry so
  a command's arity and validator are declared once.
- Entries gain a class: `client` (status, version, screenshot, toggleconsole)
  and `gameplay` (skill, god, noclip, give, impulse, kill, pause). Gameplay
  mutations and `quake_act` require `gameplay_ready` (`NOT_READY` otherwise);
  the one exception is `pause 0` (resume), which is always allowed. UI,
  config, and `quake_game` remain available at menus.
- `quake_state` and `quake_status` use the existing `_bridge_ok` helper
  instead of hand-rolled round trips.

`quake_start` sets stepped mode before returning, so an owned session starts
frozen (design §6). If physical buttons are held at that instant
(`CONTROL_BUSY`), the session stays realtime and the start result reports
`mode: "realtime"` with the reason. `quake_attach` changes nothing.

## 5. Observation, receipts and retries

### 5.1 act reporting

`quake_act` results gain:

- `yaw_applied_deg` / `pitch_applied_deg` — the effective, post-clamp deltas
  actually applied (the +80/-70 pitch clamp in `q_mcp_input.c`);
- `weapon_requested` — the impulse-derived identifier the caller asked for;
- `weapon_active` — the engine truth read from `STAT_ACTIVEWEAPON` at
  completion. An unavailable weapon shows as a mismatch, never as selected.

### 5.2 Receipt retention and `FRAME_EXPIRED`

A per-instance bounded LRU (proposed 16 entries / 8 MiB, evict-oldest) keyed by
`action_id` stores the delivered observation: encoded image bytes, structured
content and frame id.

- Bridge reply without `duplicate` (fresh execution): observe, store, return.
- Bridge reply with `duplicate:true` and a cache hit: return the cached
  observation verbatim — one action id always denotes one observation.
- Bridge reply with `duplicate:true` and no cache entry (evicted, or executed
  before an MCP-server restart): raise `FRAME_EXPIRED`; never re-shoot and
  never re-execute. Completion metadata remains answerable through
  `quake_status(action_id)` because the bridge ledger is authoritative.

Reads (`quake_observe`) keep today's `after_frame` semantics.

## 6. Dialog contract (`needs_input`)

The only reachable modal is the menu's new-game confirmation
(`Quake/client/menu.c:435` -> `SCR_ModalMessage`), whose wait loop already
pumps `MCP_Poll`.

- A bridge flag is set around `Key_Event` dispatch of a `key` op. If
  `SCR_ModalMessage` is entered on that path, the bridge records the modal and
  replies `needs_input` (modal text, original `action_id` pending) instead of
  letting the tool call block; the triggering op is marked already-replied.
- Python returns modal text plus the modal frame. The modal wait loop gains
  one hook: when a capture is pending, render once (`SCR_UpdateScreen`) so the
  observe request captures the dialog.
- The client answers with a normal `quake_ui` key (`y`, `n`, or escape). The
  modal exits, `menu.c` proceeds or returns, and the bridge records the
  original action's completion or denial; `quake_status(action_id)` tracks
  `needs_input -> completed|denied`.
- Safety: `release`, lease expiry, or heartbeat loss while a modal waits
  injects escape through `Key_Event` and marks the receipt interrupted. A
  human-triggered modal (bridge flag clear) blocks exactly as today.

## 7. Death flow and intermission

Review finding: `kill` had no observable effect. Verified mechanism:
`Host_Kill_f` forwards the command through `Cmd_ForwardToServer`
(`Quake/host_cmd.c:1194-1197`), and forwarded client commands reach the server
only during simulation steps — so in a frozen stepped session the command
queues indefinitely. Server commands execute directly in this in-process
server, while client commands travel in the client message — consistent with
`_world_op` already having to resume realtime for map/restart sign-on.

- Reproduce `kill` in realtime and stepped; record both.
- Classify allowlisted commands by whether they forward through the client
  message path (`kill`, `give`, `impulse`, server-visible cvars). Those run
  with the clock live for a bounded flush window (proposed: up to 250 ms
  realtime), then the session's mode is restored — the same pattern
  `_world_op` already uses.
- With the mechanism verified, implement `quake_game respawn` as a bounded
  input action (attack tap) that waits for health/death-state recovery, and
  record its evidence. If the observed mechanism differs, implement what is
  observed, not what is assumed.
- Reach intermission via `map end` (or the observed equivalent) and record the
  result. Unreachable paths stay FAIL with cause, never inflated.

## 8. Hygiene and documentary truth

- Revert the two whitespace-only hunks in `Quake/host.c` (trailing tabs).
- Re-sync `QuakeMCP/docs/engine-integration.md` line numbers and replace the
  stale "re-verified" claim with the date and method actually used. Its
  `FRAME_EXPIRED` claim becomes true in this round.
- Reword the `#ifdef` claim in `QuakeMCP/README.md` and
  `QuakeMCP/AGENTS.md`: engine hook call sites are guarded by `#ifdef` or
  compile to no-ops through macro shims (`#else #define MCP_FreezeSim() 0`);
  vanilla behavior is unchanged. Say that, not "every edit is behind an
  `#ifdef`".
- List real commit hashes in the existing Fixes Ledger entry (replace "and the
  Task 10 fixes").
- Small code hygiene: drop `_wait_token`'s unused `pid`; close the engine log
  once per `launch` path; unify `mcap_slot_t`/`mcap_snapshot_t` field
  duplication; collapse `MCP_CheckAction`'s repeated cap comparison.

## 9. Tests and acceptance

Unit (no engine):

- envelope parsing, lazy acquisition, sequence assignment, explicit-sequence
  pass-through;
- receipt cache: duplicate hit returns identical bytes; evicted id raises
  `FRAME_EXPIRED`;
- policy: `save`/`load`/`connect`/`disconnect` denied; gameplay commands
  rejected before `gameplay_ready`;
- cancellation: a cancelled tool call issues an emergency release (fake
  bridge records it) and re-raises.

Contract:

- the 13-tool list is unchanged and mutating tools expose the envelope
  parameters;
- `needs_input` result shape is stable.

Integration (skip without `game/id1/pak0.pak`):

- `exec` lazily acquires a lease; a stale lease is `STALE_STATE`; a repeated
  `exec` with an `action_id` returns `"duplicate":true`;
- `quake_start` yields a stepped session (`state.mode == "stepped"`);
- `quake_status` reports capabilities and answers an action query;
- modal flow: menu -> `quake_ui(enter)` returns `needs_input` plus a dialog
  frame; answering escape leaves no stuck modal; the receipt closes as denied;
- `kill` in realtime kills; the stepped flush fix makes it work in stepped;
- act reports effective clamped deltas and `weapon_active`;
- retrying `quake_act` after eviction raises `FRAME_EXPIRED`.

Existing gates stay: vanilla and MCP builds both exit 0; the 3-binary SIGKILL
smoke shows zero "Received signal"; integration test basenames stay unique.

## 10. Delivery order and definition of done

1. Bridge protocol: envelope + shared preconditions + receipts (`duplicate`),
   `tail`, `status`, version check, concurrent slots, uniform lease expiry.
2. Python `_mutate` + envelope params + async tool conversion + cancellation.
3. Tool surface: status/capabilities, stepped default, policy corrections,
   gameplay gating.
4. Observation: receipt LRU, `FRAME_EXPIRED`, act effective/weapon reporting.
5. Modal `needs_input`.
6. Death flow, respawn, intermission evidence.
7. Hygiene, doc truth, ledger entry, acceptance-matrix update.

Done when both build gates and the full test suite pass, the acceptance matrix
records the new evidence (cancellation, death/respawn, intermission,
`FRAME_EXPIRED`, capabilities), every review finding is either fixed or
recorded with cause, and the Fixes Ledger entry lists the round's commits.

## Appendix A: review findings traceability

| Finding | Treatment |
|---|---|
| `#ifdef` claim overbroad | §8 doc reword |
| whitespace-only `host.c` hunks | §8 revert |
| stale `engine-integration.md` line numbers | §8 re-sync |
| ledger entry missing hashes | §8 |
| duplicated `_bridge` round trips | §4 reuse helper |
| duplicated lease/epoch checks (`hb`/`act`) | §3.2 shared helper |
| `mcap_slot_t`/`mcap_snapshot_t` clump | §8 |
| `_wait_token` unused `pid`; double `log.close()` | §8 |
| validator table duplication | §4 fold into `CONSOLE_COMMANDS` |
| `MCP_CheckAction` repeated cap test | §8 |
| `quake_status` thin | §4 |
| stepped opt-in vs default | §2, §4 |
| `needs_input` missing | §6 |
| `FRAME_EXPIRED` never emitted | §5.2 |
| effective/weapon reporting | §5.1 |
| wire version never read | §3.1 |
| gates: cancellation | §3.3, §3.4 |
| gates: death/respawn/intermission | §7 |
| gates: vision with a named model | excluded, environment-blocked |
| `connect`/`disconnect` scope creep | §4 |
| lease bypass on non-act mutations | §3 |
| `save` overwrite bypass via console | §4 |
| lease expiry during actions | §3.4 |
| `gameplay_ready` never enforced | §4 |
| `exec` doubling as tail read (new) | §3.1 |
| release/heartbeat during deferred replies (new) | §3.4 |
