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