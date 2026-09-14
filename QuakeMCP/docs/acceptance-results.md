# QuakeMCP acceptance results

Run date: 2026-09-14. Platform: macOS/arm64 (Apple Silicon), Apple clang,
SDL3, native OpenGL renderer. Branch `fix/quakemcp-review-findings`,
measured at the round tip `d8aa7a6` (Tasks 1–13); the plan-corrections and
acceptance+ledger commits sit on top, and the physical-takeover
observations await human execution (protocol below). Game data: local
retail install mounted at `game/` (never committed); every integration
module ran against the real engine, none skipped.

## Verification set

| Command | Result |
|---|---|
| `make clean && make build-release build-server build-client` | exit 0 (re-run after the MCP suite so the smoke binaries are vanilla) |
| `python3 -m pytest QuakeMCP/tests/unit QuakeMCP/tests/contract -v` | 65 passed (45 unit + 20 contract) |
| `make clean && make build-release QUAKE_MCP=1` | exit 0 |
| `python3 -m pytest QuakeMCP/tests/integration -v` | 22 passed, 0 skipped |
| SIGKILL smoke, `glquake` (vanilla, game data loaded) | 0 `Received signal` lines (391 log lines) |
| SIGKILL smoke, `qwsv` (vanilla) | 0 `Received signal` lines (24 log lines) |
| SIGKILL smoke, `glqwcl` (vanilla) | 0 `Received signal` lines (110 log lines) |

The conformance round ended at 59 unit/contract and 19 integration. This
round adds six unit tests — `test_lease.py`: the shared `clear_lease`
reset and three heartbeat-loss tests; `test_models.py`: the state-schema
consistency test; `test_vision.py`: the error-code prefix test — and
three integration tests (`test_envelope.py`: `exec` text required, parser
key impersonation, escaped quotes). The contract suite grew by assertions
inside an existing test (`ui.keys` equality in
`test_status_reports_capabilities`), not new test functions.

The SIGKILL smoke used the standing protocol: launch all three binaries
against `game/` in one pass, SIGKILL after 5 s, and count `Received
signal` lines per log (0 means the abort was clean).

## Manual takeover protocol (physical stop control)

A real window click cannot be injected by this repo's harness, so this
sequence is recorded for a human to run; the observed-result fields stay
`manual — awaiting human execution` until the follow-up run records them.

1. `make build-release QUAKE_MCP=1`; launch
   `Quake/build-macosx/glquake -basedir game -mcp_port 29890
   +mcp_enabled 1` (port 29890 is free: the test modules own 29876–29889
   and nothing else runs here).
2. Connect with the stdio server (or a raw socket), `control acquire`,
   start an `act` with `ticks=72` so the reply is deferred.
3. Confirm the top-center banner `MCP CONTROL - LEFT-CLICK TO STOP` is
   visible in the window while the lease is held.
4. Click once inside the game window. Expected: the act reply returns
   `interrupted: true`; a subsequent `hb` is `STALE_STATE`; `state` shows
   neutral input (no held MCP buttons); `control acquire` succeeds again.
5. No click is delivered to the game (menus/console unaffected).

| Observed result | Recorded value |
|---|---|
| Step 3: banner visible while the lease is held | manual — awaiting human execution |
| Step 4: deferred act returns `interrupted: true` | manual — awaiting human execution |
| Step 4: subsequent `hb` -> `STALE_STATE` | manual — awaiting human execution |
| Step 4: `state` neutral (no held MCP buttons) | manual — awaiting human execution |
| Step 4: `control acquire` succeeds again | manual — awaiting human execution |
| Step 5: click not delivered to the game | manual — awaiting human execution |

Supporting automated evidence: the server half is unit-tested — a
`STALE_STATE` heartbeat reply stops the beat and clears the local lease,
generation-guarded (`test_lease.py`), and a stale mutation does the same
(`test_mutate.py::test_mutate_stale_state_drops_lease_and_beat`). The
banner draw and the click swallow have no synthetic test; they rest on
this protocol.

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

## Prior-tip end-to-end measurement (carried)

The stdio autonomous loop below was measured at the conformance tip
`58fa94f` and is carried unchanged; the review-fixes round re-verified the
same surface through the 22-test integration suite at `d8aa7a6` instead
of re-running the loop.

Method: a stdio MCP client launched the real server process (`python3 -m
quakemcp.server` over the SDK stdio transport, not the in-memory test
helper) and drove one end-to-end session: `quake_start` (stepped),
`quake_game new_game`, `quake_observe`, a 12-tick forward `quake_act`
with `action_id loop-act-1`, a second `quake_observe(after_frame)`,
`quake_status(action_id)`, `quake_release`, `quake_stop`. Measured
transcript highlights:

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

One row per 09-13 design §9 acceptance gate.

| Gate | Verdict | Evidence |
|---|---|---|
| Integration inventory | PASS | `QuakeMCP/docs/engine-integration.md` hook table, op map and capability matrix re-verified against this tip on 2026-09-14 (Task 13 truth pass), including the new `q_mcp_ui.c` seams: `MCP_UiDraw` immediately before `MCAP_Frame`, `MCP_UiMouseClick` before the `in_sdl.c` access funnel. Fork, platform, renderer and build path recorded there. |
| MCP interoperability | PASS | Real-engine lifecycle suites initialize, list the 13 tools with schemas, receive image blocks plus `structuredContent` and shut down cleanly (`contract/test_tools_list.py`, `test_lifecycle.py`). Wire v1 is enforced after auth: `test_envelope.py::test_protocol_version_gate` (`v: 2` -> `UNSUPPORTED_CAPABILITY`). The capability manifest advertises `ui.keys` equal to the key table the policy enforces (`contract/test_policy.py::test_status_reports_capabilities`); `status` answers named, newest and unknown receipts (`test_status_action_query`, `test_status_returns_receipt`). Two authenticated connection slots, a third is closed (`test_slots.py`). The end-to-end stdio client loop is the carried prior-tip measurement above. |
| Complete gameplay input | PASS (water PARTIAL) | `test_act.py` covers forward/back, both strafes, run, look, attack, jump tap/hold and every advertised weapon, with the effective post-clamp view deltas and requested-vs-active weapon asserted; `test_observe.py` and `test_stepped.py` cover observation and frozen-clock movement. The round's impulse fix composes the serialized byte so a pending human impulse wins its frame, the MCP one-shot is read only when it can be sent, and the merge never writes `in_impulse` (`Quake/client/cl_input.c`). Water movement uses the same fields but no water volume was exercised: PARTIAL. The impulse-collision frame is reasoned, not tested (below). |
| Full UI reachability | PASS (intermission FAIL; options depth PARTIAL) | `test_modal.py` drives the keyboard path — `escape` opens the main menu (`ui == 3`), `enter` walks Single Player -> New Game into the `SCR_ModalMessage` confirmation, which returns `needs_input` with `modal_text` plus the dialog image; a duplicate retry replays the same body; `escape` and `release` both deny the receipt and leave `ui == 3` (no stuck modal). `test_lifecycle.py` covers new game (map `start`), save/list/load. Death/respawn: `test_death.py` (console `kill` restarts the level through the shipped progs with `world_gen` +1; a stable dead state on `end`; `respawn` returns `respawned: true` with `waited_ms` in bounds). Intermission is a recorded FAIL with cause (below); options-dialog depth beyond the New Game confirmation was not exercised: PARTIAL. |
| Action/frame alignment | PASS | `test_observe.py` reads a state snapshot right after a frame and requires the same poll window (`0 <= delta <= 5`), then confirms a 12-tick act changes the pixels and advances the frame; `test_observe.py`, `test_act.py` and `test_modal.py` receive real frames whose structured `frame` matches the state snapshot; `test_receipts.py` replays a delivered observation byte-identically or raises `FRAME_EXPIRED`, so a skipped or evicted frame never passes as fresh. |
| Timing | PASS | `test_stepped.py` pins exact completed-tick accounting and the no-catch-up freeze while idle; `test_act.py` runs a bounded 72-tick act (`completed_ticks: 72`, not interrupted) and a 1-tick clamped-input act; `test_observe.py` pins frame correlation (`0 <= state.frame - observation.frame <= 5` on a still scene) and that a 12-tick act advances the frame with changed pixels; `duration_ms` is refused in stepped mode; `test_lifecycle.py` confirms `quake_start` mode `stepped` and the engine's own state agrees. Deferred-act bounds: the bridge's 5 s cap with the 2 s lease expiry interrupting mid-act (`test_slots.py::test_control_connection_during_deferred_act`). |
| Failure cleanup | PASS (physical takeover pending; cancellation PARTIAL) | Heartbeat loss drops the local lease, generation-guarded (`test_lease.py`, `test_mutate.py::test_mutate_stale_state_drops_lease_and_beat`), and expiry is uniform mid-act (`test_slots.py::test_lease_expiry_after_silence`); EOF on the deferred owner finishes the act and answers from the ledger (`test_slots.py::test_eof_mid_act_finishes_action`); engine abort is the SIGKILL smoke (0/0/0); a pending modal is denied with the injected escape on release or lease loss (`test_modal.py`). Cancellation is verified at the server layer with a fake bridge (`test_cancel.py`, emergency release, no synthetic input): PARTIAL. Physical takeover is the manual protocol above, awaiting human execution. |
| Retry/concurrency | PASS | Dedup identity `(op, lease, action_id, epoch)` plus the canonical hash: a duplicate replays `duplicate: true`, a hash conflict is `POLICY_DENIED`, a spent `seq` with no receipt is `RESULT_EXPIRED` (`test_envelope.py::test_receipts_dedupe_and_conflict`, `test_receipts.py`, `test_dedup.py`); a retried receipt whose delivered frame was evicted raises `FRAME_EXPIRED` and is never re-shot. The control connection answers `state`/`hb`/`release` while an act reply is deferred and `release` interrupts at once; a third connection is closed, so a slow or extra reader cannot block emergency release (`test_slots.py`). |
| Image fidelity | PASS (transport) | Frames from the real renderer with state-matched identity (above); dialog overlays are asserted as image blocks and in the modal observation (`test_modal.py`); `test_vision.py` pins the bottom-up RGB->PNG transform, resize/crop mapping, the telemetry policy, the 2 MiB cap and the error codes. Vision accuracy is blocked below. |
| Save/lifecycle safety | PASS | `test_lifecycle.py`: save/list/load round trip, owned `stop`, second `stop` -> `ENGINE_DISCONNECTED`; `contract/test_policy.py`: missing-slot, traversal and overwrite guards, attached-instance stop refusal, console allowlist removals (`save`, `load`, `connect`, `disconnect`). |
| Vision usability | ENVIRONMENT-BLOCKED | Transport is verified (image blocks plus telemetry above), but no external vision model exists in this environment, so threat/HUD recognition accuracy is not claimed; transport success is not evidence of accuracy. |
| Regression | PASS | The vanilla gate exits 0 (the MCP-disabled build) and each vanilla binary survives the SIGKILL smoke with 0 `Received signal` lines; `QuakeWorld/` is untouched by the round; all 22 integration tests ran (0 skipped) and the 65 unit/contract tests pass. The parser regression pair (`test_parser_value_does_not_impersonate_a_key`, `test_parser_escaped_quotes_still_decode`) guards the shared JSON member scanner against the old key/value walk. |

## Recorded negatives and limitations

- Intermission remains unreachable from the tool surface: FAIL with cause.
  `svc_intermission` is written only by the progs'
  `execute_changelevel`, and no console/tool surface reaches it;
  `test_death.py` asserts the observed negative
  (`load_map end` leaves `intermission == false`) and the flag is never
  faked true.
- Vision with a model is ENVIRONMENT-BLOCKED: no external vision model
  exists here; recognition accuracy is not claimed.
- Water movement is PARTIAL: the fields are covered, no water volume was
  exercised.
- Cancellation is PARTIAL: verified at the server layer with a fake
  bridge; no integration test injects a raw stdio cancellation
  notification into a deferred act.
- Physical takeover is `manual — awaiting human execution` (protocol
  above).
- The impulse-collision policy is reasoned, not tested: when a human
  impulse and an MCP one-shot are pending in the same serialization, the
  human's wins and the MCP latch stays set for the next send (the latch
  is read only when it can be sent). The harness cannot produce the
  same-frame collision; an optional manual check would hold a human
  weapon key while queueing an MCP impulse and confirm the MCP switch
  lands on the following frame.
- Options-dialog depth is PARTIAL: only the New Game confirmation was
  walked from the tool surface.
- Stepped sessions return to realtime for world-mutating ops (map or
  savegame load) and restore the mode afterwards, so a world load never
  deadlocks the frozen clock.
- The bridge serves two authenticated connections (call plus control); a
  third is closed. Mutations stay serialized by the lease, not by the
  socket model.
