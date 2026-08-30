# Dead-code cleanup: Quake as an Apple-Silicon-only codebase

Date: 2026-08-30
Status: Approved (brainstorming session)
Companion: `docs/superpowers/specs/2026-08-29-quake-apple-silicon-design.md` (the SDL3 port this follows)

## Goal

Make this repository an Apple Silicon (macOS arm64) only codebase by deleting
unused and dead code: Windows/DOS/Linux/Sun platform code, the x86 assembly,
the software renderer, IDE/packaging/binary junk, and the platform
conditionals that referenced them inside live files.

## Live targets (the oracle)

Three builds exist and must stay green; their Makefile object lists define
what may not be deleted:

| Target | Build | Makefile |
|---|---|---|
| `WinQuake/build-macosx/glquake` | GLQuake (single-player client) | `WinQuake/Makefile.macosx` |
| `QW/build-macosx/qwsv` | QuakeWorld dedicated server | `QW/Makefile.macosx` |
| `QW/build-macosx/glqwcl` | QuakeWorld GL client | `QW/Makefile.macosx` |

## Scope decisions (approved 2026-08-30)

1. **Software renderer: deleted.** The CPU renderer (`d_*.c`, non-GL `r_*.c`,
   and their x86 asm) is dead in all three GL builds.
2. **Ifdef depth: prune `_WIN32`/`id386`/DOS blocks** inside kept files.
   `GLQUAKE`, `SERVERONLY`, `QUAKE2`, `PARANOID`, and Unix-family
   (`__linux__`, `__unix__`, `unix`, `__APPLE__`) conditionals are untouched.
3. **Side trees:** keep `QW/progs/` (QuakeC); delete the diverged duplicate
   `qw-qc/` and the never-ported `QW/qwfwd/` utility.
4. **Rename** `sys_linux.c` → `sys_unix.c` in `WinQuake/` and `QW/client/`
   (they are generic Unix layers; `QW/server` already uses the name).

## Ground rules

- Nothing referenced by the three live Makefile object lists is deleted.
- Before deleting a header, grep the surviving files for its include; if a
  live file includes it, the include line is removed in the same commit.
- All removals are `git rm` — everything stays recoverable from history.
- Untracked material (`.qwen/`, `.superpowers/`, `graphify-out/`, `game/`)
  is untouched. Nothing is pushed to origin.

## Deletion inventory

### WinQuake

- **Windows:** `sys_win.c`, `sys_wind.c`, `sys_wina.s`, `in_win.c`,
  `vid_win.c`, `snd_win.c`, `cd_win.c`, `gl_vidnt.c`, `net_win.c`,
  `net_wins.c/.h`, `net_wipx.c/.h`, `net_mp.c/.h`, `conproc.c/.h`,
  `mplib.c`, `mplpc.c`, `mpdosock.h`, `winquake.h`, `winquake.rc`,
  `winquake.aps`, `resource.h`, `qe3.ico`, `quake.ico`, `quake.gif`,
  `WinQuake.dsp/.dsw/.mdp/.ncb/.opt/.plg`, `clean.bat`, `q.bat`, `qa.bat`,
  `qb.bat`, `qt.bat`, `wq.bat`, `makezip.bat`, `quake.spec.sh`,
  `quake-data.spec.sh`, `quake-hipnotic.spec.sh`, `quake-rogue.spec.sh`,
  `quake-shareware.spec.sh`
- **DOS:** `sys_dos.c`, `sys_dosa.s`, `in_dos.c`, `vid_dos.c/.h`,
  `vid_vga.c`, `vid_ext.c`, `snd_dos.c`, `snd_gus.c`, `cd_audio.c`,
  `net_dos.c`, `net_bw.c/.h`, `net_comx.c`, `net_ipx.c/.h`, `net_ser.c/.h`,
  `net_none.c`, `dos_v2.c`, `dosasm.s`, `dosisms.h`, `cwsdpmi.exe`,
  `vgamodes.h`, `vregset.c/.h`
- **Linux / X11 / Sun:** `gl_vidlinux.c`, `gl_vidlinuxglx.c`, `snd_linux.c`,
  `cd_linux.c`, `vid_svgalib.c`, `vid_x.c`, `sys_sun.c`, `in_sun.c`,
  `snd_sun.c`, `snd_next.c`, `vid_sunx.c`, `vid_sunxil.c`,
  `Makefile.linuxi386`, `Makefile.Solaris`, `README.Solaris`
- **Unused null drivers:** `sys_null.c`, `in_null.c`, `vid_null.c`,
  `snd_null.c` (`cd_null.c` stays — it is live)
- **Software renderer:** all `d_*.c` and `d_*.s` files; non-GL renderer
  `r_aclip.c`, `r_alias.c`, `r_bsp.c`, `r_draw.c`, `r_edge.c`, `r_efrag.c`,
  `r_light.c`, `r_main.c`, `r_misc.c`, `r_sky.c`, `r_sprite.c`, `r_surf.c`,
  `r_vars.c` plus their `*.s` siblings; `math.s`, `worlda.s`, `snd_mixa.s`,
  `surf8.s`, `surf16.s`, `nonintel.c`; renderer-only headers `adivtab.h`,
  `asm_draw.h`, `asm_i386.h`, `block8.h`, `block16.h`, `d_ifacea.h`,
  `quakeasm.h` after include-audit confirms nothing live pulls them
  (`anorms.h` stays — `gl_mesh.c` needs it; `anorm_dots.h`, `d_iface.h`,
  `d_local.h`, `r_local.h`, `r_shared.h` are audited before deciding)
- **Directories & docs:** `data/`, `docs/`, `kit/` (contains `GLQUAKE.EXE`
  and `OPENGL32.DLL` binaries), `dxsdk/`, `scitech/`, `gas2masm/`,
  `3dfx.txt`, `glqnotes.txt`, `wqreadme.txt`

### QW/client

Same categories as WinQuake: Windows drivers (`cd_win.c`, `snd_win.c`,
`sys_win.c`, `vid_win.c`, `gl_vidnt.c`, `in_win.c`, `net_wins.c` plus
IPX/serial siblings found by audit), `qwcl.dsp/.dsw/.mak/.mdp/.plg`,
`winquake.h`, `winquake.rc`, `resource.h`, `qwcl2.ico`, `quakeworld.bmp`,
`q.bat`, `makezip.bat`; all `.s` and `.asm` files; Linux/X11 drivers
(`snd_linux.c`, `cd_linux.c`, `cd_audio.c`, `vid_svgalib.c`, `vid_x.c`,
`gl_vidlinuxglx.c`); unused null drivers (`sys_null.c`, `vid_null.c`);
the same software-renderer set. Stays because it is in `GLCLIENT_OBJS`:
`r_part.c`, `nonintel.c`, `skin.c`, `cd_null.c`, `snd_sdl.c`, `gl_vidsdl.c`.

### QW/server

`sys_win.c`, `qwsv.dsp/.dsw/.mak/.mdp/.plg`, `worlda.s`, `math.s`, the
legacy Linux `makefile`, dev notes (`move.txt`, `newnet.txt`, `notes.txt`,
`profile.txt`); `asm_i386.h`/`quakeasm.h` after include-audit against
`qwsvdef.h`.

### QW root

`Makefile.Linux`, `Makefile.Solaris`, `clean.bat`, `qw.dsw/.ncb/.opt`,
`glqwcl.spec.sh`, `qwcl.spec.sh`, `qwcl.x11.spec.sh`, `qwsv.spec.sh`,
`glqwcl.3dfxgl`, `quake.gif`, `quakeworld.bmp`, `makezip/`, `makezip.bat`,
`fixskins.sh`, `cmds.txt`, `docs/`, `dxsdk/`, `gas2masm/`, `scitech/`,
`qwfwd/` (entire directory), `qw2do.txt`, `qwchangelog.txt`, `qwrlnote.txt`,
`release233_notes.txt`. Stays: `Makefile.macosx`, `client/`, `server/`,
`progs/` (the kept QuakeC tree).

### Top level

Delete `qw-qc/` (superseded duplicate of `QW/progs/`).

## Internal prune rules (live files)

A grep audit first lists every occurrence of `_WIN32`, `WIN32`, `_WINDOWS`,
`__MSDOS__`, `__DJGPP__`, `__WATCOMC__`, `__i386__`, `_M_IX86`, `__DOS__`,
`id386` in the live files; each is resolved mechanically:

| Pattern | Resolution |
|---|---|
| `#ifdef _WIN32 … #endif` region | delete the region |
| `#ifdef _WIN32 A #else B #endif` | keep only `B` |
| `#if id386 A #else B` | keep only `B` |
| `quakedef.h` id386 definition block | collapse to `#define id386 0` |
| Platform includes (`winquake.h`, `<windows.h>`, `<winsock.h>`, `<ddraw.h>`, `<dsound.h>`, `<mgraph.h>`, `mpdosock.h`) | remove the line, or keep the `#else` branch's include |

If any live file uses `id386` as a runtime variable rather than a macro, it
stays pinned to 0 rather than getting branch surgery.

Known sites (from exploration): WinQuake `quakedef.h` (lines 48–70),
`snd_dma.c`, `snd_mix.c`, `menu.c`; QW client `cl_main.c` (guarded include
at the top plus a mid-file `#include <windows.h>` at line 1032), `cl_cam.c`,
`cl_pred.c`, `net_chan.c`, `snd_dma.c`, `snd_mix.c`, `menu.c` — plus whatever
the audit adds in `common.c`, `cmd.c`, `zone.c`, `host.c`, etc.

## Rename

`git mv WinQuake/sys_linux.c WinQuake/sys_unix.c` and
`git mv QW/client/sys_linux.c QW/client/sys_unix.c`; update the one object
name in each Makefile; fix the filename in each file's header comment.
Nothing else references these files by name.

## Commit plan and verification gates

| # | Commit | Gate |
|---|--------|------|
| C1 | Remove dead platform drivers: Win/DOS/Linux/Sun drivers and their asm (`sys_wina.s`, `sys_dosa.s`), unused null drivers; delete `winquake.h`/`resource.h` etc. and strip their include lines from live files in the same commit | 3 clean builds |
| C2 | Remove software renderer, including its asm (`d_*.s`, `r_*a.s`, `math.s`, `worlda.s`, `snd_mixa.s`, `surf*.s`) (post include-audit) | 3 clean builds |
| C3 | Remove junk & side material: IDE projects, `.bat`, `.spec.sh`, icons/images, `kit/`, `data/`, `docs/`, `dxsdk/`, `scitech/`, `gas2masm/`, `makezip*`, readmes; delete `qwfwd/`, `qw-qc/` | 3 clean builds |
| C4 | Prune `_WIN32`/`id386`/DOS internals per the rules above | 3 clean builds + smoke |
| C5 | Rename `sys_linux.c` → `sys_unix.c` (+ Makefiles, header comments) | 3 clean builds + smoke |
| C6 | Append cleanup entries to the Fixes Ledger in `docs/superpowers/plans/2026-08-29-quake-apple-silicon.md` | — |

**Gate definition.** Clean rebuild of all three targets:
`WinQuake` (`make -f Makefile.macosx clean build-release`) and `QW`
(`make -f Makefile.macosx clean build-server build-client`). For C4 and C5
additionally: `glquake +map start` for ~12 s (the established crash repro;
grep the log for `Received signal`), qwsv starts, glqwcl reaches the menu.

## Risks and mitigations

- *Deleted header still transitively included, or deleted `.c` still
  referenced* → compile/link failure at the very next gate; fix by
  reclassifying that one file. Per-commit gates keep fallout inside one
  commit.
- *Ifdef prune keeps the wrong branch* → the rules are mechanical (the
  non-platform branch always wins); smoke tests after C4/C5 exercise the
  QuakeC/worldspawn path where earlier porting bugs lived.
- *`QW/progs` vs `qw-qc` divergence* → decided: keep `QW/progs`; `qw-qc`
  remains in git history.
- All work is local; nothing is pushed without an explicit request.
