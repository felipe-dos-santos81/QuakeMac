# QuakeMCP review-fixes round — design

Date: 2026-09-14. Base: `main` at `8d02c52` (all QuakeMCP work is unpushed;
`origin/main` is `63c60cb`). Sources: the two-axis review of
`git diff origin/main...HEAD` — 9 Standards findings (`Quake/client/cl_input.c`
impulse merge, vision error codes, seven refactor smells) and 5 Spec findings
(missing human takeover control, capability manifest, acceptance rows,
unplanned world-spawn hook, `exec` read bypass).

This round fixes all fourteen. It is a conformance-and-cleanup round: no wire
version bump, no new error codes, no schema shape changes, no new tools.

## 1. Finding inventory

| # | Finding | Resolution |
|---|---|---|
| S1 | `in_impulse = im` writes a human input global (`cl_input.c:379-382`), contradicting the "physical input is sacred" invariant | §2.1 code fix |
| S2 | `vision.py` errors lack `"<CODE>: detail"` prefixes | §2.3 code fix |
| S3 | Lease-clearing four-line reset duplicated at three `server.py` sites | §4.1 |
| S4 | `CallToolResult` observation shape rebuilt at four sites | §4.2 |
| S5 | Mutation envelope preamble copied at five `q_mcp.c` sites | §4.3 |
| S6 | `_instance()` re-inlined; `except EngineDisconnected` bodies dead | §4.4 |
| S7 | Envelope six-field data clump | §4.7 accept with rationale |
| S8 | State schema stated three times in `models.py` | §4.5 |
| S9 | Brace-walk parser duplicated (`MCP_Field` / `MCP_HashRequest`) | §4.6 |
| P1 | No human takeover control (09-13 design §6) | §3 |
| P2 | Capabilities omit the UI key table | §2.4 |
| P3 | Acceptance matrix dropped the §9 UI-reachability and failure-cleanup rows | §5 |
| P4 | `MCP_NoteWorldSpawn` hook was outside plan Task 6's "no new hooks" | §2.5 docs-only |
| P5 | `exec` with missing/empty text still answers as an envelope-free read | §2.2 code fix |

## 2. Behavior and protocol fixes

### 2.1 Impulse merge (`Quake/client/cl_input.c`)

The hook must never assign `in_impulse`. Compose the serialized byte locally:

- a pending human impulse wins its frame and is consumed exactly as vanilla
  consumes it (`in_impulse = 0` after the write, unchanged);
- when no human impulse is pending, the MCP one-shot is read
  (`MCP_Impulse()` clears on read, `q_mcp_input.c:200-209`) and sent;
- a collision defers the MCP impulse to the next serialization — it is never
  dropped, because the latch is only read when it can be sent.

The collision frame is reasoned, not test-drivable; recorded as such in the
acceptance notes. Existing weapon-switch assertions (`test_act.py`) guard the
normal path.

### 2.2 `exec` is always a mutation (`QuakeMCP/bridge/q_mcp.c`)

Delete the empty/missing-text read branch (`:1555-1562`). Missing or empty
`text` now replies `INVALID_CONTEXT: exec needs text` before the envelope;
oversize text stays `POLICY_DENIED`. `tail` is the only console read, matching
conformance design §3.1. New integration assertion pins the rejection;
`QuakeMCP/AGENTS.md` and `QuakeMCP/docs/engine-integration.md` drop the
"`exec text=""` stays a read" wording this round introduced.

### 2.3 Vision error codes (`QuakeMCP/src/quakemcp/vision.py`)

Every raised error carries a code prefix:

| Condition | Code |
|---|---|
| crop not four ints / out of frame | `INVALID_CONTEXT` |
| crop would drop the HUD without `allow_hud_crop` | `POLICY_DENIED` |
| encoded frame over the 2 MiB cap | `POLICY_DENIED` |
| bad rgb length / unsupported format / bad `longest_edge` / bad telemetry mode | `INVALID_CONTEXT` |

`BadCrop`/`ImageTooLarge` stay as `ValueError` subclasses; message bodies are
unchanged so existing assertions keep meaning. No new error codes.

### 2.4 Capabilities UI keys (`QuakeMCP/src/quakemcp/models.py`)

`CAPABILITIES["ui"]` gains `"keys": sorted(KEY_CODES)`. The contract test
asserts the advertised set equals the table the policy enforces.

### 2.5 World-spawn hook drift (docs only)

`MCP_NoteWorldSpawn` (`Quake/server/sv_main.c:1064-1069`) replaces plan Task 6's
planned `sv.name` poll because same-map same-time reloads are invisible to a
name poll. No code change: the conformance plan gains a drift note and the
Fixes Ledger records the deviation.

## 3. Human takeover control

### 3.1 Bridge (`QuakeMCP/bridge/q_mcp_ui.c`, new; `q_mcp.c`, `q_mcp.h`)

- `MCP_LeaseHeld()` — accessor for the live lease flag.
- `MCP_HumanTakeover()` — calls the existing revoke path
  (`MCP_ClearControl`, `q_mcp.c:1100`): bumps `control_rev`, revokes the lease,
  finishes a running act as `interrupted`, neutralizes MCP input, and denies a
  pending modal with the injected escape.
- `MCP_UiDraw()` — while a lease is held, draws a top-center banner using the
  existing `Draw_FillAlpha`/`Draw_StringAlpha` pair
  (`render/gl_draw.c:585,842`): `MCP CONTROL - LEFT-CLICK TO STOP`. Nothing is
  drawn without a lease.
- `MCP_UiMouseClick(button)` — on left-button-down with a lease held: take
  over, return 1 (swallow the click); otherwise return 0.

`q_mcp_ui.c` joins `QUAKE_MCP_OBJS`. Semantics: execution mode is unchanged, so
an owned stepped session stays frozen after takeover until explicitly resumed;
the swallowed click never reaches the game or the access module.

### 3.2 Hooks

- `Quake/render/gl_screen.c`: `MCP_UiDraw()` immediately before `MCAP_Frame()`
  (`:943-948`), so the banner that is visible on screen is the banner in the
  captured frame.
- `Quake/platform/in_sdl.c`: `MCP_UiMouseClick(b)` before the
  `Access_ButtonEvent` funnel (`:178-180`).

Both under `#ifdef QUAKE_MCP`: without a lease (and in every vanilla build)
input paths remain byte-identical.

### 3.3 Server reaction (`QuakeMCP/src/quakemcp/lifecycle.py`)

The keepalive thread currently ignores the `hb` reply and beats forever. The
beat now inspects it: a `STALE_STATE` reply stops the beat and clears local
lease state via `Instance.clear_lease()` (§4.1), so `quake_status` reports
`lease_held: false` and a fresh acquire succeeds. Pinned by a unit test against
the fake bridge.

### 3.4 Verification

A real window click cannot be injected by this repo's harness. The control is
verified by a documented manual protocol in the acceptance results (launch,
acquire, click the banner, then assert: no lease, neutral input, a deferred act
reported `interrupted`), plus the §3.3 unit test. No synthetic-input test
scaffolding is added to production code. The manual step is recorded as manual.

## 4. Refactors

1. **`Instance.clear_lease()`** (`lifecycle.py`) owns `stop_keepalive()`,
   `lease=""`, `epoch=0`, `next_seq=0`; replaces the inlined copies at
   `server.py:131-134`, `:1065-1073`, `:1100-1103` and serves §3.3.
2. **`_observation_result(encoded, report, structured)`** builds the
   `CallToolResult` once; `_replay`, `quake_observe`, `quake_act` and the modal
   `needs_input` reply call it.
3. **`MCP_BeginMutation(line, id, op, &hash)`** (`q_mcp.c`): hash → receipt
   lookup → preconditions, replying on failure. Called by `exec`, `cvar` set,
   `key`, `control mode`, and `act`; `act` keeps its `CONTROL_BUSY` check
   between begin and the existing `MCP_CheckSequence`, the others check
   sequence directly.
4. **Reuse skips:** `quake_act`/`quake_stop` use `_instance()`;
   `except EngineDisconnected` folds into `except QuakeMCPError`
   (`EngineDisconnected` subclasses it, `models.py:301`).
5. **State schema single source:** `STATE_KEYS` canonical;
   `REQUIRED_STATE_KEYS` derived; a unit test asserts both tuples agree with
   the `StateRequired`/`StateOut` annotations.
6. **JSON member iterator:** one top-level member scanner extracted from
   `MCP_Field` (`:433`) and `MCP_HashRequest` (`:1152`) — escape-aware key
   read, raw value span, nested skipping; existing envelope/exec/cvar
   integration suites are the regression net.
7. **Accept with rationale — envelope data clump:** the six fields are the
   wire contract and MCP client schemas must stay flat; an internal `Envelope`
   type would be constructed from the same six parameters per tool and change
   nothing a client sees, while `_mutate` already centralizes the handling.
   Recorded in the round docs and the Fixes Ledger; no code change.

Pure refactors add no tests beyond the §4.5 consistency test; existing suites
prove behavior did not move.

## 5. Verification and acceptance

- Gates: vanilla `build-release build-server build-client`; `QUAKE_MCP=1`
  build; unit/contract and integration suites (0 skipped with game data);
  SIGKILL smoke 0/0/0; stdio autonomous loop.
- New/updated tests: `exec` empty-text `INVALID_CONTEXT` (integration); vision
  code prefixes (unit); capabilities `ui.keys` equality (contract); heartbeat
  `STALE_STATE` clears lease state (unit); schema consistency (unit).
- Acceptance matrix re-mapped to the 09-13 acceptance gates (design §9,
  plan Task 10), restoring
  **Full UI reachability** (menu/modal evidence from `test_modal` /
  `test_lifecycle`; options-dialog depth stays PARTIAL if untested) and
  **Failure cleanup** (heartbeat loss, EOF, engine abort via smoke, and the
  §3.4 physical-takeover manual protocol).
- Honest negatives stay: intermission FAIL with cause, vision
  ENVIRONMENT-BLOCKED, water and cancellation PARTIAL.

## 6. Documentation and delivery

Docs truth pass: `engine-integration.md` (impulse wording, new hooks,
re-verified line numbers), `QuakeMCP/AGENTS.md` (exec wording, takeover trap,
new module row), `README.md` (stop-control line, limitations), conformance plan
Task 6 drift note, Fixes Ledger entry, rewritten `acceptance-results.md`.

Delivery: branch `fix/quakemcp-review-findings`, subagent-driven execution,
`--no-ff` merge to `main`, no push.

## 7. Non-goals

- No protocol version bump, new error codes, new tools, or wire schema changes.
- No cursor or hit-tested widget: any left-click takes over while a lease is
  held (chosen for robustness across game/console/menu contexts).
- No optional hotkey (the 09-13 design marks it optional).
- No automatic re-acquire after takeover: the human owns the session until an
  explicit `quake_control acquire`.
