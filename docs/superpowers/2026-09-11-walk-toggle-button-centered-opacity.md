# Walk-toggle button: centered + 70% opacity with idle fade

Revision of the mouse-only control scheme (parent spec:
`docs/superpowers/2026-08-30-mouse-only-control.md`, "Revision
2026-08-31" section). This change reworks only the HUD Look/Walk toggle
button. It does **not** touch the movement model (delta-based Look/Walk
axes stay as shipped) or the button/menu/demo mouse handling already in
`Quake/client/cl_access.c`.

Reference for the intended button behavior: the Quake II Apple Silicon
port's `SCR_DrawWalkButton` (`client/screen/scrn.c` in that repo), which
draws a centered translucent button that goes solid once the mouse
rests.

## Scope

- Reposition the Look/Walk HUD button to dead-center screen.
- Render the button at 70% opacity while the mouse is moving; switch to
  fully opaque (alpha 1.0) once the mouse has been still for 500 ms.
- Remove the top-left throttle bar and the hover-brighten marker.
- Everything else is unchanged: MOUSE1 fires, a MOUSE1 double-click
  jumps, MOUSE2 toggles Look/Walk, Walk mode levels the view on entry,
  and during demo playback any click opens the main menu.

## Current state

`Quake/client/cl_access.c` already implements the full scheme. The
button is drawn by `Access_DrawHUD` at the top-left
(`ACCESS_BTN_X/Y = 6,6`, 44x12) using the opaque 8-bit `Draw_Fill`
primitive, with a walk-throttle bar beneath it and a hover-brighten
marker. Click hit-testing is `Access_CursorInButton`, which scales the
SDL window cursor into `vid.width x vid.height` space and tests against
the same top-left rect.

## Design

### 1. Alpha draw primitives (render layer)

`Quake/render/gl_draw.c` already contains the blend pattern used for the
console background (`Draw_AlphaPic`, gl_draw.c:601) and the fade screen
(`Draw_FadeScreen`, gl_draw.c:810): disable `GL_ALPHA_TEST`, enable
`GL_BLEND`, draw with `glColor4f`, then restore. The global blend func
`GL_SRC_ALPHA, GL_ONE_MINUS_SRC_ALPHA` is set once in
`Quake/platform/gl_vidsdl.c:244`, so no per-draw blendfunc is needed.

Add two functions, declared in `Quake/render/draw.h` and defined in
`gl_draw.c`, patterned on the existing `Draw_Fill` / `Draw_String`:

- `void Draw_FillAlpha (int x, int y, int w, int h, int c, float alpha)`
  — like `Draw_Fill` but the quad is drawn with
  `glColor4f (r, g, b, alpha)` under `GL_BLEND`.
- `void Draw_StringAlpha (int x, int y, char *str, float alpha)` — like
  `Draw_String` but each `Draw_Character` glyph drawn with
  `glColor4f (1, 1, 1, alpha)` under `GL_BLEND` (with matching
  `GL_ALPHA_TEST` toggling).

Both restore the previous color and blend state before returning, so
callers keep the same contract as today's `Draw_Fill` / `Draw_String`.

### 2. Button rendering (client module)

Rewrite `Access_DrawHUD` (`cl_access.c:613`) to:

- Derive the button rect from a new single source of truth,
  `Access_ButtonRect` (static helper in `cl_access.c`): a 4-character
  label box ("WALK"/"LOOK") centered at
  `x = (vid.width  - w) / 2`, `y = (vid.height - h) / 2`. The rect is
  derived dynamically from `vid.width`/`vid.height` every call (handles
  video-mode changes with no extra state).
- Track idle state with module statics `last_x, last_y, last_move_ms`.
  Each draw: read the cursor via `SDL_GetMouseState`, scale into vid
  space with the existing `Access_CursorInButton` scaling, and if the
  position differs from last frame update `last_x/last_y/last_move_ms`.
  `now = SDL_GetTicks()`; `alpha = (now - last_move_ms > 500) ? 1.0f :
  0.7f`.
- Draw the box with `Draw_FillAlpha (…, alpha)` and the label with
  `Draw_StringAlpha (…, alpha)`, both at the centered rect.
- Delete the throttle-bar block, the hover-brighten `Draw_Fill`, and the
  blink marker.

`Access_CursorInButton` switches to the `Access_ButtonRect` rect so the
click-to-toggle (and the MOUSE2-free fallback) continues to work at the
new location. `Access_ButtonEvent` needs no change: it already calls
`Access_CursorInButton` and then `Access_ToggleMode_f`.

Constants (in `cl_access.c`):
- `#define ACCESS_BTN_ALPHA     0.7f`
- `#define ACCESS_BTN_IDLE_MS   500`
- `#define ACCESS_BTN_PAD_X` / `ACCESS_BTN_PAD_Y` for the box padding
  around the 4-character label.

## Invariants

- When `access_mouseonly` is 0, this module is inert and every input
  path is byte-identical to vanilla (unchanged).
- The new render helpers only extend the `Draw_*` seam in `draw.h`;
  `cl_access.c` never calls `gl_*` directly.
- `key_dest != key_game` or `cls.demoplayback` still suppress the button
  (existing early-return guards in `Access_DrawHUD` remain).

## Edge cases and risks

- **Crosshair / center-string overlap:** a dead-center button overlaps
  the crosshair (`gl_screen.c:909`) and transient center strings. The
  shipped `configs/autoexec-mouseonly.cfg` leaves the crosshair off, and
  the button draws after those, so it is acceptable for the requested
  placement. Noted, not disguised.
- **Opacity definition:** "the mouse stops" is interpreted as "cursor
  position unchanged"; mouse-button presses alone do not restart the
  idle clock (matches the Quake II reference).

## Testing

- Build oracle: `make clean && make build-release build-server
  build-client` from the repo root (no tests/lint/CI — the build is the
  verification).
- Runtime smoke (user game data): `make run`, then:
  - toggle by MOUSE2 and by clicking the centered button;
  - confirm the button reads 70% while the mouse moves and solid after
    ~500 ms of stillness;
  - confirm Walk mode still levels the view and Look mode still aims.

---

**Part 2 — implementation plan**

# Walk-toggle button (centered + opacity) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use
> superpowers:subagent-driven-development (recommended) or
> superpowers:executing-plans to implement this plan task-by-task. Steps
> use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Move the mouse-only HUD Look/Walk toggle button to dead-center
screen and render it at 70% opacity while the mouse moves, switching to
fully opaque after 500 ms of stillness.

**Architecture:** Add two alpha-capable draw primitives to the render
layer (`draw.h`/`gl_draw.c`), then rework `Access_DrawHUD` in the client
accessibility module to derive a centered button rect (shared with the
click hit-test) and drive its opacity from a cursor-motion idle tracker.

**Tech Stack:** C (id-era style), OpenGL via the existing `Draw_*` seam,
SDL3 (`SDL_GetMouseState`, `SDL_GetTicks`). Build is the verification —
there are no unit tests, lint, or CI.

## Global Constraints

- Build oracle (must pass before claiming done): `make clean && make
  build-release build-server build-client` from the repo root.
- No tests/lint/CI; the build is the only automated verification.
- id-era C style: tabs (not spaces), K&R braces, `/* banner */` function
  comment blocks.
- When `access_mouseonly` is 0 the module is inert and every input path
  is byte-identical to vanilla — do not disturb the early-return guards.
- `cl_access.c` must never call `gl_*` directly; it renders only through
  functions declared in `render/draw.h`.
- Git: stage explicit paths only, never `git add -A`; push only when
  explicitly asked.

---

### Task 1: 2D alpha draw primitives in the render layer

**Files:**
- Modify: `Quake/render/draw.h` (add two declarations after `Draw_String`)
- Modify: `Quake/render/gl_draw.c` (add `Draw_StringAlpha` after
  `Draw_String` at ~line 581, and `Draw_FillAlpha` after `Draw_Fill` at
  ~line 801)

**Interfaces:**
- Consumes: nothing new (uses existing `host_basepal`, `Draw_Character`,
  `GL_BLEND`, `GL_ALPHA_TEST` already in `gl_draw.c`; the global blend
  func `GL_SRC_ALPHA, GL_ONE_MINUS_SRC_ALPHA` is already set in
  `Quake/platform/gl_vidsdl.c:244`).
- Produces (consumed by Task 2):
  - `void Draw_FillAlpha (int x, int y, int w, int h, int c, float alpha)`
  - `void Draw_StringAlpha (int x, int y, char *str, float alpha)`

- [ ] **Step 1: Declare the two functions in draw.h**

Open `Quake/render/draw.h`. The tail of the declarations is:

```c
void Draw_Fill (int x, int y, int w, int h, int c);
void Draw_FadeScreen (void);
void Draw_String (int x, int y, char *str);
```

Add, immediately after `Draw_String`:

```c
void Draw_FillAlpha (int x, int y, int w, int h, int c, float alpha);
void Draw_StringAlpha (int x, int y, char *str, float alpha);
```

- [ ] **Step 2: Implement Draw_StringAlpha in gl_draw.c**

`Draw_String` in `gl_draw.c` is:

```c
void Draw_String (int x, int y, char *str)
{
	while (*str)
	{
		Draw_Character (x, y, *str);
		str++;
		x += 8;
	}
}
```

Insert immediately after it (before `Draw_DebugChar`):

```c
/*
=============
Draw_StringAlpha

Draws a string of 8*8 characters at the given opacity.  Draw_Character
does not touch the current color, so a single glColor4f covers the whole
string.  The conchars texture carries a real alpha channel (index 255
uploaded as transparent), so blending alone reproduces the glyph mask.
=============
*/
void Draw_StringAlpha (int x, int y, char *str, float alpha)
{
	glDisable (GL_ALPHA_TEST);
	glEnable (GL_BLEND);
	glColor4f (1,1,1,alpha);

	while (*str)
	{
		Draw_Character (x, y, *str);
		str++;
		x += 8;
	}

	glColor4f (1,1,1,1);
	glEnable (GL_ALPHA_TEST);
	glDisable (GL_BLEND);
}
```

- [ ] **Step 3: Implement Draw_FillAlpha in gl_draw.c**

`Draw_Fill` in `gl_draw.c` is:

```c
void Draw_Fill (int x, int y, int w, int h, int c)
{
	glDisable (GL_TEXTURE_2D);
	glColor3f (host_basepal[c*3]/255.0,
		host_basepal[c*3+1]/255.0,
		host_basepal[c*3+2]/255.0);

	glBegin (GL_QUADS);

	glVertex2f (x,y);
	glVertex2f (x+w, y);
	glVertex2f (x+w, y+h);
	glVertex2f (x, y+h);

	glEnd ();
	glColor3f (1,1,1);
	glEnable (GL_TEXTURE_2D);
}
```

Insert immediately after `Draw_Fill`'s closing brace (before the
`//===` separator line that precedes `Draw_FadeScreen`):

```c
/*
=============
Draw_FillAlpha

Fills a box of pixels with a single color at the given opacity (alpha).
=============
*/
void Draw_FillAlpha (int x, int y, int w, int h, int c, float alpha)
{
	glDisable (GL_TEXTURE_2D);
	glEnable (GL_BLEND);
	glColor4f (host_basepal[c*3]/255.0,
		host_basepal[c*3+1]/255.0,
		host_basepal[c*3+2]/255.0,
		alpha);

	glBegin (GL_QUADS);

	glVertex2f (x,y);
	glVertex2f (x+w, y);
	glVertex2f (x+w, y+h);
	glVertex2f (x, y+h);

	glEnd ();
	glColor4f (1,1,1,1);
	glDisable (GL_BLEND);
	glEnable (GL_TEXTURE_2D);
}
```

- [ ] **Step 4: Build**

Run: `make build-release` from the repo root.
Expected: succeeds with no new warnings. By the end of both tasks the
full oracle (`make clean && make build-release build-server
build-client`) must pass clean.

- [ ] **Step 5: Commit**

```bash
git add Quake/render/draw.h Quake/render/gl_draw.c
git commit -m "render: add Draw_FillAlpha and Draw_StringAlpha 2D primitives"
```

---

### Task 2: Centered 70%-opacity Walk/Look button in cl_access.c

**Files:**
- Modify: `Quake/client/cl_access.c`

**Interfaces:**
- Consumes: `Draw_FillAlpha`, `Draw_StringAlpha` (Task 1).
- Produces: no header change — `Access_DrawHUD`, `Access_CursorInButton`,
  and `Access_ButtonEvent` keep their existing signatures. A new static
  helper `Access_ButtonRect` is local to `cl_access.c`.

- [ ] **Step 1: Replace the button geometry constants**

The top of the state section has:

```c
/* HUD mode-button geometry, in vid.width x vid.height screen space */
#define ACCESS_BTN_X 6
#define ACCESS_BTN_Y 6
#define ACCESS_BTN_W 44
#define ACCESS_BTN_H 12
```

Replace it with:

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

- [ ] **Step 2: Add the Access_ButtonRect helper**

Immediately before `Access_CursorInButton` (the function whose comment
describes the HUD button living in vid space), insert:

```c
/* Centered WALK/LOOK button rect, derived every call from the current
   vid size so video-mode changes need no stored state.  Label is always
   4 characters. */
static void Access_ButtonRect (int *x, int *y, int *w, int *h)
{
	*w = 4 * 8 + 2 * ACCESS_BTN_PAD_X;
	*h = 8 + 2 * ACCESS_BTN_PAD_Y;
	*x = (vid.width  - *w) / 2;
	*y = (vid.height - *h) / 2;
}
```

- [ ] **Step 3: Point the hit-test at the centered rect**

Replace the tail of `Access_CursorInButton` (the final `return` statement
that tests `cx >= ACCESS_BTN_X - 2 ...`) with a call to `Access_ButtonRect`
and a test against that rect. The new function body is:

```c
static qboolean Access_CursorInButton (void)
{
	float	cx, cy;
	int	ww, wh;
	int	x, y, w, h;

	SDL_GetMouseState (&cx, &cy);
	SDL_GetWindowSize (sdl_window, &ww, &wh);
	if (ww <= 0 || wh <= 0)
		return false;
	cx = cx * (float)vid.width / ww;
	cy = cy * (float)vid.height / wh;

	Access_ButtonRect (&x, &y, &w, &h);
	return cx >= x - 2 && cx < x + w + 2
	    && cy >= y - 2 && cy < y + h + 2;
}
```

(Despite the resize comment on `Access_CursorInButton`'s block, the
`SDL_GetMouseState`/`SDL_GetWindowSize` scaling is preserved verbatim.)

- [ ] **Step 4: Rewrite Access_DrawHUD**

Replace the entire `Access_DrawHUD` function (from its `void
Access_DrawHUD (void)` line through its closing brace before the
`/* --- menu seams */` section) with:

```c
void Access_DrawHUD (void)
{
	float	alpha;
	float	cx, cy;
	int	x, y, w, h;
	int	now;
	static float	last_x = -1, last_y = -1;
	static int	last_move_ms;

	if (!access_mouseonly.value || !access_hud.value)
		return;
	if (key_dest != key_game || cls.demoplayback)
		return;

	now = (int)SDL_GetTicks ();
	SDL_GetMouseState (&cx, &cy);
	if (cx != last_x || cy != last_y)
	{
		last_x = cx;
		last_y = cy;
		last_move_ms = now;
	}
	alpha = (now - last_move_ms > ACCESS_BTN_IDLE_MS)
	    ? 1.0f : ACCESS_BTN_ALPHA;

	Access_ButtonRect (&x, &y, &w, &h);
	Draw_FillAlpha (x, y, w, h, 12, alpha);
	Draw_FillAlpha (x + 1, y + 1, w - 2, h - 2, 0, alpha);
	Draw_StringAlpha (x + ACCESS_BTN_PAD_X, y + ACCESS_BTN_PAD_Y,
	                  access_mode == ACCESS_WALK ? "WALK" : "LOOK", alpha);
}
```

Note: this removes the throttle bar block, the hover-brighten `Draw_Fill`
and blink marker, and the transient `access_label` center text. The
`access_throttle` field and the `Access_Label`/`access_label_until`
state remain (still used by `Access_MouseMove` and `Access_ToggleMode_f`).

- [ ] **Step 5: Full build oracle**

Run: `make clean && make build-release build-server build-client`.
Expected: exit 0, no warnings.

- [ ] **Step 6: Runtime smoke (user game data required)**

Run: `make run`. Then:
- toggle by MOUSE2, confirm the button settles at screen center, reads
  70% while the mouse moves and solid after ~500 ms of stillness;
- click the centered button, confirm it toggles too;
- confirm Walk mode still levels the view and Look mode still aims.

- [ ] **Step 7: Commit**

```bash
git add Quake/client/cl_access.c
git commit -m "access: center Walk/Look HUD button at 70% opacity with idle fade"
```