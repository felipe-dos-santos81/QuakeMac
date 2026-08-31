# Quake (GPL source) — Apple Silicon port

A fork of [id-software/quake](https://github.com/id-software/quake) —
the 1999 id Software GPL release — simplified into an Apple Silicon port
and maintained for educational purposes only. The target is **macOS on
Apple Silicon (arm64) only**: Windows/DOS/Linux/Sun platform code, the
software renderer, and the x86 assembly have been removed; the engines
build through an additive SDL3 platform layer. See `gnu.txt` for the
license.

## What builds

| Binary | Tree | What it is |
|---|---|---|
| `glquake` | `Quake/` | Single-player OpenGL client |
| `qwsv` | `QuakeWorld/` | QuakeWorld dedicated server (headless) |
| `glqwcl` | `QuakeWorld/` | QuakeWorld OpenGL client |

`QuakeWorld/progs/` holds the QuakeWorld QuakeC source and `qwprogs.dat`.

## Prerequisites

- macOS on Apple Silicon, Xcode command line tools (`cc`, `make`)
- SDL3, resolvable via pkg-config: `brew install sdl3 pkg-config`
- Game data from a legally owned Quake (not included; `game/` is gitignored)
- Python 3 + Pillow (`pip install Pillow`) — only for the optional texture tools in `tools/`

## Build

A single `Makefile` lives at the repo root; bare `make` prints the target
list.

```sh
make build-release               # → Quake/build-macosx/glquake
make build-server build-client   # → QuakeWorld/build-macosx/qwsv, glqwcl
```

The three builds are independent — add `-j` to parallelize, e.g.
`make -j build-release build-server build-client`.

Other targets: `build-debug` / `build-server-debug`, `check-data`
(both games; `check-data-quake` / `check-data-qw` check one), `run` /
`run-server` / `run-client`, `export-textures` (extract game textures
to `tools/extracted/`), `clean`.

## Game data (not included)

The engines do not run without the base game files, which are not
included in this repo. Copy them from a legally owned Quake into
`game/` (gitignored):

```
game/
├── id1/   pak0.pak (and pak1.pak)          ← glquake
└── qw/    qwprogs.dat and pak0.pak         ← qwsv / glqwcl
```

`pak0.pak` (plus `pak1.pak` for the registered game) lives in the
`id1/` folder of your Quake installation. For QuakeWorld, also copy
`qwprogs.dat` into `game/qw/` — a copy ships in this repo at
`QuakeWorld/progs/qwprogs.dat`. `make check-data`
(or `check-data-quake` / `check-data-qw` per game) verifies the
layout before launching.

## Run

```sh
make run          # glquake against game/
make run-server   # qwsv against game/qw
make run-client   # glqwcl; then: connect localhost
```

## External texture overrides (optional)

Loose TGA files under `game/id1/` override the original 8-bit assets
at load time in both GL clients (`glquake` and `glqwcl`):

```
game/id1/textures/<name>.tga   — brush textures (any power-of-two size, same aspect)
game/id1/gfx/<name>.tga        — menu/HUD pics (exact original size)
game/id1/progs/<model>.tga     — alias skins / sprite frames (same aspect)
```

Toggle at runtime with `gl_externaltextures 0/1` (applies to
subsequently loaded textures). See `tools/README.md` for the
extraction pipeline.

### Texture tools (decoupled, `tools/`)

```sh
pip install Pillow                       # one-time
make export-textures                     # → tools/extracted/ + manifest.json
# edit PNGs in tools/extracted/ with any image tool (keep stem names)
python3 tools/install.py <png-or-dir>    # validates + writes TGA to game/id1/
```

`tools/extracted/` is gitignored — it derives from commercial game
data and is never committed.

## Documentation

Design specs, implementation plans, and the running Fixes Ledger live in
`docs/superpowers/` (port spec/plan dated 2026-08-29, dead-code cleanup
dated 2026-08-30, external texture overrides dated 2026-08-30).

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
