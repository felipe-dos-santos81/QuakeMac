# Module Folder Restructure Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Restructure `Quake/` and `QuakeWorld/client/` from flat directories into one subfolder per module (`common`, `client`, `render`, `server`, `net`, `sound`, `platform`), headers next to their modules, without changing a single byte of file content.

**Architecture:** Pure file moves via `git mv` plus Makefile rewiring — one `-I` flag per module dir, one pattern rule per module dir, object paths mirroring source paths. Because every module dir is on every binary's include path and all `#include` lines are bare (`#include "quakedef.h"`), every include keeps resolving from any location; no source file is edited. Each of the seven module moves is one commit gated by the full 3-binary build oracle and the SIGKILL smoke protocol.

**Tech Stack:** GNU make 3.81, `cc` (Apple clang), SDL3 via pkg-config, OpenGL framework. No test framework — the build oracle plus smoke protocol is the test cycle.

**Spec:** `docs/superpowers/specs/2026-08-30-module-folder-restructure-design.md`

## Global Constraints

- macOS arm64 only. Build from the repo root: `/Users/felipe.dos.santos/code/theirs/Quake`.
- All moves are `git mv`. Zero file-content edits, zero `#include` changes, zero header edits. If a step seems to require a content change, stop — the plan is wrong, not the code.
- `QuakeWorld/server/`, `QuakeWorld/progs/`, `game/`, `Quake/macosx-shim/`, `.qwen/`, `.superpowers/`, `graphify-out/` are untouched.
- Nothing is pushed to origin. Commits are local only.
- Commit messages: plain imperative, no apostrophes (heredocs with apostrophes break this shell wrapper). Use `git commit -m` with double quotes.
- Stage with `git add Makefile Quake QuakeWorld` (Tasks 1–7). Build output (`build-macosx/`) is gitignored and never staged.
- The QW server compiles 11 shared sources from `QuakeWorld/client/` with `-DSERVERONLY`; after the `net` and `common` moves those live in `QuakeWorld/client/net/` and `QuakeWorld/client/common/`.

### The gate (every task ends with it)

Run from the repo root:

```bash
make clean && make build-release build-server build-client
```

Expected: exit 0, three binaries produced (`Quake/build-macosx/glquake`, `QuakeWorld/build-macosx/qwsv`, `QuakeWorld/build-macosx/glqwcl`).

Then the SIGKILL smoke protocol:

```bash
Quake/build-macosx/glquake -basedir "$PWD/game" +map start > /tmp/smoke-glquake.log 2>&1 &
GLPID=$!
QuakeWorld/build-macosx/qwsv -basedir "$PWD/game" +gamedir qw > /tmp/smoke-qwsv.log 2>&1 &
SVPID=$!
QuakeWorld/build-macosx/glqwcl -basedir "$PWD/game" +gamedir qw > /tmp/smoke-glqwcl.log 2>&1 &
CLPID=$!
sleep 5
kill -9 $GLPID $SVPID $CLPID
rmdir qw 2>/dev/null
for f in /tmp/smoke-glquake.log /tmp/smoke-qwsv.log /tmp/smoke-glqwcl.log; do
  awk '/Received signal/ {c++} END {printf "%s: %d\n", FILENAME, c+0}' "$f"
done
```

Expected: `0` for all three logs. `kill -9` because the engine's SIGTERM handler output is a known false positive. If `kill` reports "No such process" for a PID, that binary died early — read its log before doing anything else. `glqwcl` mkdirs an empty `qw/` in the cwd; the `rmdir` cleans it.

### Makefile conventions (every task follows them)

- Each new `mkdir` rule goes immediately after the existing `mkdir` rule of its build dir. Each new pattern rule goes immediately after the existing pattern rule it mirrors.
- The three binaries are independent builds; `make` runs them sequentially here, which is correct.
- Object paths mirror source paths: `Quake/render/gl_rmain.c` → `Quake/build-macosx/render/gl_rmain.o`; `QuakeWorld/client/render/gl_rmain.c` → `QuakeWorld/build-macosx/client/render/gl_rmain.o`. QW server objects stay flat in `build-macosx/server/` regardless of which source dir they compile from.

---

### Task 1: platform module

**Files:**
- Move (Quake/): `gl_vidsdl.c` `in_sdl.c` `snd_sdl.c` `cd_null.c` `sys_unix.c` `sys.h` `vid.h` `input.h` `cdaudio.h` → `Quake/platform/`
- Move (QuakeWorld/client/): same nine files → `QuakeWorld/client/platform/`
- Modify: `Makefile`

**Interfaces:**
- Consumes: the baseline state (spec committed, working tree clean, all gates passing).
- Produces: `Quake/platform/` and `QuakeWorld/client/platform/` wired into the Makefile (`-I` flags, mkdir + pattern rules, prefixed objects). Tasks 2–7 repeat this exact pattern for their module.

- [ ] **Step 1: Move the Quake platform files**

```bash
mkdir -p Quake/platform
git mv Quake/gl_vidsdl.c Quake/in_sdl.c Quake/snd_sdl.c Quake/cd_null.c \
       Quake/sys_unix.c Quake/sys.h Quake/vid.h Quake/input.h Quake/cdaudio.h \
       Quake/platform/
```

- [ ] **Step 2: Move the QuakeWorld client platform files**

```bash
mkdir -p QuakeWorld/client/platform
git mv QuakeWorld/client/gl_vidsdl.c QuakeWorld/client/in_sdl.c \
       QuakeWorld/client/snd_sdl.c QuakeWorld/client/cd_null.c \
       QuakeWorld/client/sys_unix.c QuakeWorld/client/sys.h QuakeWorld/client/vid.h \
       QuakeWorld/client/input.h QuakeWorld/client/cdaudio.h \
       QuakeWorld/client/platform/
```

(`QuakeWorld/server/sys_unix.c` does NOT move — the server stays flat.)

- [ ] **Step 3: Add the include paths**

In `Makefile`, replace:

```make
QUAKE_BASE_CFLAGS    = -DGLQUAKE -Dstricmp=strcasecmp -I$(QUAKE_DIR) \
                       -I$(QUAKE_DIR)/macosx-shim $(SDL_CFLAGS)
```

with:

```make
QUAKE_BASE_CFLAGS    = -DGLQUAKE -Dstricmp=strcasecmp -I$(QUAKE_DIR) \
                       -I$(QUAKE_DIR)/platform \
                       -I$(QUAKE_DIR)/macosx-shim $(SDL_CFLAGS)
```

and replace:

```make
QW_BASE_CFLAGS           = -Wall -Dstricmp=strcasecmp -I$(QW_CLIENT_DIR) \
                           -I$(QW_SERVER_DIR)
```

with:

```make
QW_BASE_CFLAGS           = -Wall -Dstricmp=strcasecmp -I$(QW_CLIENT_DIR) \
                           -I$(QW_CLIENT_DIR)/platform -I$(QW_SERVER_DIR)
```

(Later tasks keep inserting their module dir on that second line before `-I$(QW_SERVER_DIR)`.)

- [ ] **Step 4: Add mkdir + pattern rules**

Immediately after the existing rule

```make
$(QUAKE_BUILDDIR):
	mkdir -p $(QUAKE_BUILDDIR)
```

add:

```make
$(QUAKE_BUILDDIR)/platform:
	mkdir -p $(QUAKE_BUILDDIR)/platform
```

Immediately **before** the existing catch-all rule

```make
$(QUAKE_BUILDDIR)/%.o: $(QUAKE_DIR)/%.c | $(QUAKE_BUILDDIR)
	$(CC) $(CFLAGS) -o $@ -c $<
```

add:

```make
$(QUAKE_BUILDDIR)/platform/%.o: $(QUAKE_DIR)/platform/%.c | $(QUAKE_BUILDDIR)/platform
	$(CC) $(CFLAGS) -o $@ -c $<
```

(Correction found during execution: make 3.81 picks the first matching
pattern rule whose prerequisites exist, not the shortest stem. Listing
this rule after the catch-all made the catch-all win and the build fail
with "unable to open output file" — the mkdir prereq never ran. Module
rules always precede the catch-all.)

In the glqwcl section, immediately **before** the existing catch-all
rule (same ordering constraint as above)

```make
$(QW_BUILDDIR)/client/%.o: $(QW_CLIENT_DIR)/%.c | $(QW_BUILDDIR)/client
	$(CC) $(CFLAGS) -o $@ -c $<
```

add:

```make
$(QW_BUILDDIR)/client/platform:
	mkdir -p $(QW_BUILDDIR)/client/platform

$(QW_BUILDDIR)/client/platform/%.o: $(QW_CLIENT_DIR)/platform/%.c | $(QW_BUILDDIR)/client/platform
	$(CC) $(CFLAGS) -o $@ -c $<
```

- [ ] **Step 5: Rewrite the object lists**

Replace:

```make
QUAKE_PLATFORM_OBJS = $(QUAKE_BUILDDIR)/gl_vidsdl.o \
                      $(QUAKE_BUILDDIR)/in_sdl.o $(QUAKE_BUILDDIR)/snd_sdl.o
```

with:

```make
QUAKE_PLATFORM_OBJS = $(QUAKE_BUILDDIR)/platform/gl_vidsdl.o \
                      $(QUAKE_BUILDDIR)/platform/in_sdl.o \
                      $(QUAKE_BUILDDIR)/platform/snd_sdl.o \
                      $(QUAKE_BUILDDIR)/platform/cd_null.o \
                      $(QUAKE_BUILDDIR)/platform/sys_unix.o
```

(`cd_null.o` and `sys_unix.o` leave `QUAKE_CORE_OBJS` and join `QUAKE_PLATFORM_OBJS` — the platform module owns all five platform drivers.) In `QUAKE_CORE_OBJS`, replace:

```make
	$(QUAKE_BUILDDIR)/wad.o $(QUAKE_BUILDDIR)/world.o \
	$(QUAKE_BUILDDIR)/cd_null.o $(QUAKE_BUILDDIR)/sys_unix.o \
	$(QUAKE_BUILDDIR)/snd_dma.o $(QUAKE_BUILDDIR)/snd_mem.o \
```

with:

```make
	$(QUAKE_BUILDDIR)/wad.o $(QUAKE_BUILDDIR)/world.o \
	$(QUAKE_BUILDDIR)/snd_dma.o $(QUAKE_BUILDDIR)/snd_mem.o \
```

and extend the `QUAKE_CORE_OBJS` header comment, replacing:

```make
# gl_vidsdl.c as a separate input module)
```

with:

```make
# gl_vidsdl.c as a separate input module; cd_null.o and sys_unix.o joined
# QUAKE_PLATFORM_OBJS in the module-folder restructure)
```

In `QW_CLIENT_OBJS`, replace:

```make
	$(QW_BUILDDIR)/client/cd_null.o $(QW_BUILDDIR)/client/sys_unix.o \
	$(QW_BUILDDIR)/client/snd_sdl.o \
```

with:

```make
	$(QW_BUILDDIR)/client/platform/cd_null.o \
	$(QW_BUILDDIR)/client/platform/sys_unix.o \
	$(QW_BUILDDIR)/client/platform/snd_sdl.o \
```

and replace:

```make
	$(QW_BUILDDIR)/client/gl_vidsdl.o $(QW_BUILDDIR)/client/in_sdl.o
```

with:

```make
	$(QW_BUILDDIR)/client/platform/gl_vidsdl.o \
	$(QW_BUILDDIR)/client/platform/in_sdl.o
```

- [ ] **Step 6: Delete the collision NOTE**

The NOTE exists only because `sys_unix.c` sat in both `QuakeWorld/server/` and `QuakeWorld/client/` root; that ends with this move. Replace:

```make
# all with QW_SERVER_CFLAGS. Objects land in $(QW_BUILDDIR)/server/ as in the Linux
# build.
# NOTE: the $(QW_SERVER_DIR) pattern rule below must stay first —
# QuakeWorld/client/ and QuakeWorld/server/ share filenames (model.c
# historically, sys_unix.c today), and the server variant must own
# $(QW_BUILDDIR)/server/*.o.
QW_SERVER_OBJS = \
```

with:

```make
# all with QW_SERVER_CFLAGS. Objects land in $(QW_BUILDDIR)/server/ as in the Linux
# build.
QW_SERVER_OBJS = \
```

- [ ] **Step 7: Run the gate**

Build oracle then smoke protocol (Global Constraints). Expected: build exit 0; smoke `0` × 3.

- [ ] **Step 8: Commit**

```bash
git add Makefile Quake QuakeWorld
git commit -m "Move platform module into subfolders in Quake and QuakeWorld/client"
```

---

### Task 2: sound module

**Files:**
- Move (Quake/): `snd_dma.c` `snd_mem.c` `snd_mix.c` `sound.h` → `Quake/sound/`
- Move (QuakeWorld/client/): same four files → `QuakeWorld/client/sound/`
- Modify: `Makefile`

**Interfaces:**
- Consumes: Task 1 state (`platform/` wired in; `-I` lines contain `platform`).
- Produces: `sound/` wired in both trees, same pattern.

- [ ] **Step 1: Move the files**

```bash
mkdir -p Quake/sound QuakeWorld/client/sound
git mv Quake/snd_dma.c Quake/snd_mem.c Quake/snd_mix.c Quake/sound.h Quake/sound/
git mv QuakeWorld/client/snd_dma.c QuakeWorld/client/snd_mem.c \
       QuakeWorld/client/snd_mix.c QuakeWorld/client/sound.h \
       QuakeWorld/client/sound/
```

- [ ] **Step 2: Add the include paths**

In `QUAKE_BASE_CFLAGS`, insert `-I$(QUAKE_DIR)/sound \` as a new line immediately after the `-I$(QUAKE_DIR)/platform \` line. In `QW_BASE_CFLAGS`, replace:

```make
                           -I$(QW_CLIENT_DIR)/platform -I$(QW_SERVER_DIR)
```

with:

```make
                           -I$(QW_CLIENT_DIR)/platform -I$(QW_CLIENT_DIR)/sound \
                           -I$(QW_SERVER_DIR)
```

- [ ] **Step 3: Add mkdir + pattern rules**

After `$(QUAKE_BUILDDIR)/platform:` … add:

```make
$(QUAKE_BUILDDIR)/sound:
	mkdir -p $(QUAKE_BUILDDIR)/sound
```

After the `platform` pattern rule add:

```make
$(QUAKE_BUILDDIR)/sound/%.o: $(QUAKE_DIR)/sound/%.c | $(QUAKE_BUILDDIR)/sound
	$(CC) $(CFLAGS) -o $@ -c $<
```

After the `client/platform` rules in the glqwcl section add:

```make
$(QW_BUILDDIR)/client/sound:
	mkdir -p $(QW_BUILDDIR)/client/sound

$(QW_BUILDDIR)/client/sound/%.o: $(QW_CLIENT_DIR)/sound/%.c | $(QW_BUILDDIR)/client/sound
	$(CC) $(CFLAGS) -o $@ -c $<
```

- [ ] **Step 4: Rewrite the object lists**

In `QUAKE_CORE_OBJS`, replace:

```make
	$(QUAKE_BUILDDIR)/snd_dma.o $(QUAKE_BUILDDIR)/snd_mem.o \
	$(QUAKE_BUILDDIR)/snd_mix.o
```

with:

```make
	$(QUAKE_BUILDDIR)/sound/snd_dma.o $(QUAKE_BUILDDIR)/sound/snd_mem.o \
	$(QUAKE_BUILDDIR)/sound/snd_mix.o
```

In `QW_CLIENT_OBJS`, replace:

```make
	$(QW_BUILDDIR)/client/snd_dma.o $(QW_BUILDDIR)/client/snd_mem.o \
	$(QW_BUILDDIR)/client/snd_mix.o \
```

with:

```make
	$(QW_BUILDDIR)/client/sound/snd_dma.o \
	$(QW_BUILDDIR)/client/sound/snd_mem.o \
	$(QW_BUILDDIR)/client/sound/snd_mix.o \
```

- [ ] **Step 5: Run the gate**

Build oracle then smoke protocol. Expected: build exit 0; smoke `0` × 3.

- [ ] **Step 6: Commit**

```bash
git add Makefile Quake QuakeWorld
git commit -m "Move sound module into subfolders in Quake and QuakeWorld/client"
```

---

### Task 3: net module

**Files:**
- Move (Quake/): `net_main.c` `net_dgrm.c` `net_loop.c` `net_udp.c` `net_bsd.c` `net_vcr.c` `net.h` `net_dgrm.h` `net_loop.h` `net_udp.h` `net_vcr.h` → `Quake/net/`
- Move (QuakeWorld/client/): `net_chan.c` `net_udp.c` `net.h` → `QuakeWorld/client/net/`
- Modify: `Makefile`

**Interfaces:**
- Consumes: Task 2 state.
- Produces: `net/` wired in both trees. The QW server's shared-source compile gains a dedicated rule for `QuakeWorld/client/net/` (the 11 dual-use sources split across `common/` and `net/`; the `common/` half arrives in Task 7).

- [ ] **Step 1: Move the files**

```bash
mkdir -p Quake/net QuakeWorld/client/net
git mv Quake/net_main.c Quake/net_dgrm.c Quake/net_loop.c Quake/net_udp.c \
       Quake/net_bsd.c Quake/net_vcr.c Quake/net.h Quake/net_dgrm.h \
       Quake/net_loop.h Quake/net_udp.h Quake/net_vcr.h Quake/net/
git mv QuakeWorld/client/net_chan.c QuakeWorld/client/net_udp.c \
       QuakeWorld/client/net.h QuakeWorld/client/net/
```

- [ ] **Step 2: Add the include paths**

In `QUAKE_BASE_CFLAGS`, insert `-I$(QUAKE_DIR)/net \` immediately after the `-I$(QUAKE_DIR)/sound \` line. In `QW_BASE_CFLAGS`, replace:

```make
                           -I$(QW_CLIENT_DIR)/platform -I$(QW_CLIENT_DIR)/sound \
                           -I$(QW_SERVER_DIR)
```

with:

```make
                           -I$(QW_CLIENT_DIR)/platform -I$(QW_CLIENT_DIR)/sound \
                           -I$(QW_CLIENT_DIR)/net -I$(QW_SERVER_DIR)
```

- [ ] **Step 3: Add mkdir + pattern rules**

Quake side, after the `sound` rules:

```make
$(QUAKE_BUILDDIR)/net:
	mkdir -p $(QUAKE_BUILDDIR)/net

$(QUAKE_BUILDDIR)/net/%.o: $(QUAKE_DIR)/net/%.c | $(QUAKE_BUILDDIR)/net
	$(CC) $(CFLAGS) -o $@ -c $<
```

QW client side, after the `client/sound` rules:

```make
$(QW_BUILDDIR)/client/net:
	mkdir -p $(QW_BUILDDIR)/client/net

$(QW_BUILDDIR)/client/net/%.o: $(QW_CLIENT_DIR)/net/%.c | $(QW_BUILDDIR)/client/net
	$(CC) $(CFLAGS) -o $@ -c $<
```

QW server side: in the qwsv section, immediately after the rule

```make
$(QW_BUILDDIR)/server/%.o: $(QW_SERVER_DIR)/%.c | $(QW_BUILDDIR)/server
	$(CC) $(CFLAGS) -o $@ -c $<
```

insert:

```make
$(QW_BUILDDIR)/server/%.o: $(QW_CLIENT_DIR)/net/%.c | $(QW_BUILDDIR)/server
	$(CC) $(CFLAGS) -o $@ -c $<
```

(Keep the existing `$(QW_CLIENT_DIR)/%.c` rule — it still serves the nine shared `common` sources until Task 7.)

- [ ] **Step 4: Rewrite the object lists**

In `QUAKE_CORE_OBJS`, replace:

```make
	$(QUAKE_BUILDDIR)/net_dgrm.o $(QUAKE_BUILDDIR)/net_loop.o \
	$(QUAKE_BUILDDIR)/net_main.o $(QUAKE_BUILDDIR)/net_vcr.o \
	$(QUAKE_BUILDDIR)/net_udp.o $(QUAKE_BUILDDIR)/net_bsd.o \
```

with:

```make
	$(QUAKE_BUILDDIR)/net/net_dgrm.o $(QUAKE_BUILDDIR)/net/net_loop.o \
	$(QUAKE_BUILDDIR)/net/net_main.o $(QUAKE_BUILDDIR)/net/net_vcr.o \
	$(QUAKE_BUILDDIR)/net/net_udp.o $(QUAKE_BUILDDIR)/net/net_bsd.o \
```

In `QW_CLIENT_OBJS`, replace:

```make
	$(QW_BUILDDIR)/client/net_chan.o $(QW_BUILDDIR)/client/net_udp.o \
```

with:

```make
	$(QW_BUILDDIR)/client/net/net_chan.o $(QW_BUILDDIR)/client/net/net_udp.o \
```

`QW_SERVER_OBJS` is unchanged — server objects stay flat in `build-macosx/server/`; only their source rule moved.

- [ ] **Step 5: Run the gate**

Build oracle then smoke protocol. Expected: build exit 0; smoke `0` × 3. (This task is the first real exercise of the multi-rule `server/%.o` matching — if `qwsv` fails to link, inspect which rule make chose with `make -n build-server 2>&1 | grep net_chan`.)

- [ ] **Step 6: Commit**

```bash
git add Makefile Quake QuakeWorld
git commit -m "Move net module into subfolders in Quake and QuakeWorld/client"
```

---

### Task 4: render module

**Files:**
- Move (Quake/): `gl_rmain.c` `gl_rmisc.c` `gl_rsurf.c` `gl_rlight.c` `gl_refrag.c` `gl_mesh.c` `gl_model.c` `gl_warp.c` `gl_draw.c` `gl_screen.c` `r_part.c` `glquake.h` `gl_model.h` `render.h` `r_local.h` `r_shared.h` `d_iface.h` `screen.h` `draw.h` `anorms.h` `anorm_dots.h` `gl_warp_sin.h` `bspfile.h` `spritegn.h` `modelgen.h` → `Quake/render/`
- Move (QuakeWorld/client/): `gl_draw.c` `gl_mesh.c` `gl_model.c` `gl_ngraph.c` `gl_refrag.c` `gl_rlight.c` `gl_rmain.c` `gl_rmisc.c` `gl_rsurf.c` `gl_screen.c` `gl_warp.c` `r_part.c` plus the same 14 headers → `QuakeWorld/client/render/`
- Modify: `Makefile`

**Interfaces:**
- Consumes: Task 3 state.
- Produces: `render/` wired in both trees. Largest module by file count; same pattern.

- [ ] **Step 1: Move the Quake render files**

```bash
mkdir -p Quake/render
git mv Quake/gl_rmain.c Quake/gl_rmisc.c Quake/gl_rsurf.c Quake/gl_rlight.c \
       Quake/gl_refrag.c Quake/gl_mesh.c Quake/gl_model.c Quake/gl_warp.c \
       Quake/gl_draw.c Quake/gl_screen.c Quake/r_part.c \
       Quake/glquake.h Quake/gl_model.h Quake/render.h Quake/r_local.h \
       Quake/r_shared.h Quake/d_iface.h Quake/screen.h Quake/draw.h \
       Quake/anorms.h Quake/anorm_dots.h Quake/gl_warp_sin.h Quake/bspfile.h \
       Quake/spritegn.h Quake/modelgen.h \
       Quake/render/
```

- [ ] **Step 2: Move the QuakeWorld client render files**

```bash
mkdir -p QuakeWorld/client/render
git mv QuakeWorld/client/gl_draw.c QuakeWorld/client/gl_mesh.c \
       QuakeWorld/client/gl_model.c QuakeWorld/client/gl_ngraph.c \
       QuakeWorld/client/gl_refrag.c QuakeWorld/client/gl_rlight.c \
       QuakeWorld/client/gl_rmain.c QuakeWorld/client/gl_rmisc.c \
       QuakeWorld/client/gl_rsurf.c QuakeWorld/client/gl_screen.c \
       QuakeWorld/client/gl_warp.c QuakeWorld/client/r_part.c \
       QuakeWorld/client/glquake.h QuakeWorld/client/gl_model.h \
       QuakeWorld/client/render.h QuakeWorld/client/r_local.h \
       QuakeWorld/client/r_shared.h QuakeWorld/client/d_iface.h \
       QuakeWorld/client/screen.h QuakeWorld/client/draw.h \
       QuakeWorld/client/anorms.h QuakeWorld/client/anorm_dots.h \
       QuakeWorld/client/gl_warp_sin.h QuakeWorld/client/bspfile.h \
       QuakeWorld/client/spritegn.h QuakeWorld/client/modelgen.h \
       QuakeWorld/client/render/
```

- [ ] **Step 3: Add the include paths**

In `QUAKE_BASE_CFLAGS`, insert `-I$(QUAKE_DIR)/render \` immediately after the `-I$(QUAKE_DIR)/net \` line. In `QW_BASE_CFLAGS`, replace:

```make
                           -I$(QW_CLIENT_DIR)/net -I$(QW_SERVER_DIR)
```

with:

```make
                           -I$(QW_CLIENT_DIR)/net -I$(QW_CLIENT_DIR)/render \
                           -I$(QW_SERVER_DIR)
```

- [ ] **Step 4: Add mkdir + pattern rules**

Quake side, after the `net` rules:

```make
$(QUAKE_BUILDDIR)/render:
	mkdir -p $(QUAKE_BUILDDIR)/render

$(QUAKE_BUILDDIR)/render/%.o: $(QUAKE_DIR)/render/%.c | $(QUAKE_BUILDDIR)/render
	$(CC) $(CFLAGS) -o $@ -c $<
```

QW client side, after the `client/net` rules:

```make
$(QW_BUILDDIR)/client/render:
	mkdir -p $(QW_BUILDDIR)/client/render

$(QW_BUILDDIR)/client/render/%.o: $(QW_CLIENT_DIR)/render/%.c | $(QW_BUILDDIR)/client/render
	$(CC) $(CFLAGS) -o $@ -c $<
```

- [ ] **Step 5: Rewrite the object lists**

In `QUAKE_CORE_OBJS`, replace:

```make
	$(QUAKE_BUILDDIR)/gl_draw.o $(QUAKE_BUILDDIR)/gl_mesh.o \
	$(QUAKE_BUILDDIR)/gl_model.o $(QUAKE_BUILDDIR)/gl_refrag.o \
	$(QUAKE_BUILDDIR)/gl_rlight.o $(QUAKE_BUILDDIR)/gl_rmain.o \
	$(QUAKE_BUILDDIR)/gl_rmisc.o $(QUAKE_BUILDDIR)/gl_rsurf.o \
	$(QUAKE_BUILDDIR)/gl_screen.o $(QUAKE_BUILDDIR)/gl_warp.o \
```

with:

```make
	$(QUAKE_BUILDDIR)/render/gl_draw.o $(QUAKE_BUILDDIR)/render/gl_mesh.o \
	$(QUAKE_BUILDDIR)/render/gl_model.o $(QUAKE_BUILDDIR)/render/gl_refrag.o \
	$(QUAKE_BUILDDIR)/render/gl_rlight.o $(QUAKE_BUILDDIR)/render/gl_rmain.o \
	$(QUAKE_BUILDDIR)/render/gl_rmisc.o $(QUAKE_BUILDDIR)/render/gl_rsurf.o \
	$(QUAKE_BUILDDIR)/render/gl_screen.o $(QUAKE_BUILDDIR)/render/gl_warp.o \
```

and replace:

```make
	$(QUAKE_BUILDDIR)/r_part.o $(QUAKE_BUILDDIR)/sbar.o \
```

with:

```make
	$(QUAKE_BUILDDIR)/render/r_part.o $(QUAKE_BUILDDIR)/sbar.o \
```

In `QW_CLIENT_OBJS`, replace:

```make
	$(QW_BUILDDIR)/client/r_part.o \
```

with:

```make
	$(QW_BUILDDIR)/client/render/r_part.o \
```

and replace:

```make
	$(QW_BUILDDIR)/client/gl_draw.o $(QW_BUILDDIR)/client/gl_mesh.o \
	$(QW_BUILDDIR)/client/gl_model.o $(QW_BUILDDIR)/client/gl_ngraph.o \
	$(QW_BUILDDIR)/client/gl_refrag.o $(QW_BUILDDIR)/client/gl_rlight.o \
	$(QW_BUILDDIR)/client/gl_rmain.o $(QW_BUILDDIR)/client/gl_rmisc.o \
	$(QW_BUILDDIR)/client/gl_rsurf.o $(QW_BUILDDIR)/client/gl_screen.o \
	$(QW_BUILDDIR)/client/gl_warp.o \
```

with:

```make
	$(QW_BUILDDIR)/client/render/gl_draw.o $(QW_BUILDDIR)/client/render/gl_mesh.o \
	$(QW_BUILDDIR)/client/render/gl_model.o $(QW_BUILDDIR)/client/render/gl_ngraph.o \
	$(QW_BUILDDIR)/client/render/gl_refrag.o $(QW_BUILDDIR)/client/render/gl_rlight.o \
	$(QW_BUILDDIR)/client/render/gl_rmain.o $(QW_BUILDDIR)/client/render/gl_rmisc.o \
	$(QW_BUILDDIR)/client/render/gl_rsurf.o $(QW_BUILDDIR)/client/render/gl_screen.o \
	$(QW_BUILDDIR)/client/render/gl_warp.o \
```

- [ ] **Step 6: Run the gate**

Build oracle then smoke protocol. Expected: build exit 0; smoke `0` × 3.

- [ ] **Step 7: Commit**

```bash
git add Makefile Quake QuakeWorld
git commit -m "Move render module into subfolders in Quake and QuakeWorld/client"
```

---

### Task 5: client module

**Files:**
- Move (Quake/): `cl_main.c` `cl_demo.c` `cl_input.c` `cl_parse.c` `cl_tent.c` `chase.c` `view.c` `menu.c` `keys.c` `console.c` `sbar.c` `client.h` `view.h` `menu.h` `keys.h` `console.h` `sbar.h` → `Quake/client/`
- Move (QuakeWorld/client/): `cl_main.c` `cl_demo.c` `cl_ents.c` `cl_input.c` `cl_parse.c` `cl_pred.c` `cl_tent.c` `cl_cam.c` `view.c` `menu.c` `keys.c` `console.c` `sbar.c` `skin.c` `client.h` `view.h` `menu.h` `keys.h` `console.h` `sbar.h` → `QuakeWorld/client/client/`
- Modify: `Makefile`

**Interfaces:**
- Consumes: Task 4 state.
- Produces: `client/` wired in both trees. Note the nested path `QuakeWorld/client/client/` — the client module inside the client tree; make handles it fine because object paths mirror source paths.

- [ ] **Step 1: Move the Quake client files**

```bash
mkdir -p Quake/client
git mv Quake/cl_main.c Quake/cl_demo.c Quake/cl_input.c Quake/cl_parse.c \
       Quake/cl_tent.c Quake/chase.c Quake/view.c Quake/menu.c Quake/keys.c \
       Quake/console.c Quake/sbar.c \
       Quake/client.h Quake/view.h Quake/menu.h Quake/keys.h Quake/console.h \
       Quake/sbar.h \
       Quake/client/
```

- [ ] **Step 2: Move the QuakeWorld client-module files**

```bash
mkdir -p QuakeWorld/client/client
git mv QuakeWorld/client/cl_main.c QuakeWorld/client/cl_demo.c \
       QuakeWorld/client/cl_ents.c QuakeWorld/client/cl_input.c \
       QuakeWorld/client/cl_parse.c QuakeWorld/client/cl_pred.c \
       QuakeWorld/client/cl_tent.c QuakeWorld/client/cl_cam.c \
       QuakeWorld/client/view.c QuakeWorld/client/menu.c QuakeWorld/client/keys.c \
       QuakeWorld/client/console.c QuakeWorld/client/sbar.c QuakeWorld/client/skin.c \
       QuakeWorld/client/client.h QuakeWorld/client/view.h QuakeWorld/client/menu.h \
       QuakeWorld/client/keys.h QuakeWorld/client/console.h QuakeWorld/client/sbar.h \
       QuakeWorld/client/client/
```

- [ ] **Step 3: Add the include paths**

In `QUAKE_BASE_CFLAGS`, insert `-I$(QUAKE_DIR)/client \` immediately after the `-I$(QUAKE_DIR)/render \` line. In `QW_BASE_CFLAGS`, replace:

```make
                           -I$(QW_CLIENT_DIR)/net -I$(QW_CLIENT_DIR)/render \
                           -I$(QW_SERVER_DIR)
```

with:

```make
                           -I$(QW_CLIENT_DIR)/net -I$(QW_CLIENT_DIR)/render \
                           -I$(QW_CLIENT_DIR)/client -I$(QW_SERVER_DIR)
```

- [ ] **Step 4: Add mkdir + pattern rules**

Quake side, after the `render` rules:

```make
$(QUAKE_BUILDDIR)/client:
	mkdir -p $(QUAKE_BUILDDIR)/client

$(QUAKE_BUILDDIR)/client/%.o: $(QUAKE_DIR)/client/%.c | $(QUAKE_BUILDDIR)/client
	$(CC) $(CFLAGS) -o $@ -c $<
```

QW client side, after the `client/render` rules:

```make
$(QW_BUILDDIR)/client/client:
	mkdir -p $(QW_BUILDDIR)/client/client

$(QW_BUILDDIR)/client/client/%.o: $(QW_CLIENT_DIR)/client/%.c | $(QW_BUILDDIR)/client/client
	$(CC) $(CFLAGS) -o $@ -c $<
```

- [ ] **Step 5: Rewrite the object lists**

In `QUAKE_CORE_OBJS`, apply these five replacements:

```make
	$(QUAKE_BUILDDIR)/cl_demo.o $(QUAKE_BUILDDIR)/cl_input.o \
	$(QUAKE_BUILDDIR)/cl_main.o $(QUAKE_BUILDDIR)/cl_parse.o \
	$(QUAKE_BUILDDIR)/cl_tent.o $(QUAKE_BUILDDIR)/chase.o \
```

→

```make
	$(QUAKE_BUILDDIR)/client/cl_demo.o $(QUAKE_BUILDDIR)/client/cl_input.o \
	$(QUAKE_BUILDDIR)/client/cl_main.o $(QUAKE_BUILDDIR)/client/cl_parse.o \
	$(QUAKE_BUILDDIR)/client/cl_tent.o $(QUAKE_BUILDDIR)/client/chase.o \
```

```make
	$(QUAKE_BUILDDIR)/console.o $(QUAKE_BUILDDIR)/crc.o \
```

→

```make
	$(QUAKE_BUILDDIR)/client/console.o $(QUAKE_BUILDDIR)/crc.o \
```

```make
	$(QUAKE_BUILDDIR)/keys.o $(QUAKE_BUILDDIR)/menu.o \
```

→

```make
	$(QUAKE_BUILDDIR)/client/keys.o $(QUAKE_BUILDDIR)/client/menu.o \
```

```make
	$(QUAKE_BUILDDIR)/render/r_part.o $(QUAKE_BUILDDIR)/sbar.o \
```

→

```make
	$(QUAKE_BUILDDIR)/render/r_part.o $(QUAKE_BUILDDIR)/client/sbar.o \
```

```make
	$(QUAKE_BUILDDIR)/zone.o $(QUAKE_BUILDDIR)/view.o \
```

→

```make
	$(QUAKE_BUILDDIR)/zone.o $(QUAKE_BUILDDIR)/client/view.o \
```

In `QW_CLIENT_OBJS`, apply these six replacements:

```make
	$(QW_BUILDDIR)/client/cl_demo.o $(QW_BUILDDIR)/client/cl_ents.o \
	$(QW_BUILDDIR)/client/cl_input.o $(QW_BUILDDIR)/client/cl_main.o \
	$(QW_BUILDDIR)/client/cl_parse.o $(QW_BUILDDIR)/client/cl_pred.o \
	$(QW_BUILDDIR)/client/cl_tent.o $(QW_BUILDDIR)/client/cl_cam.o \
```

→

```make
	$(QW_BUILDDIR)/client/client/cl_demo.o $(QW_BUILDDIR)/client/client/cl_ents.o \
	$(QW_BUILDDIR)/client/client/cl_input.o $(QW_BUILDDIR)/client/client/cl_main.o \
	$(QW_BUILDDIR)/client/client/cl_parse.o $(QW_BUILDDIR)/client/client/cl_pred.o \
	$(QW_BUILDDIR)/client/client/cl_tent.o $(QW_BUILDDIR)/client/client/cl_cam.o \
```

```make
	$(QW_BUILDDIR)/client/console.o $(QW_BUILDDIR)/client/crc.o \
```

→

```make
	$(QW_BUILDDIR)/client/client/console.o $(QW_BUILDDIR)/client/crc.o \
```

```make
	$(QW_BUILDDIR)/client/keys.o $(QW_BUILDDIR)/client/mathlib.o \
```

→

```make
	$(QW_BUILDDIR)/client/client/keys.o $(QW_BUILDDIR)/client/mathlib.o \
```

```make
	$(QW_BUILDDIR)/client/md4.o $(QW_BUILDDIR)/client/menu.o \
```

→

```make
	$(QW_BUILDDIR)/client/md4.o $(QW_BUILDDIR)/client/client/menu.o \
```

```make
	$(QW_BUILDDIR)/client/sbar.o $(QW_BUILDDIR)/client/skin.o \
```

→

```make
	$(QW_BUILDDIR)/client/client/sbar.o $(QW_BUILDDIR)/client/client/skin.o \
```

```make
	$(QW_BUILDDIR)/client/view.o $(QW_BUILDDIR)/client/wad.o \
```

→

```make
	$(QW_BUILDDIR)/client/client/view.o $(QW_BUILDDIR)/client/wad.o \
```

- [ ] **Step 6: Run the gate**

Build oracle then smoke protocol. Expected: build exit 0; smoke `0` × 3.

- [ ] **Step 7: Commit**

```bash
git add Makefile Quake QuakeWorld
git commit -m "Move client module into subfolders in Quake and QuakeWorld/client"
```

---

### Task 6: server module (Quake/ only)

**Files:**
- Move (Quake/): `sv_main.c` `sv_phys.c` `sv_move.c` `sv_user.c` `world.c` `pr_cmds.c` `pr_edict.c` `pr_exec.c` `server.h` `world.h` `progs.h` `progdefs.h` `pr_comp.h` → `Quake/server/`
- Modify: `Makefile`

**Interfaces:**
- Consumes: Task 5 state.
- Produces: `Quake/server/` wired. `QuakeWorld/server/` is untouched (already a module). After this task `Quake/` root holds only `host.c` and `host_cmd.c` plus the module dirs.

- [ ] **Step 1: Move the files**

```bash
mkdir -p Quake/server
git mv Quake/sv_main.c Quake/sv_phys.c Quake/sv_move.c Quake/sv_user.c \
       Quake/world.c Quake/pr_cmds.c Quake/pr_edict.c Quake/pr_exec.c \
       Quake/server.h Quake/world.h Quake/progs.h Quake/progdefs.h \
       Quake/pr_comp.h \
       Quake/server/
```

- [ ] **Step 2: Add the include path**

In `QUAKE_BASE_CFLAGS`, insert `-I$(QUAKE_DIR)/server \` immediately after the `-I$(QUAKE_DIR)/client \` line. (`QW_BASE_CFLAGS` is unchanged — it already has `-I$(QW_SERVER_DIR)`.)

- [ ] **Step 3: Add mkdir + pattern rules**

Quake side, after the `client` rules:

```make
$(QUAKE_BUILDDIR)/server:
	mkdir -p $(QUAKE_BUILDDIR)/server

$(QUAKE_BUILDDIR)/server/%.o: $(QUAKE_DIR)/server/%.c | $(QUAKE_BUILDDIR)/server
	$(CC) $(CFLAGS) -o $@ -c $<
```

- [ ] **Step 4: Rewrite the object lists**

In `QUAKE_CORE_OBJS`, apply these three replacements:

```make
	$(QUAKE_BUILDDIR)/pr_cmds.o $(QUAKE_BUILDDIR)/pr_edict.o \
	$(QUAKE_BUILDDIR)/pr_exec.o \
```

→

```make
	$(QUAKE_BUILDDIR)/server/pr_cmds.o $(QUAKE_BUILDDIR)/server/pr_edict.o \
	$(QUAKE_BUILDDIR)/server/pr_exec.o \
```

```make
	$(QUAKE_BUILDDIR)/sv_main.o $(QUAKE_BUILDDIR)/sv_phys.o \
	$(QUAKE_BUILDDIR)/sv_move.o $(QUAKE_BUILDDIR)/sv_user.o \
```

→

```make
	$(QUAKE_BUILDDIR)/server/sv_main.o $(QUAKE_BUILDDIR)/server/sv_phys.o \
	$(QUAKE_BUILDDIR)/server/sv_move.o $(QUAKE_BUILDDIR)/server/sv_user.o \
```

```make
	$(QUAKE_BUILDDIR)/wad.o $(QUAKE_BUILDDIR)/world.o \
```

→

```make
	$(QUAKE_BUILDDIR)/wad.o $(QUAKE_BUILDDIR)/server/world.o \
```

- [ ] **Step 5: Run the gate**

Build oracle then smoke protocol. Expected: build exit 0; smoke `0` × 3.

- [ ] **Step 6: Commit**

```bash
git add Makefile Quake QuakeWorld
git commit -m "Move Quake server module into its subfolder"
```

---

### Task 7: common module (last — carries quakedef.h)

**Files:**
- Move (Quake/): `common.c` `cmd.c` `cvar.c` `crc.c` `mathlib.c` `zone.c` `wad.c` `quakedef.h` `common.h` `cmd.h` `cvar.h` `crc.h` `mathlib.h` `zone.h` `wad.h` `protocol.h` `model.h` → `Quake/common/`
- Move (QuakeWorld/client/): `common.c` `cmd.c` `cvar.c` `crc.c` `mathlib.c` `md4.c` `zone.c` `wad.c` `pmove.c` `pmovetst.c` `quakedef.h` `bothdefs.h` `common.h` `cmd.h` `cvar.h` `crc.h` `mathlib.h` `zone.h` `wad.h` `pmove.h` `protocol.h` `model.h` → `QuakeWorld/client/common/`
- Modify: `Makefile`

**Interfaces:**
- Consumes: Task 6 state. The QW server's `$(QW_CLIENT_DIR)/%.c` rule still serves the nine shared sources from the `QuakeWorld/client/` root.
- Produces: `common/` wired in both trees; the server's shared-source rule repointed at `client/common/`; the `QuakeWorld/client/` root empty. After this commit every source in both trees lives in a module dir except `Quake/host.c` and `Quake/host_cmd.c`.

- [ ] **Step 1: Move the Quake common files**

```bash
mkdir -p Quake/common
git mv Quake/common.c Quake/cmd.c Quake/cvar.c Quake/crc.c Quake/mathlib.c \
       Quake/zone.c Quake/wad.c \
       Quake/quakedef.h Quake/common.h Quake/cmd.h Quake/cvar.h Quake/crc.h \
       Quake/mathlib.h Quake/zone.h Quake/wad.h Quake/protocol.h Quake/model.h \
       Quake/common/
```

- [ ] **Step 2: Move the QuakeWorld client common files**

```bash
mkdir -p QuakeWorld/client/common
git mv QuakeWorld/client/common.c QuakeWorld/client/cmd.c QuakeWorld/client/cvar.c \
       QuakeWorld/client/crc.c QuakeWorld/client/mathlib.c QuakeWorld/client/md4.c \
       QuakeWorld/client/zone.c QuakeWorld/client/wad.c QuakeWorld/client/pmove.c \
       QuakeWorld/client/pmovetst.c \
       QuakeWorld/client/quakedef.h QuakeWorld/client/bothdefs.h \
       QuakeWorld/client/common.h QuakeWorld/client/cmd.h QuakeWorld/client/cvar.h \
       QuakeWorld/client/crc.h QuakeWorld/client/mathlib.h QuakeWorld/client/zone.h \
       QuakeWorld/client/wad.h QuakeWorld/client/pmove.h QuakeWorld/client/protocol.h \
       QuakeWorld/client/model.h \
       QuakeWorld/client/common/
```

- [ ] **Step 3: Add the include paths**

In `QUAKE_BASE_CFLAGS`, insert `-I$(QUAKE_DIR)/common \` immediately after the `-I$(QUAKE_DIR)/server \` line. In `QW_BASE_CFLAGS`, replace:

```make
                           -I$(QW_CLIENT_DIR)/client -I$(QW_SERVER_DIR)
```

with:

```make
                           -I$(QW_CLIENT_DIR)/client -I$(QW_CLIENT_DIR)/common \
                           -I$(QW_SERVER_DIR)
```

- [ ] **Step 4: Add mkdir + pattern rules; repoint the server shared-source rule**

Quake side, after the `server` rules:

```make
$(QUAKE_BUILDDIR)/common:
	mkdir -p $(QUAKE_BUILDDIR)/common

$(QUAKE_BUILDDIR)/common/%.o: $(QUAKE_DIR)/common/%.c | $(QUAKE_BUILDDIR)/common
	$(CC) $(CFLAGS) -o $@ -c $<
```

QW client side, after the `client/client` rules:

```make
$(QW_BUILDDIR)/client/common:
	mkdir -p $(QW_BUILDDIR)/client/common

$(QW_BUILDDIR)/client/common/%.o: $(QW_CLIENT_DIR)/common/%.c | $(QW_BUILDDIR)/client/common
	$(CC) $(CFLAGS) -o $@ -c $<
```

QW server side: replace the now-dead root rule

```make
$(QW_BUILDDIR)/server/%.o: $(QW_CLIENT_DIR)/%.c | $(QW_BUILDDIR)/server
	$(CC) $(CFLAGS) -o $@ -c $<
```

with:

```make
$(QW_BUILDDIR)/server/%.o: $(QW_CLIENT_DIR)/common/%.c | $(QW_BUILDDIR)/server
	$(CC) $(CFLAGS) -o $@ -c $<
```

After this edit the qwsv section has exactly three pattern rules: `$(QW_SERVER_DIR)/%.c`, `$(QW_CLIENT_DIR)/net/%.c`, `$(QW_CLIENT_DIR)/common/%.c`.

- [ ] **Step 5: Rewrite the object lists**

In `QUAKE_CORE_OBJS`, apply these six replacements:

```make
	$(QUAKE_BUILDDIR)/cmd.o $(QUAKE_BUILDDIR)/common.o \
```

→

```make
	$(QUAKE_BUILDDIR)/common/cmd.o $(QUAKE_BUILDDIR)/common/common.o \
```

```make
	$(QUAKE_BUILDDIR)/client/console.o $(QUAKE_BUILDDIR)/crc.o \
```

→

```make
	$(QUAKE_BUILDDIR)/client/console.o $(QUAKE_BUILDDIR)/common/crc.o \
```

```make
	$(QUAKE_BUILDDIR)/cvar.o \
```

→

```make
	$(QUAKE_BUILDDIR)/common/cvar.o \
```

```make
	$(QUAKE_BUILDDIR)/mathlib.o \
```

→

```make
	$(QUAKE_BUILDDIR)/common/mathlib.o \
```

```make
	$(QUAKE_BUILDDIR)/zone.o $(QUAKE_BUILDDIR)/client/view.o \
```

→

```make
	$(QUAKE_BUILDDIR)/common/zone.o $(QUAKE_BUILDDIR)/client/view.o \
```

```make
	$(QUAKE_BUILDDIR)/wad.o $(QUAKE_BUILDDIR)/server/world.o \
```

→

```make
	$(QUAKE_BUILDDIR)/common/wad.o $(QUAKE_BUILDDIR)/server/world.o \
```

In `QW_CLIENT_OBJS`, apply these seven replacements:

```make
	$(QW_BUILDDIR)/client/cmd.o $(QW_BUILDDIR)/client/common.o \
```

→

```make
	$(QW_BUILDDIR)/client/common/cmd.o $(QW_BUILDDIR)/client/common/common.o \
```

```make
	$(QW_BUILDDIR)/client/client/console.o $(QW_BUILDDIR)/client/crc.o \
```

→

```make
	$(QW_BUILDDIR)/client/client/console.o $(QW_BUILDDIR)/client/common/crc.o \
```

```make
	$(QW_BUILDDIR)/client/cvar.o \
```

→

```make
	$(QW_BUILDDIR)/client/common/cvar.o \
```

```make
	$(QW_BUILDDIR)/client/client/keys.o $(QW_BUILDDIR)/client/mathlib.o \
```

→

```make
	$(QW_BUILDDIR)/client/client/keys.o $(QW_BUILDDIR)/client/common/mathlib.o \
```

```make
	$(QW_BUILDDIR)/client/md4.o $(QW_BUILDDIR)/client/client/menu.o \
```

→

```make
	$(QW_BUILDDIR)/client/common/md4.o $(QW_BUILDDIR)/client/client/menu.o \
```

```make
	$(QW_BUILDDIR)/client/pmove.o $(QW_BUILDDIR)/client/pmovetst.o \
```

→

```make
	$(QW_BUILDDIR)/client/common/pmove.o $(QW_BUILDDIR)/client/common/pmovetst.o \
```

```make
	$(QW_BUILDDIR)/client/client/view.o $(QW_BUILDDIR)/client/wad.o \
	$(QW_BUILDDIR)/client/zone.o \
```

→

```make
	$(QW_BUILDDIR)/client/client/view.o $(QW_BUILDDIR)/client/common/wad.o \
	$(QW_BUILDDIR)/client/common/zone.o \
```

- [ ] **Step 6: Verify the final shape**

```bash
ls Quake/*.c Quake/*.h
```

Expected: exactly `Quake/host.c` and `Quake/host_cmd.c` (the glob prints those two and nothing else).

```bash
ls QuakeWorld/client/*.c QuakeWorld/client/*.h
```

Expected: both globs report "No such file or directory" — the client root is empty; everything lives in the six module dirs.

- [ ] **Step 7: Run the gate**

Build oracle then smoke protocol. Expected: build exit 0; smoke `0` × 3.

- [ ] **Step 8: Commit**

```bash
git add Makefile Quake QuakeWorld
git commit -m "Move common module into subfolders in Quake and QuakeWorld/client"
```

---

### Task 8: Documentation (CONTEXT.md + Fixes Ledger)

**Files:**
- Modify: `CONTEXT.md`
- Modify: `docs/superpowers/plans/2026-08-29-quake-apple-silicon.md` (append-only ledger)

**Interfaces:**
- Consumes: all seven module commits landed and gated.
- Produces: the domain vocabulary updated to the new layout; the ledger entry recording the restructure.

- [ ] **Step 1: Update CONTEXT.md**

Replace the **Platform modules** bullet with folder-qualified paths, and add a **Modules** entry before it. The new text for those two bullets:

```markdown
- **Modules** — every source lives in one subfolder per module:
  `common/` (core services + the shared header pool: `quakedef.h`,
  `protocol.h`, `model.h`, …), `client/` (CL_* plus menu, keys, console,
  sbar, view), `render/` (gl_*, r_part), `server/` (sv_*, pr_*, world —
  Quake/ only; QuakeWorld's is `QuakeWorld/server/`), `net/`, `sound/`,
  `platform/`. Headers live with their module; a header included by two
  or more modules lives in `common/`. `Quake/host.c` and `host_cmd.c`
  stay at the tree root — Host orchestrates every module.
- **Platform modules** — the SDL3 layer, per GL client, in each tree's
  `platform/` subdir: the **video module** (`gl_vidsdl.c` — window/GL
  lifecycle), the **input module** (`in_sdl.c` — IN_* interface plus the
  SDL event pump), the **sound driver** (`snd_sdl.c` — SNDDMA_*), and
  the **null CD adapter** (`cd_null.c`); the **platform bootstrap**
  (`sys_unix.c` — main, clock, the Sys_* family) sits there too, one
  copy per binary, deliberately separate.
```

- [ ] **Step 2: Append the ledger entry**

Append to the end of `docs/superpowers/plans/2026-08-29-quake-apple-silicon.md` (fill in the actual hash range from `git log --oneline` — the seven module commits are the most recent seven):

```markdown
## Module folder restructure: one subfolder per module

Commits: <oldest-module-hash>..<newest-module-hash> (seven module
commits, platform → sound → net → render → client → server → common).

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

Spec: docs/superpowers/specs/2026-08-30-module-folder-restructure-design.md
Gate: every module commit passed make clean && make build-release
build-server build-client plus the 3-binary SIGKILL smoke protocol
("Received signal" count 0 for glquake, qwsv, glqwcl).
```

- [ ] **Step 3: Commit**

```bash
git add CONTEXT.md docs/superpowers/plans/2026-08-29-quake-apple-silicon.md
git commit -m "Docs: module vocabulary in CONTEXT.md; ledger entry for the restructure"
```

- [ ] **Step 4: Final verification and report**

```bash
git status -sb
git log --oneline -9
```

Expected: clean tree, nine new commits (7 modules + docs; the spec commit predates Task 1). Report the commit range to the user. The binaries are ready for a playtest — that is the final gate, and it belongs to the user.
