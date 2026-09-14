# QuakeMCP engine integration — hook inventory

Fork commit: `0c8a6fc`. Scope: glquake (`Quake/` tree) only.
Renderer: OpenGL (`Quake/platform/gl_vidsdl.c`).
Platform pump: SDL (`Quake/platform/in_sdl.c`).

All line numbers re-verified with `rg -n` against the tree at `e0e0eb0` on
2026-09-14. If the tree moves, re-run the greps for each symbol in the hook
inventory.

## Hook inventory

| Hook | File | Role |
|---|---|---|
| `MCP_Poll` | `Quake/host.c:664`, `Quake/render/gl_screen.c:766` | socket pump, modal wait loop included |
| `MCP_FreezeSim` | `Quake/host.c:694,706,723,732` | stepped-mode gate |
| `MCP_NoteTick` | `Quake/host.c:711,728` | completed-step counter |
| `MCP_Move` | `Quake/client/cl_main.c:693` | input merge in `CL_SendCmd` |
| `MCP_Buttons` | `Quake/client/cl_input.c:377` | button bits in `CL_SendMove` |
| `MCP_Impulse` | `Quake/client/cl_input.c:384` | one-shot impulse byte in `CL_SendMove` |
| `MCP_NoteWorldSpawn` | `Quake/server/sv_main.c:1068` | exact `world_gen` bump on every new world |
| `MCP_UiDraw` | `Quake/render/gl_screen.c:948` | takeover banner, drawn before `MCAP_Frame` while a lease is held |
| `MCP_UiMouseClick` | `Quake/platform/in_sdl.c:185` | left-click takeover before the access funnel |
| `MCAP_Frame` | `Quake/render/gl_screen.c:949` | framebuffer capture at end of `SCR_UpdateScreen` |

## Hook sites

### 1. Movement injection — `CL_SendCmd`

`Quake/client/cl_main.c:677` (`CL_SendCmd`):

```c
CL_BaseMove (&cmd);   // cl_main.c:687 — keyboard movement
IN_Move (&cmd);       // cl_main.c:690 — mouse/external controllers
MCP_Move (&cmd);      // cl_main.c:693 — MCP input state
CL_SendMove (&cmd);   // cl_main.c:696 — serialize + send
```

`MCP_Move` injects between `IN_Move` and `CL_SendMove`, mirroring how
`IN_Move` adds to the command built by `CL_BaseMove`.

Movement source details (`Quake/client/cl_input.c`):

| Symbol | Location | Notes |
|---|---|---|
| `CL_BaseMove` | `cl_input.c:287` | keyboard movement baseline |
| `CL_SendMove` | `cl_input.c:336` | builds + sends `usercmd_t` |
| attack/jump bits + impulse | `cl_input.c:368-388` | only attack (bit 0) and jump (bit 1) serialized; the engine writes and clears `in_impulse`; MCP never assigns it — the pending MCP impulse merges at :377-384 only when no human impulse is pending that frame |
| `+use` registered | `cl_input.c:450` | button exists as a command but is NOT in the movement packet — not a baseline capability |
| startup drop | `cl_input.c:407-408` | first two movement messages dumped (`++cl.movemessages <= 2`); `gameplay_ready` requires `movemessages > 2` (counter declared `client.h:150`) |

### 2. Frame pump — `_Host_Frame`

`Quake/host.c:650` (`_Host_Frame`):

```c
MCP_Poll ();                   // host.c:664 — bridge pump, before the frame gate
...
if (!Host_FilterTime (time))   // host.c:668-669 — early return when over host_maxfps
    return;
Sys_SendKeyEvents ();          // host.c:676
IN_Commands ();                // host.c:679
Access_Frame (...);            // host.c:682
```

`MCP_Poll` runs **before** the `Host_FilterTime` early return
(host.c:668-669), so filtered frames cannot stall the 100 ms release path.

Scheduling references:

| Symbol | Location | Notes |
|---|---|---|
| `Host_FilterTime` | `host.c:512` | frame-rate cap gate |
| `Host_ServerFrame` / `_Host_ServerFrame` | `host.c:585` / `host.c:571` | local-server simulation step |
| `Sys_SendKeyEvents` | called `host.c:676` | key event pump |
| `host_maxfps` | `host.c:65`, default `72` | frame-rate cap for `Host_FilterTime` |
| outer loop | `Quake/platform/sys_unix.c` (`Host_Frame` call :416) | client frames otherwise unthrottled |

### 3. Modal dialog pump — `SCR_ModalMessage`

`Quake/render/gl_screen.c:742` (`SCR_ModalMessage`):

```c
SCR_UpdateScreen ();            // gl_screen.c:752 — draw dialog frame
...
do {
    key_count = -1;
    Sys_SendKeyEvents ();       // gl_screen.c:764 — modal key-wait loop (761-774)
    MCP_Poll ();                // gl_screen.c:766 — bridge pump inside the wait
} while (...);
```

The modal wait loop pumps `Sys_SendKeyEvents` and `MCP_Poll`; when the
bridge entered the dialog and a capture is pending, the loop renders once
(`gl_screen.c:771-772`) so observe can capture the dialog. `Key_Event`
injection flows through this existing pump.

UI dispatch references:

| Symbol | Location | Notes |
|---|---|---|
| `Key_Event` | `Quake/client/keys.c:599` | entry point for injected UI keys |
| `keydest_t` | `Quake/client/keys.h:121` | `key_game / key_console / key_message / key_menu` — route UI input by `key_dest` |
| `SCR_UpdateScreen` | `gl_screen.c:845` | full frame composition; capture after world/HUD/menu, before present |
| `GL_BeginRendering` viewport | `gl_screen.c:873` | sets `glx/gly/glwidth/glheight` |
| `glReadPixels` shot path | `gl_screen.c:633` | RGB capture with RGB-to-BGR swap; reuse geometry, no disk files on repeat path |

## Observation sources

- Frame id: `host_framecount` (`Quake/host.c:51`, declared
  `Quake/common/quakedef.h:276`, incremented `host.c:771`).
  Incremented every frame, never reset.
- Console tail: `con_text` ring (`Quake/client/console.c:43`,
  `CON_TEXTSIZE 16384`, `con_linewidth` :31, `con_current` :41,
  `con_totallines` :39).

## Mouse-only arbitration

`access_mouseonly` (`Quake/client/cl_access.c:40`) defaults to `"1"`.
`Access_Frame` runs every frame (`host.c:682`) with early-outs on
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
| `tail` | — | bridge read: last console lines (`MCP_ConsoleTail`, `q_mcp.c`) |
| `status` | — | bridge read: newest receipt in the ledger, or the `action_id` match |

Every mutating op (`exec`, `key`, `control mode`, `cvar` set, `act`) carries
the mutation envelope: `lease`, `epoch`, `seq`, optional `action_id`, and the
optional pinned `world_generation` / `control_revision`. The bridge answers
from the receipt ring first (a match replies `"duplicate":true`; the same
identity with different arguments is `POLICY_DENIED`), then checks the live
lease/epoch/generations (`STALE_STATE`), then the sequence high-water (a spent
`seq` with no receipt is `RESULT_EXPIRED`). Reads (`ping`, `state`,
`observe`, `tail`, `cvar` without `value`) carry no envelope.

## Death/respawn flow (observed 2026-09-14)

Traced on the MCP build with a live controller lease against the retail id1
data (engine log under `$TMPDIR/quakemcp-logs/`; the measured table below is
the record). The suspected mechanism was half right: a stepped session queues
the forwarded command, but a realtime kill does **not** leave a corpse —
the shipped progs restart the level.

| Mode | Command (`exec`) | Observed health / dead | Tail / other |
|---|---|---|---|
| realtime | `kill` | 100 / false at +0.0 s; 0 / true at +0.026 s (restart sign-on); 100 / false at +0.080 s | engine log `player suicides` then `SpawnServer: start`; `world_gen` +1, `movemessages` reset |
| stepped | `kill` | unchanged for 1 s (frame/time/movemessages frozen) | forwarded command queues; after a 0.25 s realtime window: same as realtime, `world_gen` +1, mode restored to stepped (3/3 runs settled) |
| realtime | `pause 1` | alive | tail `player paused the game` — forwarding itself works |
| realtime | `map end` | -1..-3 / true, stable over 30 s | tail `Shub-Niggurath's Pit`, `player was fed to the Rotfish`; `intermission` stays false |
| realtime, dead | `act attack=1` | 100 / false | `world_gen` +1, `movemessages` reset — single-player respawn restarts the level |

Progs evidence (disassembly of `progs.dat` at the root of
`game/id1/pak0.pak`):
`respawn`'s single-player branch is `localcmd ("restart\n")`;
`PlayerDeathThink` needs a tick with all buttons released
(`deadflag = DEAD_RESPAWNABLE`) before an attack tap calls `respawn()`.
`svc_intermission` is written by the QC `execute_changelevel` (a level-exit
path) — no console command reaches it, so `map end` cannot show
intermission.

`quake_game respawn` is the attack tap above: `NOT_READY` unless
`state.dead`; it runs the clock, settles one buttons-released window, taps
attack across a death think (`attack=1, ticks=8, respawn=1`, the bridge
relaxing its readiness gate for that flag), and repeats until alive or the
bounds pass, returning `{respawned, waited_ms}`.

## Capability matrix (as measured, 2026-09-14)

| Capability | Surface | Measured behavior |
|---|---|---|
| Bounded action | `quake_act` -> bridge `act` | Axes forward/strafe/vertical [-1,1] scaled through `cl_forwardspeed`/`cl_sidespeed`/`cl_upspeed`; `run` multiplies by `cl_movespeedkey`. Yaw/pitch deltas applied once, pitch clamped +80/-70. Jump `none`/`tap`/`hold`. Attack and weapon impulse 1..8 carried in the usercmd bits/impulse only; `in_attack`/`in_jump`/`in_impulse` are never written; a pending human impulse wins its frame, a pending MCP impulse is deferred to the next serialization. Wall cap 5 s; tick budget 1..72 counted from completed simulation steps. |
| Timing modes | `quake_control mode=stepped\|realtime` | Owned sessions start stepped (`quake_start` sets the mode; a held button falls back to realtime and says why); `quake_attach` changes nothing. Stepped freezes `CL_SendCmd`/`Host_ServerFrame`/`host_time` while idle and advances exactly N steps per ticks act; `duration_ms` is refused in stepped mode (`UNSUPPORTED_CAPABILITY`). World-mutating ops temporarily resume realtime and restore stepped. |
| Axes/turn signs | bridge merge | Forward/right/up positive; positive yaw turns right (`viewangles[YAW] -= delta`); positive pitch looks up (`viewangles[PITCH] -= delta`, engine pitch is positive-down). |
| Weapon switching | `quake_act weapon_id` | `impulse 1..8`; observed `impulse 1` switching to the axe (current-ammo stat 25 -> 0). |
| UI keys/text | `quake_ui` | Engine `Key_Event(int, qboolean)` with the fork's key codes; escape opens/closes the menu (state `ui` 0 -> 3 -> 0) and the menu frame is delivered as an image. Text types printable ASCII into a live console/chat field only, never submits. |
| Console | `quake_console` | Allowlisted commands with typed argument validators (never a raw string): status, version, skill, god, noclip, give, impulse, kill, pause, map, restart, changelevel, quit, screenshot, toggleconsole. `save`/`load`/`connect`/`disconnect` are denied (`quake_game` owns saves and the design forbids remote-connection commands). `god`, `pause`, `impulse` observed executing; `kill` observed forwarding and executing (engine log `player suicides`; single player answers with a level restart — see the death-flow table). Gameplay-class commands run a bounded realtime flush in a stepped session. |
| Settings | `quake_config` | Curated cvar read list plus `access_*` readable; only `access_mouseonly` writable (other access cvars hold command strings). |
| Frame formats | bridge capture -> `vision.py` | `glReadPixels` raw 8-bit RGB, bottom-up, HUD included; converted to PNG by default (JPEG optional), longest edge 1280 with no upscaling, 2-slot ring, 32 MiB source cap, `GL_PACK_ALIGNMENT` 1. Unavailable readback -> `RENDER_UNAVAILABLE`; no fresh frame -> `FRAME_TIMEOUT`; a retried action whose cached result frame was evicted -> `FRAME_EXPIRED` (server receipt LRU, never a re-shoot). |
| Telemetry policy | `quake_observe`/`quake_act telemetry=` | `hud` (default) keeps health/ammo/dead with provenance; `pixels_only` strips gameplay telemetry and derived dead flag. |
| State snapshot | `quake_state` / observation state group | epoch, world_gen (exact: bumped in `SV_SpawnServer`), control_rev, frame (completed simulation step), time, map, mode, pos, angles, health, ammo, ui, loading, dead, intermission, signon, movemessages. |
| Lifecycle | `quake_start`/`status`/`game`/`stop`, `attach` | Owned profile `local` launches the MCP build with `-mcp_port`/`+mcp_enabled 1` and waits for the token + ping; map ids come from validated pak/loose inventories; save slots are confined and traversal-checked; attached instances refuse `stop`. |
| Controller lease | `quake_control`/`quake_release` | Acquire requires neutral physical attack/jump; server beats every 500 ms; bridge expires after 2 s of silence uniformly — beats land on the second connection while a reply is deferred, and expiry interrupts a deferred act at 2 s instead of its 5 s cap; release/revoke clears synthetic input and is idempotent. |
| Dedup/retry | bridge receipt ring (64) | Identity `(lease, action_id, epoch)` plus an FNV-1a argument hash; a duplicate replies `"duplicate":true` with the recorded result, different arguments `POLICY_DENIED`, consumed sequence without a ring hit `RESULT_EXPIRED`, precondition mismatch `STALE_STATE`; the Python client retries a dropped connection once only when an action_id is present, and a retry whose cached observation was evicted raises `FRAME_EXPIRED` (never a re-shoot). |
