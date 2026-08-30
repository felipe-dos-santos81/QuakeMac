# Quake (GPL source) — Apple Silicon port

The 1999 id Software GPL Quake source, maintained for **macOS on Apple
Silicon (arm64) only**. Windows/DOS/Linux/Sun platform code, the software
renderer, and the x86 assembly have been removed; the engines build through
an additive SDL3 platform layer. See `gnu.txt` for the license.

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
`run-server` / `run-client`, `clean`.

## Game data

```
game/
├── id1/   pak0.pak (and pak1.pak)          ← glquake
└── qw/    qwprogs.dat and pak0.pak         ← qwsv / glqwcl
```

`qwprogs.dat` ships in this repo at `QuakeWorld/progs/qwprogs.dat`; the
`.pak` files must come from your copy of Quake. `make check-data`
(or `check-data-quake` / `check-data-qw` per game) verifies the
layouts before launching.

## Run

```sh
make run          # glquake against game/
make run-server   # qwsv against game/qw
make run-client   # glqwcl; then: connect localhost
```

## Documentation

Design specs, implementation plans, and the running Fixes Ledger live in
`docs/superpowers/` (port spec/plan dated 2026-08-29, dead-code cleanup
spec/plan dated 2026-08-30).
