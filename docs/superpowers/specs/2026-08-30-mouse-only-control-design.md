# Mouse-Only Control — Design

**Date:** 2026-08-30
**Status:** Approved (brainstorming session)
**Scope:** `glquake` (`Quake/` tree) only. `QuakeWorld/` (glqwcl, qwsv)
untouched — per-tree isolation rule (CONTEXT.md), and saving/menus
requirements are single-player.

## Goal

Let a player control glquake with only a pointing device and its
buttons — no keyboard — including menus and saving. "Mouse" means any
relative pointer (standard mouse or trackball). The game runs in window
mode and never locks the cursor (already the port's behavior:
`install_grabs()` in platform/in_sdl.c deliberately never engages SDL
relative mode; gl_vidsdl.c:417 creates a plain windowed
`SDL_WINDOW_OPENGL`).

Two operating modes:

- **Look mode** (default): standard mouselook.
- **Walk mode**: mouse Y moves forward/backward, mouse X turns; while
  a strafe button is held, X sidesteps. Pitch stays level; `sv_aim`
  (pr_cmds.c:1332) vertical auto-aim compensates while firing.

One symmetric toggle switches between them; fire, jump, and weapon
cycling never change mode.

Deliverables: (1) a ready-to-use `autoexec.cfg` for everything the
engine can express natively, (2) the source patch described here with
its cvar list, (3) a validation plan (§Validation).

## Non-goals

- QuakeWorld client/server changes.
- External OS-level shims (evdev/uinput, AutoHotkey/vJoy): unavailable
  on macOS, and throttle/HUD/gestures/menus need in-engine code
  regardless.
- New sound assets — feedback reuses pak0 sounds.
- Free-text input anywhere except the player-name palette (§Menus).
- Gamepad/joystick support (K_JOY*/K_AUX* stay orphaned; only the two
  slots needed for K_MOUSE4/5 are repurposed).

## Engine facts (evidence base)

- **Walk mode is almost native.** With `mlook` off, IN_MouseMove
  (in_sdl.c) already maps Y→`forwardmove` via `m_forward` and X→yaw;
  `lookspring` re-levels pitch; `+strafe` makes X sidestep
  (cl_input.c:119-122, 444-445). The patch replaces this branch with a
  governed path instead of reusing `mlook` state, so all tunables have
  one home.
- **Input surface today:** in_sdl.c emits only MOUSE1–3 (no wheel,
  no MOUSE4/5). keys.h has K_MOUSE1–3 (200–202), K_MWHEELUP/DOWN
  (239–240, already consumed by console scrolling in keys.c:237-245,
  but never emitted by this port); K_JOY1–4/K_AUX1–32 (203–238) are
  orphaned names with no generator.
- **Frame pacing:** `Host_FilterTime` (host.c:505) hard-caps at
  `1.0/72.0`; no `host_maxfps` cvar exists. `-DFPS_20` is not in the
  Makefile, so the `#ifdef FPS_20` block is dead.
- **Menus:** entirely keyboard-driven (`M_Keydown`, menu.c; `m_state`
  enum at menu.c:25). No mouse support anywhere.
- **Command chain:** `_Host_Frame` (host.c:633) → `CL_SendCmd`
  (cl_main.c:670) → `CL_BaseMove` + `IN_Move` (cl_main.c:683) →
  `IN_MouseMove` (in_sdl.c). `CL_Init` is cl_main.c:717.
- **Feedback sounds exist in pak0:** `sound/misc/menu1.wav`,
  `menu2.wav`, `menu3.wav`, `buttons/switch02.wav`.
- **Movement speeds:** `cl_forwardspeed`/`cl_backspeed` default 200
  (cl_input.c:213-214); Options menu already has Always Run (400).

## Architecture

One new module, `client/cl_access.c` (+ `cl_access.h`), owns the whole
feature: mode state machine, gesture engine, movement pipeline
(throttle + velocity profiles), cruise control, safety resets, sticky
layer, HUD indicator, feedback sounds, and the cvar/command table.
Registered from `CL_Init` via a new `Access_Init()` call.

Kill switch: `access_mouseonly` (default 1). At 0 the module is inert:
IN_MouseMove takes the vanilla path, no HUD draws, menus fall back to
keyboard-only. Behavior must be byte-identical to the unpatched port.

Call seams in existing files (all small and mechanical):

| File | Seam |
|---|---|
| platform/in_sdl.c | Handle `SDL_EVENT_MOUSE_WHEEL` → `Key_Event(K_MWHEELUP/DOWN, true)` immediately followed by the matching `false` (momentary press); map `SDL_BUTTON_X1/X2` → new `K_MOUSE4/K_MOUSE5`; route every mouse button event through `Access_ButtonEvent(keynum, down, SDL_GetTicks())` — the module forwards it to `Key_Event` (immediately, or deferred while a gesture is pending) unless the press is consumed by a gesture |
| platform/in_sdl.c `IN_MouseMove` | Early branch: `if (access_mouseonly) { Access_MouseMove(cmd, mx, my); return; }` before the vanilla block; the vanilla block stays untouched |
| client/keys.h | `K_MOUSE4 203`, `K_MOUSE5 204` replace orphaned `K_JOY1/K_JOY2`; `K_JOY3/4`, `K_AUX*` stay (separate cleanup concern) |
| client/keys.c | `{"MOUSE4", K_MOUSE4}`, `{"MOUSE5", K_MOUSE5}` in the keynames table |
| host.c | `Host_FilterTime`: `1.0/72.0` → `1.0/host_maxfps.value`; new cvar `host_maxfps` default 72 (behavior unchanged, now pinnable/tunable) |
| host.c `_Host_Frame` | One call `Access_Frame(host_frametime)` before `CL_SendCmd()` (host.c:663) — gesture timers, safety checks, idle timeout, layer expiry, cruise bookkeeping |
| client/menu.c | Per-page item registration `Access_MenuItem(index, x, y, w, h, cursor_ptr)` at draw time; module owns hover, click→(set cursor + Enter, queued to frame boundary), right-click→Escape, wheel→prev/next; one new `m_mouse` page (§Menus) |
| client/sbar.c | One call `Access_DrawHUD()` after the status bar draws |
| client/cl_main.c | `Access_Reset()` from `CL_Disconnect` path; `Access_Init()` from `CL_Init` (cl_main.c:717) |
| Makefile | Add `$(QUAKE_BUILDDIR)/client/cl_access.o` to the glquake object list (Makefile:78-90 region) |

## Mode model

- **Look mode:** mouse Y→pitch, X→yaw — standard mouselook, identical
  feel to vanilla `mlook` (sensitivity and `m_pitch`/`m_yaw` apply as
  today).
- **Walk mode:** Y drives movement (§Movement), X drives yaw with a
  turn-rate cap; while the strafe button is held, X sidesteps instead
  (same capped gain via `m_side`). Pitch is governed engine-side: on
  entering Walk mode it eases toward 0 (horizon) at 120°/s and stays
  there. `sv_aim` (default 0.93) supplies vertical auto-aim while
  firing — no change to the engine's existing behavior.
- **Toggle:** the command `access_toggle_mode` works both directions.
  It is invoked only by the configured toggle button/gesture (§Gesture
  engine). Fire, jump, weapon cycle, cruise — none of them touch mode,
  by construction.
- The vanilla `mlook` key state is left alone; the module's own mode
  flag is authoritative while `access_mouseonly` is on.

## Gesture engine

Three primitives, all in-module, all configurable:

1. **Dedicated toggle button** — `access_toggle_button` (default
   `MOUSE3`, the wheel click): toggles mode on press. The engine
   consumes this button entirely — no cfg binding is needed or
   honored for it.
2. **Long-press** — `access_longpress_button` (0 = off),
   `access_longpress_ms` (default 400). For the configured button the
   down event is held pending: released before the threshold → the
   press is delivered normally (down then up); held past the threshold
   → the pending press is discarded and the sticky layer opens
   (§Button maps). Non-toggle bindings on a long-press button therefore incur
   up to `access_longpress_ms` of latency — an inherent trade-off,
   which is why the 5-button default map leaves this button at 0.
3. **Double-click** — `access_doubleclick_button` (0 = off),
   `access_doubleclick_ms` (default 350, clamped to 300–1000),
   `access_doubleclick_command` (string). Presses of the configured
   button are held pending: a second press inside the window runs the
   command (both presses suppressed); if the window lapses, the
   pending press is delivered normally. **Never-on-fire rule enforced
   in code:** configuring MOUSE1 is rejected with a `Con_Printf` and
   the cvar keeps its old value.

Long-press and double-click may share one button (the ≤3-button
fallback needs both on MOUSE2). Precedence on a shared button: held
past the long-press threshold → discard + open layer; otherwise two
presses inside the double-click window → command; otherwise the
pending press is delivered once the window lapses. The cost is that
every press on a shared button is delayed up to the window — which is
why the default 5-button map configures neither.

Sticky layer: `access_layer_timeout` (default 3 s). Opened by a
long-press fire; one-shot; announced by sound + `LAYER` HUD label.
While open: next MOUSE1 click runs `access_layer_cmd1` (default
`save quick`), next MOUSE2 click runs `access_layer_cmd2` (default
`togglemenu`). The layer exists for ≤3-button fallbacks; with the
default 5-button map the long-press button stays 0 and the layer is
never involved.

Gesture *command strings* (`access_doubleclick_command`,
`access_layer_cmd1/2`) are set via cfg/console only — they are
set-once-per-device settings, not menu material.

## Safety and resets

Forced back to Look mode with throttle, cruise, and all movement
zeroed, on:

- player death (`cl.stats[STAT_HEALTH] <= 0`);
- level change / intermission / disconnect (`Access_Reset` seam);
- menu or console open (`key_dest != key_game`).

Optional inactivity timeout: `access_idle_timeout` seconds (0 = off,
default 0). While Walk mode is active and no mouse button or motion
input has arrived for that long, return to Look mode.

Every forced or user-initiated transition emits one
`Con_Printf("access: ...")` line when `access_log` (default 1) — this
doubles as instrumentation for the validation metric "mode errors per
minute".

## Movement model

Shared input pipeline, applied to raw per-frame deltas in both modes
and both profiles. First the global `sensitivity` multiplier applies
to both axes, exactly as vanilla (in_sdl.c) does (`m_filter` is
ignored on the access path; `access_tremor` supersedes it). Then, in
order:

1. **Tremor low-pass** — `s = a·new + (1−a)·old` per axis;
   `access_tremor` ∈ [0,1), default 0 (off).
2. **Dead zone** — `|delta| < access_deadzone` counts → 0 per axis
   (default 0).
3. **Response curve** — applied where the magnitude is consumed:
   `sign(v)·|v|^access_curve` (default 1 = linear).

**Profile select:** `access_move_profile` — 0 = throttle (default,
mice/trackballs), 1 = velocity (stick-driven pointers).

**Throttle profile.** Entering Walk mode zeroes `throttle`. Each frame:
`throttle += delta_y · access_throttle_gain` (default 0.002), clamped
to ±1, then decayed by `access_throttle_decay` per second (default 0 =
hold indefinitely — the mouse can rest still while walking). Movement
speed = curve-shaped throttle × `access_walkspeed`, applied to
`cmd->forwardmove` (negative Y = forward). Leaving Walk mode zeroes
the throttle.

**Velocity profile.** Per-frame `cmd->forwardmove = −delta_y ·
access_velocity_gain` (default 1.0) — Quake's native `m_forward`
semantics. Deterministic because `host_maxfps` pins the frame rate.
Result clamped to ±`access_walkspeed`.

**Speed cap:** `access_walkspeed` (default 190) is hard-clamped at
`cl_forwardspeed − 10`, so Walk mode can never reach run speed.

**Turn-rate cap (Walk mode only):** yaw from
`delta_x · sensitivity · m_yaw` is clamped to ±`access_turnrate`
deg/s (default 240; 0 = uncapped). The strafe-hold sidemove uses the
same cap. Look mode turning stays uncapped vanilla mouselook.

All tunables take effect immediately (no restart); all are archived
cvars.

## Cruise control

Command `access_toggle_cruise` (bound to MOUSE2 in the default map).

- In Look mode, adds a constant forward input of
  `access_cruise_speed` each frame. The cvar default is 0, meaning
  "follow `cl_forwardspeed` live" — so Always Run carries through
  automatically; any positive value is a fixed override.
- Toggle is refused in Walk mode (throttle already sustains movement):
  sound blip + console line, no state change.
- Entering Walk mode cancels cruise.
- Any explicit backward input cancels cruise.
- Safety resets (death/menu/level change) cancel cruise.
- HUD shows a `CRUISE` tag while active (§Feedback).

## Feedback

- **Sounds** (`access_sounds` 0/1, default 1), via `S_LocalSound` so
  they work while menus pause the world — all from pak0, no new
  assets: entering Walk `misc/menu1.wav`, entering Look
  `misc/menu2.wav`, cruise toggle `misc/menu3.wav`, layer open/close
  `buttons/switch02.wav`.
- **Transient label:** 1.5 s centered large text `WALK MODE` /
  `LOOK MODE` (also `LAYER` when the sticky layer opens) on every
  transition, via `Draw_String`.
- **Persistent HUD indicator** (`access_hud` 0/1, default 1), drawn in
  the engine's 2D coordinate space at the top-left corner:
  mode text `LOOK`/`WALK` plus `CRUISE` tag when active, and a 64 px
  horizontal throttle bar (`Draw_Fill`) showing signed throttle in
  Walk mode and cruise state in Look mode.

## Mouse-navigable menus

The port never hides the system cursor, so in menus the OS cursor is
the pointer. The module reads its position with `SDL_GetMouseState`,
scaled from window pixels into the engine's 2D coordinate space
(`vid.width × vid.height` — 640×480 by default on this port, set by
`GL_Set2D`; menu coordinates live in that same space) using the
current window size (gl_vidsdl.c owns the window).

- Each menu page registers its selectable items at draw time via
  `Access_MenuItem(index, x, y, w, h, &page_cursor)` — vanilla draws
  every item at hardcoded coordinates, so rects are free. Item rect =
  text/pic bounds; hit rects are padded by 2 px on each side for
  fat-finger tolerance.
- Hover: the page queries `Access_MenuHovered(index)` and draws the
  standard highlight.
- Left click = set that page's cursor to the hovered item, then
  synthesize Enter — reusing every page's existing cursor switch, no
  new action logic. Right click = Escape (back/cancel). Wheel =
  prev/next item — wheel key events reach `M_Keydown` like arrow keys
  and move the page cursor.
- Coverage is total: every `m_state` page registers its items — main,
  single player, episode, skill, load, save, options, video, keys,
  help, quit, and the multiplayer subtree. No dead ends.
- Keys menu: rebinding works mouse-only — click a row, then click a
  mouse button (mouse buttons are bindable keys, K_MOUSE*).
- Player name (the only free-text UI, m_setup): clickable ◀/▶ caret
  arrows plus one drawn character-palette row (A–Z, 0–9, ⌫) at the
  bottom of the setup page; palette click inserts at the caret.

**New `m_mouse` page** (entry added to the Options menu): sliders and
checkboxes via the existing `M_DrawSlider`/`M_DrawCheckbox` helpers
for — `access_mouseonly`, `access_move_profile`, throttle/velocity
gains, `access_deadzone`, `access_curve`, `access_walkspeed`,
`access_tremor`, `access_turnrate`, `access_throttle_decay`,
`access_toggle_button`, `access_longpress_ms`, `access_idle_timeout`,
`access_hud`, `access_sounds` — plus the supporting settings the
requirement names: `sv_aim`, `cl_bob`, `cl_rollangle`, `v_kicktime`,
`host_timescale`, `host_maxfps`. (Always Run stays in Options; skill
is chosen at New Game.)

## Button maps

Default map (5-button mouse + wheel):

| Input | Binding |
|---|---|
| MOUSE1 | `+attack` |
| MOUSE2 | `access_toggle_cruise` |
| MOUSE3 (wheel click) | `access_toggle_mode` |
| MOUSE4 | `+strafe` (hold) |
| MOUSE5 | `+jump` (jump; swim up — vanilla `+jump` drives upmove when swimming) |
| MWHEELUP | `impulse 10` (next weapon) |
| MWHEELDOWN | `impulse 12` (previous weapon) |

Fallback for ≤3-button devices (documented in the autoexec header and
README): M1 fire; M2 tap = cruise; M2 double-click = mode toggle
(`access_doubleclick_button MOUSE2`, command `access_toggle_mode`);
M2 long-press = sticky layer (M1 → `save quick`, M2 → `togglemenu`);
M3 (if present) = mode toggle; wheel (if present) = weapon cycle.

Documented limitation: a true 2-button device with no wheel has no
weapon cycling (double-click on the fire button is forbidden by
spec). Weapons 1–8 remain reachable only via pickup order.

## Deliverable 1 — autoexec.cfg

Committed at `configs/autoexec-mouseonly.cfg` (game/ is gitignored —
the repo can't ship anything under `game/id1/`; the file header says
to copy it to `game/id1/autoexec.cfg`, which the engine execs
automatically at startup).

Contents: the default bindings above except MOUSE3 (engine-consumed
via `access_toggle_button`, see §Gesture engine); `access_*` settings tuned for a
mouse with the throttle profile; supporting settings `sv_aim 0.93`,
`cl_bob 0`, `cl_rollangle 0`, `v_kicktime 0`, `host_timescale 1`,
`cl_forwardspeed 400` / `cl_backspeed 400` (always-run),
`host_maxfps 72`, `skill 0`, `sensitivity 3`; a commented-out
2-button fallback block (double-click + layer configuration). No
`unbindall` — keyboard stays intact for anyone sharing the install.

## Deliverable 2 — cvar and command list

New commands: `access_toggle_mode`, `access_toggle_cruise`.

New cvars (all registered by `Access_Init`, all archived unless noted):

| Cvar | Default | Meaning |
|---|---|---|
| `access_mouseonly` | 1 | Master switch; 0 restores byte-identical vanilla behavior |
| `access_move_profile` | 0 | 0 = throttle, 1 = velocity |
| `access_throttle_gain` | 0.002 | Throttle accumulation per delta count |
| `access_velocity_gain` | 1.0 | Velocity-profile gain (native `m_forward` analog) |
| `access_deadzone` | 0 | Per-axis dead zone in counts |
| `access_curve` | 1 | Response-curve exponent |
| `access_walkspeed` | 190 | Walk-mode speed cap (clamped ≤ `cl_forwardspeed − 10`) |
| `access_tremor` | 0 | Low-pass coefficient, [0,1) |
| `access_turnrate` | 240 | Walk-mode turn cap deg/s; 0 = uncapped |
| `access_throttle_decay` | 0 | Throttle decay per second; 0 = hold |
| `access_toggle_button` | MOUSE3 | Dedicated toggle button (MOUSE1–5 keynums; wheel keynums are not gesture targets) |
| `access_longpress_button` | 0 | Long-press/layer trigger; 0 = off |
| `access_longpress_ms` | 400 | Long-press threshold |
| `access_layer_timeout` | 3 | Sticky-layer window, seconds |
| `access_layer_cmd1` | `save quick` | Layer MOUSE1 command (not archived) |
| `access_layer_cmd2` | `togglemenu` | Layer MOUSE2 command (not archived) |
| `access_doubleclick_button` | 0 | Double-click button; 0 = off; MOUSE1 rejected; may share a button with long-press |
| `access_doubleclick_ms` | 350 | Double-click window, clamped 300–1000 |
| `access_doubleclick_command` | (empty) | Double-click command (not archived) |
| `access_cruise_speed` | 0 | Cruise forward speed; 0 = follow `cl_forwardspeed` live |
| `access_idle_timeout` | 0 | Walk-mode idle revert, seconds; 0 = off |
| `access_hud` | 1 | Persistent HUD indicator |
| `access_sounds` | 1 | Transition/layer sounds |
| `access_log` | 1 | `access:` console event lines |
| `host_maxfps` | 72 | Frame-rate cap used by `Host_FilterTime` (host.c); values below 10 are clamped to 10 |

New key constants: `K_MOUSE4` 203, `K_MOUSE5` 204 (+ keynames
entries). `K_MWHEELUP/DOWN` are now actually emitted.

## Files touched

New: `Quake/client/cl_access.c`, `Quake/client/cl_access.h`,
`configs/autoexec-mouseonly.cfg`.

Modified: `Quake/platform/in_sdl.c`, `Quake/client/keys.h`,
`Quake/client/keys.c`, `Quake/client/menu.c`, `Quake/client/sbar.c`,
`Quake/client/cl_main.c`, `Quake/host.c`, `Makefile` (one object
line), `README.md` (short usage section: install + fallback map).

## Validation

Deliverable 3 — test protocol:

- **Testers:** one per device class (standard mouse, trackball), same
  build, throttle profile, matching map (5-button or 2-button
  fallback).
- **Scenario:** E1M1 (The Slipgate Complex), skill 0, fresh game.
- **Protocol:** 10 min familiarization, then 3 timed full completions
  (reach the exit slipgate), observer present.
- **Metrics:** (1) completion time — wall clock + in-level `time`,
  median of 3; (2) mode errors per minute — observer-flagged
  wrong-mode inputs plus `access:` log lines for unintended toggles,
  ÷ run minutes; (3) self-reported fatigue — 1–10 scale and short
  NASA-TLX after each run.
- **Pass criteria:** all runs completed with zero keyboard touches
  (observer-verified); mode errors < 2/min by run 3; fatigue ≤ 5/10;
  plus a mouse-only full loop: new game → save → quit → load →
  resume.

Build gates before any completion claim (AGENTS.md): `make clean &&
make build-release build-server build-client` from the repo root, and
the 3-binary SIGKILL smoke protocol ("Received signal" count 0).

## Risks and mitigations

- **menu.c diff is wide but shallow** (registration call per item,
  ~15 pages): mechanical, per-page reviewable, no logic changes to
  existing handlers.
- **Throttle feel is subjective:** every coefficient is a live cvar
  and exposed in the Mouse menu; defaults are conservative.
- **Window-edge behavior:** the cursor is free and never grabbed; if
  the user parks the cursor outside the window, motion input stops —
  inherent to the never-lock requirement, documented in README.
- **`host_maxfps` set above 72** changes vanilla server physics
  timing: README notes 72 is the fidelity value; the cvar exists to
  pin, not to overclock.

---

## Revision 2026-08-31 — control scheme redesign (supersedes above)

Post-release feedback showed the original scheme mismatched user
expectations: the mode toggle sat on the wheel click while the right
button toggled cruise, so right-clicking to walk activated cruise
instead (HUD read "look cruise"; no backward path existed in that
state). The sections above are retained as history; the live scheme:

- MOUSE1 fires; double-click MOUSE1 jumps (`access_doubleclick_button
  200`, command `+jump; wait; -jump` — the vanilla one-frame jump
  idiom, Cmd_Wait_f in common/cmd.c). The never-on-fire rule is
  lifted: presses are delivered immediately, so fire is never delayed
  by the double-click window.
- MOUSE2 (right button, `access_toggle_button` default 201) toggles
  Look/Walk.
- Entering Walk mode levels the view: pitch snaps to the horizon
  instantly, yaw keeps whichever way the player faced. Walk mode:
  Y = forward/backward throttle, X = sidestep (capped at
  cl_sidespeed). There is no turning in Walk mode — Look mode turns.
- Cruise control is removed (cvars, command, HUD tag, logic).
- `access_turnrate` is removed with the Walk-mode turn; the Mouse
  options page lost its turn-rate row (MOUSE_ITEMS 21 → 20, items
  renumbered).
- HUD: the boxed LOOK/WALK label is clickable — a toggle fallback for
  devices whose right button cannot be used; hover brightens the
  frame. The access HUD is suppressed during demo playback.
- Demo playback: any mouse click opens the main menu, mirroring what
  keyboard keys already do in Key_Event (keys.c).
- The wheel and side buttons are unused: config drops the wheel
  weapon-cycle, MOUSE4 strafe, and MOUSE5 jump bindings. Consequence:
  no mouse-driven weapon switching.

Build gate unchanged: `make clean && make build-release build-server
build-client`.
