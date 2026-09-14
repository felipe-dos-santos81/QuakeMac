# AGENTS.md — QuakeMCP

Subproject rules for the MCP server and the engine bridge. The repo-wide
rules in [`../AGENTS.md`](../AGENTS.md) still apply (id-era C style,
explicit-path commits, game data never committed, spec before rounds).

Scope: `Quake/` glquake only. `QuakeWorld/` and the vanilla build must stay
behaviorally identical — every engine edit is behind `#ifdef QUAKE_MCP`.

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
  before running the integration tests.
- **Worktree game data:** a git worktree has no `game/`. Link it
  (`ln -s ../../game game`) for tests, and remove the link before committing.
- **Test module names must be unique** across `unit/` and `integration/`
  (pytest import collision); the integration modules each own a fixed port
  (29876–29885) and skip without the MCP build or `game/id1/pak0.pak`.
- **Spawn contract:** `lifecycle.launch` must keep `stdin=DEVNULL` and close
  the log fd in the parent. The child inherits the stdio transport otherwise
  and steals or EOFs MCP traffic.
- **Logs and tokens never enter the repo:** engine logs go to
  `$TMPDIR/quakemcp-logs/`, the token to `$TMPDIR/quakemcp-<pid>.token`
  (`0600`), and stdout is MCP traffic — log to stderr only.
- **Hook line numbers** in `docs/engine-integration.md` are pinned to a fork
  commit; re-run the greps there after moving engine code.

## Architecture

One loopback TCP channel per call, line-JSON control ops plus a framed binary
blob for frames. The C side (`bridge/q_mcp.c` dispatch, `q_mcp_input.c` input
merge, `q_mcp_capture.c` readback) runs **on the main thread only** —
`MCP_Poll` pumps from `_Host_Frame` and the modal-dialog loop. The Python side
(`src/quakemcp/`) owns process supervision, policy, and MCP shape.

Engine hook sites (all `#ifdef QUAKE_MCP`):

| Hook | File | Role |
|---|---|---|
| `MCP_Poll` | `Quake/host.c`, `Quake/render/gl_screen.c` | Socket pump, modal loop included |
| `MCP_FreezeSim`, `MCP_NoteTick` | `Quake/host.c` | Stepped-mode gate and the completed-step counter |
| `MCP_Move`/`MCP_Buttons`/`MCP_Impulse` | `Quake/client/cl_main.c`, `cl_input.c` | Input merge in `CL_SendCmd` |
| `MCP_NoteWorldSpawn` | `Quake/server/sv_main.c` | Exact `world_gen` bump on every new world |
| `MCAP_Frame` | `Quake/render/gl_screen.c` | Framebuffer capture at end of `SCR_UpdateScreen` |

## Invariants

- **Lease:** the bridge expires a lease after 2 s of wall silence; the server
  beats every 500 ms from `Instance.keepalive`. An in-flight action defers
  expiry and refreshes on completion. `hb` validates lease/epoch — it never
  revives an unknown lease.
- **Receipts:** dedup identity is `(lease, action_id, epoch)` plus an FNV-1a
  hash of the canonical arguments. The duplicate lookup precedes every
  precondition; a consumed `action_seq` without a ring hit is
  `RESULT_EXPIRED`. An empty `action_id` is never deduplicated.
- **Stepped mode:** `MCP_FreezeSim` gates the four host-loop sim sites;
  `ticks` advances exactly N completed steps and `duration_ms` is refused.
  World-mutating ops resume realtime and restore the mode in a `finally`.
- **Physical input is sacred:** MCP merges move/button state only; never
  write `in_attack`/`in_jump`/`in_impulse`. Acquire refuses while those
  buttons are held.
- **Policy is the security boundary:** `CONSOLE_COMMANDS` + typed validators,
  `CONFIG_CVARS` + the `access_*` rules, and the name/map/slot regexes. No
  tool takes a raw command line; adding one is a design change, not a patch.
- **Claim nothing unverified:** `respawn` stays `UNSUPPORTED_CAPABILITY`, and
  `kill`'s current no-op stays recorded until the death flow is traced.

## Change recipes

- **New bridge op:** C dispatch in `q_mcp.c`, declare in `q_mcp.h`, add to
  `models.KNOWN_OPS`, wrap it in a tool in `server.py`, extend the contract
  test's expected tool list, and cover it in `tests/unit` or
  `tests/integration`.
- **New tool:** every tool raises `ValueError("<CODE>: detail")` with a code
  from `models.ERROR_CODES` (the C side mirrors the wire semantics).
  Read-only tools use `RO_TRUE` annotations, `quake_stop` is destructive.
- **Vision changes:** keep the telemetry policy in `vision.apply_telemetry`
  and the bottom-up RGB→PNG transform in `encode_frame`; state and pixels
  must share one `frame`.

## Pointers

- `docs/engine-integration.md` — hook inventory, op map, capability matrix
- `docs/acceptance-results.md` — measured gates, failures, launch config
- `../docs/superpowers/2026-09-13-quakemcp-design.md` and
  `2026-09-13-quakemcp-plan.md` — the binding design and task plan
- `../docs/superpowers/2026-08-29-quake-apple-silicon.md` — Fixes Ledger
