# Quake (GPL source) — Apple Silicon port

The 1999 id Software GPL Quake source, maintained for **macOS on Apple
Silicon (arm64) only**. Windows/DOS/Linux/Sun platform code, the software
renderer, and the x86 assembly have been removed; the engines build through
an additive SDL3 platform layer. See `readme.txt` for John Carmack's
original release notes and `gnu.txt` for the license.

## What builds

| Binary | Tree | What it is |
|---|---|---|
| `glquake` | `WinQuake/` | Single-player OpenGL client |
| `qwsv` | `QW/` | QuakeWorld dedicated server (headless) |
| `glqwcl` | `QW/` | QuakeWorld OpenGL client |

`QW/progs/` holds the QuakeWorld QuakeC source and `qwprogs.dat`.

## Prerequisites

- macOS on Apple Silicon, Xcode command line tools (`cc`, `make`)
- SDL3, resolvable via pkg-config: `brew install sdl3 pkg-config`
- Game data from a legally owned Quake (not included; `game/` is gitignored)

## Build

Bare `make` prints the target list in each tree.

```sh
cd WinQuake && make build-release        # → build-macosx/glquake
cd QW && make build-server build-client  # → build-macosx/qwsv, glqwcl
```

Other targets: `build-debug` / `build-server-debug`, `check-data`, `run` /
`run-server` / `run-client`, `clean`.

## Game data

```
game/
├── id1/   pak0.pak (and pak1.pak)          ← glquake
└── qw/    qwprogs.dat and pak0.pak         ← qwsv / glqwcl
```

`qwprogs.dat` ships in this repo at `QW/progs/qwprogs.dat`; the `.pak`
files must come from your copy of Quake. `make check-data` verifies the
layout before launching.

## Run

```sh
cd WinQuake && make run          # glquake against ../game
cd QW && make run-server         # qwsv against ../game/qw
cd QW && make run-client         # glqwcl; then: connect localhost
```

## Documentation

Design specs, implementation plans, and the running Fixes Ledger live in
`docs/superpowers/` (port spec/plan dated 2026-08-29, dead-code cleanup
spec/plan dated 2026-08-30).
