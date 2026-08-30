# Makefile — Quake (glquake) and QuakeWorld (qwsv, glqwcl) for Apple
# Silicon (macOS arm64). Consolidates the former WinQuake/Makefile and
# QW/Makefile at the repo root; the trees were renamed WinQuake → Quake
# and QW → QuakeWorld in the same change.
#
# glquake: object list cloned from Makefile.linuxi386 (GLQUAKE_OBJS) minus
# the four x86 asm objects; Linux vid/snd/cd drivers replaced by
# gl_vidsdl.c / snd_sdl.c / cd_null.c.
# qwsv: object list cloned from Makefile.Linux (QWSV_OBJS) — server files
# compile from QuakeWorld/server/, shared files from QuakeWorld/client/,
# all with -DSERVERONLY. Headless: no SDL, no OpenGL.
# glqwcl: object list cloned from Makefile.Linux GLQWCL_OBJS + the
# glqwcl.glx vid object; every object gets -DGLQUAKE (as DO_GL_CC did);
# asm objects (math/snd_mixa/sys_dosa) omitted for arm64;
# cd_linux->cd_null, snd_linux->snd_sdl, gl_vidlinuxglx->gl_vidsdl.
# Spec: docs/superpowers/specs/2026-08-29-quake-apple-silicon-design.md

SERVICE = Quake + QuakeWorld (macOS arm64)

# Variables
CC             = cc
QUAKE_DIR      = Quake
QW_DIR         = QuakeWorld
QUAKE_BUILDDIR = $(QUAKE_DIR)/build-macosx
QW_BUILDDIR    = $(QW_DIR)/build-macosx
QW_CLIENT_DIR  = $(QW_DIR)/client
QW_SERVER_DIR  = $(QW_DIR)/server
GAMEDIR       ?= $(CURDIR)/game
SDL_CFLAGS     = $(shell pkg-config sdl3 --cflags)
SDL_LIBS       = $(shell pkg-config sdl3 --libs)
GL_LIBS        = -framework OpenGL

# glquake (single-player)
QUAKE_BASE_CFLAGS    = -DGLQUAKE -Dstricmp=strcasecmp -I$(QUAKE_DIR) \
                       -I$(QUAKE_DIR)/macosx-shim $(SDL_CFLAGS)
QUAKE_RELEASE_CFLAGS = $(QUAKE_BASE_CFLAGS) -O2 -ffast-math
QUAKE_DEBUG_CFLAGS   = $(QUAKE_BASE_CFLAGS) -g -O0
QUAKE_LDFLAGS        = $(SDL_LIBS) $(GL_LIBS) -lm

# qwsv (dedicated server): server files compile from QuakeWorld/server/,
# shared files from QuakeWorld/client/, all with -DSERVERONLY. Headless:
# no SDL, no OpenGL.
QW_BASE_CFLAGS           = -Wall -Dstricmp=strcasecmp -I$(QW_CLIENT_DIR) \
                           -I$(QW_SERVER_DIR)
QW_SERVER_CFLAGS         = $(QW_BASE_CFLAGS) -DSERVERONLY
QW_SERVER_RELEASE_CFLAGS = $(QW_SERVER_CFLAGS) -O2 -ffast-math
QW_SERVER_DEBUG_CFLAGS   = $(QW_SERVER_CFLAGS) -g -O0
QW_SERVER_LDFLAGS        = -lm

# glqwcl (GL client): Makefile.Linux compiles every glclient object with
# $(CFLAGS) $(GLCFLAGS) (GLCFLAGS = -DGLQUAKE ...), so -DGLQUAKE applies to
# the whole client, not just the gl_*.c files. GL/gl.h + GL/glu.h come from
# the shared Phase-1 shim (reused, not duplicated).
QW_CLIENT_CFLAGS         = $(QW_BASE_CFLAGS) -DGLQUAKE \
                           -I$(QUAKE_DIR)/macosx-shim $(SDL_CFLAGS)
QW_CLIENT_RELEASE_CFLAGS = $(QW_CLIENT_CFLAGS) -O2 -ffast-math
QW_CLIENT_LDFLAGS        = $(SDL_LIBS) $(GL_LIBS) -lm

# ── glquake objects ──────────────────────────────────────────────────────────
# Engine core (Makefile.linuxi386 GLQUAKE_OBJS minus asm objects math/worlda/
# snd_mixa/sys_dosa; cd_linux→cd_null; snd_linux & gl_vidlinuxglx moved to
# QUAKE_PLATFORM_OBJS as their SDL3 replacements)
QUAKE_CORE_OBJS = \
	$(QUAKE_BUILDDIR)/cl_demo.o $(QUAKE_BUILDDIR)/cl_input.o \
	$(QUAKE_BUILDDIR)/cl_main.o $(QUAKE_BUILDDIR)/cl_parse.o \
	$(QUAKE_BUILDDIR)/cl_tent.o $(QUAKE_BUILDDIR)/chase.o \
	$(QUAKE_BUILDDIR)/cmd.o $(QUAKE_BUILDDIR)/common.o \
	$(QUAKE_BUILDDIR)/console.o $(QUAKE_BUILDDIR)/crc.o \
	$(QUAKE_BUILDDIR)/cvar.o \
	$(QUAKE_BUILDDIR)/gl_draw.o $(QUAKE_BUILDDIR)/gl_mesh.o \
	$(QUAKE_BUILDDIR)/gl_model.o $(QUAKE_BUILDDIR)/gl_refrag.o \
	$(QUAKE_BUILDDIR)/gl_rlight.o $(QUAKE_BUILDDIR)/gl_rmain.o \
	$(QUAKE_BUILDDIR)/gl_rmisc.o $(QUAKE_BUILDDIR)/gl_rsurf.o \
	$(QUAKE_BUILDDIR)/gl_screen.o $(QUAKE_BUILDDIR)/gl_test.o \
	$(QUAKE_BUILDDIR)/gl_warp.o \
	$(QUAKE_BUILDDIR)/host.o $(QUAKE_BUILDDIR)/host_cmd.o \
	$(QUAKE_BUILDDIR)/keys.o $(QUAKE_BUILDDIR)/menu.o \
	$(QUAKE_BUILDDIR)/mathlib.o \
	$(QUAKE_BUILDDIR)/net_dgrm.o $(QUAKE_BUILDDIR)/net_loop.o \
	$(QUAKE_BUILDDIR)/net_main.o $(QUAKE_BUILDDIR)/net_vcr.o \
	$(QUAKE_BUILDDIR)/net_udp.o $(QUAKE_BUILDDIR)/net_bsd.o \
	$(QUAKE_BUILDDIR)/pr_cmds.o $(QUAKE_BUILDDIR)/pr_edict.o \
	$(QUAKE_BUILDDIR)/pr_exec.o \
	$(QUAKE_BUILDDIR)/r_part.o $(QUAKE_BUILDDIR)/sbar.o \
	$(QUAKE_BUILDDIR)/sv_main.o $(QUAKE_BUILDDIR)/sv_phys.o \
	$(QUAKE_BUILDDIR)/sv_move.o $(QUAKE_BUILDDIR)/sv_user.o \
	$(QUAKE_BUILDDIR)/zone.o $(QUAKE_BUILDDIR)/view.o \
	$(QUAKE_BUILDDIR)/wad.o $(QUAKE_BUILDDIR)/world.o \
	$(QUAKE_BUILDDIR)/cd_null.o $(QUAKE_BUILDDIR)/sys_unix.o \
	$(QUAKE_BUILDDIR)/snd_dma.o $(QUAKE_BUILDDIR)/snd_mem.o \
	$(QUAKE_BUILDDIR)/snd_mix.o

QUAKE_PLATFORM_OBJS = $(QUAKE_BUILDDIR)/gl_vidsdl.o $(QUAKE_BUILDDIR)/snd_sdl.o

QUAKE_OBJS = $(QUAKE_CORE_OBJS) $(QUAKE_PLATFORM_OBJS)

# ── QuakeWorld objects ───────────────────────────────────────────────────────
# Server objects (Makefile.Linux QWSV_OBJS, verbatim): the first 15 compile
# from $(QW_SERVER_DIR), the last 11 from $(QW_CLIENT_DIR), all with
# QW_SERVER_CFLAGS. Objects land in $(QW_BUILDDIR)/server/ as in the Linux
# build.
# NOTE: the $(QW_SERVER_DIR) pattern rule below must stay first —
# QuakeWorld/client/ and QuakeWorld/server/ share filenames (model.c
# historically, sys_unix.c today), and the server variant must own
# $(QW_BUILDDIR)/server/*.o.
QW_SERVER_OBJS = \
	$(QW_BUILDDIR)/server/pr_cmds.o $(QW_BUILDDIR)/server/pr_edict.o \
	$(QW_BUILDDIR)/server/pr_exec.o $(QW_BUILDDIR)/server/sv_init.o \
	$(QW_BUILDDIR)/server/sv_main.o $(QW_BUILDDIR)/server/sv_nchan.o \
	$(QW_BUILDDIR)/server/sv_ents.o $(QW_BUILDDIR)/server/sv_send.o \
	$(QW_BUILDDIR)/server/sv_move.o $(QW_BUILDDIR)/server/sv_phys.o \
	$(QW_BUILDDIR)/server/sv_user.o $(QW_BUILDDIR)/server/sv_ccmds.o \
	$(QW_BUILDDIR)/server/world.o $(QW_BUILDDIR)/server/sys_unix.o \
	$(QW_BUILDDIR)/server/model.o \
	$(QW_BUILDDIR)/server/cmd.o $(QW_BUILDDIR)/server/common.o \
	$(QW_BUILDDIR)/server/crc.o $(QW_BUILDDIR)/server/cvar.o \
	$(QW_BUILDDIR)/server/mathlib.o $(QW_BUILDDIR)/server/md4.o \
	$(QW_BUILDDIR)/server/zone.o $(QW_BUILDDIR)/server/pmove.o \
	$(QW_BUILDDIR)/server/pmovetst.o $(QW_BUILDDIR)/server/net_chan.o \
	$(QW_BUILDDIR)/server/net_udp.o

# GL client objects (Makefile.Linux GLQWCL_OBJS, all compiled with -DGLQUAKE
# via DO_GL_CC, plus the glqwcl.glx vid object). Swaps vs the Linux list:
#   cd_linux -> cd_null (Linux cdrom ioctls have no macOS equivalent; same
#               replacement as Phase 1), snd_linux -> snd_sdl,
#   gl_vidlinuxglx -> gl_vidsdl (both new files adapted copies, see their
#               headers), asm objects math/snd_mixa/sys_dosa omitted (arm64;
#               nonintel.o covers the !id386 paths as on Linux non-i386).
# Objects land in $(QW_BUILDDIR)/client/ as in the Linux build
# ($(BUILDDIR)/glclient/ there).
QW_CLIENT_OBJS = \
	$(QW_BUILDDIR)/client/cl_demo.o $(QW_BUILDDIR)/client/cl_ents.o \
	$(QW_BUILDDIR)/client/cl_input.o $(QW_BUILDDIR)/client/cl_main.o \
	$(QW_BUILDDIR)/client/cl_parse.o $(QW_BUILDDIR)/client/cl_pred.o \
	$(QW_BUILDDIR)/client/cl_tent.o $(QW_BUILDDIR)/client/cl_cam.o \
	$(QW_BUILDDIR)/client/cmd.o $(QW_BUILDDIR)/client/common.o \
	$(QW_BUILDDIR)/client/console.o $(QW_BUILDDIR)/client/crc.o \
	$(QW_BUILDDIR)/client/cvar.o \
	$(QW_BUILDDIR)/client/keys.o $(QW_BUILDDIR)/client/mathlib.o \
	$(QW_BUILDDIR)/client/md4.o $(QW_BUILDDIR)/client/menu.o \
	$(QW_BUILDDIR)/client/net_chan.o $(QW_BUILDDIR)/client/net_udp.o \
	$(QW_BUILDDIR)/client/nonintel.o \
	$(QW_BUILDDIR)/client/pmove.o $(QW_BUILDDIR)/client/pmovetst.o \
	$(QW_BUILDDIR)/client/r_part.o \
	$(QW_BUILDDIR)/client/sbar.o $(QW_BUILDDIR)/client/skin.o \
	$(QW_BUILDDIR)/client/snd_dma.o $(QW_BUILDDIR)/client/snd_mem.o \
	$(QW_BUILDDIR)/client/snd_mix.o \
	$(QW_BUILDDIR)/client/view.o $(QW_BUILDDIR)/client/wad.o \
	$(QW_BUILDDIR)/client/zone.o \
	$(QW_BUILDDIR)/client/cd_null.o $(QW_BUILDDIR)/client/sys_unix.o \
	$(QW_BUILDDIR)/client/snd_sdl.o \
	$(QW_BUILDDIR)/client/gl_draw.o $(QW_BUILDDIR)/client/gl_mesh.o \
	$(QW_BUILDDIR)/client/gl_model.o $(QW_BUILDDIR)/client/gl_ngraph.o \
	$(QW_BUILDDIR)/client/gl_refrag.o $(QW_BUILDDIR)/client/gl_rlight.o \
	$(QW_BUILDDIR)/client/gl_rmain.o $(QW_BUILDDIR)/client/gl_rmisc.o \
	$(QW_BUILDDIR)/client/gl_rsurf.o $(QW_BUILDDIR)/client/gl_screen.o \
	$(QW_BUILDDIR)/client/gl_warp.o \
	$(QW_BUILDDIR)/client/gl_vidsdl.o

.DEFAULT_GOAL := help

.PHONY: help objects build-release build-debug build-server \
	build-server-debug build-client check-data check-data-qw run \
	run-server run-client clean

# ── Help ─────────────────────────────────────────────────────────────────────

help: ## Print this help message
	@printf '\033[01;32m${SERVICE}\033[00;37m\n\n'
	@printf "\033[33mUsage:\033[0m\n  make [target]\n\n\033[33mTargets:\033[0m\n"
	@grep -E '^[-a-zA-Z0-9_\.\/]+:.*?## .*$$' $(MAKEFILE_LIST) | \
		awk 'BEGIN {FS = ":.*?## "}; \
		{printf "  \033[36m%-16s\033[0m %s\n", $$1, $$2}'

# ── glquake build ────────────────────────────────────────────────────────────

$(QUAKE_BUILDDIR):
	mkdir -p $(QUAKE_BUILDDIR)

$(QUAKE_BUILDDIR)/%.o: $(QUAKE_DIR)/%.c | $(QUAKE_BUILDDIR)
	$(CC) $(CFLAGS) -o $@ -c $<

objects: CFLAGS = $(QUAKE_RELEASE_CFLAGS)
objects: $(QUAKE_CORE_OBJS) ## Compile glquake engine core objects only (no platform drivers)

build-release: CFLAGS = $(QUAKE_RELEASE_CFLAGS)
build-release: $(QUAKE_BUILDDIR)/glquake ## Build optimized glquake

build-debug: CFLAGS = $(QUAKE_DEBUG_CFLAGS)
build-debug: $(QUAKE_BUILDDIR)/glquake ## Build glquake with -g -O0

$(QUAKE_BUILDDIR)/glquake: $(QUAKE_OBJS)
	$(CC) -o $@ $(QUAKE_OBJS) $(QUAKE_LDFLAGS)

# ── qwsv build ───────────────────────────────────────────────────────────────

$(QW_BUILDDIR)/server:
	mkdir -p $(QW_BUILDDIR)/server

$(QW_BUILDDIR)/server/%.o: $(QW_SERVER_DIR)/%.c | $(QW_BUILDDIR)/server
	$(CC) $(CFLAGS) -o $@ -c $<

$(QW_BUILDDIR)/server/%.o: $(QW_CLIENT_DIR)/%.c | $(QW_BUILDDIR)/server
	$(CC) $(CFLAGS) -o $@ -c $<

build-server: CFLAGS = $(QW_SERVER_RELEASE_CFLAGS)
build-server: $(QW_BUILDDIR)/qwsv ## Build optimized qwsv

build-server-debug: CFLAGS = $(QW_SERVER_DEBUG_CFLAGS)
build-server-debug: $(QW_BUILDDIR)/qwsv ## Build qwsv with -g -O0

$(QW_BUILDDIR)/qwsv: $(QW_SERVER_OBJS)
	$(CC) -o $@ $(QW_SERVER_OBJS) $(QW_SERVER_LDFLAGS)

# ── glqwcl build ─────────────────────────────────────────────────────────────

$(QW_BUILDDIR)/client:
	mkdir -p $(QW_BUILDDIR)/client

$(QW_BUILDDIR)/client/%.o: $(QW_CLIENT_DIR)/%.c | $(QW_BUILDDIR)/client
	$(CC) $(CFLAGS) -o $@ -c $<

build-client: CFLAGS = $(QW_CLIENT_RELEASE_CFLAGS)
build-client: $(QW_BUILDDIR)/glqwcl ## Build optimized glqwcl

$(QW_BUILDDIR)/glqwcl: $(QW_CLIENT_OBJS)
	$(CC) -o $@ $(QW_CLIENT_OBJS) $(QW_CLIENT_LDFLAGS)

# ── Data gates & run ─────────────────────────────────────────────────────────

check-data: ## Verify single-player game data (id1/pak0.pak) is present
	@if [ ! -f "$(GAMEDIR)/id1/pak0.pak" ]; then \
		echo "ERROR: game data not found."; \
		echo "Expected: $(GAMEDIR)/id1/pak0.pak"; \
		echo "Copy pak0.pak (and pak1.pak) from your legally owned Quake"; \
		echo "into $(GAMEDIR)/id1/ and re-run."; \
		exit 1; \
	fi
	@echo "Game data OK: $(GAMEDIR)/id1"

check-data-qw: ## Verify QuakeWorld game data (qw/qwprogs.dat, qw/pak0.pak) is present
	@if [ ! -f "$(GAMEDIR)/qw/qwprogs.dat" ] || [ ! -f "$(GAMEDIR)/qw/pak0.pak" ]; then \
		echo "ERROR: game data not found."; \
		echo "Expected: $(GAMEDIR)/qw/qwprogs.dat and $(GAMEDIR)/qw/pak0.pak"; \
		echo "Copy qwprogs.dat (ships in this repo: $(QW_DIR)/progs/qwprogs.dat)"; \
		echo "and pak0.pak from your legally owned Quake into $(GAMEDIR)/qw/"; \
		echo "and re-run."; \
		exit 1; \
	fi
	@echo "Game data OK: $(GAMEDIR)/qw"

run: check-data build-release ## Launch glquake against $(GAMEDIR)
	$(QUAKE_BUILDDIR)/glquake -basedir "$(GAMEDIR)"

run-server: check-data-qw build-server ## Launch qwsv against $(GAMEDIR)/qw
	$(QW_BUILDDIR)/qwsv -basedir "$(GAMEDIR)" +gamedir qw

run-client: check-data-qw build-client ## Launch glqwcl against $(GAMEDIR)/qw
	$(QW_BUILDDIR)/glqwcl -basedir "$(GAMEDIR)" +gamedir qw

clean: ## Remove build output
	rm -rf $(QUAKE_BUILDDIR) $(QW_BUILDDIR)
