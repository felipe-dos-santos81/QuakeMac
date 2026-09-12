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

---

**Part 2 — implementation plan**

# Walk face map center + 50% button opacity Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use
> superpowers:subagent-driven-development (recommended) or
> superpowers:executing-plans to implement this plan task-by-task. Steps
> use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Drop the HUD Walk/Look button's moving-state opacity to 50% and
make entering Walk mode snap the view (pitch level + yaw aiming at the
loaded map's bounding-box midpoint) instead of only leveling pitch.

**Architecture:** Two file-local changes in `Quake/client/cl_access.c`:
change the `ACCESS_BTN_ALPHA` constant, then add a small yaw helper
(`Access_YawToPoint`) plus an entry effect (`Access_FaceMapCenter`) that
the Walk branch of `Access_ToggleMode_f` calls in place of its current
`cl.viewangles[PITCH] = 0;`.

**Tech Stack:** C (id-era style), math from `<math.h>` (`atan2`, `fabs`,
`M_PI`). Build is the verification — there are no unit tests, lint, or CI.

## Global Constraints

- Build oracle (must pass before claiming done): `make clean && make
  build-release build-server build-client` from the repo root.
- No tests/lint/CI; the build is the only automated verification.
- id-era C style: tabs (not spaces), K&R braces, `/* banner */` comment
  blocks.
- When `access_mouseonly` is 0 the module is inert and every input path
  is byte-identical to vanilla — do not disturb the early-return guards.
- `cl_access.c` must never call `gl_*` directly; this change touches no
  render code.
- The movement model (`Access_MouseMove` delta/throttle/velocity) and
  Look mode are untouched.
- Git: stage explicit paths only, never `git add -A`; push only when
  explicitly asked.

---

### Task 1: 50% button opacity

**Files:**
- Modify: `Quake/client/cl_access.c` (constant + its comment)

**Interfaces:**
- Consumes: nothing new.
- Produces: the value change only; `Access_DrawHUD` picks it up via the
  existing `ACCESS_BTN_ALPHA` macro (no signature change).

- [ ] **Step 1: Change the opacity constant**

The button state section currently reads:

```c
/* HUD mode-button: centered, 70% opaque while the mouse moves, fully
   opaque once the mouse rests.  Geometry is derived in
   Access_ButtonRect from a 4-character label ("WALK"/"LOOK") plus
   padding, in vid.width x vid.height screen space. */
#define ACCESS_BTN_ALPHA     0.7f
#define ACCESS_BTN_IDLE_MS   500
#define ACCESS_BTN_PAD_X     6
#define ACCESS_BTN_PAD_Y     2
```

Change it to:

```c
/* HUD mode-button: centered, 50% opaque while the mouse moves, fully
   opaque once the mouse rests.  Geometry is derived in
   Access_ButtonRect from a 4-character label ("WALK"/"LOOK") plus
   padding, in vid.width x vid.height screen space. */
#define ACCESS_BTN_ALPHA     0.5f
#define ACCESS_BTN_IDLE_MS   500
#define ACCESS_BTN_PAD_X     6
#define ACCESS_BTN_PAD_Y     2
```

Note: `Access_DrawHUD`'s idle fade (`ACCESS_BTN_IDLE_MS 500` -> fully
opaque) is untouched.

- [ ] **Step 2: Build**

Run: `make build-release` from the repo root.
Expected: exit 0, no new warnings.

- [ ] **Step 3: Commit**

```bash
git add Quake/client/cl_access.c
git commit -m "access: render Walk/Look button at 50% opacity"
```

---

### Task 2: Face map center on Walk entry

**Files:**
- Modify: `Quake/client/cl_access.c` (add helpers, edit the Walk branch)

**Interfaces:**
- Consumes: `cl.worldmodel` (`model_s *` with `vec3_t mins, maxs`,
  model.h:314), `cl_entities[cl.viewentity].origin`, `cl.viewangles`,
  and `<math.h>` (`atan2`, `fabs`, `M_PI`).
- Produces: two file-local statics (no header change):
  - `static float Access_YawToPoint (vec3_t target, vec3_t origin)` —
    yaw in degrees facing `target` from `origin`.
  - `static void  Access_FaceMapCenter (void)` — levels pitch and sets
    yaw to face the map midpoint.

- [ ] **Step 1: Add the two helpers**

Immediately before `Access_ToggleMode_f` (the first function under the
`/* commands */` banner), insert:

```c
/*
================
Access_YawToPoint

Yaw (degrees) that faces the given world point from the given origin.
Quake forward is (cos yaw, sin yaw) in the XY plane, so atan2(dy,dx)
is the direct yaw; no range normalization is needed (sin/cos accept
any view angle).
================
*/
static float Access_YawToPoint (vec3_t target, vec3_t origin)
{
	float	dx = target[0] - origin[0];
	float	dy = target[1] - origin[1];

	return atan2 (dy, dx) * (180.0f / M_PI);
}

/*
================
Access_FaceMapCenter

Walk-mode entry effect: level the view to the horizon and aim the yaw
at the loaded map's bounding-box midpoint.  Guards the case where the
player is already at that midpoint so the snap is not driven by a null
direction.
================
*/
static void Access_FaceMapCenter (void)
{
	vec3_t	mid;
	vec3_t	org;
	float	dx, dy;

	if (!cl.worldmodel)
		return;

	mid[0] = (cl.worldmodel->mins[0] + cl.worldmodel->maxs[0]) * 0.5f;
	mid[1] = (cl.worldmodel->mins[1] + cl.worldmodel->maxs[1]) * 0.5f;
	mid[2] = (cl.worldmodel->mins[2] + cl.worldmodel->maxs[2]) * 0.5f;
	VectorCopy (cl_entities[cl.viewentity].origin, org);

	cl.viewangles[PITCH] = 0;

	dx = mid[0] - org[0];
	dy = mid[1] - org[1];
	if (fabs (dx) + fabs (dy) < 0.001f)
		return;			/* player at map midpoint: no meaningful yaw */

	cl.viewangles[YAW] = Access_YawToPoint (mid, org);
}
```

- [ ] **Step 2: Route the Walk branch through it**

The Walk branch of `Access_ToggleMode_f` currently reads:

```c
	if (access_mode == ACCESS_WALK)
	{
	/* entering walk mode levels the view: pitch snaps to the horizon,
	   yaw keeps whichever way the player was facing */
		cl.viewangles[PITCH] = 0;
		access_lastinput = realtime;
		Access_Log ("walk mode");
		Access_Label ("WALK MODE");
		Access_Sound ("misc/menu1.wav");
	}
```

Change it to:

```c
	if (access_mode == ACCESS_WALK)
	{
	/* entering walk mode recenters the view: pitch snaps to the horizon
	   and yaw swings to face the loaded map's bounding-box midpoint */
		access_lastinput = realtime;
		Access_Log ("walk mode");
		Access_Label ("WALK MODE");
		Access_Sound ("misc/menu1.wav");
		Access_FaceMapCenter ();
	}
```

`Access_FaceMapCenter` sets `PITCH = 0` itself, so the pitch leveling
behavior is preserved (and the stray `cl.viewangles[PITCH] = 0` line is
removed rather than duplicated).

- [ ] **Step 3: Full build oracle**

Run: `make clean && make build-release build-server build-client` from
the repo root.
Expected: exit 0, no warnings.

- [ ] **Step 4: Runtime smoke (user game data required)**

Run: `make run`. Then:
- toggle into Walk mode by MOUSE2 and by clicking the centered button;
- confirm the view pitches level AND yaw swings to face the map center;
- confirm the button reads 50% while the mouse moves and solid after
  ~500 ms of stillness;
- confirm Look mode still aims freely (yaw and pitch) and the movement
  model is unchanged (Y = forward, X = sidestep).

- [ ] **Step 5: Commit**

```bash
git add Quake/client/cl_access.c
git commit -m "access: face map center on walk mode entry"
```

- [ ] **Step 6: Update the running Fixes Ledger**

Append one line to the ledger at the end of
`docs/superpowers/2026-08-29-quake-apple-silicon.md` noting this change
(walk mode faces map center; button now 50% opaque), matching the
ledger's existing one-line-per-fix style.