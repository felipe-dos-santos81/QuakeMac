# QuakeMCP acceptance results

Run date: 2026-09-14. Platform: macOS/arm64 (Apple Silicon), Apple clang,
SDL3, native OpenGL renderer. Branch `fix/quakemcp-conformance`, accepted
at the round tip `48120a4` plus the acceptance commit recorded in the
Fixes Ledger. Game data: local retail install mounted at `game/` (never
committed); every integration module ran against the real engine, none
skipped.

## Verification set

| Command | Result |
|---|---|
| `make clean && make build-release build-server build-client` | exit 0 |
| `python3 -m pytest QuakeMCP/tests/unit QuakeMCP/tests/contract -v` | 58 passed (38 unit + 20 contract) |
| `make clean && make build-release QUAKE_MCP=1` | exit 0 |
| `python3 -m pytest QuakeMCP/tests/integration -v` | 18 passed, 0 skipped |
| SIGKILL smoke, `glquake` (vanilla, game data loaded) | 0 `Received signal` lines (488 log lines) |
| SIGKILL smoke, `qwsv` (vanilla) | 0 `Received signal` lines (24 log lines) |
| SIGKILL smoke, `glqwcl` (vanilla) | 0 `Received signal` lines (129 log lines) |
| Autonomous loop through the real stdio server | PASS: 13 tools; frame 32 -> 44 over a 12-tick act; receipt `done`; no orphaned engines |

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

Method: a stdio MCP client launched the real server process
(`python3 -m quakemcp.server` over the SDK stdio transport, not the
in-memory test helper) and drove one end-to-end session:
`quake_start` (stepped), `quake_game new_game`, `quake_observe`, a
12-tick forward `quake_act` with `action_id loop-act-1`, a second
`quake_observe(after_frame)`, `quake_status(action_id)`, `quake_release`,
`quake_stop`. Measured transcript highlights:

- 13 tools listed; `quake_start` -> `mode: "stepped"`, `bridge_ready: true`.
- `new_game` -> `map: "start"`, `gameplay_ready: true`.
- `observe` -> `frame: 32`, one PNG image block (734,216 base64 chars).
- `act` -> `completed_ticks: 12`, `interrupted: false`, `frame: 44`;
  `observe(after_frame=32)` -> `frame: 44` (the frame advanced exactly
  with the act).
- `status` -> `bridge_ready: true`, `gameplay_ready: true`,
  `lease_held: true`, capability groups `actions, console, frames, game,
  modes, settings, telemetry, tools, ui`; the `loop-act-1` receipt
  reports `state: "done"`.
- `release` -> `released: true`; `stop` -> `stopped: true`; orphan check
  before/after: no engine processes left behind.
- Engine session wall time 0.8 s; whole run 2.4 s, zero errors.

## Gate matrix

| Gate | Verdict | Evidence |
|---|---|---|
| Integration inventory | PASS | `QuakeMCP/docs/engine-integration.md` hook table, op map and capability matrix re-verified against this tip on 2026-09-14 (Task 8). |
| MCP interoperability | PASS | Real stdio session: initialize, 13 tools with schemas, image blocks plus `structuredContent`, clean shutdown (`contract/test_tools_list.py`, autonomous loop). Wire v1 enforced after auth: `test_envelope.py::test_protocol_version_gate` (`v: 2` -> `UNSUPPORTED_CAPABILITY`). Two authenticated connection slots; a third is closed (`test_slots.py`). |
| Complete gameplay input | PASS (water PARTIAL) | Forward/back, strafe, run, look, attack, jump tap/hold and weapons by impulse covered by `test_act.py`, `test_observe.py`, `test_stepped.py` and the loop; effective post-clamp view deltas and requested-vs-active weapon asserted in `test_act.py`. Water movement uses the same fields but was not separately exercised in a water volume: PARTIAL. |
| Lease enforcement, uniform expiry | PASS | `test_envelope.py::test_exec_without_lease_is_stale` (no-lease `exec` -> `STALE_STATE`); `test_act.py::test_bridge_act` (act after 2.6 s silence -> `STALE_STATE`); `test_slots.py::test_lease_expiry_after_silence` (`hb` on an expired lease -> `STALE_STATE`, never revives). Expiry is uniform mid-act: in `test_slots.py::test_control_connection_during_deferred_act` a heartbeat lands while a 72-tick act reply is deferred, and `release` interrupts that act at once (`interrupted: true`). |
| Two-connection responsiveness | PASS | `test_slots.py::test_control_connection_during_deferred_act`: `state`, `hb` and `release` answered on the control connection while the act reply is deferred on the call connection. `test_slots.py::test_eof_mid_act_finishes_action`: EOF on the requesting connection finishes the act, records the receipt, and a retry 2.7 s later (after lease expiry) answers from the ledger. |
| Receipt retry + `FRAME_EXPIRED` | PASS | `test_envelope.py::test_receipts_dedupe_and_conflict`: repeated `(lease, action_id, args)` -> `duplicate: true` without re-execution; same id with different arguments -> `POLICY_DENIED`; spent `seq` with no surviving receipt -> `RESULT_EXPIRED`. `test_receipts.py` (unit): replay is byte-identical, eviction is oldest-first under both limits, a consumed duplicate whose frame was evicted -> `FRAME_EXPIRED`, never re-shot. `test_modal.py` adds an idempotent duplicate retry of a pending `needs_input`. |
| Modal `needs_input` | PASS | `test_modal.py::test_modal_needs_input`: menu `enter` opens `SCR_ModalMessage` and returns `needs_input` with `modal_text` plus the dialog image (the wait loop pumps `MCP_Poll`); a duplicate retry replays the same body; answering escape flips the receipt to `denied` and leaves `ui == 3` (no stuck modal); a second dialog closed by `release` is denied the same way. |
| Cancellation | PASS (server layer) | `test_cancel.py::test_cancelled_tool_sends_emergency_release`: cancelling the surrounding anyio scope sends the `release` op and leaves the instance clean (`lease == ""`, `epoch == 0`, `next_seq == 0`, heartbeat stopped). The emergency path synthesizes no gameplay input: it is a release, and `MCP_ClearControl` clears the bridge's input merge state (`MCP_EndInput`) and marks a pending modal denied; the only synthetic key event is the modal-closing escape (`q_mcp.c`). No integration test drives a raw stdio cancel notification into a deferred act on this host. |
| Death/respawn (level restart) | PASS | `test_death.py::test_death_flow_tools`: a stepped `quake_console kill` is forwarded during the bounded realtime flush, `world_gen` advances by exactly 1, the mode returns to stepped, and the player is alive again — the shipped single-player progs answer `kill` with `localcmd("restart")` (level restart), so no standing corpse exists. On `end`, the Rotfish leaves a stable dead state; `quake_game respawn` then returns `respawned: true` with `waited_ms` in bounds and `dead: false`, `health > 0`, mode stepped. |
| Intermission | FAIL (cause recorded) | `test_death.py::test_death_flow_tools` step (c) asserts the observed negative: `load_map end` leaves `intermission == false`. `svc_intermission` is written only by the progs' `execute_changelevel` (a level-exit path) and no console/tool surface reaches it; never faked true (`docs/engine-integration.md`, design §7). |
| Stepped default | PASS | `test_lifecycle.py::test_lifecycle_tools`: `quake_start` returns `mode: "stepped"` and the engine's own `state.mode` agrees; the autonomous loop reproduces it. `test_stepped.py` pins exact tick/frame accounting and the no-catch-up freeze. A held physical attack/jump button refuses the acquire (`CONTROL_BUSY`) and leaves the session realtime with a `reason` (start-tool path; not exercised with a held button on this host). |
| Capabilities/status | PASS | `contract/test_policy.py::test_status_reports_capabilities`, `test_status_action_query`, `test_status_returns_receipt`; `test_envelope.py::test_status_reports_receipts` (raw `status`: newest, named, and unknown-id -> `INVALID_CONTEXT`); the loop reports the capability groups and the `done` receipt for a named action. |
| Image transport and frame identity | PASS | `test_observe.py`, `test_act.py` and `test_modal.py` receive real image blocks whose structured `frame` matches the state snapshot; the loop's PNG block above; `test_vision.py` (unit) pins the bottom-up readback transform, resize/crop mapping and telemetry policy. |
| Save/lifecycle safety | PASS | `test_lifecycle.py::test_lifecycle_tools`: save/list/load round trip, owned `stop`, second `stop` -> `ENGINE_DISCONNECTED`; `contract/test_policy.py`: missing-slot/traversal/overwrite guards, attached-instance stop refusal, console allowlist removals (`save`, `load`, `connect`, `disconnect`). |
| Vision with a model | ENVIRONMENT-BLOCKED | Transport is verified (image blocks plus telemetry above), but no external vision model exists in this environment, so threat/HUD recognition accuracy is not claimed; transport success is not evidence of accuracy. |
| Regression | PASS | The vanilla gate exits 0 and each vanilla binary survives the SIGKILL smoke with 0 `Received signal` lines; `QuakeWorld/` is untouched by the round; all 18 integration tests ran (0 skipped) and the 58 unit/contract tests pass. |

## Known limitations

- Intermission remains unreachable from the tool surface (recorded above as
  FAIL with cause); no claim of intermission coverage is made.
- Stepped sessions return to realtime for world-mutating ops (map or
  savegame load) and restore the mode afterwards, so a world load never
  deadlocks the frozen clock.
- Cancellation is verified at the server layer with a fake bridge; the
  bridge contracts are exercised directly, but no integration test injects
  a raw stdio cancellation notification into a deferred act.
- The bridge serves two authenticated connections (call plus control); a
  third is closed. Mutations stay serialized by the lease, not by the
  socket model.
