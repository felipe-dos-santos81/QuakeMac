# QuakeMCP engine integration — hook inventory

Fork commit: `0c8a6fc`. Scope: glquake (`Quake/` tree) only.
Renderer: OpenGL (`Quake/platform/gl_vidsdl.c`).
Platform pump: SDL (`Quake/platform/in_sdl.c`).

All line numbers re-verified with `rg -n` against this fork on 2026-09-13.
If the tree moves, re-run the Task 1 greps and update every number below.

## Hook sites

### 1. Movement injection — `CL_SendCmd`

`Quake/client/cl_main.c:673` (`CL_SendCmd`):

```c
CL_BaseMove (&cmd);   // cl_main.c:683 — keyboard movement
IN_Move (&cmd);       // cl_main.c:686 — mouse/external controllers
CL_SendMove (&cmd);   // cl_main.c:689 — serialize + send
```

Inject MCP input state between `IN_Move` and `CL_SendMove`, mirroring
how `IN_Move` adds to the command built by `CL_BaseMove`.

Movement source details (`Quake/client/cl_input.c`):

| Symbol | Location | Notes |
|---|---|---|
| `CL_BaseMove` | `cl_input.c:283` | keyboard movement baseline |
| `CL_SendMove` | `cl_input.c:332` | builds + sends `usercmd_t` |
| attack/jump bits + impulse | `cl_input.c:364-375` | only attack (bit 0) and jump (bit 1) serialized; `in_impulse` written then cleared |
| `+use` registered | `cl_input.c:437` | button exists as a command but is NOT in the movement packet — not a baseline capability |
| startup drop | `cl_input.c:391-394` | first two movement messages dumped (`++cl.movemessages <= 2`); `gameplay_ready` requires `movemessages > 2` (counter declared `client.h:150`) |

### 2. Frame pump — `_Host_Frame`

`Quake/host.c:644` (`_Host_Frame`):

```c
if (!Host_FilterTime (time))   // host.c:658-659 — early return when over host_maxfps
    return;
Sys_SendKeyEvents ();          // host.c:666
IN_Commands ();                // host.c:669
Access_Frame (...);            // host.c:672
```

`MCP_Poll` must run **before** the `Host_FilterTime` early return
(host.c:658-659), otherwise filtered frames stall the 100 ms release path.

Scheduling references:

| Symbol | Location | Notes |
|---|---|---|
| `Host_FilterTime` | `host.c:506` | frame-rate cap gate |
| `Host_ServerFrame` / `_Host_ServerFrame` | `host.c:579` / `host.c:565` | local-server simulation step |
| `Sys_SendKeyEvents` | called `host.c:666` | key event pump |
| `host_maxfps` | `host.c:59`, default `72` | frame-rate cap for `Host_FilterTime` |
| outer loop | `Quake/platform/sys_unix.c` (`Host_Frame` call :416) | client frames otherwise unthrottled |

### 3. Modal dialog pump — `SCR_ModalMessage`

`Quake/render/gl_screen.c:739` (`SCR_ModalMessage`):

```c
SCR_UpdateScreen ();            // gl_screen.c:749 — draw dialog frame
...
do {
    key_count = -1;
    Sys_SendKeyEvents ();       // gl_screen.c:757 — modal key-wait loop (754-758)
} while (...);
```

The modal loop pumps `Sys_SendKeyEvents` only, so the bridge socket poll
needs a one-line hook there (or a poll inside the SDL pump path), or
`quake_ui` input starves during dialogs. `Key_Event` injection flows
through the existing modal pump.

UI dispatch references:

| Symbol | Location | Notes |
|---|---|---|
| `Key_Event` | `Quake/client/keys.c:599` | entry point for injected UI keys |
| `keydest_t` | `Quake/client/keys.h:121` | `key_game / key_console / key_message / key_menu` — route UI input by `key_dest` |
| `SCR_UpdateScreen` | `gl_screen.c:824` | full frame composition; capture after world/HUD/menu, before present |
| `GL_BeginRendering` viewport | `gl_screen.c:852` | sets `glx/gly/glwidth/glheight` |
| `glReadPixels` shot path | `gl_screen.c:630` | RGB capture with RGB-to-BGR swap; reuse geometry, no disk files on repeat path |

## Observation sources

- Frame id: `host_framecount` (`Quake/host.c:45`, declared
  `Quake/common/quakedef.h:276`, incremented `host.c:744`).
  Incremented every frame, never reset.
- Console tail: `con_text` ring (`Quake/client/console.c:43`,
  `CON_TEXTSIZE 16384`, `con_linewidth` :31, `con_current` :41,
  `con_totallines` :40).

## Mouse-only arbitration

`access_mouseonly` (`Quake/client/cl_access.c:40`) defaults to `"1"`.
`Access_Frame` runs every frame (`host.c:672`) with early-outs on
`access_mouseonly == 0` (`cl_access.c:182,239`; registered :211).
MCP input must be deterministic under both states; never alter
access-module behavior when `access_mouseonly` is 0.

## Op map (`Quake/host_cmd.c` registrations :1881-1912, plus `wait` in `Quake/common/cmd.c:438`)

| Op | Engine command | Status |
|---|---|---|
| `new_game` | `map <firstmap>` (`Host_Map_f`, host_cmd.c:256) | exists (`map` registered :1886) |
| `restart` | `restart` (`Host_Restart_f`, :1887) | exists |
| `load_map` | `map` / `changelevel` (`Host_Changelevel_f`, :1888) | exists |
| `save` / `load` | `save` / `load` (`Host_Savegame_f` :465, `Host_Loadgame_f` :561) | exist (:1911-1912) |
| `kill` | `kill` (`Host_Kill_f`, :1904) | exists |
| `pause` | `pause` (`Host_Pause_f`, :1905) | exists (explicit state, no toggle) |
| `list_maps` | — | DOES NOT EXIST — sidecar gamedir inventory |
| `list_saves` | — | DOES NOT EXIST — sidecar gamedir inventory |

## Death/respawn flow — UNVERIFIED

Death/respawn semantics (`kill`, death state, respawn path) are marked
**UNVERIFIED**. Task 8 must verify actual engine behavior before the
`respawn` op is specified. Do not assume `kill` + respawn round-trips.

## Capability matrix (as measured, 2026-09-14)

| Capability | Surface | Measured behavior |
|---|---|---|
| Bounded action | `quake_act` -> bridge `act` | Axes forward/strafe/vertical [-1,1] scaled through `cl_forwardspeed`/`cl_sidespeed`/`cl_upspeed`; `run` multiplies by `cl_movespeedkey`. Yaw/pitch deltas applied once, pitch clamped +80/-70. Jump `none`/`tap`/`hold`. Attack and weapon impulse 1..8 carried in the usercmd bits/impulse only; `in_attack`/`in_jump`/`in_impulse` are never written. Wall cap 5 s; tick budget 1..72 counted from completed simulation steps. |
| Timing modes | `quake_control mode=stepped\|realtime` | Realtime is the default. Stepped freezes `CL_SendCmd`/`Host_ServerFrame`/`host_time` while idle and advances exactly N steps per ticks act; `duration_ms` is refused in stepped mode (`UNSUPPORTED_CAPABILITY`). World-mutating ops temporarily resume realtime and restore stepped. |
| Axes/turn signs | bridge merge | Forward/right/up positive; positive yaw turns right (`viewangles[YAW] -= delta`); positive pitch looks up (`viewangles[PITCH] -= delta`, engine pitch is positive-down). |
| Weapon switching | `quake_act weapon_id` | `impulse 1..8`; observed `impulse 1` switching to the axe (current-ammo stat 25 -> 0). |
| UI keys/text | `quake_ui` | Engine `Key_Event(int, qboolean)` with the fork's key codes; escape opens/closes the menu (state `ui` 0 -> 3 -> 0) and the menu frame is delivered as an image. Text types printable ASCII into a live console/chat field only, never submits. |
| Console | `quake_console` | Allowlisted commands with typed argument validators (never a raw string): status, version, skill, god, noclip, give, impulse, kill, pause, save, load, map, restart, changelevel, connect, disconnect, quit, screenshot, toggleconsole. `god`, `pause`, `impulse` observed executing; `kill` observed with no effect (see the acceptance matrix). |
| Settings | `quake_config` | Curated cvar read list plus `access_*` readable; only `access_mouseonly` writable (other access cvars hold command strings). |
| Frame formats | bridge capture -> `vision.py` | `glReadPixels` raw 8-bit RGB, bottom-up, HUD included; converted to PNG by default (JPEG optional), longest edge 1280 with no upscaling, 2-slot ring, 32 MiB source cap, `GL_PACK_ALIGNMENT` 1. Unavailable readback -> `RENDER_UNAVAILABLE`; no fresh frame -> `FRAME_TIMEOUT`; evicted -> `FRAME_EXPIRED`. |
| Telemetry policy | `quake_observe`/`quake_act telemetry=` | `hud` (default) keeps health/ammo/dead with provenance; `pixels_only` strips gameplay telemetry and derived dead flag. |
| State snapshot | `quake_state` / observation state group | epoch, world_gen (exact: bumped in `SV_SpawnServer`), control_rev, frame (completed simulation step), time, map, mode, pos, angles, health, ammo, ui, loading, dead, intermission, signon, movemessages. |
| Lifecycle | `quake_start`/`status`/`game`/`stop`, `attach` | Owned profile `local` launches the MCP build with `-mcp_port`/`+mcp_enabled 1` and waits for the token + ping; map ids come from validated pak/loose inventories; save slots are confined and traversal-checked; attached instances refuse `stop`. |
| Controller lease | `quake_control`/`quake_release` | Acquire requires neutral physical attack/jump; server beats every 500 ms; bridge expires after 2 s of silence (an in-flight action defers expiry and refreshes on completion); release/revoke clears synthetic input and is idempotent. |
| Dedup/retry | bridge receipt ring (64) | Identity `(lease, action_id, epoch)` plus an FNV-1a argument hash; duplicate returns the receipt, different arguments `POLICY_DENIED`, consumed sequence without a ring hit `RESULT_EXPIRED`, precondition mismatch `STALE_STATE`; the Python client retries a dropped connection once only when an action_id is present. |
