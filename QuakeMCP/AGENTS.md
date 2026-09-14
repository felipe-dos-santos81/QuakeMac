# AGENTS.md — QuakeMCP

Subproject rules for the MCP server and the engine bridge. The repo-wide
rules in [`../AGENTS.md`](../AGENTS.md) still apply (id-era C style,
explicit-path commits, game data never committed, spec before rounds).

Scope: `Quake/` glquake only. `QuakeWorld/` and the vanilla build stay
behaviorally identical — every hook lives behind `#ifdef QUAKE_MCP` or a
no-op macro shim.

## Commands

```
make clean && make build-release QUAKE_MCP=1                    # MCP gate
make clean && make build-release build-server build-client      # vanilla gate
python3 -m pytest QuakeMCP/tests/unit QuakeMCP/tests/contract   # no engine
python3 -m pytest QuakeMCP/tests/integration                    # real binary
Quake/build-macosx/glquake -basedir game +mcp_enabled 1         # manual smoke
```

## Traps

- **Relink staleness:** switching `QUAKE_MCP=1` on after a vanilla build does
  not relink. `make clean` between variants; leave the MCP build in place
  before integration tests.
- **Worktree game data:** a git worktree has no `game/`. Link it
  (`ln -s ../../game game`) for tests, and remove the link before committing.
- **Test basenames must be unique** across `unit/` and `integration/`
  (pytest import collision). Integration modules own fixed ports
  29876–29889 (`test_slots` 29886, `test_envelope` 29887, `test_modal`
  29888, `test_death` 29889) and skip without `game/id1/pak0.pak` (most
  also without the MCP binary).
- **Spawn contract:** `lifecycle.launch` keeps `stdin=DEVNULL` and closes
  the log fd in the parent; the child inherits the stdio transport
  otherwise and steals or EOFs MCP traffic.
- **Logs and tokens never enter the repo:** engine logs go to
  `$TMPDIR/quakemcp-logs/`, the token to `$TMPDIR/quakemcp-<pid>.token`
  (`0600`), and stdout is MCP traffic — log to stderr only.
- **Hook line numbers** in `docs/engine-integration.md` are re-verified per
  engine change (method and date in that file); re-run the greps after
  moving engine code.

## Architecture

Up to two loopback TCP connections (the call channel plus one control
channel), line-JSON control ops plus a framed binary blob for frames. The C
side (`bridge/q_mcp.c` dispatch, `q_mcp_input.c` input merge,
`q_mcp_capture.c` readback) runs **on the main thread only** — `MCP_Poll`
pumps from `_Host_Frame` and the modal-dialog loop. The Python side
(`src/quakemcp/`) owns process supervision, policy, and MCP shape.

Engine hook sites (`#ifdef QUAKE_MCP` or no-op macro shim):

| Hook | File | Role |
|---|---|---|
| `MCP_Poll` | `Quake/host.c`, `Quake/render/gl_screen.c` | Socket pump, modal loop included |
| `MCP_FreezeSim`, `MCP_NoteTick` | `Quake/host.c` | Stepped-mode gate and completed-step counter |
| `MCP_Move`/`MCP_Buttons`/`MCP_Impulse` | `Quake/client/cl_main.c`, `cl_input.c` | Input merge in `CL_SendCmd` |
| `MCP_NoteWorldSpawn` | `Quake/server/sv_main.c` | Exact `world_gen` bump on every new world |
| `MCAP_Frame` | `Quake/render/gl_screen.c` | Framebuffer capture at end of `SCR_UpdateScreen` |

## Protocol

- **Envelope:** every mutation (`act`, `exec` with text, `key`, `control
  mode`, `cvar` set) carries `lease`, `epoch`, `seq` plus optional
  `action_id`/`world_generation`/`control_revision`; reads (`ping`, `state`,
  `observe`, `tail`, `status`, `cvar` get) carry none. Request `v` must
  equal 1 (checked after auth).
- **Receipts:** dedup identity is `(op, lease, action_id, epoch)` plus an
  FNV-1a hash of the op and canonical arguments. The duplicate lookup
  precedes every precondition; a duplicate replies the stored body with
  `"duplicate":true`, a hash conflict is `POLICY_DENIED`, and a spent `seq`
  with no receipt is `RESULT_EXPIRED`. An empty `action_id` is never
  deduplicated. The server's bounded receipt LRU holds the delivered
  observation; a retry whose entry was evicted raises `FRAME_EXPIRED` and
  is never re-shot.
- **Two slots:** deferred `act`/`observe` replies return on the requesting
  connection; a third connection is closed. EOF on the deferred owner
  finishes the act (or cancels the observe).
- **Reads:** `tail` returns the console ring (the old `exec text=""` idiom
  stays a read); `status {action_id}` reads the receipt ledger, so
  completion stays answerable after the retry frame is gone.
- **Modal:** a key that raises a menu confirmation replies `needs_input`
  with the dialog frame; release or lease loss injects the escape a human
  would press and denies the receipt.
- **Stepped:** `ticks` advances exactly N completed steps; `duration_ms` is
  refused in stepped mode. World-mutating ops resume realtime and restore
  the mode in a `finally`; gameplay-class console commands run a 250 ms
  realtime flush so forwarded commands land.

## Invariants

- **Lease:** the bridge expires a lease after 2 s of wall silence; the
  server beats every 500 ms. Beats land mid-act on the control connection,
  and expiry interrupts a deferred act at 2 s instead of its 5 s cap. `hb`
  validates lease/epoch — it never revives an unknown lease.
- **Physical input is sacred:** MCP merges move/button state only; never
  write `in_attack`/`in_jump`/`in_impulse`. Acquire refuses while those
  buttons are held.
- **Policy is the security boundary:** `CONSOLE_COMMANDS` + typed
  validators, `CONFIG_CVARS` + the `access_*` rules, and the name/map/slot
  regexes. No tool takes a raw command line; adding one is a design change.
- **Gameplay gate:** `act` requires a ready local game unless its `respawn`
  field is set; gameplay-class console commands are gated except `pause 0`.
- **Claim nothing unverified:** capability claims name their measurement.
  The death flow is traced — `kill` restarts the level through the shipped
  progs and `quake_game respawn` requires `state.dead`; intermission is
  unreachable from the tool surface and is recorded as such, never faked
  (`docs/engine-integration.md`).

## Change recipes

- **New bridge op:** dispatch in `q_mcp.c`, declare in `q_mcp.h`, add to
  `models.KNOWN_OPS`, wrap it in a tool in `server.py`, extend the contract
  test's expected tool list, cover it in `tests/unit` or
  `tests/integration`.
- **New tool:** every tool raises `ValueError("<CODE>: detail")` with a code
  from `models.ERROR_CODES` (the C side mirrors the wire semantics);
  read-only tools use `RO_TRUE` annotations, `quake_stop` is destructive.
- **Vision changes:** keep the telemetry policy in
  `vision.apply_telemetry` and the bottom-up RGB→PNG transform in
  `encode_frame`; state and pixels must share one `frame`.

## Pointers

- `docs/engine-integration.md` — hook inventory, op map, capability matrix
- `docs/acceptance-results.md` — measured gates, failures, launch config
- `../docs/superpowers/2026-09-14-quakemcp-conformance-design.md` and its
  plan — current design (refines `2026-09-13-quakemcp-design.md`)
- `../docs/superpowers/2026-08-29-quake-apple-silicon.md` — Fixes Ledger
