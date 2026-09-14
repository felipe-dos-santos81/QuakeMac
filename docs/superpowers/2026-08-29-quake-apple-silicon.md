> Merged on 2026-09-01 from `docs/superpowers/specs/2026-08-29-quake-apple-silicon-design.md` (Part 1 below) and
> `docs/superpowers/plans/2026-08-29-quake-apple-silicon.md` (Part 2). Both parts are verbatim; internal
> cross-references keep their pre-merge paths.

# Design: Build and Run Quake on Apple Silicon (from this repo's sources)

- **Date:** 2026-08-29
- **Status:** Approved (design); implementation plan to follow via writing-plans
- **Phases:** Phase 1 — GLQuake single-player (`WinQuake/`); Phase 2 — QuakeWorld `qwsv` + `glqwcl` (`QW/`)

## 1. Background

This repository is id Software's 1999 GPL release of the complete Quake engine
source: `WinQuake/` (single-player + GLQuake), `QW/` (QuakeWorld client +
server), `qw-qc/` (QuakeWorld QuakeC game logic). The goal is to compile and
run these engines natively on this machine: macOS arm64 (Apple Silicon).

The repo ships its own Unix build path (`WinQuake/Makefile.linuxi386`,
`QW/Makefile.Linux`) targeting 1998-era Linux i386 with x86 GAS assembly.
The porting strategy reuses that path's file selection and replaces only the
Linux-specific platform drivers (video, sound) with SDL3 equivalents.

## 2. Goals and non-goals

**Goals**

- Phase 1: build `glquake` (OpenGL single-player engine) from `WinQuake/`
  sources for macOS arm64 and run it against user-provided game data
  (`id1/pak0.pak`, `pak1.pak`).
- Phase 2: build `qwsv` (dedicated QuakeWorld server) and `glqwcl` (GL
  QuakeWorld client) from `QW/` sources; acceptance is a local server + local
  client connection.

**Non-goals**

- Software renderer (`squake`, `quake.x11`) — requires porting ~20 x86 GAS
  asm files; explicitly out of scope.
- Rewriting game logic, renderer internals, or netcode.
- New build systems (CMake, autotools). Makefiles only, matching repo
  convention.
- Windows/DOS/Sun platform drivers.
- x86_64 or universal binaries — arm64 only.
- Multiplayer over the internet in Phase 2 — localhost only.

## 3. Established facts (evidence)

| Fact | Evidence |
|---|---|
| Host is macOS 26.6.2 arm64, Homebrew 6.0.20, Apple clang 23 | `sw_vers`, `uname -m`, toolchain checks |
| SDL3 3.4.14 installed (`pkg-config sdl3`, headers in `/opt/homebrew/include/SDL3`) | `pkg-config --modversion sdl3` |
| `id386` self-disables off x86: `#ifdef __i386__ → 1 else 0` in `WinQuake/quakeasm.h`, `WinQuake/quakedef.h`, `QW/client/quakeasm.h`, `QW/client/bothdefs.h`, `QW/server/quakeasm.h` | Source inspection; **no source edit needed** |
| `UNALIGNED_OK` likewise becomes 0 off x86 (correct for arm64) | `WinQuake/quakedef.h` |
| GLQuake (Linux) links only 4 asm objects: `math.s`, `worlda.s`, `snd_mixa.s`, `sys_dosa.s`; all have C fallbacks behind `id386` | `WinQuake/Makefile.linuxi386` GLQUAKE_OBJS; readme.txt confirms C-only build via a #define |
| Unix platform drivers already exist in-tree: `sys_linux.c`, `gl_vidlinuxglx.c`, `snd_linux.c`, `cd_null.c`, `cd_linux.c`, `net_udp.c`, `net_bsd.c`, `in_null.c` | File listing of `WinQuake/` |
| QW server is pure C + `sys_unix.c` (headless) | `QW/server/` listing, `QW/Makefile.Linux` |
| Sound driver contract: `SNDDMA_Init`, `SNDDMA_GetDMAPos`, `SNDDMA_Shutdown`, `SNDDMA_Submit` | `WinQuake/snd_linux.c` |
| GL vid driver contract: `VID_Init(palette)`, `VID_Shutdown`, `VID_SetPalette`, `VID_Init8bitPalette`, `Sys_SendKeyEvents` | `WinQuake/gl_vidlinuxglx.c` |
| Game data (`*.pak`) is NOT in the repo; it remains proprietary | readme.txt (Carmack); glob found zero `.pak` files |
| User owns Quake and will supply the pak files | Stated during brainstorming |

## 4. Design overview

**Additive SDL3 platform layer.** All new platform code goes into new files;
original 1999 files are compiled as-is except minimal portability fixes
(§7.4). This mirrors how the GL Linux target swaps one vid/snd driver file —
we swap in SDL3 ones instead.

```
┌────────────────────────────────────────────────────────────┐
│ Engine core (unchanged 1999 C): host, cl_*, sv_*, gl_*     │
│ renderer, model, pr_* (QuakeC VM), net_dgrm/net_loop       │
├────────────────────────────────────────────────────────────┤
│ Platform layer                                             │
│  REUSED: sys_linux.c · net_udp.c+net_bsd.c · cd_null.c     │
│  NEW:    gl_vidsdl.c (video/GL/input) · snd_sdl.c (audio)  │
├────────────────────────────────────────────────────────────┤
│ SDL3 3.4.x (Homebrew) · OpenGL.framework (macOS legacy GL) │
└────────────────────────────────────────────────────────────┘
```

## 5. Phase 1 components (GLQuake)

### 5.1 New files

| File | Role |
|---|---|
| `WinQuake/gl_vidsdl.c` | SDL3 window + OpenGL compatibility-profile context + input. Implements `VID_Init`, `VID_Shutdown`, `VID_SetPalette`, `VID_Init8bitPalette`, `Sys_SendKeyEvents` (SDL event pump → `Key_Event`), mouse via relative mode, `gl*` symbols bound against macOS OpenGL framework. Modeled on `gl_vidlinuxglx.c`. Windowed mode by default at 1024×768; size overridable via `vid_width`/`vid_height` cvars. |
| `WinQuake/snd_sdl.c` | SDL3 audio. Implements `SNDDMA_Init` (open default playback device/stream matching the engine's requested format, populate `shm`), `SNDDMA_GetDMAPos`, `SNDDMA_Submit` (push newly mixed bytes into an `SDL_AudioStream`), `SNDDMA_Shutdown`. Modeled on `snd_linux.c`'s DMA-buffer model but using SDL3's push-stream API instead of mmap. |
| `WinQuake/Makefile.macosx` | New build file (§6). |
| `WinQuake/sys_sdl.c` | Fallback only: written if `sys_linux.c` proves impractical to fix (e.g. Linux-only facilities). Same `Sys_*` contract as `sys_linux.c`. |

### 5.2 Reused without change (expected)

`sys_linux.c` (POSIX), `cd_null.c`, `net_udp.c`, `net_bsd.c`,
`net_dgrm.c`, `net_loop.c`, `net_main.c`, `net_vcr.c`, and the entire
engine/renderer/game-logic object list from `Makefile.linuxi386`
`GLQUAKE_OBJS` minus the four asm objects and minus `gl_vidlinux*.o`.

### 5.3 Assembly

None. `id386` is 0 on arm64 by existing header guards, so all asm-accelerated
paths fall back to their C implementations. The four asm objects from the
Linux makefile are simply omitted from `Makefile.macosx`.

## 6. Build system

New `WinQuake/Makefile.macosx`, styled after the user's reference Makefile
(self-documenting), with object lists cloned from `Makefile.linuxi386`.

**Style requirements**

- Header comment naming the project and phases
- Variables block at top (`CC`, `CFLAGS`, `SDL_CFLAGS/LIBS` from
  `pkg-config sdl3`, `GL_LIBS = -framework OpenGL`, `BUILDDIR`)
- `.PHONY` declaration
- `help` target generated from `## ` doc comments (grep/awk pattern)
- Section dividers `# ── Section ─────…`
- `clean` target

**Targets**

| Target | Purpose |
|---|---|
| `help` | Self-documenting target list |
| `build-release` | Build `$(BUILDDIR)/glquake` with `-O2` (default) |
| `build-debug` | Build with `-g -O0` |
| `check-data` | Verify `$(GAMEDIR)/id1/pak0.pak` exists; print where to put data if missing |
| `run` | Depends on `check-data`; launch the binary against `$(GAMEDIR)` |
| `clean` | Remove `$(BUILDDIR)` |

**Flags:** `-DGLQUAKE`, `$(shell pkg-config sdl3 --cflags)`, `-O2
-ffast-math` (release) or `-g -O0` (debug). Warnings: no `-Werror` (1998 C
under modern clang is warning-heavy); errors must be zero.
**Link:** `$(shell pkg-config sdl3 --libs) -framework OpenGL -lm`.
**Output:** `WinQuake/build-macosx/glquake`.

Phase 2 adds `QW/Makefile.macosx` in the same style with targets
`build-release`, `build-debug`, `check-data`, `run-server`, `run-client`,
`clean`, building `qwsv` and `glqwcl` from `QW/Makefile.Linux` object lists
(same asm omission; QW client's C-only build is likewise gated by `id386`).

## 7. Runtime and game data

- **Data layout:** `game/id1/pak0.pak`, `game/id1/pak1.pak` (repo root,
  git-ignored; `check-data` validates). User copies them from a legally owned
  copy.
- **Launch:** `glquake -basedir <absolute path to game/>`. SDL3 window opens;
  GL context is the macOS legacy compatibility profile (GLQuake uses GL 1.x
  fixed function).
- **First-run config:** `config.cfg` written by the engine into `id1/` as
  usual.
- **CD audio:** disabled (`cd_null.c`). Music via pak sound files only.

### 7.4 Portability-fix policy (shared C files)

Modern clang/arm64 compiling 1998 C will surface errors. Policy:

1. Fix with the minimal semantic change (add missing `extern`/include, cast
   through `uintptr_t`, correct a type). No gameplay or behavior changes.
2. If the classic duplicate-tentative-definition pattern in `common.c` trips
   the linker, add `-fcommon` to `CFLAGS` (standard in Quake ports) rather
   than restructuring globals.
3. Every fix is recorded in a "fixes ledger" section appended to this spec's
   companion implementation plan, with file + one-line rationale.
4. No edits to files that compile cleanly.

## 8. Phase 2 (QuakeWorld)

Order chosen by surface area:

1. **`qwsv`** (server): headless, pure C + `sys_unix.c`; no video/audio.
   Expected to be the smallest port in the whole project. Data layout: a
   `game/qw/` directory alongside `game/id1/`, containing `qwprogs.dat`
   (copied from this repo's `qw-qc/qwprogs.dat`) plus the map/model paks
   (`pak0.pak`/`pak1.pak` copied from the user's `id1/`).
2. **`glqwcl`** (client): reuses the Phase-1 SDL3 layer. Sharing strategy:
   attempt to compile `WinQuake/gl_vidsdl.c`/`snd_sdl.c` directly against QW
   headers via `-I`; if QW's header drift causes friction, create adapted
   copies `QW/client/gl_vidsdl.c`/`snd_sdl.c` (isolation preferred over a
   shared-compat shim at this scale).
3. **Acceptance:** `qwsv` boots and runs a map; `glqwcl` connects to
   `localhost` and a player can move and shoot.

## 9. Testing and acceptance

There is no test framework in a 1998 codebase; verification is:

**Build acceptance (both phases)**
- `make -f Makefile.macosx clean && make -f Makefile.macosx build-release`
  completes with zero errors, reproducible from clean.
- Binary is arm64: `file build-macosx/glquake` reports `arm64`.

**Phase 1 runtime smoke checklist**
1. `check-data` passes with user paks in place.
2. `glquake` launches; SDL3 window + menu render.
3. New game → episode loads; world renders (no missing textures / black
   screen).
4. Movement, jumping, swimming; weapon fires with sound.
5. Enemy appears and animates; taking damage works.
6. Save → quit → load restores position.
7. Clean quit (no hang, no crash on exit).

**Phase 2 runtime smoke checklist**
1. `qwsv` starts, loads map, listens on UDP.
2. `glqwcl` connects to `localhost`; player spawns and moves.

## 10. Risks and mitigations

| Risk | Likelihood | Mitigation |
|---|---|---|
| macOS legacy GL on arm64 lacks something GLQuake uses | Low (QuakeSpasm-lineage ports run on M-series via the same compat profile) | Early `build-debug` + launch test before polishing; if blocked, scope stops at build success and the GL issue is reported, not papered over |
| `sys_linux.c` uses Linux-only facilities | Medium | Budgeted fallback: `sys_sdl.c` with the same `Sys_*` contract |
| Volume of 1998-C compile fixes is larger than expected | Medium | Fixes are mechanical; ledger keeps them auditable; no redesign unless a fix requires behavior change (escalate to user) |
| QW client header drift vs WinQuake | Medium | Adapted-copy fallback (§8.2) |
| Game data absent/licensing | n/a | `check-data` gate; user owns the game and supplies paks |

## 11. Stated assumptions

1. "PyGame" in the original request is interpreted as **SDL** (PyGame is a
   Python binding over SDL and cannot host a C engine); the user refined the
   choice to **SDL3**, which is installed (3.4.14).
2. macOS arm64's legacy OpenGL compatibility profile supports GLQuake's
   fixed-function GL 1.x usage.
3. The user supplies `pak0.pak`/`pak1.pak` before the Phase-1 run step; QW
   data needs are resolved at Phase 2.
4. Windowed rendering is sufficient for acceptance; fullscreen polish is out
   of scope.
5. The reference Makefile style (self-documenting `help`, `## ` comments,
   variables block, section dividers, kebab-case targets) is applied on top of
   repo-native makefile object lists.

## 12. Alternatives considered

- **B: X11/GLX via XQuartz** — fewer new files (reuse `gl_vidlinuxglx.c`) but
  adds a heavy external dependency with poor macOS integration (windowing,
  mouse grab). Rejected: SDL3 is already installed and is the ecosystem
  standard.
- **C: CMake + modern renderer (GL 3.2+/Metal)** — violates repo conventions
  and the minimal-blast-radius constraint; no demonstrated need. Rejected.
- **Software renderer target** — requires porting ~20 x86 GAS asm files; the
  repo readme explicitly discourages touching the asm. Rejected.
- **Run a maintained port (QuakeSpasm) instead of compiling this repo** —
  rejected by the user's explicit goal to compile this repository's engine.

## 13. Deliverables

1. `WinQuake/gl_vidsdl.c`, `WinQuake/snd_sdl.c`, `WinQuake/Makefile.macosx`
   (+ `sys_sdl.c` only if needed)
2. Working arm64 `glquake` binary + passing Phase-1 smoke checklist
3. `QW/Makefile.macosx` (+ adapted SDL layer if needed), working `qwsv` and
   `glqwcl`, passing Phase-2 smoke checklist
4. Fixes ledger appended to the implementation plan
5. This spec + implementation plan committed under `docs/superpowers/`

---

**Part 2 — implementation plan** (verbatim; formerly `docs/superpowers/plans/2026-08-29-quake-apple-silicon.md`)

# Quake on Apple Silicon Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Compile this repo's GLQuake engine (Phase 1) and QuakeWorld server + client (Phase 2) natively on macOS arm64 using an additive SDL3 platform layer, then run them against user-provided game data.

**Architecture:** All 1999 engine C code compiles unchanged in principle; the Linux platform drivers (`gl_vidlinuxglx.c`, `snd_linux.c`, `cd_linux.c`) are replaced by new SDL3 files (`gl_vidsdl.c`, `snd_sdl.c`, existing `cd_null.c`). x86 asm is excluded via the existing `id386` guards (auto-0 on arm64). Builds use new reference-styled Makefiles with object lists cloned from the repo's own `Makefile.linuxi386` / `Makefile.Linux`.

**Tech Stack:** C (C89-era), Apple clang, GNU make, SDL3 3.4.x (`pkg-config sdl3`), macOS OpenGL framework (legacy compatibility profile), arm64.

**Spec:** `docs/superpowers/specs/2026-08-29-quake-apple-silicon-design.md`

## Global Constraints

- **arm64 only** — no x86_64/universal builds.
- **SDL3 via pkg-config** — `$(shell pkg-config sdl3 --cflags)` / `--libs`; do not hardcode paths. SDL2 must not be used.
- **No asm objects** — `math.s`, `worlda.s`, `snd_mixa.s`, `sys_dosa.s` (and all QW asm) are never compiled; `id386` must stay header-automatic (never force it with a `-D` flag).
- **Original files: minimal semantic fixes only** — no gameplay/behavior changes; no refactoring; every fix is appended to the Fixes Ledger at the bottom of this plan as `file:line — one-line rationale`.
- **Warnings allowed, errors zero** — never build with `-Werror`; never silence warnings globally except `-fcommon` if Task 5's decision rule triggers.
- **Never commit** build output (`build-macosx/`, `*.o`, binaries) or game data (`game/`).
- **Reference Makefile style** — header comment, variables block, `.PHONY`, self-documenting `help` from `## ` comments, `# ── Section ─` dividers, kebab-case targets, `clean` (as established in Task 1 and mirrored in Task 6).
- **Game data** — user supplies `pak0.pak`/`pak1.pak` into `game/id1/`; `check-data` gates every `run` target.

## File Structure

| File | Responsibility | Status |
|---|---|---|
| `WinQuake/Makefile.macosx` | Phase-1 build: objects, link, data gate, run | Create (Task 1) |
| `WinQuake/macosx-shim/GL/gl.h` | Redirect `<GL/gl.h>` → `<OpenGL/gl.h>` | Create (Task 1) |
| `WinQuake/macosx-shim/GL/glu.h` | Redirect `<GL/glu.h>` → `<OpenGL/glu.h>` | Create (Task 1) |
| `.gitignore` | Ignore `game/`, `build-macosx/`, objects | Create (Task 1) |
| `WinQuake/snd_sdl.c` | `SNDDMA_*` sound driver on SDL3 audio | Create (Task 3) |
| `WinQuake/gl_vidsdl.c` | `VID_*`/`Sys_SendKeyEvents`/GL context on SDL3 | Create (Task 4) |
| `QW/Makefile.macosx` | Phase-2 build: `qwsv`, `glqwcl` | Create (Task 6) |
| `QW/client/gl_vidsdl.c`, `QW/client/snd_sdl.c` | Adapted SDL3 layer for QW client — only if direct reuse fails (Task 7 decision rule) | Conditional (Task 7) |
| `game/id1/`, `game/qw/` | User game data (git-ignored) | User-supplied |

---

### Task 1: Build scaffold, GL header shims, data gate

**Files:**
- Create: `WinQuake/Makefile.macosx`
- Create: `WinQuake/macosx-shim/GL/gl.h`
- Create: `WinQuake/macosx-shim/GL/glu.h`
- Create: `.gitignore` (repo root)

**Interfaces:**
- Consumes: nothing (first task)
- Produces: the `make -f Makefile.macosx` interface used by every later task — targets `help`, `objects`, `build-release`, `build-debug`, `check-data`, `run`, `clean`; variables `BUILDDIR=build-macosx`, `GAMEDIR`; the `CORE_OBJS` object list (engine minus platform drivers) and `PLATFORM_OBJS` (`gl_vidsdl.o snd_sdl.o`)

- [ ] **Step 1: Write `WinQuake/macosx-shim/GL/gl.h`**

```c
#include <OpenGL/gl.h>
```

- [ ] **Step 2: Write `WinQuake/macosx-shim/GL/glu.h`**

```c
#include <OpenGL/glu.h>
```

Rationale: `glquake.h:30-31` includes `<GL/gl.h>`/`<GL/glu.h>`; macOS SDK exposes them under `<OpenGL/...>`. The shim avoids editing `glquake.h`. GLU is genuinely used (`gluBuild2DMipmaps`, `gluScaleImage` in `gl_draw.c:1030,1035`), so the redirect is real, not a stub.

- [ ] **Step 3: Write repo-root `.gitignore`**

```
game/
WinQuake/build-macosx/
QW/build-macosx/
*.o
```

- [ ] **Step 4: Write `WinQuake/Makefile.macosx`**

```make
# Makefile.macosx — GLQuake for Apple Silicon (macOS arm64)
# Phase 1 of the SDL3 port. Object list cloned from Makefile.linuxi386
# (GLQUAKE_OBJS) minus the four x86 asm objects; Linux vid/snd/cd drivers
# replaced by gl_vidsdl.c / snd_sdl.c / cd_null.c.
# Spec: docs/superpowers/specs/2026-08-29-quake-apple-silicon-design.md

SERVICE = GLQuake (macOS arm64)

# Variables
CC             = cc
BUILDDIR       = build-macosx
GAMEDIR       ?= $(CURDIR)/../game
SDL_CFLAGS     = $(shell pkg-config sdl3 --cflags)
SDL_LIBS       = $(shell pkg-config sdl3 --libs)
GL_LIBS        = -framework OpenGL
BASE_CFLAGS    = -DGLQUAKE -Dstricmp=strcasecmp -I. -Imacosx-shim $(SDL_CFLAGS)
RELEASE_CFLAGS = $(BASE_CFLAGS) -O2 -ffast-math
DEBUG_CFLAGS   = $(BASE_CFLAGS) -g -O0
LDFLAGS        = $(SDL_LIBS) $(GL_LIBS) -lm

# Engine core (Makefile.linuxi386 GLQUAKE_OBJS minus asm objects math/worlda/
# snd_mixa/sys_dosa; cd_linux→cd_null; snd_linux & gl_vidlinuxglx moved to
# PLATFORM_OBJS as their SDL3 replacements)
CORE_OBJS = \
	$(BUILDDIR)/cl_demo.o $(BUILDDIR)/cl_input.o $(BUILDDIR)/cl_main.o \
	$(BUILDDIR)/cl_parse.o $(BUILDDIR)/cl_tent.o $(BUILDDIR)/chase.o \
	$(BUILDDIR)/cmd.o $(BUILDDIR)/common.o $(BUILDDIR)/console.o \
	$(BUILDDIR)/crc.o $(BUILDDIR)/cvar.o \
	$(BUILDDIR)/gl_draw.o $(BUILDDIR)/gl_mesh.o $(BUILDDIR)/gl_model.o \
	$(BUILDDIR)/gl_refrag.o $(BUILDDIR)/gl_rlight.o $(BUILDDIR)/gl_rmain.o \
	$(BUILDDIR)/gl_rmisc.o $(BUILDDIR)/gl_rsurf.o $(BUILDDIR)/gl_screen.o \
	$(BUILDDIR)/gl_test.o $(BUILDDIR)/gl_warp.o \
	$(BUILDDIR)/host.o $(BUILDDIR)/host_cmd.o $(BUILDDIR)/keys.o \
	$(BUILDDIR)/menu.o $(BUILDDIR)/mathlib.o \
	$(BUILDDIR)/net_dgrm.o $(BUILDDIR)/net_loop.o $(BUILDDIR)/net_main.o \
	$(BUILDDIR)/net_vcr.o $(BUILDDIR)/net_udp.o $(BUILDDIR)/net_bsd.o \
	$(BUILDDIR)/pr_cmds.o $(BUILDDIR)/pr_edict.o $(BUILDDIR)/pr_exec.o \
	$(BUILDDIR)/r_part.o $(BUILDDIR)/sbar.o \
	$(BUILDDIR)/sv_main.o $(BUILDDIR)/sv_phys.o $(BUILDDIR)/sv_move.o \
	$(BUILDDIR)/sv_user.o $(BUILDDIR)/zone.o $(BUILDDIR)/view.o \
	$(BUILDDIR)/wad.o $(BUILDDIR)/world.o \
	$(BUILDDIR)/cd_null.o $(BUILDDIR)/sys_linux.o \
	$(BUILDDIR)/snd_dma.o $(BUILDDIR)/snd_mem.o $(BUILDDIR)/snd_mix.o

PLATFORM_OBJS = $(BUILDDIR)/gl_vidsdl.o $(BUILDDIR)/snd_sdl.o

OBJS = $(CORE_OBJS) $(PLATFORM_OBJS)

.PHONY: help objects build-release build-debug check-data run clean

# ── Help ─────────────────────────────────────────────────────────────────────

help: ## Print this help message
	@printf '\033[01;32m${SERVICE}\033[00;37m\n\n'
	@printf "\033[33mUsage:\033[0m\n  make -f Makefile.macosx [target]\n\n\033[33mTargets:\033[0m\n"
	@grep -E '^[-a-zA-Z0-9_\.\/]+:.*?## .*$$' $(MAKEFILE_LIST) | \
		awk 'BEGIN {FS = ":.*?## "}; \
		{printf "  \033[36m%-16s\033[0m %s\n", $$1, $$2}'

# ── Build ────────────────────────────────────────────────────────────────────

$(BUILDDIR):
	mkdir -p $(BUILDDIR)

$(BUILDDIR)/%.o: %.c | $(BUILDDIR)
	$(CC) $(CFLAGS) -o $@ -c $<

objects: CFLAGS = $(RELEASE_CFLAGS)
objects: $(CORE_OBJS) ## Compile engine core objects only (no platform drivers)

build-release: CFLAGS = $(RELEASE_CFLAGS)
build-release: $(BUILDDIR)/glquake ## Build optimized glquake (default)

build-debug: CFLAGS = $(DEBUG_CFLAGS)
build-debug: $(BUILDDIR)/glquake ## Build glquake with -g -O0

$(BUILDDIR)/glquake: $(OBJS)
	$(CC) -o $@ $(OBJS) $(LDFLAGS)

# ── Data gate & run ──────────────────────────────────────────────────────────

check-data: ## Verify game data (id1/pak0.pak) is present
	@if [ ! -f "$(GAMEDIR)/id1/pak0.pak" ]; then \
		echo "ERROR: game data not found."; \
		echo "Expected: $(GAMEDIR)/id1/pak0.pak"; \
		echo "Copy pak0.pak (and pak1.pak) from your legally owned Quake"; \
		echo "into $(GAMEDIR)/id1/ and re-run."; \
		exit 1; \
	fi
	@echo "Game data OK: $(GAMEDIR)/id1"

run: check-data build-release ## Launch glquake against $(GAMEDIR)
	$(BUILDDIR)/glquake -basedir $(GAMEDIR)

clean: ## Remove build output
	rm -rf $(BUILDDIR)
```

Note: switching between `build-release` and `build-debug` without `clean` will not recompile objects (make cannot see flag changes). This matches repo-era practice; do not add dependency tracking for it.

- [ ] **Step 5: Verify help target**

Run: `make -f Makefile.macosx help` (cwd = `WinQuake/`)
Expected: colored target list containing `help`, `objects`, `build-release`, `build-debug`, `check-data`, `run`, `clean`; exit 0.

- [ ] **Step 6: Verify data gate fails cleanly without paks**

Run: `make -f Makefile.macosx check-data`
Expected: exit 1, message `ERROR: game data not found.` with the expected path.

- [ ] **Step 7: Commit**

```bash
git add WinQuake/Makefile.macosx WinQuake/macosx-shim .gitignore
git commit -m "Build scaffold for GLQuake on macOS arm64: Makefile.macosx, GL header shims, data gate"
```

---

### Task 2: Engine core compile campaign

**Files:**
- Modify: whichever core `.c/.h` files the compiler rejects (per the recipes below — nothing else)
- Test: `make -f Makefile.macosx objects` exits 0 with 51 objects in `WinQuake/build-macosx/`

**Interfaces:**
- Consumes: Task 1's `objects` target and shim includes
- Produces: all `CORE_OBJS` compiling cleanly under `RELEASE_CFLAGS`; Fixes Ledger entries appended to this plan

Known facts from compile spikes (already true, no action needed): `GL/gl.h`/`GL/glu.h` resolved by Task-1 shims; `LINUX_VERSION` already defined in `quakedef.h:30`; `stricmp` mapped by `-Dstricmp=strcasecmp`; `FNDELAY` exists on macOS; `sys_linux.c` is plain POSIX including `main()`.

- [ ] **Step 1: Run the core compile**

Run: `make -f Makefile.macosx objects` (cwd `WinQuake/`)
Expected: FAIL — this is the porting campaign input. Read errors file by file.

- [ ] **Step 2: Fix errors using only these recipes**

Apply the minimal matching recipe per error; nothing else without escalating:

| Error class | Recipe |
|---|---|
| `'X.h' file not found` for a system header | If the header is Linux-only and the file is being replaced anyway (`snd_linux.c`), ignore — it is not in CORE_OBJS. Otherwise add a shim under `WinQuake/macosx-shim/` and record it. |
| Implicit function declaration | Add the missing `#include` if the header exists in-tree; else add an `extern` prototype at top of the file. |
| `cast from pointer to integer of different size` / int↔pointer | Cast via `uintptr_t`/`intptr_t` (`#include <stdint.h>` if needed). |
| `tentative definition ... duplicate` at link time (not now) | Task 5 decision rule — do not preempt. |
| `sys_linux.c` needs a Linux-only facility beyond a trivial fix | STOP — escalate. Spec §5.1 fallback is a new `sys_sdl.c` implementing the same `Sys_*` contract; do not start it without user sign-off. |
| Anything requiring a behavior change | STOP — escalate to the user with file:line and the error text. |

- [ ] **Step 3: Re-run until clean**

Run: `make -f Makefile.macosx objects`
Expected: exit 0. Then `ls WinQuake/build-macosx/*.o | wc -l` → `51`.

- [ ] **Step 4: Update the Fixes Ledger**

Append every fix to the table in the Fixes Ledger section at the bottom of this plan file (file:line — rationale).

- [ ] **Step 5: Commit**

```bash
git add -u WinQuake docs/superpowers/plans/2026-08-29-quake-apple-silicon.md
git commit -m "Make GLQuake engine core compile on macOS arm64 (clang, C-only, id386=0)"
```

---

### Task 3: SDL3 sound driver (`snd_sdl.c`)

**Files:**
- Create: `WinQuake/snd_sdl.c`
- Test: `make -f Makefile.macosx objects` still exits 0 and `build-macosx/snd_sdl.o` exists (object compiles; full audio verification is Task 5)

**Interfaces:**
- Consumes: `shm = &sn` with fields `splitbuffer, samplebits, speed, channels, samples, submission_chunk, samplepos, buffer` (see `WinQuake/sound.h` and `snd_linux.c:37-140`); `COM_CheckParm`, `Con_Printf`
- Produces: the four `SNDDMA_*` functions the engine links against (names must match exactly):
  - `qboolean SNDDMA_Init(void)`
  - `int SNDDMA_GetDMAPos(void)` — current playback position in samples
  - `void SNDDMA_Submit(void)`
  - `void SNDDMA_Shutdown(void)`

Model: callback-driven SDL3 device. The callback copies from `shm->buffer` (the engine's ring) into the audio stream; `GetDMAPos` reports the callback's consumption position in samples. This mirrors `snd_linux.c`'s mmap model without mmap.

- [ ] **Step 1: Write `WinQuake/snd_sdl.c`**

```c
/*
snd_sdl.c — SDL3 audio driver for GLQuake on macOS arm64.
Replaces snd_linux.c (/dev/dsp + mmap) with an SDL3 callback device.
Format/parm handling mirrors snd_linux.c:37-140.
*/

#include <stdlib.h>
#include <string.h>
#include <SDL3/SDL.h>
#include "quakedef.h"

static SDL_AudioDeviceID audio_device;
static int snd_inited;
static int read_bytes;	/* bytes the callback has consumed, monotonic */

static void SDLCALL snd_callback(void *userdata, SDL_AudioStream *stream,
                                 int additional_amount, int total_amount)
{
	int bufsize = shm->samples * (shm->samplebits / 8);
	int chunk = additional_amount;

	while (chunk > 0) {
		int pos = read_bytes % bufsize;
		int n = bufsize - pos;
		if (n > chunk)
			n = chunk;
		SDL_PutAudioStreamData(stream, shm->buffer + pos, n);
		read_bytes += n;
		chunk -= n;
	}
}

qboolean SNDDMA_Init(void)
{
	SDL_AudioSpec spec;
	int i;
	char *s;

	snd_inited = 0;

	shm = &sn;
	shm->splitbuffer = 0;

	s = getenv("QUAKE_SOUND_SAMPLEBITS");
	if (s) shm->samplebits = atoi(s);
	else if ((i = COM_CheckParm("-sndbits")) != 0)
		shm->samplebits = atoi(com_argv[i+1]);
	if (shm->samplebits != 16 && shm->samplebits != 8)
		shm->samplebits = 16;

	s = getenv("QUAKE_SOUND_SPEED");
	if (s) shm->speed = atoi(s);
	else if ((i = COM_CheckParm("-sndspeed")) != 0)
		shm->speed = atoi(com_argv[i+1]);
	else
		shm->speed = 44100;

	s = getenv("QUAKE_SOUND_CHANNELS");
	if (s) shm->channels = atoi(s);
	else if ((i = COM_CheckParm("-sndmono")) != 0)
		shm->channels = 1;
	else if ((i = COM_CheckParm("-sndstereo")) != 0)
		shm->channels = 2;
	else shm->channels = 2;

	shm->samples = shm->speed * shm->channels;	/* 1 second ring */
	shm->submission_chunk = 1;
	shm->buffer = (unsigned char *) malloc(shm->samples * (shm->samplebits / 8));
	if (!shm->buffer) {
		Con_Printf("Could not allocate sound ring\n");
		return 0;
	}
	memset(shm->buffer, 0, shm->samples * (shm->samplebits / 8));

	memset(&spec, 0, sizeof(spec));
	spec.freq = shm->speed;
	spec.channels = shm->channels;
	spec.format = (shm->samplebits == 16) ? SDL_AUDIO_S16LE : SDL_AUDIO_U8;
	spec.callback = snd_callback;

	audio_device = SDL_OpenAudioDevice(SDL_AUDIO_DEVICE_DEFAULT_PLAYBACK, &spec);
	if (!audio_device) {
		Con_Printf("Could not open SDL audio device: %s\n", SDL_GetError());
		free(shm->buffer);
		shm->buffer = NULL;
		return 0;
	}
	SDL_ResumeAudioDevice(audio_device);

	read_bytes = 0;
	shm->samplepos = 0;
	snd_inited = 1;
	return 1;
}

int SNDDMA_GetDMAPos(void)
{
	if (!snd_inited)
		return 0;
	shm->samplepos = (read_bytes / (shm->samplebits / 8)) % shm->samples;
	return shm->samplepos;
}

void SNDDMA_Submit(void)
{
	/* callback-driven: nothing to push */
}

void SNDDMA_Shutdown(void)
{
	if (snd_inited) {
		SDL_CloseAudioDevice(audio_device);
		free(shm->buffer);
		shm->buffer = NULL;
		snd_inited = 0;
	}
}
```

- [ ] **Step 2: Compile**

Run: `make -f Makefile.macosx objects` — note: `snd_sdl.o` is in `PLATFORM_OBJS`, not `CORE_OBJS`, so compile it explicitly first:
Run: `make -f Makefile.macosx CFLAGS="$(shell pkg-config sdl3 --cflags) -DGLQUAKE -Dstricmp=strcasecmp -I. -Imacosx-shim" build-macosx/snd_sdl.o` — or simply `cc -c $(pkg-config sdl3 --cflags) -DGLQUAKE -Dstricmp=strcasecmp -I. -Imacosx-shim snd_sdl.c -o build-macosx/snd_sdl.o`
Expected: exit 0, `build-macosx/snd_sdl.o` exists, no warnings about missing prototypes for the four `SNDDMA_*` names.

- [ ] **Step 3: Commit**

```bash
git add WinQuake/snd_sdl.c
git commit -m "Add SDL3 sound driver (snd_sdl.c) for the macOS arm64 port"
```

---

### Task 4: SDL3 video/GL/input driver (`gl_vidsdl.c`)

**Files:**
- Create: `WinQuake/gl_vidsdl.c`
- Test: object compiles (Task 5 links it and proves the symbol inventory complete)

**Interfaces:**
- Consumes: engine symbols used by `gl_vidlinuxglx.c` (read that file first — it is the reference implementation for this task): `Key_Event`, `Sys_Quit`, `Cvar_RegisterVariable`, `GL_Init`, `vid` struct (`quakedef.h`), `K_*` key codes (`keys.h`)
- Produces: the symbol inventory `gl_vidlinuxglx.c` exports. Minimum contract:
  - `void VID_Init(unsigned char *palette)`
  - `void VID_Shutdown(void)`
  - `void VID_SetPalette(unsigned char *palette)`
  - `void VID_Init8bitPalette(void)`
  - `void Sys_SendKeyEvents(void)`
  - `void GL_BeginRendering(int *x, int *y, int *width, int *height)`
  - `void GL_EndRendering(void)`
  - plus any cvars/globals the link step reports missing (mirror them from `gl_vidlinuxglx.c`)

Reference map inside `WinQuake/gl_vidlinuxglx.c` (copy structure, swap X11→SDL3):
- `XLateKey` (keysym → `K_*`) at line 119 → write `XLateSDLKey(SDL_Keycode)` with the same coverage
- mouse grab/accumulate at lines 277-330, event pump + `Key_Event(K_MOUSE1 + b, ...)` + `mx/my` accumulation at lines 340-430
- `Sys_SendKeyEvents` at line 910 (including how `mx/my` get published to the engine — copy that publishing mechanism exactly)
- `VID_Shutdown`:441, `VID_SetPalette`:487, `VID_Init8bitPalette`:641, `VID_Init`:721

- [ ] **Step 1: Write `WinQuake/gl_vidsdl.c`**

Skeleton (complete the bodies by translating the reference file sections above):

```c
/*
gl_vidsdl.c — SDL3 video/GL/input driver for GLQuake on macOS arm64.
Replaces gl_vidlinuxglx.c (X11/GLX). Structure mirrors that file section
by section; only the windowing calls change.
*/

#include <SDL3/SDL.h>
#include "quakedef.h"

static SDL_Window *sdl_window;
static SDL_GLContext sdl_glctx;
static qboolean mouse_active;
static int mx, my;

/* mirror the cvar block gl_vidlinuxglx.c registers in VID_Init */

void VID_Init(unsigned char *palette)
{
	int width = 1024, height = 768;

	/* Cvar_RegisterVariable(...) for each cvar the reference registers */

	SDL_InitSubSystem(SDL_INIT_VIDEO);
	SDL_GL_SetAttribute(SDL_GL_DOUBLEBUFFER, 1);
	SDL_GL_SetAttribute(SDL_GL_CONTEXT_PROFILE_MASK,
	                    SDL_GL_CONTEXT_PROFILE_COMPATIBILITY);

	sdl_window = SDL_CreateWindow("GLQuake", width, height, SDL_WINDOW_OPENGL);
	if (!sdl_window)
		Sys_Error("SDL_CreateWindow failed: %s", SDL_GetError());
	sdl_glctx = SDL_GL_CreateContext(sdl_window);
	if (!sdl_glctx)
		Sys_Error("SDL_GL_CreateContext failed: %s", SDL_GetError());
	SDL_GL_MakeCurrent(sdl_window, sdl_glctx);
	SDL_SetWindowRelativeMouseMode(sdl_window, true);
	mouse_active = true;

	/* copy the vid-struct field assignments from gl_vidlinuxglx.c VID_Init
	   (vid.width/height/aspect/numpages etc.), then GL_Init(), then: */
	VID_SetPalette(palette);
	VID_Init8bitPalette();
}

static int XLateSDLKey(SDL_Keycode key)
{
	/* same coverage as XLateKey at gl_vidlinuxglx.c:119 — letters, digits,
	   F-keys, arrows, space/ctrl/shift/alt/escape/tab/tilde, etc. */
	switch (key) {
	case SDLK_ESCAPE:	return K_ESCAPE;
	case SDLK_RETURN:	return K_ENTER;
	case SDLK_TAB:		return K_TAB;
	case SDLK_SPACE:	return K_SPACE;
	case SDLK_BACKSPACE:	return K_BACKSPACE;
	/* ... complete from the reference table ... */
	default:
		if (key >= SDLK_a && key <= SDLK_z)
			return key - SDLK_a + 'a';
		if (key >= SDLK_0 && key <= SDLK_9)
			return key;
		return key < 256 ? (int)key : 0;
	}
}

void Sys_SendKeyEvents(void)
{
	SDL_Event ev;

	while (SDL_PollEvent(&ev)) {
		switch (ev.type) {
		case SDL_EVENT_KEY_DOWN:
		case SDL_EVENT_KEY_UP:
			Key_Event(XLateSDLKey(ev.key.key), ev.key.down);
			break;
		case SDL_EVENT_MOUSE_BUTTON_DOWN:
		case SDL_EVENT_MOUSE_BUTTON_UP:
			Key_Event(K_MOUSE1 + ev.button.button - SDL_BUTTON_LEFT,
			          ev.button.down);
			break;
		case SDL_EVENT_MOUSE_MOTION:
			if (mouse_active) {
				mx += (int)ev.motion.xrel;
				my += (int)ev.motion.yrel;
			}
			break;
		case SDL_EVENT_QUIT:
			Sys_Quit();
			break;
		}
	}

	/* publish mouse deltas exactly the way Sys_SendKeyEvents does in
	   gl_vidlinuxglx.c:910 (assignment to the engine's mouse_x/mouse_y
	   or equivalent), then: */
	mx = my = 0;
}

void VID_Shutdown(void)
{
	if (sdl_glctx) { SDL_GL_DestroyContext(sdl_glctx); sdl_glctx = NULL; }
	if (sdl_window) { SDL_DestroyWindow(sdl_window); sdl_window = NULL; }
	SDL_QuitSubSystem(SDL_INIT_VIDEO);
}

/* VID_SetPalette / VID_Init8bitPalette: translate the bodies from
   gl_vidlinuxglx.c:487 / :641 — they manipulate GL textures/tables and
   contain no X11 calls beyond what is already abstracted. */

void GL_BeginRendering(int *x, int *y, int *width, int *height)
{
	/* gl_screen.c:73 owns glx/gly/glwidth/glheight and passes their
	   addresses here (gl_screen.c:849) — do not define them in this file */
	*x = 0;
	*y = 0;
	SDL_GetWindowSize(sdl_window, width, height);
}

void GL_EndRendering(void)
{
	SDL_GL_SwapWindow(sdl_window);
}
```

- [ ] **Step 2: Compile**

Run: `cc -c $(pkg-config sdl3 --cflags) -DGLQUAKE -Dstricmp=strcasecmp -I. -Imacosx-shim gl_vidsdl.c -o build-macosx/gl_vidsdl.o` (cwd `WinQuake/`)
Expected: exit 0. Fix any compile errors by consulting the reference file.

- [ ] **Step 3: Commit**

```bash
git add WinQuake/gl_vidsdl.c
git commit -m "Add SDL3 video/GL/input driver (gl_vidsdl.c) for the macOS arm64 port"
```

---

### Task 5: Link, launch, Phase-1 acceptance gate

**Files:**
- Modify: `WinQuake/Makefile.macosx` only if a decision rule below triggers
- Test: Phase-1 smoke checklist (Spec §9)

**Interfaces:**
- Consumes: Tasks 1-4 artifacts; user's `game/id1/pak0.pak` (+ `pak1.pak`)
- Produces: working arm64 `glquake` binary; verified gameplay; finalized Fixes Ledger

- [ ] **Step 1: Full build**

Run: `make -f Makefile.macosx clean && make -f Makefile.macosx build-release`
Expected: link errors at first. Apply decision rules, one class at a time, re-link after each:

| Link symptom | Action |
|---|---|
| `Undefined symbols ... SV_HullPointContents` or other world/math symbols from the omitted asm | Add `$(BUILDDIR)/nonintel.o` to `CORE_OBJS` and a pattern-rule dependency exists already |
| `duplicate symbol` for globals (classic `common.c` tentative definitions) | Add `-fcommon` to `BASE_CFLAGS` |
| `Undefined symbols _SNDDMA_*` or `_VID_*` / `_GL_BeginRendering` etc. | Name mismatch in `snd_sdl.c`/`gl_vidsdl.c` — fix the new files, never the engine |
| Any undefined symbol resolved by an existing repo `.c` file (check `Makefile.linuxi386` object lists first) | Add that object to `CORE_OBJS` |
| Anything else | Escalate to user with the exact linker output |

- [ ] **Step 2: Verify architecture**

Run: `file build-macosx/glquake`
Expected: contains `arm64`.

- [ ] **Step 3: Launch without data (binary-executes proof)**

Run: `./build-macosx/glquake -basedir ../game` (with `game/id1/` empty or missing)
Expected: controlled exit with a `Sys_Error` mentioning pak loading (e.g. `W_LoadWadFile` / `COM_Init` failure) — NOT a crash, segfault, or hang. This proves `main()` → `Host_Init` executes.

- [ ] **Step 4: Data gate**

User copies `pak0.pak` (+ `pak1.pak`) into `game/id1/`.
Run: `make -f Makefile.macosx check-data`
Expected: `Game data OK`.

- [ ] **Step 5: Phase-1 smoke checklist**

Run: `make -f Makefile.macosx run` and verify each item, in order:

1. SDL3 window opens (~1024×768); main menu renders.
2. New Game → episode 1 loads; world renders (textures, light, no black screen).
3. Movement, jumping; weapon fires with sound.
4. Enemies animate; taking damage updates the HUD.
5. Save → quit → load restores position.
6. Quit exits cleanly (no hang/crash).

If an item fails: capture console output (`glquake` prints to the terminal), diagnose in the new SDL3 files first, engine files only with a ledger entry.

- [ ] **Step 6: Finalize ledger and commit**

Append all Task-5 fixes to the Fixes Ledger, then:

```bash
git add -u WinQuake docs/superpowers/plans/2026-08-29-quake-apple-silicon.md
git commit -m "Link and verify GLQuake on macOS arm64 (Phase 1 complete)"
```

---

### Task 6: QuakeWorld server (`qwsv`)

**Files:**
- Create: `QW/Makefile.macosx`
- Modify: `QW/server/*.c` or `QW/client/*.c` only per Task-2 recipes
- Test: server boots and listens (Spec §9 Phase 2 item 1)

**Interfaces:**
- Consumes: Task-2 fix recipes; user's paks; in-repo `qw-qc/qwprogs.dat`
- Produces: `QW/build-macosx/qwsv`; data layout `game/qw/`; the makefile later extended with client targets in Task 7

Object list cloned from `QW/Makefile.Linux` `QWSV_OBJS` (server files from `QW/server/`, shared files from `QW/client/`), built with `-DSERVERONLY`. No SDL, no OpenGL — headless.

- [ ] **Step 1: Stage QW game data**

Run (repo root):
```bash
mkdir -p game/qw
cp qw-qc/qwprogs.dat game/qw/
cp game/id1/pak0.pak game/id1/pak1.pak game/qw/
```
Expected: `game/qw/` holds `qwprogs.dat`, `pak0.pak`, `pak1.pak`.

- [ ] **Step 2: Write `QW/Makefile.macosx`**

Same reference style as Task 1's makefile. Variables: `CC=cc`, `BUILDDIR=build-macosx`, `CLIENT_DIR=client`, `SERVER_DIR=server`, `GAMEDIR ?= $(CURDIR)/../game`, `BASE_CFLAGS=-Wall -Dstricmp=strcasecmp -I$(CLIENT_DIR) -I$(SERVER_DIR)`, `SERVER_CFLAGS=$(BASE_CFLAGS) -DSERVERONLY`, `RELEASE_CFLAGS=$(BASE_CFLAGS) -O2 -ffast-math`, `DEBUG_CFLAGS=$(BASE_CFLAGS) -g -O0`, `LDFLAGS=-lm`.

Server objects (exact `QWSV_OBJS` from `QW/Makefile.Linux`):
`server/pr_cmds.o server/pr_edict.o server/pr_exec.o server/sv_init.o server/sv_main.o server/sv_nchan.o server/sv_ents.o server/sv_send.o server/sv_move.o server/sv_phys.o server/sv_user.o server/sv_ccmds.o server/world.o server/sys_unix.o server/model.o` — compiled from `$(SERVER_DIR)` with `SERVER_CFLAGS` — and `server/cmd.o server/common.o server/crc.o server/cvar.o server/mathlib.o server/md4.o server/zone.o server/pmove.o server/pmovetst.o server/net_chan.o server/net_udp.o` — compiled from `$(CLIENT_DIR)` with `SERVER_CFLAGS`.

Targets: `help`, `build-server` (`$(BUILDDIR)/qwsv`), `build-client` (added in Task 7), `check-data` (verifies `$(GAMEDIR)/qw/qwprogs.dat` and `$(GAMEDIR)/qw/pak0.pak`), `run-server` (`check-data` + `build-server`, then `$(BUILDDIR)/qwsv -basedir $(GAMEDIR) +gamedir qw`), `clean`.

- [ ] **Step 3: Compile campaign**

Run: `make -f Makefile.macosx build-server` (cwd `QW/`)
Expected: FAIL first; fix with Task-2 recipes only (same table). QW's `client/common.c` differs from WinQuake's — fixes may not carry over; ledger each one.

- [ ] **Step 4: Boot test**

Run: `make -f Makefile.macosx run-server`
Expected: `qwsv` loads `qwprogs.dat`, loads a default map (`map start` via server console), prints listening status on UDP 27500, no crash. Type `status` in its console if interactive, then Ctrl-C; clean exit.

- [ ] **Step 5: Commit**

```bash
git add QW/Makefile.macosx
git add -u QW docs/superpowers/plans/2026-08-29-quake-apple-silicon.md
git commit -m "Build and boot QuakeWorld server (qwsv) on macOS arm64"
```

---

### Task 7: QuakeWorld GL client (`glqwcl`) — Phase-2 gate

**Files:**
- Modify: `QW/Makefile.macosx` (add client targets)
- Create (conditional): `QW/client/gl_vidsdl.c`, `QW/client/snd_sdl.c` — only if Step 2's decision rule triggers
- Test: Phase-2 smoke checklist (Spec §9)

**Interfaces:**
- Consumes: Task-6's makefile and server; Task-4's `gl_vidsdl.c` and Task-3's `snd_sdl.c`
- Produces: `QW/build-macosx/glqwcl`; localhost client↔server gameplay

Client object list: clone `QWCL_OBJS` from `QW/Makefile.Linux`, with the same asm omission as Phase 1, and the GL variant selection used for `glqwcl.glx` — swap `gl_vidlinux*` objects for the SDL3 layer, `snd_linux` → `snd_sdl`, add `-DGLQUAKE`, link `$(shell pkg-config sdl3 --libs) -framework OpenGL -lm`.

- [ ] **Step 1: Add client targets to `QW/Makefile.macosx`**

Add `build-client` producing `$(BUILDDIR)/glqwcl`, and `run-client` (`check-data` + `build-client`, then `$(BUILDDIR)/glqwcl -basedir $(GAMEDIR) +gamedir qw`).

- [ ] **Step 2: SDL layer reuse decision rule**

First try compiling the Phase-1 drivers directly: add `-I../WinQuake` is NOT allowed (different trees); instead compile `../WinQuake/gl_vidsdl.c` by path into the QW build. If it compiles and links against QW headers, reuse it. If QW headers reject it (different `vid` struct, missing symbols, divergent prototypes), copy the files to `QW/client/gl_vidsdl.c` and `QW/client/snd_sdl.c` and adapt them there. Record which branch was taken in the Fixes Ledger.

- [ ] **Step 3: Compile campaign**

Run: `make -f Makefile.macosx build-client`
Expected: FAIL first; Task-2 recipes; ledger all fixes.

- [ ] **Step 4: Phase-2 smoke checklist**

Terminal 1: `make -f Makefile.macosx run-server`
Terminal 2: `make -f Makefile.macosx run-client`, then in the game console: `connect localhost`
Verify, in order:
1. Client connects (server logs the join).
2. Player spawns in the map and moves (server-side position updates).
3. Weapon fire works.
4. Clean disconnect and quit on both ends.

- [ ] **Step 5: Finalize and commit**

```bash
git add -u QW docs/superpowers/plans/2026-08-29-quake-apple-silicon.md
git commit -m "Build and verify QuakeWorld GL client (glqwcl) on macOS arm64 (Phase 2 complete)"
```

---

## Fixes Ledger

Append every source fix as: `file:line — one-line rationale`. Entries from Tasks 2, 5, 6, 7 land here.

| Fix | Rationale |
|---|---|
| chase.c:24 | Added extern prototype for `SV_RecursiveHullCheck` (implicit function declaration). |
| gl_draw.c:26 | Added extern prototype for `GL_LoadPicTexture` (implicit function declaration). |
| gl_draw.c:27 | Added extern prototype for `VID_Is8bit` (implicit function declaration). |
| gl_model.c:27 | Added extern prototype for `GL_SubdivideSurface` (implicit function declaration). |
| gl_model.c:28 | Added extern prototype for `GL_MakeAliasModelDisplayLists` (implicit function declaration). |
| gl_rmain.c:24 | Added extern prototype for `R_LightPoint` (implicit function declaration). |
| gl_rmain.c:25 | Added extern prototype for `R_DrawBrushModel` (implicit function declaration). |
| gl_rmain.c:26 | Added extern prototype for `RotatePointAroundVector` (implicit function declaration). |
| gl_rmain.c:27 | Added extern prototype for `R_AnimateLight` (implicit function declaration). |
| gl_rmain.c:28 | Added extern prototype for `V_CalcBlend` (implicit function declaration). |
| gl_rmain.c:29 | Added extern prototype for `R_DrawWorld` (implicit function declaration). |
| gl_rmain.c:30 | Added extern prototype for `R_RenderDlights` (implicit function declaration). |
| gl_rmain.c:31 | Added extern prototype for `R_DrawParticles` (implicit function declaration). |
| gl_rmain.c:32 | Added extern prototype for `R_DrawWaterSurfaces` (implicit function declaration). |
| gl_rmain.c:33 | Added extern prototype for `R_RenderBrushPoly` (implicit function declaration). |
| gl_rmisc.c:24 | Added extern prototype for `R_InitParticles` (implicit function declaration). |
| gl_rmisc.c:25 | Added extern prototype for `R_ClearParticles` (implicit function declaration). |
| gl_rmisc.c:26 | Added extern prototype for `GL_BuildLightmaps` (implicit function declaration). |
| gl_rmisc.c:27 | Added extern prototype for `GL_Upload8_EXT` (implicit function declaration). |
| gl_rmisc.c:28 | Added extern prototype for `VID_Is8bit` (implicit function declaration). |
| gl_rsurf.c:24 | Added extern prototype for `EmitWaterPolys` (implicit function declaration). |
| gl_rsurf.c:25 | Added extern prototype for `EmitSkyPolys` (implicit function declaration). |
| gl_rsurf.c:26 | Added extern prototype for `EmitBothSkyLayers` (implicit function declaration). |
| gl_rsurf.c:27 | Added extern prototype for `R_DrawSkyChain` (implicit function declaration). |
| gl_rsurf.c:28 | Added extern prototype for `R_CullBox` (implicit function declaration). |
| gl_rsurf.c:29 | Added extern prototype for `R_MarkLights` (implicit function declaration). |
| gl_rsurf.c:30 | Added extern prototype for `R_RotateForEntity` (implicit function declaration). |
| gl_rsurf.c:31 | Added extern prototype for `R_StoreEfrags` (implicit function declaration). |
| gl_screen.c:25 | Added extern prototype for `GL_Set2D` (implicit function declaration). |
| net_udp.c:27 | Added `#include <arpa/inet.h>` for `inet_addr` (implicit function declaration). |
| QW/server/sv_user.c:39 | Added extern prototype for `SV_FullClientUpdateToClient` (implicit function declaration). |
| QW/server/sys_unix.c:27 | Added `__APPLE__` to the POSIX-header guard so macOS uses `sys/stat.h`/`unistd.h`/`sys/time.h`/`errno.h` instead of the nonexistent `sys/dir.h`. |
| QW/client/net_chan.c:26 | Added `#include <unistd.h>` (non-Windows branch) for `getpid`/`getuid` in `Netchan_Init` (implicit function declaration). |
| **Task 7 — build decision (Step 2)** | **Adapted-copy branch.** Compiling `../WinQuake/gl_vidsdl.c`/`snd_sdl.c` by path would resolve their quoted `#include "quakedef.h"` against `WinQuake/` headers (quoted includes search the source file's directory first), pulling the WinQuake header set into QW translation units. Copied both drivers to `QW/client/` instead; isolation preferred over a compat shim. Cross-tree layouts were verified compatible first (`viddef_t` identical, `cvar_t` same layout, `Key_Event` same signature, QW `usercmd_t` has the same `forwardmove/sidemove/upmove` fields the driver writes), so adaptation stayed near-verbatim. |
| QW/client/gl_vidsdl.c (new) | Adapted copy of Task-4's SDL3 video/GL/input driver for the QW client; replaces `gl_vidlinuxglx.o` in the `glqwcl.glx` variant. Additions over the Phase-1 driver, all mirroring `gl_vidlinuxglx.c`: `_windowed_mouse` cvar (menu.c links it) and empty `VID_LockBuffer`/`VID_UnlockBuffer` stubs (menu.c `M_Draw` calls them). |
| QW/client/snd_sdl.c (new) | Adapted copy of Task-3's SDL3 audio driver for the QW client, verbatim body; replaces `snd_linux.o`. |
| QW/client/gl_vidsdl.c:53 | Defined `_windowed_mouse` cvar and registered it in `VID_Init` (referenced by menu.c `M_AdjustSliders`/`M_Options_Draw`; `gl_vidlinuxglx.c` owns it in the Linux build). |
| QW/client/gl_vidsdl.c:105 | Added empty `VID_LockBuffer`/`VID_UnlockBuffer` stubs referenced by menu.c `M_Draw` (`gl_vidlinuxglx.c:787-788` provides them in the Linux build). |
| QW/client/cd_null.c:15 | Added null `CDAudio_Pause` stub referenced by cl_parse.c:1372 (present in WinQuake/cd_null.c, missing here; `cd_linux.c` is Linux-only — `<linux/cdrom.h>` — so `cd_null.c` replaces it as in Phase 1). |
| WinQuake/macosx-shim/GL/gl.h:9 | Defined `APIENTRY` empty off Windows: Apple's `<OpenGL/gl.h>` lacks it, `QW/client/glquake.h` uses it in function-pointer typedefs, and Mesa supplied it in the original Linux build. Inert for Phase 1 (WinQuake's APIENTRY uses are `#ifdef _WIN32`). |
| QW/client/glquake.h:243 | Made the SGIS-multitexture typedefs/externs unconditional (were `#ifdef _WIN32` only), matching WinQuake/glquake.h: gl_draw.c's `GL_SelectTexture` callsite is compiled off Linux too (`#ifndef __linux__`). Declarations only; `gl_mtexable` stays false on Apple GL. |
| QW/client/gl_rsurf.c:280 | Made `qglMTexCoord2fSGIS`/`qglSelectTextureSGIS` definitions unconditional (were `#ifdef _WIN32`), matching WinQuake/gl_rsurf.c, so the gl_draw.c callsite links. |
| QW/client/gl_draw.c:26 | Added `#define GL_COLOR_INDEX8_EXT 0x80E5` (token absent from Apple's `<OpenGL/gl.h>`; same fix as WinQuake/gl_draw.c:29). |
| QW/client/cl_main.c:29 | Added `#include <ctype.h>` for `isspace` (implicit function declaration). |
| QW/client/gl_screen.c:468 | `static lastfps;` → `static int lastfps;` (implicit int is a hard error in modern clang). |
| QW/client/menu.c:1020 | `M_SinglePlayer_Key (key)` → `M_SinglePlayer_Key (int key)` (K&R parameter with no declaration; matches the file's own ANSI forward declaration at line 67). |
| QW/client/menu.c:1048 | `M_MultiPlayer_Key (key)` → `M_MultiPlayer_Key (int key)` (same as above; forward declaration at line 70). |
| WinQuake/sys_linux.c:89 | `Sys_Printf`: `text[1024]`+`vsprintf` → `text[4096]`+`vsnprintf` (matches upstream `MAXPRINTMSG`=4096); removed the post-hoc `strlen` guard, unreachable after truncation. Found at gameplay acceptance: Apple Metal GL reports a ~2.6KB `GL_EXTENSIONS` string, and macOS `-O2` fortifies `vsprintf` into `__vsprintf_chk`, which traps on overflow (SIGTRAP; engine signal handler then exited cleanly) — startup died right after the `GL_VERSION` print. This is the bug id's own `console.c:376` FIXME ("make a buffer size safe vsprintf?") points at. |
| QW/client/sys_linux.c:107 | Same `Sys_Printf` overflow defect and fix (2048→4096, `vsprintf`→`vsnprintf`) in the QW client; its `GL_Init` prints the same extension string. |
| WinQuake/snd_sdl.c:49 | `SNDDMA_Init` now calls `SDL_InitSubSystem(SDL_INIT_AUDIO)` first and `SNDDMA_Shutdown` calls the symmetric `SDL_QuitSubSystem`: SDL3 refuses to open an audio device before its subsystem is initialized ("Audio subsystem is not initialized"), and the video driver only initializes `SDL_INIT_VIDEO`. Found at gameplay acceptance; sound init previously failed silently and the game ran mute. |
| QW/client/snd_sdl.c:55 | Same SDL audio-subsystem init/quit fix in the adapted copy. |
| WinQuake/gl_vidsdl.c:173, QW/client/gl_vidsdl.c:198 (behavior change, user-requested) | Mouse never locks: removed the two `SDL_SetWindowRelativeMouseMode(sdl_window, true)` call sites (`install_grabs` and `VID_Init`) in both drivers. Pointer capture is never engaged, so the cursor stays free/visible; `uninstall_grabs` still forces relative mode off. Mouse-look accumulation stays wired (gameplay-only), `in_mouse 0` disables it entirely. |
| WinQuake/sv_main.c:1160 | **64-bit string-offset truncation — root cause of the New Game SIGSEGV.** `ent->v.model = sv.worldmodel->name - pr_strings` subtracts the string-heap base from a `mod_known[]` static-array pointer ~13 GB away; the 64-bit difference overflows the 32-bit `string_t`, so `worldspawn` reading `self.model` did `strcmp(pr_strings + garbage_offset)` on unmapped memory. Fixed by copying the name into the heap: `ED_NewString(sv.worldmodel->name) - pr_strings`. Harmless on 32-bit only because the pointer difference fit a signed `int` and `pr_strings + (name - pr_strings)` round-tripped to `name`. Diagnosed via a hardware watchpoint on the corrupted global. |
| WinQuake/sv_main.c:1170 | Same truncation bug for `pr_global_struct->mapname = sv.name - pr_strings` (`sv.name` is a global `char[]`, not in the heap). → `ED_NewString(sv.name) - pr_strings`. |
| WinQuake/pr_cmds.c:934,947,954 | Same truncation bug in `PF_ftos`/`PF_vtos`/`PF_etos`, which returned `pr_string_temp - pr_strings` (`pr_string_temp` is a static `char[128]`). → `ED_NewString(pr_string_temp) - pr_strings`. (`PF_etos` is `#ifdef QUAKE2`, fixed for parity.) |
| WinQuake/host_cmd.c:939,1311 | Same truncation bug for client `netname` (`host_client->name - pr_strings`, a global array). → `ED_NewString(host_client->name) - pr_strings`. Multiplayer path. QW is unaffected: `QW/server/pr_exec.c` already has a `PR_SetString`/`pr_strtbl` mechanism for out-of-heap strings. |

### Removed dead platform drivers and null drivers
Commit: 3b80972. Deleted Windows/DOS/Linux/Sun drivers, their x86 asm, and
unused null drivers from WinQuake/ and QW/; removed winquake.h/resource.h and
their include lines from live files. cd_null.c (live) kept.

### Removed the software renderer
Commit: fb2079e. Deleted d_* files, non-GL r_* files, renderer asm, and
renderer-only headers from WinQuake/ and QW/. Kept r_part.c, nonintel.c (QW),
anorms.h, anorm_dots.h, d_iface.h, r_local.h, r_shared.h (live include closure).

### Removed IDE/packaging/binary junk and side trees
Commit: 56bda6e. Deleted IDE projects, .bat/.spec.sh files, icons/images,
kit/ (with GLQUAKE.EXE/OPENGL32.DLL binaries), data/, docs/, dxsdk/, scitech/,
gas2masm/, makezip*, qwfwd/, and the duplicate qw-qc/ tree. QW/progs/ kept.

### Pruned platform conditionals from live files
Commit: 9da59a0. Removed _WIN32/_WINDOWS/id386/DOS regions from 36 live
files per the rules in the 2026-08-30 cleanup spec; id386 pinned to 0.

### Renamed sys_linux.c to sys_unix.c
Commit: 1b532eb. WinQuake/ and QW/client/ now match QW/server naming.

### Fixed stale qw-qc path and model.c note in QW/Makefile.macosx
Commit: 0425c63. check-data now points at QW/progs/qwprogs.dat (the
qw-qc/ tree was deleted in 56bda6e); reworded the pattern-rule NOTE for
the client/model.c removal.

### Renamed Makefile.macosx to Makefile; bare make prints help
Commit: 8c5ed66. Both trees now build with plain `make` (targets
unchanged); `.DEFAULT_GOAL := help` makes bare `make` print the target
list instead of building. QW NOTE updated: sys_unix.c now exists in both
source dirs, so the server-first pattern-rule ordering is load-bearing
for sys_unix.o. Gate commands are now `cd WinQuake && make clean &&
make build-release` and `cd QW && make clean && make build-server
build-client`.

### Simplify pass: removed cleanup residue from live files
Commit: f56abc8. /simplify review (reuse/efficiency both clean) removed
what the prune left behind: orphan Texture Object Extension block
(QW/client/glquake.h), write-only text buffer (cl_pred.c), dead
`#if WINDED` region (common.c), bare braced blocks and redundant forward
decls (snd_dma/snd_mix both trees), stale Win32/d_ifacea.h comments,
double blanks and a dangling paren. Deliberately not touched: id386
pinned-0 chain (spec-sanctioned), nonintel.c/r_local.h (kept per plan),
NeXT/__sun__ guards in untouched files, and user-visible "Linux"
strings (parked as S10).

### Renamed trees to Quake/ + QuakeWorld/; Makefile consolidated at root
Commit: 11c7dc3. git mv WinQuake → Quake, QW → QuakeWorld; a single root
Makefile replaces the two per-tree Makefiles (object lists carried over
verbatim — 53 glquake + 26 qwsv + 46 glqwcl objects; per-tree
build-macosx/ output unchanged). Bare `make` still prints help. The QW
check-data target lives on as check-data-qw (check-data now gates only
the single-player `run` target). Gate commands are now from the repo
root: `make clean && make build-release build-server build-client`.
Tree-name references in port-added comments updated; engine-internal
"qw" gamedir strings and 1996 in-game text untouched.

### Simplify pass on the consolidated root Makefile
Commit: 0fe4b59. /simplify four-agent review of 11c7dc3. Applied:
pkg-config spawns drop from ~101 to 2 per clean build (SDL_CFLAGS/
SDL_LIBS now :=); optimization tiers factored to OPT_CFLAGS/DBG_CFLAGS;
data gates renamed per game — check-data-quake and check-data-qw, with
check-data as the verify-both umbrella (`run` still gates on
check-data-quake only, so single-player-only data layouts keep working);
README documents `-j` (verified safe under make 3.81: identical outputs,
~7x faster) and the new gate names; reworded the cd_null.c CDAudio_Pause
comment, which read self-contradictorily after the path swap. Deliberately
not applied: merging QUAKE_LDFLAGS/QW_CLIENT_LDFLAGS (the two binaries'
link lines may legitimately diverge; drift fails loudly at link), adding
build-client-debug (absent in the old QW/Makefile; carryover discipline),
hoisting macosx-shim/ out of Quake/ (pre-existing, documented coupling),
collapsing the three mkdir rules, and documenting the retained `objects`
dev gate in README (make help surfaces it). Object lists unchanged; gates:
serial + parallel (-j) clean builds, debug-flag expansion check, 3-binary
smoke 0/0/0.

### Architecture pass: input module split, clock unification, dead-code deletions
Commit: 0bebb45. Architecture
review surfaced six deepening candidates; the three zero-risk ones were
applied, the rest parked for a decision loop (below).

Card 1 — input module split out of the SDL3 video driver. New
Quake/in_sdl.c and QuakeWorld/client/in_sdl.c: verbatim moves of
XLateSDLKey, install/uninstall_grabs, HandleEvents,
IN_ActivateMouse/IN_DeactivateMouse, Sys_SendKeyEvents, IN_Init,
IN_Shutdown, IN_Commands, IN_MouseMove, IN_Move plus the mouse statics
and the in_mouse/in_dgamouse/m_filter cvars (+ _windowed_mouse in QW).
Cvar registration moved from VID_Init into IN_Init — safe on every init
path (host.c calls IN_Init before VID_Init; both arms of cl_main.c's
__linux__ fork call IN_Init before quake.rc exec). VID_Init's trailing
mouse_avail/mouse_active assignments moved into IN_Init with the same
comment; nothing reads the state between the two init calls. gl_vidsdl.c
keeps window/GL only; sdl_window is non-static, extern'd by in_sdl.c;
IN_DeactivateMouse declared in input.h (VID_Shutdown calls it).
Force_CenterView_f deleted — defined in both trees but never registered
as a console command. VID_LockBuffer seam aligned: Quake's empty
quakedef.h macros removed, vid.h declarations + gl_vidsdl.c stubs added
(the form QuakeWorld already used). gl_vidsdl.c shrank 731→456 lines
(Quake), 758→468 (QW). Makefile: in_sdl.o added to
QUAKE_PLATFORM_OBJS and QW_CLIENT_OBJS.

Card 3 core — clock interface unified: Sys_FloatTime renamed
Sys_DoubleTime throughout Quake/ (26 call sites, 9 files incl. sys.h
and sys_unix.c); all three binaries now speak one clock name. The
deeper half of card 3 (merging the three sys_unix.c copies, one
documented init-order contract) is parked — it changes boot paths of
all three binaries and needs a decision on ordering semantics.

Card 5 — deletion-test cleanup. Deleted: Quake/gl_test.c (100% inside
#ifdef GLTEST, GLTEST never defined) + its five dormant call sites
(cl_tent.c TE_SPIKE keeps the #else particle branch; Test_Init/Test_Draw
blocks in gl_rmisc.c/gl_rmain.c both trees) + the commented GLTEST
define + gl_test.o from the Makefile. QuakeWorld/client/nonintel.c
(R_Surf8Patch/R_Surf16Patch/R_SurfacePatch — zero call sites) +
nonintel.o from the Makefile + the decls in both r_local.h. Orphan
declarations removed: VID_SetMode/VID_HandlePause (vid.h both trees),
d_8to16table (definition + extern, zero consumers), sintable/intsintable
externs (r_shared.h both trees), WINQUAKE_VERSION/D3DQUAKE_VERSION/
X11_VERSION macros (quakedef.h), IN_ClearStates (Quake/input.h),
IN_ModeChanged (QW input.h). Kept: LINUX_VERSION and GLQUAKE_VERSION —
still referenced by the Linux branding strings parked as S10.

Parked for the decision loop: card 2 (one filesystem module — two
~750-line COM_* implementations on different I/O substrates; substrate,
CRC policy and caching semantics need choosing), card 4 (split model
parsing from GL mesh building), card 6 (god-header reduction,
derivative of 2/4), and card 3's remainder.

Verification: `make clean && make build-release build-server
build-client` exit 0 from the repo root; 3-binary smoke (SIGKILL
protocol) with "Received signal" counts 0/0/0; glqwcl brought video up
at 1024x768.

### Architecture review: cards 2, 3-remainder, 4, 6 closed without code
No code changes; recorded so future reviews do not re-suggest these.
The four remaining candidates went through the decision loop and were
closed by user decision on 2026-08-30:

Card 2 (one filesystem module) — closed as two adapters of one
historical interface, deliberately not merged. Full convergence
contradicts the plan's isolation rule (Task 7, Step 2: no cross-tree
path compiles), the module has been frozen since 1996, and smoke-only
verification cannot catch pak/cache regressions. Reopen only on a real
filesystem bug.

Card 3 remainder (merge sys_unix.c ×3, one init-order contract) —
closed: the three copies implement three genuinely different runtimes
(GL client with SDL signals vs headless stdin-console server), and the
shared interface win — one Sys_DoubleTime clock across all binaries —
is already banked. Changing boot ordering across binaries is semantics
risk with no payoff.

Card 4 (split model parsing from GL mesh building) — closed for now.
The feasible shape would be QuakeWorld-only (GL-free parser behind the
existing -DSERVERONLY seam; delete the server's brush-only model.c
re-copy), but it cuts renderer code on the live playtest path for a
parser frozen since 1996. Reopen if a model-loading bug ever needs
fixing in more than one place.

Card 6 (god-header reduction) — closed as a standing principle, not a
project: no standalone fix exists; every future deepening should peel
one module off quakedef.h's 30-header chain with its own narrow
header.


## Module folder restructure: one subfolder per module

Commits: 5edf581^..a0940c6 (seven module commits, platform → sound →
net → render → client → server → common). Spec:
docs/superpowers/specs/2026-08-30-module-folder-restructure-design.md.
Plan: docs/superpowers/plans/2026-08-30-module-folder-restructure.md.

Quake/ and QuakeWorld/client/ restructured from flat directories into
module subfolders: common/, client/, render/, server/ (Quake only),
net/, sound/, platform/. Headers moved with their modules; headers
shared by two or more modules live in common/. Quake/host.c and
host_cmd.c remain at the Quake/ tree root (Host orchestrates every
module); QuakeWorld/server/ stays flat (already one cohesive module).

Structural only: every move is git mv, zero file-content changes, zero
#include changes — the Makefile gained one -I flag per module dir, one
pattern rule per module dir, mirrored object paths
(build-macosx/<module>/<file>.o). The QW "server pattern rule must stay
first" NOTE was deleted: mirrored paths make every object name unique,
and the server's shared-source rule now points at client/common/ and
client/net/. The card-6 closure stands — quakedef.h remains the hub;
the folders give future per-module deepenings somewhere to land.

Deviation from plan/spec, found in Task 1: make 3.81 does not prefer
the shortest-stem pattern rule; it picks the first matching rule whose
prerequisites exist, so each binary's module pattern rules must precede
the broader catch-all rule (a catch-all listed first wins and skips the
per-module mkdir prereq — Task 1's first build failed exactly this way
before the rules were reordered). The Makefile carries a comment to
this effect; spec and plan were corrected in the docs commit.

Gate: every module commit passed make clean && make build-release
build-server build-client plus the 3-binary SIGKILL smoke protocol
("Received signal" count 0 for glquake, qwsv, glqwcl).

### External texture overrides (feature)
Commits: ca8a30f, b25384c. Spec:
docs/superpowers/specs/2026-08-30-external-texture-overrides-design.md.
New GL_TryLoadExternalTexture in both GL clients' gl_draw.c:
uncompressed bottom-up 24/32-bit TGA through the COM filesystem,
soft-failing on any malformed content; cvar gl_externaltextures
(default 1). Seams: brush (Mod_LoadTextures), alias skins and
sprite frames (gl_model.c), pics (Draw_PicFromWad/Draw_CachePic/
charset). Exclusions: sky*, progs/player.mdl skins, gfx/menuplyr.
Tools: tools/extract.py (pak/BSP/WAD2/MDL/SPR -> PNG + manifest),
tools/install.py (PNG -> validated TGA under game/id1/).

### Mouse-only control (feature)
Commits: 4740530, b3c1631, 07210cf, 55f0e16, 023ee8f, 71e2c34,
fe6990e, c6e479b, 9d599f0, 9d6c365, 2d06697, c065e12, aa2fa25,
112b069. Spec:
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
Validation: pending manual session (kill-switch parity +
mouse-only functional loop) and tester sessions (E1M1 protocol,
plan Task 12 Step 5).

### Mouse-only control v2 — control scheme redesign (fix + redesign)
Commits: e11a02f. Spec revision:
docs/superpowers/specs/2026-08-30-mouse-only-control-design.md,
"Revision 2026-08-31" section.
Root cause of the "walking back not working" report: v1 put the mode
toggle on the wheel click and cruise on the right button; the user
right-clicked to enter Walk mode, which toggled cruise instead (HUD
read "look cruise"), leaving no backward path. New scheme: right
button (access_toggle_button default 202 → 201) toggles Look/Walk;
double-click MOUSE1 jumps (never-on-fire rule lifted, presses
delivered immediately, command "+jump; wait; -jump"); entering Walk
mode snaps pitch to the horizon (gradual ease removed); Walk-mode X
sidesteps instead of turning (access_turnrate deleted, Mouse options
page renumbered MOUSE_ITEMS 21 → 20); cruise control deleted (cvars,
command, HUD tag, logic); boxed HUD mode label is a clickable toggle
fallback; any click during demo playback opens the main menu and the
access HUD is suppressed; config drops wheel and side-button
bindings (no mouse-driven weapon switching). QuakeWorld untouched;
kill switch access_mouseonly unchanged.
Validation: make clean && make build-release build-server
build-client (exit 0); manual session pending.

### Walk mode: face map center + 50% button opacity (feature)
Commits: ac811e8, a2c2610. Spec + plan:
docs/superpowers/2026-09-12-walk-face-map-center.md.
Entering Walk mode now levels pitch to the horizon and swings yaw to
face the loaded map's bounding-box midpoint (file-local
Access_YawToPoint / Access_FaceMapCenter in cl_access.c, null-direction
guarded); the dead-center Walk/Look HUD button dropped to 50% opacity.
QuakeWorld untouched; kill switch access_mouseonly unchanged.
Validation: make clean && make build-release build-server build-client
(exit 0); manual session pending.

### Walk mode v3 — X turns, button caption shows action (fix)
Commits: f3db156. Follow-up runtime fix to the 2026-09-12 work. Two
reports: the HUD button caption read inverted (it showed the current
mode, not what a click would do), and Walk-mode X sidestepped instead
of turning. Walk mode now turns on X (m_yaw, same as Look mode) and
keeps Y = throttle/velocity movement; the HUD button caption shows the
action ("WALK" while looking, "LOOK" while walking). Face-map-center
entry snap and pitch leveling retained. QuakeWorld untouched; kill
switch access_mouseonly unchanged.
Validation: make clean && make build-release build-server build-client
(exit 0); manual session pending.

### QuakeMCP: engine bridge, MCP server, acceptance run (feature)
Commits: 8a60ea6, 65112a7, fc7f44b, 4101e0c, a849a4b, 5e93f24, 93c61ab,
9b8e8eb, f3098e6, 5746b0e, 7a72b19. Spec + plan:
docs/superpowers/2026-09-13-quakemcp-design.md and
2026-09-13-quakemcp-plan.md. A 13-tool MCP server
(QuakeMCP/src/quakemcp) supervises the instrumented glquake
(QuakeMCP/bridge, built with QUAKE_MCP=1) over a token-authenticated
loopback bridge: lifecycle, lease-guarded bounded actions, exact stepped
mode, state/vision observation, guarded console/config, save/map
inventories and receipts for retry safety. Task 10 found and fixed: the
game child inherited the stdio control channel (server exited cleanly
mid-call; now stdin=DEVNULL), no server-side lease heartbeat (design
calls for 500 ms; now a daemon beat, with `hb` validated against the
live lease), world ops inside a frozen stepped session could not finish
sign-on (world ops resume realtime and restore the mode), and the
world-generation poll heuristic missed same-map same-time savegame loads
(now bumped exactly in SV_SpawnServer, hook `MCP_NoteWorldSpawn`).
Acceptance: QuakeMCP/docs/acceptance-results.md; 42 tests pass; both
clean build gates exit 0; SIGKILL smoke 0 "Received signal" for all
three binaries; two autonomous 10-action observe-act-observe loops ran in
~6 s with zero errors. Recorded FAIL: `kill` has no observable effect in
this build (client health unchanged, no console output) so death/respawn
and intermission reachability stay open; water movement and vision
usability are PARTIAL. Engine tree only; QuakeWorld untouched.
Validation: make clean && make build-release build-server build-client
(exit 0); make clean && make build-release QUAKE_MCP=1 (exit 0);
python3 -m pytest QuakeMCP/tests/unit QuakeMCP/tests/contract
QuakeMCP/tests/integration (42 passed).
