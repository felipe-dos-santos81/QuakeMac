# Walk mode: face map center + 50% button opacity

Revision of the mouse-only control scheme. Parent specs:
`docs/superpowers/2026-08-30-mouse-only-control.md` (mode/axis model) and
`docs/superpowers/2026-09-11-walk-toggle-button-centered-opacity.md`
(HUD button). This change does two things only: (1) drops the HUD
button's moving-state opacity from 70% to 50%, and (2) makes entering
Walk mode snap the view to face the loaded map's bounding-box center
instead of only leveling the pitch.

Everything else is unchanged: MOUSE1 fires, a MOUSE1 double-click jumps,
MOUSE2 (or clicking the centered HUD button) toggles Look/Walk, Walk
mode moves on Y and sidesteps on X, Look mode aims on X/Y, and during
demo playback any click opens the main menu. The delta-based movement
model is not touched.

## Scope

- `ACCESS_BTN_ALPHA` `0.7f` -> `0.5f`.
- On entering Walk mode, set `cl.viewangles[PITCH] = 0` (existing) **and**
  set `cl.viewangles[YAW]` so the view faces the world-model bounding-box
  midpoint.
- No new cvars, no new files, no render-layer change.

## Current state

`Quake/client/cl_access.c` `Access_ToggleMode_f` already levels the view
on Walk entry via `cl.viewangles[PITCH] = 0` (cl_access.c:138) and leaves
yaw wherever the player was facing. The HUD button is drawn at
`ACCESS_BTN_ALPHA` (0.7f) by `Access_DrawHUD`. The world model is
available as `cl.worldmodel` (a `model_s *` whose `mins`/`maxs` bound the
whole `.bsp`, model.h:314); the local player's origin is
`cl_entities[cl.viewentity].origin`.

## Design

### 1. Opacity (client module)

In `cl_access.c` change one constant:

```c
#define ACCESS_BTN_ALPHA     0.7f
```

to:

```c
#define ACCESS_BTN_ALPHA     0.5f
```

The idle fade (`ACCESS_BTN_IDLE_MS 500` -> fully opaque) is unchanged.

### 2. Face map center (client module)

Two small static helpers added to `cl_access.c`, then a call from the
Walk branch of `Access_ToggleMode_f`.

`static void Access_FaceMapCenter (void)`:
- Guard `if (!cl.worldmodel) return;` (no usable bounds before signon).
- Compute `mid = (cl.worldmodel->mins + cl.worldmodel->maxs) / 2`.
- Guard the degenerate case: `cl_entities[cl.viewentity].origin` is
  exactly at `mid`, so `atan2` of the null vector is undefined — skip the
  yaw snap when `fabs(dx) + fabs(dy)` is near zero.
- `cl.viewangles[PITCH] = 0;`
- `cl.viewangles[YAW] = Access_YawToPoint (mid,
  cl_entities[cl.viewentity].origin);`

`static float Access_YawToPoint (vec3_t target, vec3_t origin)`:
- `dx = target[0] - origin[0]; dy = target[1] - origin[1];`
- return `atan2 (dy, dx) * (180.0f / M_PI)`. Quake stores yaw in degrees
  with forward `(cos yaw, sin yaw)` in the XY plane, so the un-adjusted
  `atan2` result is the direct yaw; no range normalization needed
  (Quake accepts unbounded view angles).

The Walk branch of `Access_ToggleMode_f` replaces its current
`cl.viewangles[PITCH] = 0;` lines with a call to `Access_FaceMapCenter`:

```c
	access_lastinput = realtime;
	Access_Log ("walk mode");
	Access_Label ("WALK MODE");
	Access_Sound ("misc/menu1.wav");
	Access_FaceMapCenter ();
```

`Access_FaceMapCenter` keeps setting `PITCH = 0`, so no behavior of the
pitch leveling is lost.

## Invariants

- When `access_mouseonly` is 0, this module is inert and every input path
  is byte-identical to vanilla (unchanged).
- `cl_access.c` never calls `gl_*` directly (unchanged; no render change
  here).
- Look mode and the movement model are untouched; only the Walk-entry
  view angle and the button alpha change.

## Edge cases and risks

- **Null direction:** if the player is at the map midpoint, the yaw snap
  is skipped (degenerate `atan2` arg), leaving yaw where it was. Harmless.
- **`cl.worldmodel` unset:** early guard returns without touching view
  angles.
- **Yaw convention sign:** Quake forward is `(cos yaw, sin yaw)`, so
  `atan2(dy, dx)` faces the target directly; verified against
  `AngleVectors` (mathlib.c). If the player ends up facing away, the sign
  is wrong — the runtime smoke catches it.
- **Crosshair overlap:** unchanged from the 2026-09-11 work; the centered
  button overlaps the (disabled) crosshair. Noted, not disguised.

## Testing

- Build oracle: `make clean && make build-release build-server
  build-client` from the repo root (no tests/lint/CI — the build is the
  verification).
- Runtime smoke (user game data): `make run`, then:
  - toggle into Walk mode by MOUSE2 and by clicking the centered button;
  - confirm the view pitches level AND yaw swings to face the map center;
  - confirm the button reads 50% while the mouse moves and solid after
    ~500 ms of stillness;
  - confirm Look mode still aims freely and the movement model is
    unchanged.