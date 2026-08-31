# CONTEXT.md — domain language

Named concepts for this repository. Plans, specs, and architecture
reviews should use these names.

- **Provenance** — a fork of id-software/quake (the 1999 GPL release),
  simplified for the Apple Silicon port; educational purposes only.
- **Trees** — `Quake/` (single-player engine) and `QuakeWorld/`
  (`client/` + `server/` + `progs/`). Same-named files in different
  trees are separate modules that may drift; files are deliberately not
  compiled by path across trees (isolation rule, port plan Task 7
  Step 2).
- **Binaries** — `glquake` (from Quake/), `qwsv` and `glqwcl` (from
  QuakeWorld/). The object lists in the root Makefile are the
  authoritative live set.
- **Modules** — every source lives in one subfolder per module:
  `common/` (core services + the shared header pool: `quakedef.h`,
  `protocol.h`, `model.h`, …), `client/` (CL_* plus menu, keys, console,
  sbar, view), `render/` (gl_*, r_part), `server/` (sv_*, pr_*, world —
  Quake/ only; QuakeWorld's is `QuakeWorld/server/`, still flat),
  `net/`, `sound/`, `platform/`. Headers live with their module; a
  header included by two or more modules lives in `common/`.
  `Quake/host.c` and `host_cmd.c` stay at the tree root — Host
  orchestrates every module.
- **Platform modules** — the SDL3 layer, per GL client, in each tree's
  `platform/` subdir: the **video module** (`gl_vidsdl.c` — window/GL
  lifecycle), the **input module** (`in_sdl.c` — IN_* interface plus
  the SDL event pump), the **sound driver** (`snd_sdl.c` — SNDDMA_*),
  and the **null CD adapter** (`cd_null.c`); the **platform bootstrap**
  (`sys_unix.c` — main, clock, the Sys_* family) sits there too for the
  two GL clients (qwsv's copy stays in the flat `QuakeWorld/server/`),
  deliberately separate.
- **Mouse-only control** — glquake-only accessibility module
  `Quake/client/cl_access.c` (QuakeWorld untouched), kill switch
  `access_mouseonly`. Scheme v2 (2026-08-31): MOUSE1 fires,
  double-click MOUSE1 jumps, MOUSE2 toggles **Look/Walk** modes; Walk
  mode moves on Y, sidesteps on X, and levels the view on entry; the
  boxed HUD LOOK/WALK label is a clickable toggle fallback; during
  demo playback any click opens the main menu. Default config
  `configs/autoexec-mouseonly.cfg` is copied to
  `game/id1/autoexec.cfg` at runtime (game data — never committed).
  Spec: `docs/superpowers/specs/2026-08-30-mouse-only-control-
  design.md` ("Revision 2026-08-31" section).
- **Game data** — `game/id1/` feeds glquake; `game/qw/` feeds
  qwsv/glqwcl; both QuakeWorld binaries mount id1 and qw at startup.
  Loose TGA overrides under `game/id1/` (and `game/qw/` for QW) are game data
  too — same rule; the committed pipeline that produces them lives in
  `tools/` (decoupled, Pillow-only).
- **Gates** — the build oracle (`make clean && make build-release
  build-server build-client` from the repo root), the 3-binary SIGKILL
  smoke protocol ("Received signal" count must be 0), and the Fixes
  Ledger (append-only record of every pass, in
  `docs/superpowers/plans/2026-08-29-quake-apple-silicon.md`).
