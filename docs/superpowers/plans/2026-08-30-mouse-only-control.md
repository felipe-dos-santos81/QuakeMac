# Mouse-Only Control Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** A player can control glquake — gameplay, menus, saving —
with only a pointing device and its buttons: Look/Walk mode toggle,
throttle and velocity movement profiles, gesture detection (long-press
layer, double-click), cruise control, point-and-click menus with a
Mouse options page, HUD feedback, and a ready-to-use autoexec.cfg.

**Architecture:** One new module `Quake/client/cl_access.c` owns the
whole feature (modes, gestures, movement pipeline, cruise, safety
resets, menu pointer, HUD, sounds). Existing files get small explicit
seams; `access_mouseonly 0` restores byte-identical vanilla behavior.
Deliverables: source patch (this plan), `configs/autoexec-mouseonly.cfg`,
and the validation runbook (Task 12).

**Tech Stack:** C (glquake, `Quake/` tree only), SDL3 (events,
ticks, cursor state), no new dependencies, no new assets (feedback
sounds come from pak0).

**Spec:** `docs/superpowers/specs/2026-08-30-mouse-only-control-design.md`

## Global Constraints

- **Build oracle** — `make clean && make build-release build-server build-client` from the repo root must exit 0 (AGENTS.md). There are no tests, no lint — the build plus runtime proofs are the verification. Intermediate per-task checks may use `make build-release` alone.
- **Warnings allowed, errors zero** — never build with `-Werror`.
- **Tree scope** — `Quake/` (glquake) only. Never touch `QuakeWorld/` (isolation rule, CONTEXT.md).
- **Never lock the cursor** — do not engage SDL relative mouse mode anywhere; `install_grabs()` stays as-is.
- **Kill switch** — with `access_mouseonly 0` the input path must behave exactly like the unpatched port.
- **No new assets** — sounds only from existing pak0 entries (`misc/menu1.wav`, `misc/menu2.wav`, `misc/menu3.wav`, `buttons/switch02.wav`).
- **Never commit game data** — nothing under `game/` is tracked.
- **Ledger** — engine touch points are recorded in the Fixes Ledger of `docs/superpowers/plans/2026-08-29-quake-apple-silicon.md` in Task 12.
- **Engine facts used by this plan (verified):** 72 fps cap in `Host_FilterTime` (host.c:505); `CL_SendCmd` → `CL_BaseMove` + `IN_Move` chain (cl_main.c:670-683); `CL_Init` (cl_main.c:717); `CL_Disconnect` (cl_main.c:99); menu pages are keyboard-only via `M_Keydown` (menu.c:3059); `SCR_UpdateScreen` (gl_screen.c:823) calls `Sbar_Draw` in three branches and `M_Draw` in the game branch; `GL_Set2D` (gl_draw.c:869) sets the 2D ortho space to `vid.width × vid.height`, which defaults to 640×480 (gl_vidsdl.c:391) — menu coordinates live in that space, so cursor mapping must use `vid.width/vid.height`, never a hardcoded 320×200; K_JOY1/2 + K_AUX* keynums are orphaned (keys.h); wheel keynums exist (K_MWHEELUP/DOWN 239/240) but are never emitted by in_sdl.c.

---

### Task 1: Frame-rate cvars (`host_maxfps`, `host_timescale`)

**Files:**
- Modify: `Quake/host.c` (cvar block near line 57, `Host_Init` registration near line 213, `Host_FilterTime` lines 501-522, `_Host_Frame` near line 645)

**Interfaces:**
- Consumes: nothing
- Produces: `cvar_t host_maxfps` (default "72") and `cvar_t host_timescale` (default "1"), both consumed by Task 3's module (walk-speed determinism, Mouse options page) and Task 11's autoexec.

- [ ] **Step 1: Declare the cvars**

In `Quake/host.c`, after the existing line:

```c
cvar_t	host_framerate = {"host_framerate","0"};	// set for slow motion
```

add:

```c
cvar_t	host_maxfps = {"host_maxfps","72"};		// frame-rate cap for Host_FilterTime
cvar_t	host_timescale = {"host_timescale","1"};	// scale simulation time (accessibility slow-down)
```

- [ ] **Step 2: Register them**

In `Host_Init`, next to `Cvar_RegisterVariable (&host_framerate);` add:

```c
	Cvar_RegisterVariable (&host_maxfps);
	Cvar_RegisterVariable (&host_timescale);
```

- [ ] **Step 3: Use `host_maxfps` in `Host_FilterTime`**

Replace the head of `Host_FilterTime`:

```c
qboolean Host_FilterTime (float time)
{
	realtime += time;

	if (!cls.timedemo && realtime - oldrealtime < 1.0/72.0)
		return false;		// framerate is too high
```

with:

```c
qboolean Host_FilterTime (float time)
{
	float		fpscap;

	realtime += time;

	fpscap = host_maxfps.value;
	if (fpscap < 10)
		fpscap = 10;

	if (!cls.timedemo && realtime - oldrealtime < 1.0/fpscap)
		return false;		// framerate is too high
```

Leave the `#ifdef FPS_20` block (host.c:552+) untouched — it is not compiled (`-DFPS_20` is absent from the Makefile).

- [ ] **Step 4: Apply `host_timescale` in `_Host_Frame`**

In `_Host_Frame` (host.c:633), immediately after:

```c
// decide the simulation time
	if (!Host_FilterTime (time))
		return;			// don't run too fast, or packets will flood out
```

add:

```c
// accessibility slow-down: scale simulation time without dropping frames
	if (host_timescale.value > 0 && host_timescale.value != 1)
		host_frametime *= host_timescale.value;
```

- [ ] **Step 5: Build**

Run: `make build-release`
Expected: exit 0, `Quake/build-macosx/glquake` relinked.

- [ ] **Step 6: Runtime proof**

Run: `make run` (needs `game/id1/pak0.pak`). In the console (tilde):

```
host_maxfps
host_timescale 0.5
```

Expected: first prints `72`; after the second, world motion (demo attract mode or a `map start`) is visibly half speed; `host_timescale 1` restores it. Quit.

- [ ] **Step 7: Commit**

```bash
git add Quake/host.c
git commit -m "host: host_maxfps + host_timescale cvars (mouse-only plan Task 1)"
```

---

### Task 2: Expose MOUSE4/5 and the wheel

**Files:**
- Modify: `Quake/client/keys.h` (K_MOUSE block, lines ~62-74)
- Modify: `Quake/client/keys.c` (keynames table, after `{"MOUSE3", K_MOUSE3}` at line ~93)
- Modify: `Quake/platform/in_sdl.c` (`HandleEvents` button/wheel cases)

**Interfaces:**
- Consumes: nothing from this plan
- Produces: `K_MOUSE4` (203), `K_MOUSE5` (204) keynums with `MOUSE4`/`MOUSE5` names bindable in cfg/console; `K_MWHEELUP/DOWN` events actually emitted (momentary down+up per notch). Task 3+ consumes all of them.

- [ ] **Step 1: keys.h — repurpose the orphaned JOY1/JOY2 slots**

In `Quake/client/keys.h`, replace:

```c
#define	K_MOUSE1		200
#define	K_MOUSE2		201
#define	K_MOUSE3		202

//
// joystick buttons
//
#define	K_JOY1			203
#define	K_JOY2			204
#define	K_JOY3			205
#define	K_JOY4			206
```

with:

```c
#define	K_MOUSE1		200
#define	K_MOUSE2		201
#define	K_MOUSE3		202
#define	K_MOUSE4		203
#define	K_MOUSE5		204

//
// joystick buttons (K_JOY1/K_JOY2 slots now belong to K_MOUSE4/5;
// the rest stay orphaned until a separate cleanup)
//
#define	K_JOY3			205
#define	K_JOY4			206
```

- [ ] **Step 2: keys.c — keynames entries**

In the keynames table, after the line `{"MOUSE3", K_MOUSE3},` add:

```c
	{"MOUSE4", K_MOUSE4},
	{"MOUSE5", K_MOUSE5},
```

- [ ] **Step 3: in_sdl.c — X1/X2 buttons and wheel**

In `HandleEvents`, replace the button case:

```c
		case SDL_EVENT_MOUSE_BUTTON_DOWN:
		case SDL_EVENT_MOUSE_BUTTON_UP:
			b = -1;
			if (event.button.button == SDL_BUTTON_LEFT)
				b = 0;
			else if (event.button.button == SDL_BUTTON_MIDDLE)
				b = 2;
			else if (event.button.button == SDL_BUTTON_RIGHT)
				b = 1;
			if (b >= 0)
				Key_Event(K_MOUSE1 + b, event.button.down);
			break;
```

with:

```c
		case SDL_EVENT_MOUSE_BUTTON_DOWN:
		case SDL_EVENT_MOUSE_BUTTON_UP:
			b = -1;
			if (event.button.button == SDL_BUTTON_LEFT)
				b = 0;
			else if (event.button.button == SDL_BUTTON_MIDDLE)
				b = 2;
			else if (event.button.button == SDL_BUTTON_RIGHT)
				b = 1;
			else if (event.button.button == SDL_BUTTON_X1)
				b = 3;
			else if (event.button.button == SDL_BUTTON_X2)
				b = 4;
			if (b >= 0)
				Key_Event(K_MOUSE1 + b, event.button.down);
			break;

		case SDL_EVENT_MOUSE_WHEEL:
			if (event.wheel.y > 0)
			{
				Key_Event(K_MWHEELUP, true);
				Key_Event(K_MWHEELUP, false);
			}
			else if (event.wheel.y < 0)
			{
				Key_Event(K_MWHEELDOWN, true);
				Key_Event(K_MWHEELDOWN, false);
			}
			break;
```

(SDL3 constants verified: `SDL_BUTTON_X1` = 4, `SDL_BUTTON_X2` = 5, `SDL_EVENT_MOUSE_WHEEL` with float `event.wheel.y`.)

- [ ] **Step 4: Build**

Run: `make build-release`
Expected: exit 0.

- [ ] **Step 5: Runtime proof**

Run: `make run`. Open the console and scroll the wheel — the console scrolls (proof K_MWHEELUP/DOWN reach the key system; keys.c:546-547 already mark them as console keys). Then:

```
bind MOUSE4 "echo mouse4 ok"
bind MOUSE5 "echo mouse5 ok"
```

Click the side buttons — both lines print. Quit.

- [ ] **Step 6: Commit**

```bash
git add Quake/client/keys.h Quake/client/keys.c Quake/platform/in_sdl.c
git commit -m "input: expose MOUSE4/MOUSE5 and wheel events (mouse-only plan Task 2)"
```

---

### Task 3: Module scaffold, Look-mode parity, seam wiring

**Files:**
- Create: `Quake/client/cl_access.h`
- Create: `Quake/client/cl_access.c`
- Modify: `Makefile` (`QUAKE_CORE_OBJS`, lines ~78-90)
- Modify: `Quake/client/cl_main.c` (`CL_Init` line 717, `CL_Disconnect` line 99)
- Modify: `Quake/host.c` (`_Host_Frame`, after `IN_Commands ();`)
- Modify: `Quake/platform/in_sdl.c` (`IN_MouseMove` early branch + button routing through the module)
- Modify: `Quake/render/gl_screen.c` (`SCR_UpdateScreen`, before `V_UpdatePalette ();`)

**Interfaces:**
- Consumes: `host_frametime` (quakedef.h:273), `sensitivity`, `m_yaw`, `m_pitch`, `m_side`, `in_strafe`, `cl`, `key_dest`, `Key_Event`, `Cvar_RegisterVariable`, `Cmd_AddCommand`, `Con_Printf`.
- Produces (used by later tasks and existing files):

```c
void Access_Init (void);
void Access_Frame (float frametime);
void Access_ButtonEvent (int keynum, int down, unsigned int ms);
void Access_MouseMove (usercmd_t *cmd, int mx, int my);
void Access_Reset (void);
void Access_DrawHUD (void);
void Access_MenuFrame (void);
int  Access_MenuItem (int index, int x, int y, int w, int h, int *cursor);
int  Access_MenuHovered (int index);
int  Access_ClickRect (int x, int y, int w, int h);
extern cvar_t access_mouseonly;
```

Menu seams are stubbed until Task 8; `Access_Frame`/`Access_DrawHUD` gain bodies in Tasks 4-7. Walk mode behaves like Look until Task 4 — toggling mid-Task-3 is harmless.

- [ ] **Step 1: Write `Quake/client/cl_access.h`**

```c
/*
cl_access.h — mouse-only control (accessibility) module interface.

Every entry point degrades to vanilla behavior when access_mouseonly
is 0. Spec: docs/superpowers/specs/2026-08-30-mouse-only-control-design.md

Engine convention: the including .c has already included quakedef.h
(this header deliberately includes nothing; quakedef.h has no guard).
*/

#ifndef CL_ACCESS_H
#define CL_ACCESS_H

void Access_Init (void);
void Access_Frame (float frametime);
void Access_ButtonEvent (int keynum, int down, unsigned int ms);
void Access_MouseMove (usercmd_t *cmd, int mx, int my);
void Access_Reset (void);
void Access_DrawHUD (void);

/* menu seams (bodies land in Task 8) */
void Access_MenuFrame (void);
int  Access_MenuItem (int index, int x, int y, int w, int h, int *cursor);
int  Access_MenuHovered (int index);
int  Access_ClickRect (int x, int y, int w, int h);

extern cvar_t access_mouseonly;

#endif /* CL_ACCESS_H */
```

- [ ] **Step 2: Write `Quake/client/cl_access.c` (scaffold state)**

```c
/*
cl_access.c — mouse-only control: Look/Walk modes, throttle & velocity
movement profiles, gesture engine, cruise control, safety resets,
point-and-click menus, HUD feedback.

Spec: docs/superpowers/specs/2026-08-30-mouse-only-control-design.md
Plan: docs/superpowers/plans/2026-08-30-mouse-only-control.md

Design invariant: when access_mouseonly is 0, this module is inert and
every input path is byte-identical to vanilla.
*/

#include <math.h>
#include <SDL3/SDL.h>

#include "quakedef.h"
#include "cl_access.h"

extern SDL_Window *sdl_window;	/* owned by gl_vidsdl.c */

#define ACCESS_LOOK 0
#define ACCESS_WALK 1

/* ---------------------------------------------------------------- cvars */

cvar_t	access_mouseonly = {"access_mouseonly", "1", true};
cvar_t	access_move_profile = {"access_move_profile", "0", true};
cvar_t	access_throttle_gain = {"access_throttle_gain", "0.002", true};
cvar_t	access_velocity_gain = {"access_velocity_gain", "1", true};
cvar_t	access_deadzone = {"access_deadzone", "0", true};
cvar_t	access_curve = {"access_curve", "1", true};
cvar_t	access_walkspeed = {"access_walkspeed", "190", true};
cvar_t	access_tremor = {"access_tremor", "0", true};
cvar_t	access_turnrate = {"access_turnrate", "240", true};
cvar_t	access_throttle_decay = {"access_throttle_decay", "0", true};
cvar_t	access_toggle_button = {"access_toggle_button", "202", true}; /* K_MOUSE3 */
cvar_t	access_longpress_button = {"access_longpress_button", "0", true};
cvar_t	access_longpress_ms = {"access_longpress_ms", "400", true};
cvar_t	access_layer_timeout = {"access_layer_timeout", "3", true};
cvar_t	access_layer_cmd1 = {"access_layer_cmd1", "save quick"};
cvar_t	access_layer_cmd2 = {"access_layer_cmd2", "togglemenu"};
cvar_t	access_doubleclick_button = {"access_doubleclick_button", "0", true};
cvar_t	access_doubleclick_ms = {"access_doubleclick_ms", "350", true};
cvar_t	access_doubleclick_command = {"access_doubleclick_command", ""};
cvar_t	access_cruise_speed = {"access_cruise_speed", "0", true};
cvar_t	access_idle_timeout = {"access_idle_timeout", "0", true};
cvar_t	access_hud = {"access_hud", "1", true};
cvar_t	access_sounds = {"access_sounds", "1", true};
cvar_t	access_log = {"access_log", "1", true};

/* ---------------------------------------------------------------- state */

static int		access_mode = ACCESS_LOOK;
static float		access_throttle;
static qboolean		access_cruise;

static void Access_Log (char *msg)
{
	if (access_log.value)
		Con_Printf ("access: %s\n", msg);
}

/* ------------------------------------------------------------- commands */

static void Access_ToggleMode_f (void)
{
	if (!access_mouseonly.value)
		return;
	access_mode = (access_mode == ACCESS_LOOK) ? ACCESS_WALK : ACCESS_LOOK;
	access_throttle = 0;
	if (access_mode == ACCESS_WALK)
		access_cruise = false;
	Access_Log (access_mode == ACCESS_WALK ? "walk mode" : "look mode");
}

static void Access_ToggleCruise_f (void)
{
	if (!access_mouseonly.value)
		return;
	if (access_mode == ACCESS_WALK)
	{
		Access_Log ("cruise refused (walk mode)");
		return;
	}
	access_cruise = !access_cruise;
	Access_Log (access_cruise ? "cruise on" : "cruise off");
}

/* ------------------------------------------------------------- lifecycle */

void Access_Init (void)
{
	Cvar_RegisterVariable (&access_mouseonly);
	Cvar_RegisterVariable (&access_move_profile);
	Cvar_RegisterVariable (&access_throttle_gain);
	Cvar_RegisterVariable (&access_velocity_gain);
	Cvar_RegisterVariable (&access_deadzone);
	Cvar_RegisterVariable (&access_curve);
	Cvar_RegisterVariable (&access_walkspeed);
	Cvar_RegisterVariable (&access_tremor);
	Cvar_RegisterVariable (&access_turnrate);
	Cvar_RegisterVariable (&access_throttle_decay);
	Cvar_RegisterVariable (&access_toggle_button);
	Cvar_RegisterVariable (&access_longpress_button);
	Cvar_RegisterVariable (&access_longpress_ms);
	Cvar_RegisterVariable (&access_layer_timeout);
	Cvar_RegisterVariable (&access_layer_cmd1);
	Cvar_RegisterVariable (&access_layer_cmd2);
	Cvar_RegisterVariable (&access_doubleclick_button);
	Cvar_RegisterVariable (&access_doubleclick_ms);
	Cvar_RegisterVariable (&access_doubleclick_command);
	Cvar_RegisterVariable (&access_cruise_speed);
	Cvar_RegisterVariable (&access_idle_timeout);
	Cvar_RegisterVariable (&access_hud);
	Cvar_RegisterVariable (&access_sounds);
	Cvar_RegisterVariable (&access_log);

	Cmd_AddCommand ("access_toggle_mode", Access_ToggleMode_f);
	Cmd_AddCommand ("access_toggle_cruise", Access_ToggleCruise_f);
}

void Access_Reset (void)
{
	if (!access_mouseonly.value)
		return;
	if (access_mode != ACCESS_LOOK || access_cruise)
	{
		access_mode = ACCESS_LOOK;
		access_throttle = 0;
		access_cruise = false;
		Access_Log ("reset to look mode");
	}
}

void Access_Frame (float frametime)
{
	if (!access_mouseonly.value)
		return;
	/* Task 4: pitch easing. Task 5: safety resets. Task 6: gestures. */
}

/* ---------------------------------------------------------------- input */

void Access_ButtonEvent (int keynum, int down, unsigned int ms)
{
	if (!access_mouseonly.value)
	{
		Key_Event (keynum, down);
		return;
	}
	/* Task 5 consumes the toggle button; Task 6 adds gestures;
	   Task 8 intercepts menu-mode clicks. */
	Key_Event (keynum, down);
}

void Access_MouseMove (usercmd_t *cmd, int mx, int my)
{
	float	fx, fy;

	if (!access_mouseonly.value)
		return;

	/* Look mode (and Walk, until Task 4): vanilla mlook semantics */
	fx = mx * sensitivity.value;
	fy = my * sensitivity.value;

	if (in_strafe.state & 1)
		cmd->sidemove += m_side.value * fx;
	else
		cl.viewangles[YAW] -= m_yaw.value * fx;

	V_StopPitchDrift ();

	cl.viewangles[PITCH] += m_pitch.value * fy;
	if (cl.viewangles[PITCH] > 80)
		cl.viewangles[PITCH] = 80;
	if (cl.viewangles[PITCH] < -70)
		cl.viewangles[PITCH] = -70;
}

/* ----------------------------------------------------------------- HUD */

void Access_DrawHUD (void)
{
	/* Task 7 */
}

/* ---------------------------------------------------------- menu seams */

void Access_MenuFrame (void)
{
	/* Task 8 */
}

int Access_MenuItem (int index, int x, int y, int w, int h, int *cursor)
{
	(void)index; (void)x; (void)y; (void)w; (void)h; (void)cursor;
	return 0;
}

int Access_MenuHovered (int index)
{
	(void)index;
	return 0;
}

int Access_ClickRect (int x, int y, int w, int h)
{
	(void)x; (void)y; (void)w; (void)h;
	return 0;
}
```

- [ ] **Step 3: Makefile — add the object**

In the root `Makefile`, change the head of `QUAKE_CORE_OBJS` from:

```make
QUAKE_CORE_OBJS = \
	$(QUAKE_BUILDDIR)/client/cl_demo.o $(QUAKE_BUILDDIR)/client/cl_input.o \
```

to:

```make
QUAKE_CORE_OBJS = \
	$(QUAKE_BUILDDIR)/client/cl_access.o $(QUAKE_BUILDDIR)/client/cl_demo.o \
	$(QUAKE_BUILDDIR)/client/cl_input.o \
```

(The existing `$(QUAKE_BUILDDIR)/client/%.o` pattern rule already covers it.)

- [ ] **Step 4: cl_main.c — init + disconnect seams**

Add near the other includes at the top of `Quake/client/cl_main.c`:

```c
#include "cl_access.h"
```

In `CL_Init` (cl_main.c:717), after `CL_InitInput ();` add:

```c
	Access_Init ();
```

In `CL_Disconnect` (cl_main.c:99), as the first statement of the body (after the comment header, before `S_StopAllSounds`), add:

```c
	Access_Reset ();
```

- [ ] **Step 5: host.c — per-frame seam**

Add `#include "cl_access.h"` to the include block of `Quake/host.c`. In `_Host_Frame`, after:

```c
// allow mice or other external controllers to add commands
	IN_Commands ();
```

add:

```c
// accessibility module: gesture timers, safety resets, pitch easing
	Access_Frame ((float)host_frametime);
```

- [ ] **Step 6: in_sdl.c — route through the module**

Add `#include "cl_access.h"` to the include block of `Quake/platform/in_sdl.c`.

In `HandleEvents`, change the button case's last two lines from:

```c
			if (b >= 0)
				Key_Event(K_MOUSE1 + b, event.button.down);
			break;
```

to:

```c
			if (b >= 0)
				Access_ButtonEvent(K_MOUSE1 + b, event.button.down,
				                   (unsigned int)SDL_GetTicks());
			break;
```

(`Access_ButtonEvent` forwards to `Key_Event` when the module is off, so behavior with `access_mouseonly 0` is unchanged.)

In `IN_MouseMove`, replace the opening:

```c
void IN_MouseMove (usercmd_t *cmd)
{
	if (!mouse_avail)
		return;

	if (m_filter.value)
```

with:

```c
void IN_MouseMove (usercmd_t *cmd)
{
	if (!mouse_avail)
		return;

	if (access_mouseonly.value)
	{
		Access_MouseMove (cmd, mx, my);
		mx = my = 0;
		return;
	}

	if (m_filter.value)
```

- [ ] **Step 7: gl_screen.c — HUD seam**

Add `#include "cl_access.h"` to the include block of `Quake/render/gl_screen.c`. In `SCR_UpdateScreen` (gl_screen.c:823), immediately before:

```c
	V_UpdatePalette ();

	GL_EndRendering ();
```

add:

```c
	Access_DrawHUD ();
```

(The seam lives here rather than in `Sbar_Draw` because `Sbar_Draw`'s `sb_updates` gate skips most frames; the HUD must draw every frame. `Access_DrawHUD` hides itself unless `access_mouseonly && access_hud && key_dest == key_game`.)

- [ ] **Step 8: Build + runtime proof**

Run: `make build-release` — expect exit 0.

Run: `make run`. Verify:
- mouselook/turning feels exactly as before (Look parity),
- console: `access_toggle_mode` prints `access: walk mode` / `access: look mode`,
- `access_mouseonly 0` restores vanilla input (no `access:` lines, module inert), then `access_mouseonly 1` back.
Quit.

- [ ] **Step 9: Commit**

```bash
git add Quake/client/cl_access.h Quake/client/cl_access.c Quake/client/cl_main.c \
        Quake/host.c Quake/platform/in_sdl.c Quake/render/gl_screen.c Makefile
git commit -m "access: cl_access module scaffold + seams, Look-mode parity (mouse-only plan Task 3)"
```

---

### Task 4: Walk mode, movement pipeline, cruise control

**Files:**
- Modify: `Quake/client/cl_access.c`

**Interfaces:**
- Consumes: Task 3's module state; `cl_forwardspeed`, `cl_sidespeed`, `host_frametime`.
- Produces: working Walk mode (throttle + velocity profiles), cruise (`access_toggle_cruise` already registered), turn-rate cap. Task 5 layers safety resets over this.

- [ ] **Step 1: Add the pipeline state and helpers**

In `Quake/client/cl_access.c`, extend the state block (after `static qboolean access_cruise;`) with:

```c
static float	tremor_x, tremor_y;	/* low-pass filter state */
static double	access_lastinput;	/* for the idle timeout (Task 5) */
```

Add the helpers above `Access_MouseMove`:

```c
static float Access_Curve (float v)
{
	float	e = access_curve.value;

	if (e == 1 || v == 0)
		return v;
	if (v > 0)
		return powf (v, e);
	return -powf (-v, e);
}

static float Access_WalkSpeedCap (void)
{
	float	cap = access_walkspeed.value;

	if (cap > cl_forwardspeed.value - 10)
		cap = cl_forwardspeed.value - 10;
	if (cap < 0)
		cap = 0;
	return cap;
}
```

- [ ] **Step 2: Replace `Access_MouseMove` with the governed path**

```c
void Access_MouseMove (usercmd_t *cmd, int mx, int my)
{
	float	fx, fy, v, cap, a;

	if (!access_mouseonly.value)
		return;

	fx = mx * sensitivity.value;
	fy = my * sensitivity.value;

	/* tremor low-pass */
	a = access_tremor.value;
	if (a > 0)
	{
		if (a >= 1)
			a = 0.99f;
		fx = a * fx + (1 - a) * tremor_x;
		fy = a * fy + (1 - a) * tremor_y;
	}
	tremor_x = fx;
	tremor_y = fy;

	/* dead zone */
	if (fabs (fx) < access_deadzone.value)
		fx = 0;
	if (fabs (fy) < access_deadzone.value)
		fy = 0;

	access_lastinput = realtime;

	if (access_mode == ACCESS_WALK)
	{
		V_StopPitchDrift ();

	/* X: turn, or sidestep while +strafe is held */
		if (in_strafe.state & 1)
		{
			v = Access_Curve (fx) * m_side.value;
			if (v > cl_sidespeed.value)
				v = cl_sidespeed.value;
			else if (v < -cl_sidespeed.value)
				v = -cl_sidespeed.value;
			cmd->sidemove += v;
		}
		else
		{
			v = fx * m_yaw.value;
			if (access_turnrate.value > 0)
			{
				cap = access_turnrate.value * (float)host_frametime;
				if (v > cap)
					v = cap;
				else if (v < -cap)
					v = -cap;
			}
			cl.viewangles[YAW] -= v;
		}

	/* Y: movement */
		cap = Access_WalkSpeedCap ();
		if (access_move_profile.value == 0)
		{
		/* throttle profile: deltas accumulate into a held throttle */
			access_throttle += fy * access_throttle_gain.value;
			if (access_throttle > 1)
				access_throttle = 1;
			else if (access_throttle < -1)
				access_throttle = -1;
			if (access_throttle_decay.value > 0)
			{
				access_throttle *= 1.0f - access_throttle_decay.value * (float)host_frametime;
				if (fabs (access_throttle) < 0.001f)
					access_throttle = 0;
			}
			cmd->forwardmove -= Access_Curve (access_throttle) * cap;
		}
		else
		{
		/* velocity profile: native m_forward semantics, per-frame */
			v = Access_Curve (fy * access_velocity_gain.value);
			if (v > cap)
				v = cap;
			else if (v < -cap)
				v = -cap;
			cmd->forwardmove -= v;
		}
		return;
	}

	/* Look mode: vanilla mlook semantics */
	if (in_strafe.state & 1)
		cmd->sidemove += m_side.value * fx;
	else
		cl.viewangles[YAW] -= m_yaw.value * fx;

	V_StopPitchDrift ();

	cl.viewangles[PITCH] += m_pitch.value * fy;
	if (cl.viewangles[PITCH] > 80)
		cl.viewangles[PITCH] = 80;
	if (cl.viewangles[PITCH] < -70)
		cl.viewangles[PITCH] = -70;

	/* cruise: constant forward in Look mode */
	if (access_cruise)
	{
		v = (access_cruise_speed.value > 0) ? access_cruise_speed.value
		                                    : cl_forwardspeed.value;
		cmd->forwardmove += v;
		if (cmd->forwardmove < 0)
		{
			access_cruise = false;
			Access_Log ("cruise cancelled (backward input)");
		}
	}
}
```

Sign convention matches vanilla: positive `my` (pointer down) accumulates positive throttle → `forwardmove` decreases (backward). Pushing the pointer forward (negative `my`) moves the player forward.

- [ ] **Step 3: Walk-mode pitch easing in `Access_Frame`**

Replace the Task-3 stub body of `Access_Frame`:

```c
void Access_Frame (float frametime)
{
	float	step;

	if (!access_mouseonly.value)
		return;

	/* Walk mode: pitch stays level — ease toward the horizon */
	if (access_mode == ACCESS_WALK)
	{
		step = 120.0f * frametime;
		if (cl.viewangles[PITCH] > step)
			cl.viewangles[PITCH] -= step;
		else if (cl.viewangles[PITCH] < -step)
			cl.viewangles[PITCH] += step;
		else
			cl.viewangles[PITCH] = 0;
	}
}
```

- [ ] **Step 4: Build**

Run: `make build-release`
Expected: exit 0. (`cl_sidespeed` is declared in client.h via quakedef.h; `powf` via math.h.)

- [ ] **Step 5: Runtime proof (manual, needs game data)**

Run: `make run`, then `map start` in the console. Verify:
- `access_toggle_mode`: in Walk mode pushing the pointer forward walks forward; **rest the mouse still — the player keeps walking** (throttle holds); pulling back slows/reverses;
- hold the binding for `+strafe` (temporarily `bind MOUSE2 +strafe` in the console) — X sidesteps instead of turning;
- pitch drifts back to level in Walk mode;
- `access_move_profile 1` — velocity feel (stops when the mouse stops); `access_move_profile 0` back;
- `access_deadzone 10` kills tiny jitters; `access_curve 2` makes small motions gentler;
- `access_toggle_cruise` in Look mode walks forward hands-free; toggle again stops; trying it in Walk mode logs `cruise refused`;
- Walk-mode turning feels capped (`access_turnrate 60` makes the cap obvious); restore 240.
Restore defaults (`exec default.cfg` does NOT reset access_* cvars — set them back by hand) and quit.

- [ ] **Step 6: Commit**

```bash
git add Quake/client/cl_access.c
git commit -m "access: Walk mode, throttle/velocity pipeline, cruise control (mouse-only plan Task 4)"
```

---

### Task 5: Toggle-button consumption, safety resets, idle timeout

**Files:**
- Modify: `Quake/client/cl_access.c`

**Interfaces:**
- Consumes: `cls.state`, `cl.stats[STAT_HEALTH]`, `key_dest`, `realtime`, Task 4 state.
- Produces: engine-consumed toggle button (MOUSE3 by default), forced-Look safety resets (death / menu-console / disconnect / idle), edge-triggered logging.

- [ ] **Step 1: Safety state + force-Look helper**

Add to the state block:

```c
static int		access_lasthealth = 100;
static keydest_t	access_lastdest = key_game;
```

Add above `Access_Frame`:

```c
static void Access_ForceLook (char *reason)
{
	if (access_mode == ACCESS_LOOK && !access_cruise)
		return;
	access_mode = ACCESS_LOOK;
	access_throttle = 0;
	access_cruise = false;
	Access_Log (va ("forced look mode (%s)", reason));
}
```

- [ ] **Step 2: Safety checks in `Access_Frame`**

Append to `Access_Frame` (after the pitch-easing block, before the closing brace):

```c
	/* safety: death */
	if (cls.state == ca_connected)
	{
		if (cl.stats[STAT_HEALTH] <= 0 && access_lasthealth > 0)
			Access_ForceLook ("death");
		access_lasthealth = cl.stats[STAT_HEALTH];
	}
	else
		access_lasthealth = 100;

	/* safety: menu or console opened */
	if (key_dest != key_game && access_lastdest == key_game)
		Access_ForceLook ("menu/console");
	access_lastdest = key_dest;

	/* optional inactivity timeout (default off) */
	if (access_idle_timeout.value > 0 && access_mode == ACCESS_WALK
	    && realtime - access_lastinput > access_idle_timeout.value)
		Access_ForceLook ("idle timeout");
```

- [ ] **Step 3: Consume the dedicated toggle button**

Replace `Access_ButtonEvent` with:

```c
void Access_ButtonEvent (int keynum, int down, unsigned int ms)
{
	if (!access_mouseonly.value)
	{
		Key_Event (keynum, down);
		return;
	}

	access_lastinput = realtime;

	/* dedicated toggle button: engine-consumed, no binding honored */
	if (down && keynum == (int)access_toggle_button.value)
	{
		Access_ToggleMode_f ();
		return;
	}

	Key_Event (keynum, down);
}
```

- [ ] **Step 4: Build**

Run: `make build-release`
Expected: exit 0.

- [ ] **Step 5: Runtime proof**

Run: `make run`, `map start`. Verify:
- wheel click (MOUSE3) toggles Look↔Walk with `access: ...` lines — no binding exists for MOUSE3 (engine-consumed);
- open the menu (there is no Escape-free path; use the console: `togglemenu`) — `access: forced look mode (menu/console)`;
- die (e.g. `kill` in the console) in Walk mode — `access: forced look mode (death)`;
- `access_idle_timeout 3`, enter Walk mode, touch nothing for 3 s — `access: forced look mode (idle timeout)`; set it back to 0;
- `disconnect` in Walk mode — reset line appears.
Quit.

- [ ] **Step 6: Commit**

```bash
git add Quake/client/cl_access.c
git commit -m "access: toggle-button consumption + safety resets + idle timeout (mouse-only plan Task 5)"
```

---

### Task 6: Gesture engine — long-press layer and double-click

**Files:**
- Modify: `Quake/client/cl_access.c`

**Interfaces:**
- Consumes: `SDL_GetTicks` ms values from Task 3's `Access_ButtonEvent` routing, `Key_KeynumToString`, `Cbuf_AddText`, `S_LocalSound`.
- Produces: pending-press gesture state machine, sticky layer (`access_layer_cmd1/2`), double-click command. Task 7 adds sounds/labels at the transition points created here.

- [ ] **Step 1: Gesture + layer state**

Add to the state block:

```c
/* gesture engine */
static qboolean	gest_down;	/* physical button held */
static qboolean	gest_pending;	/* a press is held pending */
static unsigned	gest_down_time;	/* ms timestamp of the pending press */
static qboolean	gest_tap;	/* released tap waiting out the dbl window */
static int	gest_tap_key;
static unsigned	gest_tap_time;

/* sticky layer */
static qboolean	access_layer;
static double	access_layer_until;
```

- [ ] **Step 2: Layer helpers + sound helper**

Add above `Access_ButtonEvent`:

```c
static void Access_Sound (char *name)
{
	if (access_sounds.value)
		S_LocalSound (name);
}

static void Access_OpenLayer (void)
{
	access_layer = true;
	access_layer_until = realtime + access_layer_timeout.value;
	Access_Log ("layer open");
	Access_Sound ("buttons/switch02.wav");
}

static void Access_CloseLayer (void)
{
	if (!access_layer)
		return;
	access_layer = false;
	Access_Log ("layer closed");
	Access_Sound ("buttons/switch02.wav");
}

static qboolean Access_GestureButton (int keynum)
{
	static qboolean	warned_fire;

	/* never-on-fire rule: double-click on MOUSE1 is rejected */
	if (keynum == K_MOUSE1
	    && keynum == (int)access_doubleclick_button.value)
	{
		if (!warned_fire)
		{
			Con_Printf ("access: double-click on MOUSE1 (fire) is not allowed; ignoring\n");
			warned_fire = true;
		}
		return false;
	}

	if (keynum == (int)access_longpress_button.value
	    && access_longpress_button.value >= K_MOUSE1)
		return true;
	if (keynum == (int)access_doubleclick_button.value
	    && access_doubleclick_button.value >= K_MOUSE1)
		return true;
	return false;
}

static void Access_GestureEvent (int keynum, int down, unsigned int ms)
{
	unsigned	dbl = (unsigned)access_doubleclick_ms.value;

	if (down)
	{
		gest_down = true;
		if (gest_tap && keynum == gest_tap_key
		    && keynum == (int)access_doubleclick_button.value
		    && ms - gest_tap_time <= dbl)
		{
		/* second press inside the window: run the command, eat both */
			gest_tap = false;
			gest_pending = false;
			Access_Log (va ("double-click on %s", Key_KeynumToString (keynum)));
			if (access_doubleclick_command.string[0])
				Cbuf_AddText (va ("%s\n", access_doubleclick_command.string));
			return;
		}
		gest_pending = true;
		gest_down_time = ms;
		return;
	}

	/* release */
	gest_down = false;
	if (!gest_pending)
	{
		Key_Event (keynum, down);	/* release of a consumed press */
		return;
	}
	gest_pending = false;
	if (keynum == (int)access_doubleclick_button.value
	    && access_doubleclick_button.value >= K_MOUSE1)
	{
		/* hold the tap: a second press may still claim it */
		gest_tap = true;
		gest_tap_key = keynum;
		gest_tap_time = ms;
		return;
	}
	/* plain long-press button, released early: deliver the press */
	Key_Event (keynum, true);
	Key_Event (keynum, false);
}
```

- [ ] **Step 3: Full `Access_ButtonEvent`**

Replace `Access_ButtonEvent` with:

```c
void Access_ButtonEvent (int keynum, int down, unsigned int ms)
{
	if (!access_mouseonly.value)
	{
		Key_Event (keynum, down);
		return;
	}

	access_lastinput = realtime;

	/* dedicated toggle button: engine-consumed, no binding honored */
	if (down && keynum == (int)access_toggle_button.value)
	{
		Access_ToggleMode_f ();
		return;
	}

	/* sticky layer: next M1/M2 click runs the layer commands */
	if (access_layer)
	{
		if (down)
		{
			if (keynum == K_MOUSE1)
			{
				Access_CloseLayer ();
				Cbuf_AddText (va ("%s\n", access_layer_cmd1.string));
				return;
			}
			if (keynum == K_MOUSE2)
			{
				Access_CloseLayer ();
				Cbuf_AddText (va ("%s\n", access_layer_cmd2.string));
				return;
			}
		}
		Key_Event (keynum, down);
		return;
	}

	if (Access_GestureButton (keynum))
	{
		Access_GestureEvent (keynum, down, ms);
		return;
	}

	Key_Event (keynum, down);
}
```

- [ ] **Step 4: Gesture timers in `Access_Frame`**

Append to `Access_Frame` (after the idle-timeout block):

```c
	/* gesture timers */
	{
		unsigned	now = (unsigned)SDL_GetTicks ();
		unsigned	dbl = (unsigned)access_doubleclick_ms.value;

		/* long-press threshold crossed: discard the press, open the layer */
		if (gest_pending && gest_down
		    && access_longpress_button.value >= K_MOUSE1
		    && now - gest_down_time >= (unsigned)access_longpress_ms.value)
		{
			gest_pending = false;
			Access_Log (va ("long-press on %s",
			                Key_KeynumToString ((int)access_longpress_button.value)));
			Access_OpenLayer ();
		}

		/* a tap that survived the double-click window is delivered */
		if (gest_tap && now - gest_tap_time > dbl)
		{
			gest_tap = false;
			Key_Event (gest_tap_key, true);
			Key_Event (gest_tap_key, false);
		}

		if (access_layer && realtime > access_layer_until)
			Access_CloseLayer ();
	}
```

- [ ] **Step 5: Reset gesture state in `Access_Reset`**

Append to `Access_Reset` body:

```c
	gest_pending = false;
	gest_tap = false;
	Access_CloseLayer ();
```

- [ ] **Step 6: Build**

Run: `make build-release`
Expected: exit 0.

- [ ] **Step 7: Runtime proof**

Run: `make run`. In the console set up a 2-button-style fallback on a 5-button mouse for testing:

```
bind MOUSE2 access_toggle_cruise
access_doubleclick_button 201
access_doubleclick_command access_toggle_mode
access_longpress_button 201
```

Verify: quick MOUSE2 tap → cruise toggles (after the ~350 ms window); two quick taps → mode toggles (`access: double-click on MOUSE2`); hold MOUSE2 ≥ 400 ms → `access: layer open`, then click MOUSE1 → `save quick` runs (no save exists yet → the `save` command prints its usage/`can't save` message, which is the proof the command executed), or click MOUSE2 → menu opens. `access_longpress_button 0` / `access_doubleclick_button 0` restore the plain tap. Quit.

- [ ] **Step 8: Commit**

```bash
git add Quake/client/cl_access.c
git commit -m "access: gesture engine — long-press sticky layer + double-click (mouse-only plan Task 6)"
```

---

### Task 7: Feedback — sounds, transient labels, HUD indicator

**Files:**
- Modify: `Quake/client/cl_access.c`

**Interfaces:**
- Consumes: `Draw_String`, `Draw_Fill` (draw.h), Task 6's `Access_Sound`, `realtime`.
- Produces: entry/exit sounds at every transition, 1.5 s centered label, persistent top-left HUD with throttle bar.

- [ ] **Step 1: Label state + helper**

Add to the state block:

```c
static char	access_label[32];
static double	access_label_until;
```

Add next to `Access_Log`:

```c
static void Access_Label (char *text)
{
	Q_strcpy (access_label, text);
	access_label_until = realtime + 1.5;
}
```

- [ ] **Step 2: Wire sounds + labels into the transition points**

In `Access_ToggleMode_f`, replace the final `Access_Log (...)` line with:

```c
	if (access_mode == ACCESS_WALK)
	{
		Access_Log ("walk mode");
		Access_Label ("WALK MODE");
		Access_Sound ("misc/menu1.wav");
	}
	else
	{
		Access_Log ("look mode");
		Access_Label ("LOOK MODE");
		Access_Sound ("misc/menu2.wav");
	}
```

(`Access_Sound` was added in Task 6; if you are implementing tasks out of order, add it here: `if (access_sounds.value) S_LocalSound (name);`.)

In `Access_ToggleCruise_f`, replace the final `Access_Log` line with:

```c
	if (access_cruise)
	{
		Access_Log ("cruise on");
		Access_Label ("CRUISE ON");
		Access_Sound ("misc/menu3.wav");
	}
	else
	{
		Access_Log ("cruise off");
		Access_Label ("CRUISE OFF");
		Access_Sound ("misc/menu3.wav");
	}
```

and add a blip to the walk-mode refusal branch (the `access_mode == ACCESS_WALK` early return):

```c
		Access_Sound ("misc/menu3.wav");
```

In `Access_OpenLayer`, after `Access_Log ("layer open");` add:

```c
	Access_Label ("LAYER");
```

In `Access_ForceLook`, replace the final `Access_Log` line with:

```c
	Access_Log (va ("forced look mode (%s)", reason));
	Access_Label ("LOOK MODE");
	Access_Sound ("misc/menu2.wav");
```

- [ ] **Step 3: HUD body**

Replace the `Access_DrawHUD` stub:

```c
void Access_DrawHUD (void)
{
	char	line[40];
	int	x, y, w, mid, fill;

	if (!access_mouseonly.value || !access_hud.value)
		return;
	if (key_dest != key_game)
		return;

	x = 8;
	y = 8;

	Q_strcpy (line, access_mode == ACCESS_WALK ? "WALK" : "LOOK");
	if (access_cruise)
		Q_strcpy (line + Q_strlen (line), " CRUISE");
	Draw_String (x, y, line);

	/* throttle bar */
	w = 64;
	y += 10;
	Draw_Fill (x, y, w, 4, 0);		/* background (palette index 0) */
	mid = x + w / 2;
	Draw_Fill (mid, y, 1, 4, 15);		/* center notch */
	if (access_mode == ACCESS_WALK)
	{
		fill = (int)(access_throttle * (w / 2));
		if (fill > 0)
			Draw_Fill (mid, y, fill, 4, 12);
		else if (fill < 0)
			Draw_Fill (mid + fill, y, -fill, 4, 12);
	}
	else if (access_cruise)
		Draw_Fill (x, y, w, 4, 12);

	/* transient mode label, centered in the 2D space (vid.width may be
	   640 — never assume 320) */
	if (access_label[0] && realtime < access_label_until)
		Draw_String (vid.width / 2 - 4 * Q_strlen (access_label),
		             vid.height / 2 - 4, access_label);
}
```

Palette indices (0 background, 12 fill, 15 notch) are cosmetic defaults — adjust freely if they read poorly.

- [ ] **Step 4: Build + runtime proof**

Run: `make build-release` (exit 0), then `make run`, `map start`. Verify: top-left `LOOK` indicator; toggle → sound + centered `WALK MODE` label for ~1.5 s + bar; push forward → bar fills to the right of center; cruise in Look mode → `CRUISE` tag + full bar; `access_hud 0` hides everything; `access_sounds 0` silences transitions. Quit.

- [ ] **Step 5: Commit**

```bash
git add Quake/client/cl_access.c
git commit -m "access: feedback — transition sounds, labels, HUD throttle bar (mouse-only plan Task 7)"
```

---

### Task 8: Point-and-click menu infrastructure + play-flow pages

**Files:**
- Modify: `Quake/client/cl_access.c` (menu seams)
- Modify: `Quake/client/menu.c` (`M_Draw`, `M_Keydown`, main/singleplayer/load/save/options pages)
- Modify: `Quake/client/menu.h` (new prototype `M_BindGrabActive`)

**Interfaces:**
- Consumes: `SDL_GetMouseState`, `SDL_GetWindowSize`, `sdl_window` (extern from gl_vidsdl.c), `Key_Event`.
- Produces: `Access_MenuFrame/MenuItem/Hovered/ClickRect` bodies; left-click = set cursor + Enter (queued to the frame boundary), right-click = Escape, wheel = arrows. Task 9 registers the remaining pages on top of this; Task 10's new page uses it too.

- [ ] **Step 1: Menu state in cl_access.c**

Add to the state block:

```c
/* -------------------------------------------------- menu pointer state */

#define ACCESS_MAX_MENU_ITEMS 64

typedef struct {
	int	index;
	int	x, y, w, h;
	int	*cursor;
} access_menuitem_t;

static access_menuitem_t	menu_items[ACCESS_MAX_MENU_ITEMS];
static int	menu_numitems;
static int	menu_hover = -1;
static float	menu_cx, menu_cy;	/* cursor in 320x200 space */

/* synthesized key, dispatched at the next frame boundary */
static int	menu_queued_key;
static int	*menu_queued_cursor;
static int	menu_queued_index;

/* one click lives for exactly one draw pass */
static qboolean	menu_click_pending;	/* set by the event pump */
static qboolean	menu_click_live;	/* set by Access_MenuFrame */
static float	menu_click_x, menu_click_y;
```

- [ ] **Step 2: Replace the four menu seam stubs**

```c
void Access_MenuFrame (void)
{
	int	mx, my, ww, wh;

	if (!access_mouseonly.value)
		return;

	menu_numitems = 0;
	menu_hover = -1;
	menu_click_live = menu_click_pending;
	menu_click_pending = false;

	if (key_dest != key_menu)
		return;

	SDL_GetMouseState (&mx, &my);
	SDL_GetWindowSize (sdl_window, &ww, &wh);
	if (ww <= 0 || wh <= 0)
		return;
	/* GL_Set2D (gl_draw.c:875) makes the 2D ortho space vid.width x
	   vid.height (640x480 by default, gl_vidsdl.c:391); menu.c draws
	   in that same space, so map window pixels into it directly */
	menu_cx = mx * (float)vid.width / ww;
	menu_cy = my * (float)vid.height / wh;

	/* dispatch last frame's queued action before this frame draws */
	if (menu_queued_key)
	{
		if (menu_queued_cursor)
			*menu_queued_cursor = menu_queued_index;
		Key_Event (menu_queued_key, true);
		Key_Event (menu_queued_key, false);
		menu_queued_key = 0;
		menu_queued_cursor = NULL;
	}
}

int Access_MenuItem (int index, int x, int y, int w, int h, int *cursor)
{
	access_menuitem_t	*it;

	if (!access_mouseonly.value || key_dest != key_menu)
		return 0;
	if (menu_numitems >= ACCESS_MAX_MENU_ITEMS)
		return 0;

	it = &menu_items[menu_numitems++];
	it->index = index;
	it->x = x - 2;			/* 2 px hit padding */
	it->y = y - 2;
	it->w = w + 4;
	it->h = h + 4;
	it->cursor = cursor;

	if (menu_cx >= it->x && menu_cx < it->x + it->w
	    && menu_cy >= it->y && menu_cy < it->y + it->h)
		menu_hover = menu_numitems - 1;

	return 0;
}

int Access_MenuHovered (int index)
{
	return menu_hover >= 0 && menu_items[menu_hover].index == index;
}

int Access_ClickRect (int x, int y, int w, int h)
{
	if (!access_mouseonly.value || !menu_click_live)
		return 0;
	if (menu_click_x >= x && menu_click_x < x + w
	    && menu_click_y >= y && menu_click_y < y + h)
	{
		menu_click_live = false;	/* one click, one consumer */
		return 1;
	}
	return 0;
}
```

- [ ] **Step 3: Menu-mode button handling**

Add above `Access_ButtonEvent`:

```c
static void Access_MenuButton (int keynum, int down)
{
	if (!down)
		return;
	if (keynum == K_MOUSE1)
	{
		menu_click_pending = true;
		menu_click_x = menu_cx;
		menu_click_y = menu_cy;
		if (menu_hover >= 0)
		{
			menu_queued_key = K_ENTER;
			menu_queued_cursor = menu_items[menu_hover].cursor;
			menu_queued_index = menu_items[menu_hover].index;
		}
	}
	else if (keynum == K_MOUSE2)
	{
		menu_queued_key = K_ESCAPE;
		menu_queued_cursor = NULL;
	}
}
```

In `Access_ButtonEvent`, insert immediately after the `access_lastinput = realtime;` line:

```c
	/* menus: point-and-click; buttons never fire gameplay bindings here */
	if (key_dest == key_menu)
	{
		if (M_BindGrabActive ())
			Key_Event (keynum, down);	/* rebinding flow needs raw keys */
		else
			Access_MenuButton (keynum, down);
		return;
	}
```

Declare the accessor in `Quake/client/menu.h` (visible to cl_access.c via quakedef.h:231):

```c
int M_BindGrabActive (void);
```

and define it in menu.c next to the `bind_grab` declaration (menu.c:1289):

```c
int M_BindGrabActive (void)
{
	return m_state == m_keys && bind_grab;
}
```

- [ ] **Step 4: menu.c — wheel mapping, frame hook, bind-grab accessor**

Add `#include "cl_access.h"` to menu.c's include block.

At the very top of `M_Keydown (int key)` (menu.c:3059), before the `switch (m_state)`, add:

```c
	if (key == K_MWHEELUP)
		key = K_UPARROW;
	else if (key == K_MWHEELDOWN)
		key = K_DOWNARROW;
```

At the top of `M_Draw` (menu.c:2943), right after the existing early return (`if (m_state == m_none || key_dest != key_menu) return;`), add:

```c
	Access_MenuFrame ();
```

Add the `M_BindGrabActive` definition from Step 3 after the `bind_grab` declaration (menu.c:1289).

- [ ] **Step 5: Register the play-flow pages**

**Main menu** — replace `M_Main_Draw`'s body (menu.c:287) so the shared pic is registered:

```c
void M_Main_Draw (void)
{
	int		f;
	qpic_t	*p;
	qpic_t	*items;
	int		i;

	M_DrawTransPic (16, 4, Draw_CachePic ("gfx/qplaque.lmp") );
	p = Draw_CachePic ("gfx/ttl_main.lmp");
	M_DrawPic ( (320-p->width)/2, 4, p);
	items = Draw_CachePic ("gfx/mainmenu.lmp");
	M_DrawTransPic (72, 32, items);

	for (i = 0; i < MAIN_ITEMS; i++)
		Access_MenuItem (i, 72, 32 + i * 20, items->width, 20, &m_main_cursor);

	f = (int)(host_time * 10)%6;

	M_DrawTransPic (54, 32 + m_main_cursor * 20,Draw_CachePic( va("gfx/menudot%i.lmp", f+1 ) ) );
}
```

**Single player** — same pattern in `M_SinglePlayer_Draw` (menu.c:368): capture the pic into `items = Draw_CachePic ("gfx/sp_menu.lmp");` and after the `M_DrawTransPic (72, 32, items);` add:

```c
	for (i = 0; i < SINGLEPLAYER_ITEMS; i++)
		Access_MenuItem (i, 72, 32 + i * 20, items->width, 20, &m_singleplayer_cursor);
```

(declare `int i;` in that function's locals.)

**Load and Save** — in both `M_Load_Draw` and `M_Save_Draw` (menu.c:490/502), inside the existing `for (i=0 ; i< MAX_SAVEGAMES; i++)` loop, after each `M_Print (16, 32 + 8*i, m_filenames[i]);` add:

```c
		Access_MenuItem (i, 16, 32 + 8*i,
		                 SAVEGAME_COMMENT_LENGTH * 8, 8, &load_cursor);
```

**Options** — in `M_Options_Draw` (menu.c:1145), after each of the twelve `M_Print (16, Y, "...")` rows, register that row. The rows and their texts (from the current code) are:

| index | y | text |
|---|---|---|
| 0 | 32 | `    Customize controls` |
| 1 | 40 | `         Go to console` |
| 2 | 48 | `     Reset to defaults` |
| 3 | 56 | `           Screen size` |
| 4 | 64 | `            Brightness` |
| 5 | 72 | `           Mouse Speed` |
| 6 | 80 | `       CD Music Volume` |
| 7 | 88 | `          Sound Volume` |
| 8 | 96 | `            Always Run` |
| 9 | 104 | `          Invert Mouse` |
| 10 | 112 | `            Lookspring` |
| 11 | 120 | `            Lookstrafe` |

After each row's `M_Print` line add (substituting the row's text):

```c
	Access_MenuItem (0, 16, 32, 8 * (int)strlen ("    Customize controls"), 8, &options_cursor);
```

(i.e. width = `8 * strlen(text)` — the leading spaces are part of the drawn string and widen the hit rect, which is desirable).

- [ ] **Step 6: Build + runtime proof**

Run: `make build-release` (exit 0), then `make run`. Verify with the mouse only (no keyboard):
- main menu: hover an entry, click → submenu opens; right-click → back;
- Single player → New Game starts E1M1 (confirm dialog may appear only when a game is active); Load/Save lists: clicking a slot moves the cursor and activates it;
- Options: click `Always Run` row toggles it; sliders respond to left/right arrows via keyboard too (unchanged); wheel scrolls the selection;
- `access_mouseonly 0` → clicks do nothing (keyboard still works).
Quit.

- [ ] **Step 7: Commit**

```bash
git add Quake/client/cl_access.c Quake/client/menu.c Quake/client/menu.h
git commit -m "access: point-and-click menus — infra + play-flow pages (mouse-only plan Task 8)"
```

---

### Task 9: Remaining menu pages + setup name palette

**Files:**
- Modify: `Quake/client/menu.c`

**Interfaces:**
- Consumes: Task 8's `Access_MenuItem`, `Access_ClickRect`, `Access_MenuHovered`.
- Produces: complete mouse coverage — no menu state left keyboard-only.

The pattern for every page is identical to Task 8's: inside the page's draw function, register one `Access_MenuItem` per selectable row using the exact coordinates already hardcoded there (text rows: width `8 * strlen(text)`, height 8; pic menus: `(72, 32 + i*20, pic->width, 20)`), passing that page's cursor variable. Copy every coordinate from the existing `M_Print`/`M_DrawTransPic` calls in each draw function — never invent coordinates. After this task, walking the `m_state` enum must find no page without registrations (except `m_none` and `m_search`).

- [ ] **Step 1: Multiplayer subtree pic menus**

`M_MultiPlayer_Draw` (menu.c:~610): same pattern as main, pic `gfx/mp_menu.lmp`, `MULTIPLAYER_ITEMS` (3), cursor `m_multiplayer_cursor`.

- [ ] **Step 2: Text-row menus**

Register each drawn row (read the coordinates from each draw function; heights are 8):

| Page | Draw function | Cursor | Items to register |
|---|---|---|---|
| Net | `M_Net_Draw` | `m_net_cursor` | every selectable row it draws |
| Keys | `M_Keys_Draw` | `keys_cursor` | the `NUMCOMMANDS` rows at `(16, 48 + 8*i)`, width `304` (16..320) — clicking a row toggles `bind_grab` via Enter; while `bind_grab`, the Task-8 `M_BindGrabActive` path feeds raw mouse buttons to `M_Keys_Key` for rebinding |
| Game options (episode/skill) | `M_GameOptions_Draw` | `gameoptions_cursor` | every row the function draws |
| Serial config | `M_SerialConfig_Draw` | `serialConfig_cursor` | every row |
| Modem config | `M_ModemConfig_Draw` | `modemConfig_cursor` | every row |
| LAN config | `M_LanConfig_Draw` | — | register each row with a dummy `static int lan_dummy;` cursor, or the variable the key handler uses; rows whose Enter-action edits a value still activate via click |
| Server list | `M_ServerList_Draw` | `slist_cursor` | each printed server line |
| Help | `M_Help_Draw` | — | one whole-page item registered against the help-page counter variable the key handler advances (read `M_Help_Key`: it advances on Enter/arrows) |
| Quit | `M_Quit_Draw` | — | one `Access_ClickRect` covering the drawn panel, wired to synthesize the quit-confirm key the key handler uses (read `M_Quit_Key`) |
| Video | `M_Video_Draw` (only reachable when `vid_menudrawfn` is set; on this port it is NULL) | — | still register its rows with the same pattern; harmless when unreachable |

Search-page (`M_Search_Draw`) has no selectable items — leave it unregistered.

- [ ] **Step 3: Setup page — color sliders**

In `M_Setup_Draw`, for each of the two color slider rows, register two half-row click zones with `Access_ClickRect`: left half synthesizes `K_LEFTARROW`-equivalent logic, right half `K_RIGHTARROW`-equivalent. Implement by calling the same adjust code `M_Setup_Key` uses — factor that case body into a static helper:

```c
static void M_Setup_AdjustColor (int dir)	/* dir -1 / +1 */
{
	/* body copied from M_Setup_Key's K_LEFTARROW/K_RIGHTARROW cases
	   for the active slider (read which slider the cursor selects) */
}
```

and in the draw function:

```c
	if (Access_ClickRect (x, y, w/2, 8))
		M_Setup_AdjustColor (-1);
	if (Access_ClickRect (x + w/2, y, w/2, 8))
		M_Setup_AdjustColor (1);
```

- [ ] **Step 4: Setup page — player-name palette**

Extract the character-insertion branch of `M_Setup_Key` (the `if (k >= 32 && k < 127 ...)` case) into:

```c
static void M_Setup_TypeChar (int k)
```

and call it from `M_Setup_Key` unchanged. Then, at the bottom of `M_Setup_Draw`, draw a one-row palette at `y = 184`, characters `A-Z 0-9` then backspace (draw code 127), 8 px each, starting `x = (320 - 37*8)/2`:

```c
	{
		static char palette[] = "ABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789";
		int	i, px, py = 184;
		int	x0 = (320 - 37 * 8) / 2;

		for (i = 0; i < 36; i++)
		{
			px = x0 + i * 8;
			M_DrawCharacter (px, py, palette[i]);
			if (Access_ClickRect (px, py, 8, 8))
				M_Setup_TypeChar (palette[i]);
		}
		px = x0 + 36 * 8;
		M_DrawCharacter (px, py, 127);
		if (Access_ClickRect (px, py, 8, 8))
			M_Setup_TypeChar (K_BACKSPACE);
	}
```

(Adjust `y` if 184 collides with existing setup art — pick the lowest free 8-px row.)

- [ ] **Step 5: Build + runtime proof**

Run: `make build-release` (exit 0), `make run`. Mouse-only walk-through of every menu state: main → multiplayer → net/setup, options → keys (click a row, then click MOUSE4 to rebind it — the row shows MOUSE4), single player → new game → episode/skill selection, load/save, help (click turns pages), quit (click quits — relaunch after). Setup: click palette characters to build a name; click color sliders. Quit.

- [ ] **Step 6: Commit**

```bash
git add Quake/client/menu.c
git commit -m "access: point-and-click for all remaining menus + name palette (mouse-only plan Task 9)"
```

---

### Task 10: Mouse options page (`m_mouse`)

**Files:**
- Modify: `Quake/client/menu.c` (enum, declarations, new page, Options entry)
- Modify: `Quake/client/cl_access.c` (extern cvar accessors only if needed — all cvars are already registered there; menu.c references them via `extern` declarations)

**Interfaces:**
- Consumes: Task 8/9 menu infra; every `access_*` cvar from Task 3; `sv_aim` (pr_cmds.c:1332), `cl_bob`/`cl_rollangle`/`v_kicktime` (view.c), `host_timescale`/`host_maxfps` (Task 1).
- Produces: in-game mouse-tunable surface for the whole feature — no console needed for runtime tuning.

- [ ] **Step 1: Extern declarations in menu.c**

Add to menu.c's declaration block (near the other `extern cvar_t` lines):

```c
extern cvar_t	access_mouseonly, access_move_profile, access_throttle_gain;
extern cvar_t	access_velocity_gain, access_deadzone, access_curve;
extern cvar_t	access_walkspeed, access_tremor, access_turnrate;
extern cvar_t	access_throttle_decay, access_toggle_button, access_longpress_ms;
extern cvar_t	access_idle_timeout, access_hud, access_sounds;
extern cvar_t	sv_aim, cl_bob, cl_rollangle, v_kicktime;
extern cvar_t	host_timescale, host_maxfps;
```

(`sv_aim` lives in server code but is linked into glquake; if the compiler complains about a specific one, find its defining file with `grep "cvar_t.*<name>" Quake/` and confirm the name.)

- [ ] **Step 2: Enum + declarations**

In the `m_state` enum (menu.c:25), add `m_mouse` after `m_video`. In the forward-declaration block (menu.c:27-44), inside the options group add:

```c
		void M_Menu_Mouse_f (void);
```

- [ ] **Step 3: Page state, enter function, adjust function**

Add the page after the video-menu section:

```c
//=============================================================================
/* MOUSE-ONLY MENU */

#define	MOUSE_ITEMS	21

int		mouse_cursor;

void M_Menu_Mouse_f (void)
{
	key_dest = key_menu;
	m_state = m_mouse;
	m_entersound = true;
}

static float Mouse_SliderRange (int item, float value)
{
	switch (item)
	{
	case 2: return (access_throttle_gain.value - 0.0005f) / 0.0095f;
	case 3: return (access_velocity_gain.value - 0.1f) / 4.9f;
	case 4: return access_deadzone.value / 20.0f;
	case 5: return (access_curve.value - 0.25f) / 3.75f;
	case 6: return (access_walkspeed.value - 50) / 340.0f;
	case 7: return access_tremor.value / 0.95f;
	case 8: return access_turnrate.value / 720.0f;
	case 9: return access_throttle_decay.value / 3.0f;
	case 11: return (access_longpress_ms.value - 100) / 1400.0f;
	case 12: return access_idle_timeout.value / 30.0f;
	case 15: return (sv_aim.value - 0.5f) / 0.5f;
	case 16: return cl_bob.value / 0.05f;
	case 17: return cl_rollangle.value / 2.0f;
	case 18: return v_kicktime.value / 0.5f;
	case 19: return (host_timescale.value - 0.25f) / 1.25f;
	case 20: return (host_maxfps.value - 30) / 114.0f;
	}
	return 0;
}

static void M_AdjustMouse (int dir)
{
	S_LocalSound ("misc/menu3.wav");

	switch (mouse_cursor)
	{
	case 0:	/* master switch */
		Cvar_SetValue ("access_mouseonly", !access_mouseonly.value);
		break;
	case 1:	/* profile */
		Cvar_SetValue ("access_move_profile", !access_move_profile.value);
		break;
	case 2:
		Cvar_SetValue ("access_throttle_gain", access_throttle_gain.value + dir * 0.0005);
		if (access_throttle_gain.value < 0.0005) Cvar_SetValue ("access_throttle_gain", 0.0005);
		if (access_throttle_gain.value > 0.01) Cvar_SetValue ("access_throttle_gain", 0.01);
		break;
	case 3:
		Cvar_SetValue ("access_velocity_gain", access_velocity_gain.value + dir * 0.1);
		if (access_velocity_gain.value < 0.1) Cvar_SetValue ("access_velocity_gain", 0.1);
		if (access_velocity_gain.value > 5) Cvar_SetValue ("access_velocity_gain", 5);
		break;
	case 4:
		Cvar_SetValue ("access_deadzone", access_deadzone.value + dir);
		if (access_deadzone.value < 0) Cvar_SetValue ("access_deadzone", 0);
		if (access_deadzone.value > 20) Cvar_SetValue ("access_deadzone", 20);
		break;
	case 5:
		Cvar_SetValue ("access_curve", access_curve.value + dir * 0.25);
		if (access_curve.value < 0.25) Cvar_SetValue ("access_curve", 0.25);
		if (access_curve.value > 4) Cvar_SetValue ("access_curve", 4);
		break;
	case 6:
		Cvar_SetValue ("access_walkspeed", access_walkspeed.value + dir * 10);
		if (access_walkspeed.value < 50) Cvar_SetValue ("access_walkspeed", 50);
		if (access_walkspeed.value > 390) Cvar_SetValue ("access_walkspeed", 390);
		break;
	case 7:
		Cvar_SetValue ("access_tremor", access_tremor.value + dir * 0.05);
		if (access_tremor.value < 0) Cvar_SetValue ("access_tremor", 0);
		if (access_tremor.value > 0.95) Cvar_SetValue ("access_tremor", 0.95);
		break;
	case 8:
		Cvar_SetValue ("access_turnrate", access_turnrate.value + dir * 30);
		if (access_turnrate.value < 0) Cvar_SetValue ("access_turnrate", 0);
		if (access_turnrate.value > 720) Cvar_SetValue ("access_turnrate", 720);
		break;
	case 9:
		Cvar_SetValue ("access_throttle_decay", access_throttle_decay.value + dir * 0.1);
		if (access_throttle_decay.value < 0) Cvar_SetValue ("access_throttle_decay", 0);
		if (access_throttle_decay.value > 3) Cvar_SetValue ("access_throttle_decay", 3);
		break;
	case 10:	/* toggle button: cycle MOUSE1..MOUSE5 */
	{
		int	b = (int)access_toggle_button.value + dir;
		if (b < K_MOUSE1) b = K_MOUSE5;
		if (b > K_MOUSE5) b = K_MOUSE1;
		Cvar_SetValue ("access_toggle_button", b);
		break;
	}
	case 11:
		Cvar_SetValue ("access_longpress_ms", access_longpress_ms.value + dir * 50);
		if (access_longpress_ms.value < 100) Cvar_SetValue ("access_longpress_ms", 100);
		if (access_longpress_ms.value > 1500) Cvar_SetValue ("access_longpress_ms", 1500);
		break;
	case 12:
		Cvar_SetValue ("access_idle_timeout", access_idle_timeout.value + dir);
		if (access_idle_timeout.value < 0) Cvar_SetValue ("access_idle_timeout", 0);
		if (access_idle_timeout.value > 30) Cvar_SetValue ("access_idle_timeout", 30);
		break;
	case 13:
		Cvar_SetValue ("access_hud", !access_hud.value);
		break;
	case 14:
		Cvar_SetValue ("access_sounds", !access_sounds.value);
		break;
	case 15:
		Cvar_SetValue ("sv_aim", sv_aim.value + dir * 0.01);
		if (sv_aim.value < 0.5) Cvar_SetValue ("sv_aim", 0.5);
		if (sv_aim.value > 1) Cvar_SetValue ("sv_aim", 1);
		break;
	case 16:
		Cvar_SetValue ("cl_bob", cl_bob.value + dir * 0.005);
		if (cl_bob.value < 0) Cvar_SetValue ("cl_bob", 0);
		if (cl_bob.value > 0.05) Cvar_SetValue ("cl_bob", 0.05);
		break;
	case 17:
		Cvar_SetValue ("cl_rollangle", cl_rollangle.value + dir * 0.5);
		if (cl_rollangle.value < 0) Cvar_SetValue ("cl_rollangle", 0);
		if (cl_rollangle.value > 2) Cvar_SetValue ("cl_rollangle", 2);
		break;
	case 18:
		Cvar_SetValue ("v_kicktime", v_kicktime.value + dir * 0.05);
		if (v_kicktime.value < 0) Cvar_SetValue ("v_kicktime", 0);
		if (v_kicktime.value > 0.5) Cvar_SetValue ("v_kicktime", 0.5);
		break;
	case 19:
		Cvar_SetValue ("host_timescale", host_timescale.value + dir * 0.05);
		if (host_timescale.value < 0.25) Cvar_SetValue ("host_timescale", 0.25);
		if (host_timescale.value > 1.5) Cvar_SetValue ("host_timescale", 1.5);
		break;
	case 20:
		Cvar_SetValue ("host_maxfps", host_maxfps.value + dir * 6);
		if (host_maxfps.value < 30) Cvar_SetValue ("host_maxfps", 30);
		if (host_maxfps.value > 144) Cvar_SetValue ("host_maxfps", 144);
		break;
	}
}
```

- [ ] **Step 4: Draw + key handler**

```c
void M_Mouse_Draw (void)
{
	int		y0 = 30;
	int		i;

	M_Print (16, y0 + 8*0,  "       Mouse-only play");
	M_DrawCheckbox (220, y0 + 8*0, access_mouseonly.value);

	M_Print (16, y0 + 8*1,  "        Move profile");
	M_Print (220, y0 + 8*1, access_move_profile.value ? "velocity" : "throttle");

	M_Print (16, y0 + 8*2,  "       Throttle gain");
	M_DrawSlider (220, y0 + 8*2, Mouse_SliderRange (2, 0));

	M_Print (16, y0 + 8*3,  "       Velocity gain");
	M_DrawSlider (220, y0 + 8*3, Mouse_SliderRange (3, 0));

	M_Print (16, y0 + 8*4,  "           Dead zone");
	M_DrawSlider (220, y0 + 8*4, Mouse_SliderRange (4, 0));

	M_Print (16, y0 + 8*5,  "      Response curve");
	M_DrawSlider (220, y0 + 8*5, Mouse_SliderRange (5, 0));

	M_Print (16, y0 + 8*6,  "       Walk speed cap");
	M_DrawSlider (220, y0 + 8*6, Mouse_SliderRange (6, 0));

	M_Print (16, y0 + 8*7,  "      Tremor filter");
	M_DrawSlider (220, y0 + 8*7, Mouse_SliderRange (7, 0));

	M_Print (16, y0 + 8*8,  "   Walk turn-rate cap");
	M_DrawSlider (220, y0 + 8*8, Mouse_SliderRange (8, 0));

	M_Print (16, y0 + 8*9,  "      Throttle decay");
	M_DrawSlider (220, y0 + 8*9, Mouse_SliderRange (9, 0));

	M_Print (16, y0 + 8*10, "        Toggle button");
	M_Print (220, y0 + 8*10, Key_KeynumToString ((int)access_toggle_button.value));

	M_Print (16, y0 + 8*11, "      Long-press (ms)");
	M_DrawSlider (220, y0 + 8*11, Mouse_SliderRange (11, 0));

	M_Print (16, y0 + 8*12, " Idle timeout (s,0=off)");
	M_DrawSlider (220, y0 + 8*12, Mouse_SliderRange (12, 0));

	M_Print (16, y0 + 8*13, "          HUD display");
	M_DrawCheckbox (220, y0 + 8*13, access_hud.value);

	M_Print (16, y0 + 8*14, "       Sounds");
	M_DrawCheckbox (220, y0 + 8*14, access_sounds.value);

	M_Print (16, y0 + 8*15, "     Vertical auto-aim");
	M_DrawSlider (220, y0 + 8*15, Mouse_SliderRange (15, 0));

	M_Print (16, y0 + 8*16, "             View bob");
	M_DrawSlider (220, y0 + 8*16, Mouse_SliderRange (16, 0));

	M_Print (16, y0 + 8*17, "       View roll angle");
	M_DrawSlider (220, y0 + 8*17, Mouse_SliderRange (17, 0));

	M_Print (16, y0 + 8*18, "       Damage kick time");
	M_DrawSlider (220, y0 + 8*18, Mouse_SliderRange (18, 0));

	M_Print (16, y0 + 8*19, "        Time scale");
	M_DrawSlider (220, y0 + 8*19, Mouse_SliderRange (19, 0));

	M_Print (16, y0 + 8*20, "          Max FPS");
	M_DrawSlider (220, y0 + 8*20, Mouse_SliderRange (20, 0));

	/* registration for point-and-click. Label area (x 16..212) is the
	   item — click = Enter = default action. Rows with a control at
	   x=220 additionally get left/right adjust zones; rects never
	   overlap, so one click fires exactly one action. */
	for (i = 0; i < MOUSE_ITEMS; i++)
	{
		Access_MenuItem (i, 16, y0 + 8*i, 196, 8, &mouse_cursor);
		if (i >= 2 && i != 13 && i != 14)
		{
			if (Access_ClickRect (212, y0 + 8*i, 52, 8))
			{
				mouse_cursor = i;
				M_AdjustMouse (-1);
			}
			if (Access_ClickRect (264, y0 + 8*i, 52, 8))
			{
				mouse_cursor = i;
				M_AdjustMouse (1);
			}
		}
	}

	/* cursor */
	M_DrawCharacter (200, y0 + mouse_cursor*8, 12+((int)(realtime*4)&1));
}

void M_Mouse_Key (int k)
{
	switch (k)
	{
	case K_ESCAPE:
		M_Menu_Options_f ();
		break;

	case K_ENTER:
		m_entersound = true;
		M_AdjustMouse (1);
		return;

	case K_UPARROW:
		S_LocalSound ("misc/menu1.wav");
		mouse_cursor--;
		if (mouse_cursor < 0)
			mouse_cursor = MOUSE_ITEMS-1;
		break;

	case K_DOWNARROW:
		S_LocalSound ("misc/menu1.wav");
		mouse_cursor++;
		if (mouse_cursor >= MOUSE_ITEMS)
			mouse_cursor = 0;
		break;

	case K_LEFTARROW:
		M_AdjustMouse (-1);
		break;

	case K_RIGHTARROW:
		M_AdjustMouse (1);
		break;
	}
}
```

- [ ] **Step 5: Options page entry**

Change `#define OPTIONS_ITEMS 13` (menu.c:1027) to `14`.

In `M_Options_Draw`, after the Lookstrafe row (`M_Print (16, 120, ...)`), add:

```c
	M_Print (16, 128, "    Mouse-only options");
```

and move the existing video-options line from y=128 to y=136:

```c
	if (vid_menudrawfn)
		M_Print (16, 136, "         Video Options");
```

Register both rows with `Access_MenuItem` (index 12 at y=128, index 13 at y=136, width `8 * strlen(text)`, cursor `&options_cursor`).

In `M_Options_Key`, change the Enter switch:

```c
		case 12:
			M_Menu_Mouse_f ();
			break;
		case 13:
			M_Menu_Video_f ();
			break;
```

and the skip block at the bottom from `options_cursor == 12` to:

```c
	if (options_cursor == 13 && vid_menudrawfn == NULL)
	{
		if (k == K_UPARROW)
			options_cursor = 12;
		else
			options_cursor = 0;
	}
```

- [ ] **Step 6: Dispatchers**

In `M_Draw`'s switch, add:

```c
	case m_mouse:
		M_Mouse_Draw ();
		break;
```

In `M_Keydown`'s switch, add:

```c
	case m_mouse:
		M_Mouse_Key (key);
		break;
```

- [ ] **Step 7: Build + runtime proof**

Run: `make build-release` (exit 0), `make run`. Options → `Mouse-only options` (click it). With the mouse only: toggle master switch, switch profiles, click the left/right halves of slider rows to decrement/increment them, click a label to apply its default action, cycle the toggle button, set `cl_bob` 0 and `v_kicktime` 0, escape back. Quit.

- [ ] **Step 8: Commit**

```bash
git add Quake/client/menu.c
git commit -m "access: Mouse options page with all tunables (mouse-only plan Task 10)"
```

---

### Task 11: `configs/autoexec-mouseonly.cfg` + README section

**Files:**
- Create: `configs/autoexec-mouseonly.cfg`
- Modify: `README.md` (append a section)

**Interfaces:**
- Consumes: every command/cvar from Tasks 1-10.
- Produces: deliverable 1 (ready-to-use config) and user-facing docs.

- [ ] **Step 1: Write `configs/autoexec-mouseonly.cfg`**

```
// Mouse-only control for the Apple Silicon glquake port.
//
// INSTALL: copy this file to game/id1/autoexec.cfg — the engine execs
// it automatically at startup. game/ is gitignored, so it is not
// committed. Full design: docs/superpowers/specs/
// 2026-08-30-mouse-only-control-design.md
//
// Default map (5-button mouse + wheel):
//   MOUSE1 fire            MOUSE2 cruise toggle
//   MOUSE3 mode toggle (engine-consumed via access_toggle_button;
//                      do NOT bind it)
//   MOUSE4 hold to strafe  MOUSE5 jump / swim up
//   wheel: weapon cycle
// Look mode = mouselook. Walk mode = Y moves, X turns, +strafe
// sidesteps. Fire/jump/weapons never change mode.

access_mouseonly 1

// buttons
bind MOUSE1 +attack
bind MOUSE2 access_toggle_cruise
bind MOUSE4 +strafe
bind MOUSE5 +jump
bind MWHEELUP "impulse 10"
bind MWHEELDOWN "impulse 12"

// movement: throttle profile for mice and trackballs
access_move_profile 0
access_throttle_gain 0.002
access_velocity_gain 1
access_deadzone 0
access_curve 1
access_walkspeed 190
access_tremor 0
access_turnrate 240
access_throttle_decay 0
sensitivity 3

// supporting settings (also tunable in Options > Mouse-only options)
sv_aim 0.93
cl_bob 0
cl_rollangle 0
v_kicktime 0
host_timescale 1
host_maxfps 72
cl_forwardspeed 400
cl_backspeed 400
skill 0

// feedback
access_hud 1
access_sounds 1
access_log 1

// gestures: the 5-button default needs none
access_longpress_button 0
access_doubleclick_button 0

// ── Fallback for 2-3 button devices ──────────────────────────────
// Uncomment if your device has no wheel click / side buttons.
// MOUSE2: tap = cruise (delayed up to the double-click window),
//         double-click = mode toggle, long-press = sticky layer
//         (next MOUSE1 click = quicksave, next MOUSE2 click = menu).
// Note: a true 2-button device with no wheel has no weapon cycling.
//access_doubleclick_button 201
//access_doubleclick_command access_toggle_mode
//access_longpress_button 201
//access_layer_cmd1 "save quick"
//access_layer_cmd2 "togglemenu"
```

- [ ] **Step 2: README section**

Append to `README.md`:

```markdown
## Mouse-only play (accessibility)

glquake can be played entirely with a pointing device — no keyboard,
including menus and saving. Copy `configs/autoexec-mouseonly.cfg` to
`game/id1/autoexec.cfg` and launch.

- Look mode (default): standard mouselook. Wheel click toggles to
  Walk mode: mouse forward/back sets a throttle (the mouse can rest
  still while walking), X turns, holding MOUSE4 sidesteps. Pitch stays
  level; vertical auto-aim (`sv_aim`) covers aiming while firing.
- MOUSE1 fire, MOUSE2 cruise control, MOUSE4 strafe, MOUSE5 jump /
  swim up, wheel cycles weapons.
- Menus are point-and-click: left click selects, right click goes
  back, wheel scrolls. Options > Mouse-only options tunes everything
  (gains, dead zone, response curve, speed cap, tremor filter,
  turn-rate cap, gestures, HUD, sounds, plus sv_aim / cl_bob /
  cl_rollangle / v_kicktime / host_timescale / host_maxfps).
- 2-3 button devices: see the commented fallback block at the bottom
  of the cfg (double-click toggles modes, long-press opens a sticky
  layer: next click = quicksave, next = menu).
- The cursor is never locked; keep the window focused for motion
  input. `access_mouseonly 0` restores stock input.
```

- [ ] **Step 3: Verify the cfg parses**

```bash
cp configs/autoexec-mouseonly.cfg game/id1/autoexec.cfg
make run
```

Expected: boot with no `couldn't exec` errors; console shows the binds (`bindlist` lists MOUSE1/2/4/5 + MWHEEL entries); the HUD shows `LOOK`. Then remove the copy so the repo state stays clean:

```bash
rm game/id1/autoexec.cfg
```

Quit the game.

- [ ] **Step 4: Commit**

```bash
git add configs/autoexec-mouseonly.cfg README.md
git commit -m "Add mouse-only autoexec.cfg + README section (mouse-only plan Task 11)"
```

---

### Task 12: Final gates, ledger, validation runbook

**Files:**
- Modify: `docs/superpowers/plans/2026-08-29-quake-apple-silicon.md` (append Fixes Ledger entry)
- Test: full build oracle, SIGKILL smoke, manual validation protocol

**Interfaces:**
- Consumes: all previous tasks.
- Produces: deliverable 3 (validation protocol run — or scheduled with testers), ledger record, final green build.

- [ ] **Step 1: Full build oracle**

Run: `make clean && make build-release build-server build-client`
Expected: exit 0, all three binaries rebuilt (glquake, qwsv, glqwcl). QuakeWorld binaries are untouched by this feature but must still build.

- [ ] **Step 2: SIGKILL smoke protocol**

Per CONTEXT.md: launch each binary against the game data, SIGKILL it, and confirm zero `Received signal` lines in its output:

```bash
./Quake/build-macosx/glquake -basedir game > /tmp/smoke-glquake.log 2>&1 & \
  PID=$!; sleep 8; kill -9 $PID; wait $PID 2>/dev/null
./QuakeWorld/build-macosx/qwsv -basedir game +gamedir qw > /tmp/smoke-qwsv.log 2>&1 & \
  PID=$!; sleep 5; kill -9 $PID; wait $PID 2>/dev/null
grep -c "Received signal" /tmp/smoke-glquake.log /tmp/smoke-qwsv.log
```

Expected: 0 and 0. (glqwcl needs a server to be meaningful; launch+SIGKILL it the same way for the count. If a binary needs game data you don't have, record that in the ledger entry instead of faking the result.)

- [ ] **Step 3: Kill-switch parity check**

```bash
cp configs/autoexec-mouseonly.cfg game/id1/autoexec.cfg
make run
```

In game: `access_mouseonly 0` — confirm input behaves exactly as before this feature (mouselook via `+mlook` bindings unchanged, no HUD, no `access:` lines, menus keyboard-only). `access_mouseonly 1` restores it. Remove the cfg copy afterwards.

- [ ] **Step 4: Mouse-only functional loop (deliverable-3 dry run)**

With the cfg installed, complete this loop using the pointing device only (observer watches for keyboard touches):

1. Launch → main menu → Single player → New Game → episode/skill 0 → start.
2. Play ~2 minutes: toggle Look↔Walk, cruise on/off, cycle weapons, jump.
3. Menu → Save → slot 0. Quit to desktop (menu → Quit → confirm).
4. Relaunch → Single player → Load → slot 0 → resume at the saved spot.
5. In Walk mode, die (`kill`) — verify forced Look + zero movement.

Expected: every step achievable without the keyboard; HUD and sounds confirm each transition; `access:` log lines match the observed transitions.

- [ ] **Step 5: Tester validation protocol (deliverable 3)**

Run with one tester per device class (standard mouse; trackball), same build, throttle profile, matching map (5-button default or 2-button fallback):

| Item | Value |
|---|---|
| Scenario | E1M1 (The Slipgate Complex), skill 0, fresh game |
| Familiarization | 10 min free play |
| Runs | 3 timed full completions (reach the exit slipgate), observer present |
| Metric 1 | Completion time — wall clock + in-level `time`, median of 3 |
| Metric 2 | Mode errors per minute — observer-flagged wrong-mode inputs + unintended-toggle `access:` lines, ÷ run minutes |
| Metric 3 | Self-reported fatigue — 1-10 scale + short NASA-TLX after each run |
| Pass | All runs completed with zero keyboard touches; mode errors < 2/min by run 3; fatigue ≤ 5/10 |

Record results in the ledger entry below (or a sibling doc if the tests happen later).

- [ ] **Step 6: Ledger entry + commit**

Append to the Fixes Ledger section of `docs/superpowers/plans/2026-08-29-quake-apple-silicon.md`:

```markdown
### Mouse-only control (feature)
Commits: <task shas>. Spec:
docs/superpowers/specs/2026-08-30-mouse-only-control-design.md. Plan:
docs/superpowers/plans/2026-08-30-mouse-only-control.md.
New module Quake/client/cl_access.c (Look/Walk modes, throttle +
velocity profiles with dead zone/response curve/tremor filter/turn
cap, cruise control, gesture engine with long-press sticky layer and
double-click, safety resets on death/menu/disconnect/idle, HUD
indicator with throttle bar, transition sounds from pak0). Seams:
in_sdl.c (MOUSE4/5 + wheel + button routing + IN_MouseMove branch),
keys.h/keys.c (K_MOUSE4/5 203/204, replacing orphaned K_JOY1/2),
host.c (host_maxfps/host_timescale + Access_Frame), cl_main.c
(Access_Init/Access_Reset), gl_screen.c (Access_DrawHUD), menu.c
(point-and-click all pages, setup name palette, new m_mouse options
page). Kill switch: access_mouseonly. Config:
configs/autoexec-mouseonly.cfg. QuakeWorld untouched.
Validation: <testers / dates / metrics or "pending tester sessions">.
```

Fill in the real commit SHAs and validation data, then:

```bash
git add docs/superpowers/plans/2026-08-29-quake-apple-silicon.md
git commit -m "Ledger: mouse-only control feature"
```
