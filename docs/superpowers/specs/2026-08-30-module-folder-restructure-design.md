# Module folder restructure: subfolders per module

Date: 2026-08-30
Status: Approved (brainstorming session)
Companion: `docs/superpowers/plans/2026-08-29-quake-apple-silicon.md` (Fixes Ledger), `CONTEXT.md` (domain vocabulary)

## Goal

Give every source file a module home: restructure `Quake/` and
`QuakeWorld/client/` from flat directories into one subfolder per module
(`common`, `client`, `render`, `server`, `net`, `sound`, `platform`), with
headers living next to their module. `QuakeWorld/server/` is already one
cohesive module and stays as-is.

This is a **structural** restructure only. It names the modules on disk so
future deepenings can work on one module at a time; it does not perform the
deepenings. The architecture review's card-6 closure (god-header reduction
is a standing principle, not a project) is honored: `quakedef.h` stays the
hub, and no dependency surgery happens here.

## Approved decisions (2026-08-30)

1. **Depth — structural only.** Files move; `#include` lines, header
   contents, and `quakedef.h` are untouched. The Makefile already passes
   `-I` flags covering each tree root, and will gain one `-I` per module
   dir, so every include keeps resolving from any location.
2. **Scope — both trees.** `Quake/` and `QuakeWorld/client/` get the same
   module layout; `QuakeWorld/server/` stays flat.
3. **Naming — Quake-native.** Folder names match the code's function
   prefixes (`CL_*` → `client/`, `R_*`/`GL_*` → `render/`, …) and the
   platform-module vocabulary already in `CONTEXT.md`.
4. **Headers move with their modules.** A header included by ≥ 2 modules
   lives in `common/`; everything else sits with its owner. Placement is
   organizational — every module dir is on every binary's `-I` path, so no
   placement can break an include.
5. **Makefile mechanics — mirrored build tree.** Object paths mirror source
   paths (one pattern rule per module dir), extending the existing QW
   precedent (`build-macosx/server/`, `build-macosx/client/`). Mirrored
   paths make every object name unique, retiring the "server pattern rule
   must stay first" collision footnote.

## The oracle

The three live builds must stay green at every step:

| Target | Build | Sources |
|---|---|---|
| `Quake/build-macosx/glquake` | GLQuake (single-player client) | `Quake/` |
| `QuakeWorld/build-macosx/qwsv` | QuakeWorld dedicated server | `QuakeWorld/server/` + shared files from `QuakeWorld/client/` |
| `QuakeWorld/build-macosx/glqwcl` | QuakeWorld GL client | `QuakeWorld/client/` |

Gate per commit: `make clean && make build-release build-server build-client`
from the repo root, then the 3-binary SIGKILL smoke protocol ("Received
signal" count must be 0; `glqwcl`'s stray empty `qw/` dir removed after).

## Module map — `Quake/`

53 sources, 45 headers. After the restructure the tree root holds exactly
`host.c`, `host_cmd.c`, the module dirs, `macosx-shim/`, and
`build-macosx/`.

| Module | Sources | Headers |
|---|---|---|
| `common/` | `common.c` `cmd.c` `cvar.c` `crc.c` `mathlib.c` `zone.c` `wad.c` | `quakedef.h` `common.h` `cmd.h` `cvar.h` `crc.h` `mathlib.h` `zone.h` `wad.h` `protocol.h` `model.h` |
| `client/` | `cl_main.c` `cl_demo.c` `cl_input.c` `cl_parse.c` `cl_tent.c` `chase.c` `view.c` `menu.c` `keys.c` `console.c` `sbar.c` | `client.h` `view.h` `menu.h` `keys.h` `console.h` `sbar.h` |
| `render/` | `gl_rmain.c` `gl_rmisc.c` `gl_rsurf.c` `gl_rlight.c` `gl_refrag.c` `gl_mesh.c` `gl_model.c` `gl_warp.c` `gl_draw.c` `gl_screen.c` `r_part.c` | `glquake.h` `gl_model.h` `render.h` `r_local.h` `r_shared.h` `d_iface.h` `screen.h` `draw.h` `anorms.h` `anorm_dots.h` `gl_warp_sin.h` `bspfile.h` `spritegn.h` `modelgen.h` |
| `server/` | `sv_main.c` `sv_phys.c` `sv_move.c` `sv_user.c` `world.c` `pr_cmds.c` `pr_edict.c` `pr_exec.c` | `server.h` `world.h` `progs.h` `progdefs.h` `pr_comp.h` |
| `net/` | `net_main.c` `net_dgrm.c` `net_loop.c` `net_udp.c` `net_bsd.c` `net_vcr.c` | `net.h` `net_dgrm.h` `net_loop.h` `net_udp.h` `net_vcr.h` |
| `sound/` | `snd_dma.c` `snd_mem.c` `snd_mix.c` | `sound.h` |
| `platform/` | `sys_unix.c` `gl_vidsdl.c` `in_sdl.c` `snd_sdl.c` `cd_null.c` | `sys.h` `vid.h` `input.h` `cdaudio.h` |
| (root) | `host.c` `host_cmd.c` | — |

Edge decisions:

- **`host.c` / `host_cmd.c` stay at the root.** `Host_*` orchestrates every
  module (`Host_Init` calls `CL_Init`, `VID_Init`, `S_Init`, `NET_Init`);
  placing it inside any one module would misstate that.
- **`cd_null.c` is `platform/`, not `sound/`.** It is the null CD adapter
  of the SDL3 platform layer (per `CONTEXT.md`); `sound/` keeps the three
  mixer/DMA files.
- **`protocol.h` and `model.h` are `common/`** — both are read by client
  and server code.
- `gl_screen.c` / `gl_draw.c` are `render/` — they implement `screen.h` /
  `draw.h`.

## Module map — `QuakeWorld/client/`

46 sources, 38 headers. Same module names; no `server/` (that module is
`QuakeWorld/server/`) and no root-resident files.

| Module | Sources | Headers |
|---|---|---|
| `common/` | `common.c` `cmd.c` `cvar.c` `crc.c` `mathlib.c` `md4.c` `zone.c` `wad.c` `pmove.c` `pmovetst.c` | `quakedef.h` `bothdefs.h` `common.h` `cmd.h` `cvar.h` `crc.h` `mathlib.h` `zone.h` `wad.h` `pmove.h` `protocol.h` `model.h` |
| `client/` | `cl_main.c` `cl_demo.c` `cl_ents.c` `cl_input.c` `cl_parse.c` `cl_pred.c` `cl_tent.c` `cl_cam.c` `view.c` `menu.c` `keys.c` `console.c` `sbar.c` `skin.c` | `client.h` `view.h` `menu.h` `keys.h` `console.h` `sbar.h` |
| `render/` | `gl_draw.c` `gl_mesh.c` `gl_model.c` `gl_ngraph.c` `gl_refrag.c` `gl_rlight.c` `gl_rmain.c` `gl_rmisc.c` `gl_rsurf.c` `gl_screen.c` `gl_warp.c` `r_part.c` | `glquake.h` `gl_model.h` `render.h` `r_local.h` `r_shared.h` `d_iface.h` `screen.h` `draw.h` `anorms.h` `anorm_dots.h` `gl_warp_sin.h` `bspfile.h` `spritegn.h` `modelgen.h` |
| `net/` | `net_chan.c` `net_udp.c` | `net.h` |
| `sound/` | `snd_dma.c` `snd_mem.c` `snd_mix.c` | `sound.h` |
| `platform/` | `sys_unix.c` `gl_vidsdl.c` `in_sdl.c` `snd_sdl.c` `cd_null.c` | `sys.h` `vid.h` `input.h` `cdaudio.h` |

The 11 dual-use sources that `qwsv` compiles from `client/`
(`cmd common crc cvar mathlib md4 zone pmove pmovetst` in `common/`,
`net_chan net_udp` in `net/`) keep that role — the server's second pattern
rule follows them into those subdirs (below). `QuakeWorld/server/` itself
(15 sources, 7 headers) is unchanged.

## Makefile changes

**Include paths.** glquake gains seven `-I` flags —
`-I$(QUAKE_DIR)/{common,client,render,server,net,sound,platform}` — alongside
the existing root `-I$(QUAKE_DIR)` (kept for `host.c`) and
`-I$(QUAKE_DIR)/macosx-shim`. `QW_BASE_CFLAGS` gains the six
`$(QW_CLIENT_DIR)` module dirs; both QW binaries need them because `qwsv`
compiles the shared sources from there. Zero `#include` lines change.

**Pattern rules.** One per module dir, mirroring the existing style, with
order-only `mkdir -p` prerequisites per module build dir:

```make
$(QUAKE_BUILDDIR)/render/%.o: $(QUAKE_DIR)/render/%.c | $(QUAKE_BUILDDIR)/render
	$(CC) $(CFLAGS) -o $@ -c $<
```

The existing root rule stays for `host.o` / `host_cmd.o`; each binary's
module rules must be listed **before** that catch-all — make 3.81 picks
the first matching pattern rule whose prerequisites exist, not the
shortest stem, so a catch-all listed first wins and skips the
per-module mkdir prereq. On the QW
side, the server's shared-source rule becomes two rules —
`$(QW_BUILDDIR)/server/%.o: $(QW_CLIENT_DIR)/common/%.c` and
`$(QW_BUILDDIR)/server/%.o: $(QW_CLIENT_DIR)/net/%.c`. QW server objects
stay flat in `build-macosx/server/` (today's convention).

**Object lists** gain their subdir prefixes; link lines and binary paths
(`build-macosx/glquake`, `qwsv`, `glqwcl`) are unchanged, so `run`,
`check-data*`, `clean`, and `help` need no edits.

**The collision NOTE is deleted.** With mirrored paths,
`build-macosx/server/sys_unix.o` and `build-macosx/client/platform/sys_unix.o`
are distinct targets; rule order is no longer load-bearing.

## Migration plan

One commit per module, spanning both trees (each module is one conceptual
unit), every file moved with `git mv` so `git log --follow` survives:

1. `platform` — smallest, fully leaf
2. `sound`
3. `net` — includes the QW dual-use `net_chan`/`net_udp` and the server's
   split second rule
4. `render`
5. `client`
6. `server` (Quake/ only)
7. `common` — last: carries `quakedef.h` and the shared header pool

Order is technically free (the `-I` lists make every header visible from
the first commit), but leaf-first / hub-last keeps each intermediate state
maximally sensible. Each commit updates the Makefile (object paths, `-I`
flags, pattern rules, mkdir prereqs) in the same commit as the files it
references, and must pass the full gate above before the next begins. A
commit that fails its gate is fixed forward or reverted whole; modules do
not interleave, so every commit is independently revertible.

## Documentation updates (after commit 7)

- `CONTEXT.md`: the platform-modules entry gains folder paths; a new
  "Modules" entry names the seven folders and their contents rule
  (headers live with their module; cross-module headers in `common/`).
- Fixes Ledger (`docs/superpowers/plans/2026-08-29-quake-apple-silicon.md`):
  one append-only entry recording the restructure and its commit range.
- README needs no changes (binary paths and run instructions are
  unchanged).

## Out of scope

- No `#include` rewrites and no file-content edits of any kind.
- No public/private header split and no `quakedef.h` dismantlement
  (card-6 closure stands; this restructure gives that future work
  somewhere to land).
- No changes to `QuakeWorld/server/`'s layout, `QuakeWorld/progs/`,
  `game/`, `macosx-shim/`, or the `check-data*` rules.
- Nothing is pushed to origin.

## Risks

- *Pattern-rule matching:* with several candidate rules for
  `build-macosx/server/%.o`, make resolves by prerequisite existence; a
  wrong match fails loudly (missing source) at build time, never silently.
  The per-commit clean build is the test.
- *Gray-zone header placement* (e.g. `bspfile.h` in `render/`):
  organizational only — every module dir is on every binary's `-I` path,
  so no placement can break an include. Disagreements are one-line
  `git mv`s.
- *`-DSERVERONLY` compilation of shared sources:* `QW_BASE_CFLAGS` is
  shared, so the new `-I` dirs reach `qwsv` and `glqwcl` identically.
- *Behavioral:* none by construction — every moved file is byte-identical
  (`git mv`); the smoke protocol plus playtesting is the final gate.
- *Rollback:* any module commit reverts independently; worst case is seven
  reverts to the exact pre-restructure state.

## Ground rules

- All moves are `git mv`; nothing is deleted, renamed in content, or
  edited.
- Each module commit is gated: full clean build of all three binaries,
  then the SIGKILL smoke protocol.
- Untracked material (`.qwen/`, `.superpowers/`, `graphify-out/`,
  `game/`) is untouched. Nothing is pushed to origin.
