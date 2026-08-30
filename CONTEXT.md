# CONTEXT.md — domain language

Named concepts for this repository. Plans, specs, and architecture
reviews should use these names.

- **Trees** — `Quake/` (single-player engine) and `QuakeWorld/`
  (`client/` + `server/` + `progs/`). Same-named files in different
  trees are separate modules that may drift; files are deliberately not
  compiled by path across trees (isolation rule, port plan Task 7
  Step 2).
- **Binaries** — `glquake` (from Quake/), `qwsv` and `glqwcl` (from
  QuakeWorld/). The object lists in the root Makefile are the
  authoritative live set.
- **Platform modules** — the SDL3 layer, per GL client: the **video
  module** (`gl_vidsdl.c` — window/GL lifecycle), the **input module**
  (`in_sdl.c` — IN_* interface plus the SDL event pump), the **sound
  driver** (`snd_sdl.c` — SNDDMA_*), the **null CD adapter**
  (`cd_null.c`), and the **platform bootstrap** (`sys_unix.c` — main,
  clock, the Sys_* family; one copy per binary, deliberately separate).
- **Game data** — `game/id1/` feeds glquake; `game/qw/` feeds
  qwsv/glqwcl; both QuakeWorld binaries mount id1 and qw at startup.
- **Gates** — the build oracle (`make clean && make build-release
  build-server build-client` from the repo root), the 3-binary SIGKILL
  smoke protocol ("Received signal" count must be 0), and the Fixes
  Ledger (append-only record of every pass, in
  `docs/superpowers/plans/2026-08-29-quake-apple-silicon.md`).
