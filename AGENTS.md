# AGENTS.md — Quake Apple Silicon Port

## Project Overview

A fork of id Software's GPL Quake (the 1999 release), ported to native Apple
Silicon (arm64) macOS with an SDL3 platform layer and an OpenGL renderer. The
Windows/DOS/Linux/Sun platform code, the software renderer, and the x86
assembly were removed. Built for educational purposes only; license is GPL,
see `gnu.txt`.

Three binaries build from two source **trees**:

| Binary | Tree | What it is |
|---|---|---|
| `glquake` | `Quake/` | Single-player OpenGL client |
| `qwsv` | `QuakeWorld/` | QuakeWorld dedicated server (headless) |
| `glqwcl` | `QuakeWorld/` | QuakeWorld OpenGL client |

The game ships **without game data**: `game/` (git-ignored) must be populated
from a retail Quake install. The codebase is id-era C, built with Apple clang
+ GNU make.

## Repository Layout

| Path | Contents |
|---|---|
| `Quake/` | Single-player engine tree. Per-module subfolders: `common/` (core services + shared header pool: `quakedef.h`, `protocol.h`, `model.h`, …), `client/` (`cl_*` plus menu, keys, console, sbar, view, and the mouse-only `cl_access.c`), `render/` (`gl_*`, `r_part`), `server/` (`sv_*`, `pr_*`, `world`), `net/`, `sound/`, `platform/` (SDL3 drivers), plus `macosx-shim/` GL headers. `host.c`/`host_cmd.c` sit at the tree root — Host orchestrates every module |
| `QuakeWorld/` | QuakeWorld tree — a separate copy that may drift from `Quake/`. `server/` (qwsv), `client/` (glqwcl), `progs/` (QuakeC + `qwprogs.dat`) |
| `configs/` | Committed `autoexec-mouseonly.cfg`, meant to be copied to `game/id1/autoexec.cfg` at runtime (game data — never committed there) |
| `tools/` | Standalone, decoupled Pillow-only texture pipeline (`extract.py`, `install.py`) |
| `QuakeMCP/` | Optional MCP control surface for glquake — C bridge (`bridge/`), Python server (`src/quakemcp/`), pytest suites, engine-hook docs. Rules: [`QuakeMCP/AGENTS.md`](QuakeMCP/AGENTS.md) |
| `docs/superpowers/` | One file per feature (design spec, then implementation plan); the append-only Fixes Ledger at the end of `2026-08-29-quake-apple-silicon.md` |
| `game/` | **User territory** — game data (paks, `qwprogs.dat`, saves, configs). Git-ignored — never commit |
| `.superpowers/` | Transient SDD agent workspaces. Git-ignored — never commit |

## Architecture Notes (load-bearing)

- **Two independent trees:** `Quake/` and `QuakeWorld/` are separate copies;
  same-named files (e.g. `common/common.c`) may drift. Files are deliberately
  not compiled by path across trees — never assume a change to one applies to
  the other.
- **Platform modules** (per GL client, in each tree's `platform/`): the video
  module (`gl_vidsdl.c` — window/GL lifecycle), the input module (`in_sdl.c` —
  `IN_*` plus the SDL event pump), the sound driver (`snd_sdl.c` — `SNDDMA_*`),
  and the null CD adapter (`cd_null.c`). The platform bootstrap (`sys_unix.c` —
  main, clock, the `Sys_*` family) sits there too for the two GL clients
  (qwsv's copy stays in the flat `QuakeWorld/server/`, deliberately separate).
- **Mouse-only control (accessibility):** glquake-only module
  `Quake/client/cl_access.c`, kill switch `access_mouseonly`. Invariant: when
  `access_mouseonly` is 0 the module is inert and every input path is
  byte-identical to vanilla. Its HUD Look/Walk button is drawn dead-center
  and translucent via two alpha draw primitives (`Draw_FillAlpha`,
  `Draw_StringAlpha` in `render/gl_draw.c`) that toggle `GL_MODULATE` /
  `GL_ALPHA_TEST` state — preserve that pattern when touching them.
- **QuakeMCP (optional):** glquake-only MCP control surface, built with
  `make build-mcp` (`QUAKE_MCP=1`). Every hook sits behind `#ifdef QUAKE_MCP`
  or a no-op macro shim, so vanilla builds are behaviorally identical; its
  invariants (lease, input merge, takeover, policy) live in
  [`QuakeMCP/AGENTS.md`](QuakeMCP/AGENTS.md).
- **Game data is never committed:** `game/` (paks, assets, and the loose TGA
  overrides under `game/id1/` and `game/qw/`) is user-supplied — `game/id1/`
  feeds glquake, `game/qw/` feeds qwsv/glqwcl (the QW binaries mount id1 too).
  The committed pipeline that produces the TGAs lives in `tools/`.

## Building and Running

Requirements: macOS/arm64, Xcode CLT (`cc`, `make`), and SDL3 resolvable via
pkg-config (`brew install sdl3 pkg-config`); without it the Makefile fails
opaquely at parse time.

`make` with no target prints the target list (the Makefile's `##` comments are
the source of truth).

**Gates** — the build is the verification for the engine trees; there is no
lint or CI:

- build oracle: `make clean && make build-release build-server build-client`
  from the repo root (must exit 0);
- the 3-binary SIGKILL smoke protocol (launch each binary, SIGKILL it — the
  "Received signal" count in its output must be 0);
- the Fixes Ledger (append-only record of every pass).

QuakeMCP adds its own gates — `make build-mcp`, the pytest suites, and the
manual takeover protocol: see [`QuakeMCP/AGENTS.md`](QuakeMCP/AGENTS.md).

Runtime smoke checks (need user game data): `make run` / `make run-server` /
`make run-client`.

## Development Conventions

- **id-era C style:** tabs, K&R braces, `/* banner */` function comment blocks. Match surrounding code exactly.
- **Minimal blast radius:** prefer the narrowest correct change; reuse existing code paths before adding new ones.
- **Git discipline:** stage explicit paths only — never `git add -A` or `git commit -a`. Game data must never enter a commit. Push only when explicitly asked. Feature work lands on a branch, then is `--no-ff` merged to `main`.
- **Specs before rounds:** larger changes follow the `docs/superpowers/` pattern — a dated design spec then its implementation plan; the append-only Fixes Ledger records follow-up fixes.