# QuakeMCP acceptance results

Run date: 2026-09-14. Platform: macOS/arm64 (Apple Silicon), Apple clang,
SDL3, native OpenGL renderer. Branch `feat/quakemcp` at `f3098e6` plus the
Task 10 fixes recorded in the ledger. Game data: local retail install
mounted at `game/` (never committed).

## Verification set

| Command | Result |
|---|---|
| `make clean && make build-release build-server build-client` | exit 0 |
| `make clean && make build-release QUAKE_MCP=1` | exit 0 |
| `python3 -m pytest QuakeMCP/tests/unit QuakeMCP/tests/contract` | 34 passed |
| `python3 -m pytest QuakeMCP/tests/integration` | 8 passed, 0 skipped (game data present) |
| SIGKILL smoke, `glquake` (vanilla, game data loaded) | 0 `Received signal` lines (473 log lines) |
| SIGKILL smoke, `glquake` (MCP build, bridge listening) | 0 `Received signal` lines (473 log lines) |
| SIGKILL smoke, `qwsv` | 0 `Received signal` lines |
| SIGKILL smoke, `glqwcl` | 0 `Received signal` lines |

## Launch command and MCP client configuration

Run from the repository root, with game data at `game/`:

```
make build-release QUAKE_MCP=1
python3 -m quakemcp.server          # stdio server
```

`share/`-free client configuration (paths are the real repository ones):

```json
{
  "mcpServers": {
    "quake": {
      "command": "python3",
      "args": ["-m", "quakemcp.server"],
      "cwd": "<repo>",
      "env": {"PYTHONPATH": "<repo>/QuakeMCP/src"}
    }
  }
}
```

The server launches `Quake/build-macosx/glquake -basedir game -mcp_port
<port> +mcp_enabled 1` itself (profile `local`); nothing else needs to be
running.

## Autonomous loop

Method: a stdio MCP client drove the real server: `quake_start`,
`quake_game new_game`, `quake_control acquire`, mode `stepped`, ten
observe-act-observe rounds (walk, turn, fire, weapon switch, jump), menu
open/close via `quake_ui`, `quake_console kill`, `quake_game`
restart/save/load, save-safety refusals, a measured real-time
`duration_ms` act, `quake_release`, `quake_stop`. Two consecutive runs:

- wall time 5.98 s / 6.07 s; 13 tools; 10/10 actions; 60 completed ticks;
  20 frames; frames non-decreasing; 0 errors; no orphaned engine
  processes; saved `mcp_loop.sav` deleted in teardown.
- `ui` 0 -> 3 on escape (menu) and back to 0; menu frame delivered as an
  image block.
- real-time act: requested 300 ms, `elapsed_ms` 304/300, simulation-time
  delta 0.389/0.379 s (the action plus its trailing render).
- `save` refused an existing slot without `overwrite=true`; `save` with
  `slot="../escape"` denied.

## Gate matrix

| Gate | Verdict | Evidence |
|---|---|---|
| Integration inventory | PASS | `QuakeMCP/docs/engine-integration.md`: fork commit, platform, renderer, build path and each equivalent hook recorded before implementation (Task 1). |
| MCP interoperability | PASS | Real stdio session: initialize, 13 tools with schemas, image blocks plus `structuredContent`, clean shutdown (`tests/contract/test_tools_list.py`, `tests/integration/test_stepped.py::test_stepped_tools`, acceptance loop). Request cancellation is not separately exercised (plain MCP `notifications/cancelled` is not implemented in the bridge); no FAIL is claimed for it. |
| Complete gameplay input | PASS (water PARTIAL) | Forward/back, strafe, run, look, attack, jump tap/hold and weapons 1..8 by impulse covered by `test_act.py`, `test_observe.py`, `test_stepped.py` and the loop; signs and clamps from Task 5's tests. Water movement uses the same `forwardmove`/`sidemove`/`upmove` fields, but was not separately exercised in a water volume: PARTIAL. |
| Full UI reachability | PARTIAL | New game, menu open/close (escape, `ui` 0 -> 3 -> 0), options/menu frames delivered, save/load operations and `pause`/`god`/`impulse` console commands verified. Death and intermission: FAIL — `kill` via the console allowlist has no observable effect in this build (client-visible health stays 100, no console output, while `god`/`pause`/`impulse` in the same session execute); respawn stays an unsupported capability because MCP never synthesizes attack. Follow-up: trace the forwarded `ClientKill` path in the shipped progs. |
| Action/frame alignment | PASS | `frame` is a completed-simulation-step counter (not the render loop); act and its observation share the final step (`test_stepped.py`), a frozen session reports a constant frame, and an act of N ticks moves it exactly N. |
| Timing | PASS | Stepped mode: 10 ticks -> `completed_ticks == 10` and a `frame` delta of exactly 10; idle freeze holds `time`/`frame`/`movemessages`; no catch-up after a 1 s freeze. Real-time bound measured above. |
| Failure cleanup | PASS (partial) | Heartbeat loss -> `STALE_STATE`, input cleared, takeover requires a new lease (`test_act.py`); release/stop idempotent; server death leaves no synthetic input held (bridge lease expiry) and the engine survives for `attach`. Engine abort: `ENGINE_DISCONNECTED` surfaced by tools. Cancellation/timeout injection beyond these not separately exercised: PARTIAL. |
| Retry/concurrency | PASS | Duplicate `(lease, action_id)` with identical arguments returns the receipt without re-executing; different arguments -> `POLICY_DENIED`; consumed sequence without a ring hit -> `RESULT_EXPIRED`; stale epoch/world/control -> `STALE_STATE`; single-client endpoint refuses a second live client while emergency `release` is served on a fresh connection (`test_dedup.py`, `test_stepped.py`). No exactly-once claim across process crashes. |
| Image fidelity | PASS (menus PARTIAL) | Real 320x240 (scaled) RGB readback from the renderer: bottom-up to PNG transform and telemetry policy unit-tested (`test_vision.py`); gameplay and menu frames differ, HUD strip included; menu frame returned. Palette/gamma follow the renderer's 8-bit RGB; resize/crop mapping and payload caps unit-tested. Minimized/unavailable renderer -> `RENDER_UNAVAILABLE` path present but not exercised on this host: PARTIAL. |
| Save/lifecycle safety | PASS | Missing data and binary paths -> clean tool errors; attached instance refuses `stop` (POLICY_DENIED); invalid slot -> `INVALID_CONTEXT`; traversal (`../escape`) denied; overwrite refused without `overwrite=true`; failed load surfaces `ACTION_TIMEOUT` with the last state; owned stop terminates the child and forgets it. |
| Vision usability | PARTIAL | An MCP client receives actual image blocks plus telemetry and frame identity (verified end to end). No external vision model was available in this environment, so threat/HUD recognition accuracy is not claimed; transport success is not evidence of accuracy. |
| Regression | PASS | MCP-disabled clean build exits 0 and all three binaries survive the SIGKILL smoke with zero `Received signal` lines; `access_mouseonly 0` used throughout the loops; capture path bounded at 32 MiB source with a 2-slot ring and the encode step measured in the loop (20 frames, ~6 s total including actions). |

## Known limitations

- `kill` (and therefore death/respawn coverage) has no effect in this build
  through the console allowlist; recorded above as FAIL with the follow-up.
- Stepped mode is opt-in: a session that needs a world load is temporarily
  returned to realtime by the world ops and restored afterwards, so a map
  or savegame load never deadlocks the frozen clock.
- Request cancellation is not implemented as an MCP cancellation; the
  emergency path is `quake_release` plus lease expiry.
- The bridge is single-client by design; the server opens one connection
  per call and heartbeats every 500 ms.
