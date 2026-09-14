# Quake — Apple Silicon Port

An educational fork of [id Software's Quake](https://github.com/id-software/quake)
(the 1999 GPL release), simplified for a native Apple Silicon (arm64) macOS
port: SDL3 for video/input/audio, an OpenGL renderer, and every other platform
backend removed (Windows/DOS/Linux/Sun, the software renderer, the x86
assembly).

Three binaries build from two source trees:

| Binary | Tree | What it is |
|---|---|---|
| `glquake` | `Quake/` | Single-player OpenGL client |
| `qwsv` | `QuakeWorld/` | QuakeWorld dedicated server (headless) |
| `glqwcl` | `QuakeWorld/` | QuakeWorld OpenGL client |

`QuakeWorld/progs/` holds the QuakeWorld QuakeC source and `qwprogs.dat`.

Built for educational purposes only. License: GPL, see `gnu.txt`.

Development conventions and agent instructions live in [`AGENTS.md`](AGENTS.md).

**This repository does not ship the game data.** You must add the base game
files from a retail Quake installation yourself.

## Requirements

- macOS on Apple Silicon (arm64)
- Xcode Command Line Tools (`cc`, `make`)
- SDL3 — `brew install sdl3 pkg-config`
- (optional) Python 3 + Pillow — texture tools; QuakeMCP needs Python 3.12+
  with `mcp` and Pillow (pinned in `QuakeMCP/pyproject.toml`)

## Game data

Copy the base game files into `game/` at the repository root:

```
game/
├── id1/   pak0.pak (and pak1.pak)          ← glquake
└── qw/    qwprogs.dat and pak0.pak         ← qwsv / glqwcl
```

`pak0.pak` (plus `pak1.pak` for the registered game) lives in the `id1/`
folder of your Quake installation. For QuakeWorld, also copy `qwprogs.dat`
into `game/qw/` — a copy ships in this repo at `QuakeWorld/progs/qwprogs.dat`.
Saves, screenshots, and configs also live under `game/` and are git-ignored.

## Build and run

```
make build-release                  # → Quake/build-macosx/glquake
make build-server build-client      # → QuakeWorld/build-macosx/qwsv, glqwcl
make run                            # glquake against game/
make run-server                     # qwsv against game/qw
make run-client                     # glqwcl; then: connect localhost
```

The three builds are independent — add `-j` to parallelize them. `make` with
no target prints the target list; `make check-data` verifies the game-data
layout before launching, `make clean` removes build output, and
`build-debug` / `build-server-debug` build the debug variants.

## Mouse-only play (accessibility)

glquake can be played entirely with a pointing device — no keyboard, including
the menus and saving. Copy `configs/autoexec-mouseonly.cfg` to
`game/id1/autoexec.cfg` and launch.

Look mode (the default) is standard mouselook. Right-click (or the HUD button)
toggles **walk mode**: the view levels and swings to face the map center on
entry, the mouse turns left/right, and forward/back sets a throttle (the mouse
can rest still while walking). Vertical auto-aim (`sv_aim`) covers aiming
while firing. A fixed button at the dead center of the HUD offers the same
toggle for mice without a working right button — labeled with the mode a click
switches to ("WALK" while looking, "LOOK" while walking), drawn 50% opaque
while the mouse moves and solid once it rests (~500 ms).

MOUSE1 fires and a MOUSE1 double-click jumps; no wheel or side buttons are
used (so there is no mouse-driven weapon switching). The cursor is never
locked — keep the window focused for motion input; `access_mouseonly 0`
restores the stock input behavior. During demo playback, any click opens the
main menu.

## Texture overrides

Loose TGA files under `game/id1/` override the original 8-bit assets at load
time in both GL clients (`glquake` and `glqwcl`):

```
game/id1/textures/<name>.tga   — brush textures (any power-of-two size, same aspect)
game/id1/gfx/<name>.tga        — menu/HUD pics (exact original size)
game/id1/progs/<model>.tga     — alias skins / sprite frames (same aspect)
```

Toggle at runtime with `gl_externaltextures 0/1` (applies to subsequently
loaded textures). The extraction pipeline lives in `tools/` (decoupled,
Pillow-only):

```
pip install Pillow                       # one-time
make export-textures                     # → tools/extracted/ + manifest.json
python3 tools/install.py <png-or-dir>    # validates + writes TGA to game/id1/
```

`tools/extracted/` is git-ignored — it derives from commercial game data and
is never committed.

## QuakeMCP (optional)

`QuakeMCP/` adds an MCP control surface to glquake: a C bridge compiled in
under `QUAKE_MCP=1`, plus a Python server that exposes 13 tools for movement,
frames, telemetry, and guarded console access. Vanilla builds and
`QuakeWorld/` are untouched.

```
make build-mcp                  # glquake + bridge (cleans first)
```

Setup, tools, and tests: [`QuakeMCP/README.md`](QuakeMCP/README.md).

## Documentation

Each feature has one file in `docs/superpowers/` — its design spec followed
by its implementation plan. The running Fixes Ledger is appended at the end of
the `2026-08-29-quake-apple-silicon.md` port file.