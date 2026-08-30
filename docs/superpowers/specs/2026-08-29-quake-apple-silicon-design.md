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
