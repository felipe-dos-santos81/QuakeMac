# Dead-Code Cleanup Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Delete all Windows/DOS/Linux/Sun platform code, the software renderer, IDE/packaging/binary junk, and prune platform conditionals from live files, leaving an Apple-Silicon-only Quake codebase with three green builds.

**Architecture:** Pure deletion plus mechanical conditional-pruning over the existing 1999 GPL source trees (`WinQuake/`, `QW/`). The three live Makefile object lists are the oracle for what stays; every task ends with a clean rebuild of all three targets (plus runtime smoke tests where code is edited).

**Tech Stack:** C (C89-ish, clang), GNU make, SDL3, OpenGL. No new dependencies.

**Spec:** `docs/superpowers/specs/2026-08-30-quake-dead-code-cleanup-design.md`

## Global Constraints

- Target platform: macOS arm64 only. Three live targets must stay green:
  `WinQuake/build-macosx/glquake`, `QW/build-macosx/qwsv`, `QW/build-macosx/glqwcl`.
- Build gate (run after EVERY task, from repo root `/Users/felipe.dos.santos/code/theirs/Quake`):
  ```bash
  cd WinQuake && make -f Makefile.macosx clean && make -f Makefile.macosx build-release && cd ..
  cd QW && make -f Makefile.macosx clean && make -f Makefile.macosx build-server build-client && cd ..
  ```
  Both must exit 0.
- Smoke gate (Tasks 4 and 5 only):
  ```bash
  cd WinQuake
  ./build-macosx/glquake -basedir "$(pwd)/../game" +map start > /tmp/glquake-smoke.log 2>&1 &
  SMOKE_PID=$!
  sleep 12
  kill $SMOKE_PID 2>/dev/null; sleep 1
  grep -c "Received signal" /tmp/glquake-smoke.log
  ```
  Expected: `0`. Then:
  ```bash
  cd ../QW
  ./build-macosx/qwsv -basedir "$(pwd)/../game" +gamedir qw > /tmp/qwsv-smoke.log 2>&1 &
  QWSV_PID=$!
  sleep 5
  kill $QWSV_PID 2>/dev/null; sleep 1
  grep -c "Received signal" /tmp/qwsv-smoke.log
  ```
  Expected: `0`. Then:
  ```bash
  ./build-macosx/glqwcl -basedir "$(pwd)/../game" +gamedir qw > /tmp/glqwcl-smoke.log 2>&1 &
  GLQWCL_PID=$!
  sleep 8
  kill $GLQWCL_PID 2>/dev/null; sleep 1
  grep -c "Received signal" /tmp/glqwcl-smoke.log
  ```
  Expected: `0`. Kill any strays afterwards: `pkill -f glquake; pkill -f qwsv; pkill -f glqwcl`.
- All removals use `git rm` (recoverable from history). Never `rm` a tracked file directly.
- Never touch untracked material: `game/`, `.qwen/`, `.superpowers/`, `graphify-out/`, `.DS_Store`, any `build-macosx/` output, `WinQuake/macosx-shim/`.
- Never push to origin. One commit per task, messages given in each task.
- The delete lists below were produced by an include-closure audit against the exact live object lists; execute them verbatim. Do not improvise extra deletions.

---

### Task 1: Remove dead platform drivers, their asm, and null drivers (commit C1)

**Files:**
- Delete: the WinQuake and QW lists below (~100 files)
- Modify (include-line removal only): `WinQuake/menu.c:22-24`, `WinQuake/snd_dma.c:24-26`, `WinQuake/snd_mix.c:24-26`, `QW/client/cl_cam.c:29`, `QW/client/cl_main.c:23`, `QW/client/cl_pred.c:21`, `QW/client/menu.c:21`, `QW/client/net_chan.c:24`, `QW/client/snd_dma.c:25`, `QW/client/snd_mix.c:25`

**Interfaces:**
- Consumes: clean master with the three builds green (run the build gate once before starting to confirm the baseline).
- Produces: commit C1; no file outside the lists above is removed; no live file references `winquake.h` or `resource.h` anymore.

- [ ] **Step 1: Baseline build gate**

Run the build gate from Global Constraints. Expected: both builds exit 0. If not, stop and report — do not proceed on a broken baseline.

- [ ] **Step 2: Delete WinQuake platform drivers**

```bash
cd /Users/felipe.dos.santos/code/theirs/Quake
git rm WinQuake/cd_audio.c WinQuake/cd_linux.c WinQuake/cd_win.c \
  WinQuake/conproc.c WinQuake/conproc.h WinQuake/cwsdpmi.exe \
  WinQuake/dos_v2.c WinQuake/dosasm.s WinQuake/dosisms.h \
  WinQuake/gl_vidlinux.c WinQuake/gl_vidlinuxglx.c WinQuake/gl_vidnt.c \
  WinQuake/in_dos.c WinQuake/in_null.c WinQuake/in_sun.c WinQuake/in_win.c \
  WinQuake/Makefile.linuxi386 WinQuake/Makefile.Solaris WinQuake/README.Solaris \
  WinQuake/mpdosock.h WinQuake/mplib.c WinQuake/mplpc.c \
  WinQuake/net_bw.c WinQuake/net_bw.h WinQuake/net_comx.c WinQuake/net_dos.c \
  WinQuake/net_ipx.c WinQuake/net_ipx.h WinQuake/net_mp.c WinQuake/net_mp.h \
  WinQuake/net_none.c WinQuake/net_ser.c WinQuake/net_ser.h WinQuake/net_win.c \
  WinQuake/net_wins.c WinQuake/net_wins.h WinQuake/net_wipx.c WinQuake/net_wipx.h \
  WinQuake/net_wso.c \
  WinQuake/snd_dos.c WinQuake/snd_gus.c WinQuake/snd_linux.c WinQuake/snd_next.c \
  WinQuake/snd_null.c WinQuake/snd_sun.c WinQuake/snd_win.c \
  WinQuake/sys_dos.c WinQuake/sys_dosa.s WinQuake/sys_null.c WinQuake/sys_sun.c \
  WinQuake/sys_win.c WinQuake/sys_wina.s WinQuake/sys_wind.c \
  WinQuake/vgamodes.h WinQuake/vid_dos.c WinQuake/vid_dos.h WinQuake/vid_ext.c \
  WinQuake/vid_null.c WinQuake/vid_sunx.c WinQuake/vid_sunxil.c \
  WinQuake/vid_svgalib.c WinQuake/vid_vga.c WinQuake/vid_win.c WinQuake/vid_x.c \
  WinQuake/vregset.c WinQuake/vregset.h \
  WinQuake/winquake.h WinQuake/winquake.aps WinQuake/winquake.rc WinQuake/resource.h
```

- [ ] **Step 3: Delete QW platform drivers**

```bash
git rm QW/Makefile.Linux QW/Makefile.Solaris \
  QW/client/cd_audio.c QW/client/cd_linux.c QW/client/cd_win.c \
  QW/client/gl_vidlinux.c QW/client/gl_vidlinux_svga.c QW/client/gl_vidlinux_x11.c \
  QW/client/gl_vidlinuxglx.c QW/client/gl_vidnt.c \
  QW/client/in_null.c QW/client/in_win.c QW/client/makefile.svgalib \
  QW/client/net_wins.c \
  QW/client/snd_linux.c QW/client/snd_win.c \
  QW/client/sys_dosa.s QW/client/sys_null.c QW/client/sys_win.c \
  QW/client/sys_wina.asm QW/client/sys_wina.s \
  QW/client/vid_null.c QW/client/vid_svgalib.c QW/client/vid_win.c QW/client/vid_x.c \
  QW/client/winquake.h QW/client/winquake.aps QW/client/winquake.rc QW/client/resource.h \
  QW/server/sys_win.c QW/server/makefile
```

- [ ] **Step 4: Remove `winquake.h` include lines from live files**

Three WinQuake files have the guarded form — delete all three lines in each:

```bash
perl -0pi -e 's/\n#ifdef _WIN32\n#include "winquake\.h"\n#endif\n/\n/g' \
  WinQuake/menu.c WinQuake/snd_dma.c WinQuake/snd_mix.c
```

Seven QW client files include it unguarded — delete the single line in each:

```bash
perl -pi -e '$_ = "" if /^\s*#include "winquake\.h"\s*$/' \
  QW/client/cl_cam.c QW/client/cl_main.c QW/client/cl_pred.c QW/client/menu.c \
  QW/client/net_chan.c QW/client/snd_dma.c QW/client/snd_mix.c
```

- [ ] **Step 5: Verify no references remain**

Run: `grep -rn "winquake.h\|resource.h" WinQuake QW --include="*.c" --include="*.h"`
Expected: no output. If any line remains, remove that include line (guarded or bare) and re-run.

- [ ] **Step 6: Build gate**

Run the build gate. Expected: both builds exit 0.

- [ ] **Step 7: Commit**

```bash
git commit -m "Remove dead Windows/DOS/Linux/Sun platform drivers and null drivers"
```

---

### Task 2: Remove the software renderer (commit C2)

**Files:**
- Delete: the WinQuake and QW lists below (~115 files)

**Interfaces:**
- Consumes: commit C1 on master.
- Produces: commit C2; no `d_*` renderer file and no non-GL `r_*` file remains in either tree; `r_part.c`, `nonintel.c` (QW only), `anorms.h`, `anorm_dots.h`, `d_iface.h`, `r_local.h`, `r_shared.h` are confirmed kept (live include closure needs them).

- [ ] **Step 1: Delete WinQuake software renderer**

```bash
git rm WinQuake/adivtab.h WinQuake/asm_draw.h WinQuake/asm_i386.h \
  WinQuake/block16.h WinQuake/block8.h \
  WinQuake/d_copy.s WinQuake/d_draw.s WinQuake/d_draw16.s WinQuake/d_edge.c \
  WinQuake/d_fill.c WinQuake/d_ifacea.h WinQuake/d_init.c WinQuake/d_local.h \
  WinQuake/d_modech.c WinQuake/d_part.c WinQuake/d_parta.s WinQuake/d_polysa.s \
  WinQuake/d_polyse.c WinQuake/d_scan.c WinQuake/d_scana.s WinQuake/d_sky.c \
  WinQuake/d_spr8.s WinQuake/d_sprite.c WinQuake/d_surf.c WinQuake/d_vars.c \
  WinQuake/d_varsa.s WinQuake/d_zpoint.c \
  WinQuake/draw.c WinQuake/math.s WinQuake/model.c WinQuake/nonintel.c \
  WinQuake/quakeasm.h \
  WinQuake/r_aclip.c WinQuake/r_aclipa.s WinQuake/r_alias.c WinQuake/r_aliasa.s \
  WinQuake/r_bsp.c WinQuake/r_draw.c WinQuake/r_drawa.s WinQuake/r_edge.c \
  WinQuake/r_edgea.s WinQuake/r_efrag.c WinQuake/r_light.c WinQuake/r_main.c \
  WinQuake/r_misc.c WinQuake/r_sky.c WinQuake/r_sprite.c WinQuake/r_surf.c \
  WinQuake/r_vars.c WinQuake/r_varsa.s \
  WinQuake/screen.c WinQuake/snd_mixa.s WinQuake/surf16.s WinQuake/surf8.s \
  WinQuake/worlda.s WinQuake/glquake2.h
```

- [ ] **Step 2: Delete QW software renderer**

```bash
git rm QW/client/adivtab.h QW/client/asm_draw.h QW/client/asm_i386.h \
  QW/client/block16.h QW/client/block8.h \
  QW/client/d_copy.s QW/client/d_draw.asm QW/client/d_draw.s \
  QW/client/d_draw16.asm QW/client/d_draw16.s QW/client/d_edge.c \
  QW/client/d_fill.c QW/client/d_ifacea.h QW/client/d_init.c QW/client/d_local.h \
  QW/client/d_modech.c QW/client/d_part.c QW/client/d_parta.asm QW/client/d_parta.s \
  QW/client/d_polysa.asm QW/client/d_polysa.s QW/client/d_polyse.c \
  QW/client/d_scan.c QW/client/d_scana.asm QW/client/d_scana.s QW/client/d_sky.c \
  QW/client/d_spr8.asm QW/client/d_spr8.s QW/client/d_sprite.c QW/client/d_surf.c \
  QW/client/d_vars.c QW/client/d_varsa.asm QW/client/d_varsa.s QW/client/d_zpoint.c \
  QW/client/draw.c QW/client/gl_test.c QW/client/glquake2.h \
  QW/client/math.asm QW/client/math.s QW/client/model.c QW/client/quakeasm.h \
  QW/client/r_aclip.c QW/client/r_aclipa.asm QW/client/r_aclipa.s \
  QW/client/r_alias.c QW/client/r_aliasa.asm QW/client/r_aliasa.s QW/client/r_bsp.c \
  QW/client/r_draw.c QW/client/r_drawa.asm QW/client/r_drawa.s QW/client/r_edge.c \
  QW/client/r_edgea.asm QW/client/r_edgea.s QW/client/r_efrag.c QW/client/r_light.c \
  QW/client/r_main.c QW/client/r_misc.c QW/client/r_sky.c QW/client/r_sprite.c \
  QW/client/r_surf.c QW/client/r_vars.c QW/client/r_varsa.asm QW/client/r_varsa.s \
  QW/client/screen.c QW/client/snd_mixa.asm QW/client/snd_mixa.s \
  QW/client/surf16.asm QW/client/surf16.s QW/client/surf8.asm QW/client/surf8.s \
  QW/server/asm_i386.h QW/server/math.s QW/server/quakeasm.h QW/server/worlda.s
```

- [ ] **Step 3: Sanity check the kept renderer-adjacent headers still exist**

Run: `ls WinQuake/anorms.h WinQuake/anorm_dots.h WinQuake/d_iface.h WinQuake/r_local.h WinQuake/r_shared.h QW/client/anorms.h QW/client/anorm_dots.h QW/client/d_iface.h QW/client/r_local.h QW/client/r_shared.h`
Expected: all ten listed, no error. These are in the live include closure — if any is missing, a previous step over-deleted; restore it with `git checkout HEAD~1 -- <path>` before continuing.

- [ ] **Step 4: Build gate**

Run the build gate. Expected: both builds exit 0.

- [ ] **Step 5: Commit**

```bash
git commit -m "Remove the software renderer (dead in all three GL builds)"
```

---

### Task 3: Remove IDE/packaging/binary junk and side trees (commit C3)

**Files:**
- Delete: the WinQuake, QW, and top-level lists below (~110 files plus directories `data/`, `docs/`, `kit/`, `dxsdk/`, `scitech/`, `gas2masm/`, `makezip`, `qwfwd/`, `qw-qc/`)

**Interfaces:**
- Consumes: commit C2 on master.
- Produces: commit C3; no IDE project, batch, spec, icon, README-era, or third-party-SDK file remains; `QW/progs/` (QuakeC) is untouched.

- [ ] **Step 1: Delete WinQuake junk**

```bash
git rm WinQuake/3dfx.txt WinQuake/clean.bat WinQuake/glqnotes.txt \
  WinQuake/makezip.bat WinQuake/q.bat WinQuake/qa.bat WinQuake/qb.bat \
  WinQuake/qe3.ico WinQuake/qt.bat WinQuake/quake.gif WinQuake/quake.ico \
  WinQuake/quake.spec.sh WinQuake/quake-data.spec.sh WinQuake/quake-hipnotic.spec.sh \
  WinQuake/quake-rogue.spec.sh WinQuake/quake-shareware.spec.sh \
  WinQuake/WinQuake.dsp WinQuake/WinQuake.dsw WinQuake/WinQuake.mdp \
  WinQuake/WinQuake.ncb WinQuake/WinQuake.opt WinQuake/WinQuake.plg \
  WinQuake/wq.bat WinQuake/wqreadme.txt
git rm -r WinQuake/data WinQuake/docs WinQuake/kit WinQuake/dxsdk \
  WinQuake/scitech WinQuake/gas2masm
```

- [ ] **Step 2: Delete QW junk**

```bash
git rm QW/clean.bat QW/cmds.txt QW/fixskins.sh QW/glqwcl.3dfxgl \
  QW/glqwcl.spec.sh QW/qwcl.spec.sh QW/qwcl.x11.spec.sh QW/qwsv.spec.sh \
  QW/makezip QW/makezip.bat QW/quake.gif QW/quakeworld.bmp \
  QW/qw.dsw QW/qw.ncb QW/qw.opt \
  QW/qw2do.txt QW/qwchangelog.txt QW/qwrlnote.txt QW/release233_notes.txt \
  QW/client/buildnum.c QW/client/docs.txt QW/client/exitscrn.txt QW/client/notes.txt \
  QW/client/q.bat QW/client/qe3.ico QW/client/quakeworld.bmp \
  QW/client/qwcl.dsp QW/client/qwcl.dsw QW/client/qwcl.mak QW/client/qwcl.mdp \
  QW/client/qwcl.plg QW/client/qwcl2.ico \
  QW/server/move.txt QW/server/newnet.txt QW/server/notes.txt QW/server/profile.txt \
  QW/server/qwsv.dsp QW/server/qwsv.dsw QW/server/qwsv.mak QW/server/qwsv.mdp \
  QW/server/qwsv.plg
git rm -r QW/docs QW/dxsdk QW/gas2masm QW/scitech QW/qwfwd
```

- [ ] **Step 3: Delete the duplicate QuakeC tree**

```bash
git rm -r qw-qc
```

`QW/progs/` (the QuakeC tree that ships with the QW release) stays.

- [ ] **Step 4: Verify QuakeC survived**

Run: `ls QW/progs/progs.src QW/progs/qwprogs.dat`
Expected: both listed, no error.

- [ ] **Step 5: Build gate**

Run the build gate. Expected: both builds exit 0 (this task touches no compiled code; the gate is insurance).

- [ ] **Step 6: Commit**

```bash
git commit -m "Remove IDE projects, packaging, SDKs, binaries, and side trees"
```

---

### Task 4: Prune `_WIN32`/`id386`/DOS conditionals from live files (commit C4)

**Files:**
- Modify: WinQuake: `quakedef.h`, `cl_parse.c`, `common.c`, `gl_draw.c`, `glquake.h`, `host.c`, `mathlib.c`, `menu.c`, `net.h`, `net_dgrm.c`, `snd_dma.c`, `snd_mix.c`, `sys_linux.c`, `world.c`, `r_local.h`
- Modify: QW client: `bothdefs.h`, `cl_main.c`, `cl_pred.c`, `common.h`, `gl_draw.c`, `gl_rsurf.c`, `glquake.h`, `keys.c`, `mathlib.c`, `menu.c`, `net_chan.c`, `nonintel.c`, `quakedef.h`, `r_local.h`, `snd_dma.c`, `snd_mix.c`, `sys_linux.c`, `zone.c`
- Modify: QW server: `qwsvdef.h`, `sv_send.c`, `world.c`

**Interfaces:**
- Consumes: commit C3 on master.
- Produces: commit C4; `grep -rnE "_WIN32|_WINDOWS|__MSDOS__|__DJGPP__|__WATCOMC__|__i386__|_M_IX86|__DOS__" WinQuake QW --include="*.c" --include="*.h"` returns nothing in the modified files; runtime behavior unchanged (smoke gate).

**Prune rules** (apply mechanically to every site listed below):

| Pattern | Resolution |
|---|---|
| `#ifdef _WIN32 … #endif` region (no `#else`) | delete the region including both directive lines |
| `#ifdef _WIN32 A #else B #endif` | keep only `B` (drop the three directive lines) |
| `#ifndef _WIN32 A #endif` | keep only `A` |
| `#ifndef _WIN32 A #else B #endif` | keep only `A` |
| `#ifdef _WINDOWS …` / `#if defined(_WIN32) …` | treat exactly like the `_WIN32` forms |
| `#if id386 A #else B #endif` | keep only `B` |
| `#if !id386 A #endif` | keep only `A` |
| `#if !id386 A #else B #endif` | keep only `A` |
| Platform includes (`<windows.h>`, `"winsock.h"`) inside such regions | disappear with their region; for `#ifdef _WIN32 A #else B` include pairs, keep `B` |

Do **not** touch `GLQUAKE`, `SERVERONLY`, `QUAKE2`, `PARANOID`, `BAN_TEST`, `__linux__`, `__unix__`, `__sun__`, or `__APPLE__` conditionals except where a rule above removes a nested platform branch (see `net_dgrm.c`).

- [ ] **Step 1: WinQuake `quakedef.h` (lines ~48-72)**

Replace:

```c
#if defined(_WIN32) && !defined(WINDED)

#if defined(_M_IX86)
#define __i386__	1
#endif

void	VID_LockBuffer (void);
void	VID_UnlockBuffer (void);

#else

#define	VID_LockBuffer()
#define	VID_UnlockBuffer()

#endif

#if defined __i386__ // && !defined __sun__
#define id386	1
#else
#define id386	0
#endif
```

with:

```c
#define	VID_LockBuffer()
#define	VID_UnlockBuffer()

#define id386	0
```

Leave the following `#if id386 … UNALIGNED_OK …` block untouched — it now evaluates the id386=0 branch.

- [ ] **Step 2: QW `bothdefs.h` (lines ~28-36)**

Replace:

```c
#if (defined(_M_IX86) || defined(__i386__)) && !defined(id386)
#define id386	1
#else
#define id386	0
#endif

#ifdef SERVERONLY		// no asm in dedicated server
#undef id386
#endif
```

with:

```c
#define id386	0
```

Leave the following `#if id386 … UNALIGNED_OK …` block untouched.

- [ ] **Step 3: QW `quakedef.h` (line 26), `qwsvdef.h` (line 26), `common.h` (line 146)**

Apply the rules table to each `_WIN32` region in these three headers. In `common.h` the `_WIN32` branch defines `_stricmp` mappings and the `#else` branch the `strcasecmp` ones — keep the `#else` branch.

- [ ] **Step 4: WinQuake `host.c` (lines ~885-908)**

Three adjacent regions. Result after pruning: `IN_Init ();` stays before `VID_Init (host_basepal);` (drop its `#ifndef _WIN32`/`#endif`), the `S_Init ();` call stays in place of the second region (drop the `#else` GLQUAKE-duplicate branch and all directive lines), and the trailing `#ifdef _WIN32 IN_Init (); #endif` region is deleted entirely.

- [ ] **Step 5: WinQuake `net.h` (line ~241)**

The region `#if !defined(_WIN32 ) && !defined (__linux__) && !defined (__sun__) … #endif` declares `htonl/htons/ntohl/ntohs` externs guarded by `#ifndef`. Keep the body, delete the outer `#if`/`#endif` lines (this region is already active on macOS today, so behavior is unchanged).

- [ ] **Step 6: WinQuake `net_dgrm.c` (line ~26)**

Inside the existing `#ifdef BAN_TEST` wrapper, replace the `#if defined(_WIN32) / #elif NeXT / #else` chain by dropping only the `_WIN32` branch, leaving:

```c
#if defined (NeXT)
#include <sys/socket.h>
#include <arpa/inet.h>
#else
#define AF_INET 		2	/* internet */
```

(and the rest of that chain unchanged). Keep the outer `#ifdef BAN_TEST` wrapper.

- [ ] **Step 7: Remaining WinQuake sites**

Apply the rules table to every `_WIN32`/`id386` region in: `cl_parse.c` (two `VID_HandlePause` regions, ~lines 887/894), `common.c` (~1435, keep `#else` branch), `gl_draw.c` (~83), `glquake.h` (~26/37/75/239), `mathlib.c` (~88/148 `_WIN32` regions; ~180/564 `#if !id386` — keep bodies), `menu.c` (~916/934/1039/1055/1095/1140/1226/1301/1583/1684), `snd_dma.c` (~150 `#ifndef _WIN32` keep body; ~563/575/847/880 `_WIN32` regions), `snd_mix.c` (~70/82/133/149/169/231 `_WIN32` regions; ~38/344 `#if !id386` keep bodies), `sys_linux.c` (~131 `#if id386` delete region; ~341 `#if !id386` keep body), `world.c` (~483 `#if !id386` keep body), `r_local.h` (~143 `#if id386` delete region).

- [ ] **Step 8: QW client sites**

Apply the rules table to every `_WIN32`/`_WINDOWS`/`id386` region in: `cl_main.c` (~24 include pair keep `#else` netinet branch; ~413/842 `_WIN32` regions; ~1031/1176 `_WINDOWS` regions), `cl_pred.c` (~150), `gl_draw.c` (~93), `glquake.h` (~26/74), `gl_rsurf.c` (~305 `#ifndef _WIN32` keep body), `keys.c` (~21 `_WINDOWS` include region delete; ~208/315 `_WIN32` regions), `mathlib.c` (~88/148 `_WIN32`; ~178/562 `#if !id386` keep bodies), `menu.c` (~408/550/556/628), `net_chan.c` (~96), `nonintel.c` (~26 `#if !id386` keep body), `snd_dma.c` (~153 `#ifndef _WIN32` keep body; ~566/578/845/880 `_WIN32`), `snd_mix.c` (~70/82/133/149/169/231 `_WIN32`; ~38/344 `#if !id386` keep bodies), `sys_linux.c` (~133 `#if id386` delete region; ~336 `#if !id386` keep body), `zone.c` (~414 keep `#else` branch), `r_local.h` (~144 `#if id386` delete region).

- [ ] **Step 9: QW server sites**

Apply the rules table to: `sv_send.c` (~689/782 `_WIN32` regions), `world.c` (~445 `#if !id386` keep body). (`qwsvdef.h` was handled in Step 3.)

- [ ] **Step 10: Verify no platform conditionals remain in live files**

Run:

```bash
grep -rnE "_WIN32|_WINDOWS|__MSDOS__|__DJGPP__|__WATCOMC__|__i386__|_M_IX86|__DOS__" \
  WinQuake QW --include="*.c" --include="*.h"
```

Expected: no output. Also run `grep -rnE "\bid386\b" WinQuake QW --include="*.c" --include="*.h"` and confirm every remaining hit is either `#define id386 0`, `#if id386`/`#if !id386` blocks kept by the rules (e.g. the `UNALIGNED_OK` definitions), or `int id386` runtime uses pinned to 0 — no `#ifdef _WIN32`-style platform guard remains.

- [ ] **Step 11: Build gate + smoke gate**

Run the build gate, then the full smoke gate from Global Constraints. Expected: builds exit 0; all three `grep -c` smoke checks print `0`.

- [ ] **Step 12: Commit**

```bash
git commit -m "Prune _WIN32/id386/DOS conditionals from live files"
```

---

### Task 5: Rename `sys_linux.c` to `sys_unix.c` (commit C5)

**Files:**
- Rename: `WinQuake/sys_linux.c` → `WinQuake/sys_unix.c`, `QW/client/sys_linux.c` → `QW/client/sys_unix.c`
- Modify: `WinQuake/Makefile.macosx:42`, `QW/Makefile.macosx:86`

**Interfaces:**
- Consumes: commit C4 on master.
- Produces: commit C5; no reference to `sys_linux` remains in `WinQuake/` or `QW/` except none.

- [ ] **Step 1: Rename the files**

```bash
git mv WinQuake/sys_linux.c WinQuake/sys_unix.c
git mv QW/client/sys_linux.c QW/client/sys_unix.c
```

- [ ] **Step 2: Update `WinQuake/Makefile.macosx` line 42**

Replace:

```
	$(BUILDDIR)/cd_null.o $(BUILDDIR)/sys_linux.o \
```

with:

```
	$(BUILDDIR)/cd_null.o $(BUILDDIR)/sys_unix.o \
```

- [ ] **Step 3: Update `QW/Makefile.macosx` line 86**

Replace:

```
	$(BUILDDIR)/client/cd_null.o $(BUILDDIR)/client/sys_linux.o \
```

with:

```
	$(BUILDDIR)/client/cd_null.o $(BUILDDIR)/client/sys_unix.o \
```

- [ ] **Step 4: Verify no stray references**

Run: `grep -rn "sys_linux" WinQuake QW`
Expected: no output. (Comment lines in the Makefiles that describe historical substitutions may mention `snd_linux`/`gl_vidlinuxglx` — those are fine; only `sys_linux` must be gone.)

- [ ] **Step 5: Build gate + smoke gate**

Run the build gate, then the full smoke gate. Expected: builds exit 0; all three smoke checks print `0`.

- [ ] **Step 6: Commit**

```bash
git commit -m "Rename sys_linux.c to sys_unix.c (generic Unix layer)"
```

---

### Task 6: Ledger and final verification (commit C6)

**Files:**
- Modify: `docs/superpowers/plans/2026-08-29-quake-apple-silicon.md` (append to the Fixes Ledger section)

**Interfaces:**
- Consumes: commit C5 on master.
- Produces: commit C6; the ledger records the cleanup; final full verification passes.

- [ ] **Step 1: Append ledger entries**

Append to the Fixes Ledger section of `docs/superpowers/plans/2026-08-29-quake-apple-silicon.md` (match the existing entry style; adjust commit hashes to the real ones from `git log --oneline -6`):

```markdown
### Removed dead platform drivers and null drivers
Commit: <C1 hash>. Deleted Windows/DOS/Linux/Sun drivers, their x86 asm, and
unused null drivers from WinQuake/ and QW/; removed winquake.h/resource.h and
their include lines from live files. cd_null.c (live) kept.

### Removed the software renderer
Commit: <C2 hash>. Deleted d_* files, non-GL r_* files, renderer asm, and
renderer-only headers from WinQuake/ and QW/. Kept r_part.c, nonintel.c (QW),
anorms.h, anorm_dots.h, d_iface.h, r_local.h, r_shared.h (live include closure).

### Removed IDE/packaging/binary junk and side trees
Commit: <C3 hash>. Deleted IDE projects, .bat/.spec.sh files, icons/images,
kit/ (with GLQUAKE.EXE/OPENGL32.DLL binaries), data/, docs/, dxsdk/, scitech/,
gas2masm/, makezip*, qwfwd/, and the duplicate qw-qc/ tree. QW/progs/ kept.

### Pruned platform conditionals from live files
Commit: <C4 hash>. Removed _WIN32/_WINDOWS/id386/DOS regions from 36 live
files per the rules in the 2026-08-30 cleanup spec; id386 pinned to 0.

### Renamed sys_linux.c to sys_unix.c
Commit: <C5 hash>. WinQuake/ and QW/client/ now match QW/server naming.
```

- [ ] **Step 2: Final full gate**

Run the build gate and the full smoke gate one last time. Expected: builds exit 0; all three smoke checks print `0`.

- [ ] **Step 3: Commit**

```bash
git add docs/superpowers/plans/2026-08-29-quake-apple-silicon.md
git commit -m "Ledger: dead-code cleanup for the Apple-Silicon-only codebase"
```

- [ ] **Step 4: Final state check**

Run: `git log --oneline -8 && git status --short`
Expected: six new commits (C1-C6 plus nothing else), and `git status` shows only the usual untracked entries (`.DS_Store`, `.qwen/`, `graphify-out/`, possibly `.superpowers/`). Report the result to the user; do not push.
