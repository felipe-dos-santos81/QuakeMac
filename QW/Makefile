# Makefile — QuakeWorld server (qwsv) + GL client (glqwcl) for
# Apple Silicon (macOS arm64). Phase 2 of the SDL3 port (Tasks 6-7).
# Server object list cloned from Makefile.Linux (QWSV_OBJS): server files
# compile from server/, shared files from client/, all with -DSERVERONLY.
# Headless: no SDL, no OpenGL.
# Client object list cloned from Makefile.Linux GLQWCL_OBJS + the
# glqwcl.glx vid object: every object gets -DGLQUAKE (as DO_GL_CC did);
# asm objects (math/snd_mixa/sys_dosa) omitted for arm64; cd_linux->cd_null,
# snd_linux->snd_sdl, gl_vidlinuxglx->gl_vidsdl.
# Spec: docs/superpowers/specs/2026-08-29-quake-apple-silicon-design.md

SERVICE = QuakeWorld (macOS arm64)

# Variables
CC             = cc
BUILDDIR       = build-macosx
CLIENT_DIR     = client
SERVER_DIR     = server
GAMEDIR       ?= $(CURDIR)/../game
SDL_CFLAGS     = $(shell pkg-config sdl3 --cflags)
SDL_LIBS       = $(shell pkg-config sdl3 --libs)
GL_LIBS        = -framework OpenGL
BASE_CFLAGS    = -Wall -Dstricmp=strcasecmp -I$(CLIENT_DIR) -I$(SERVER_DIR)
SERVER_CFLAGS  = $(BASE_CFLAGS) -DSERVERONLY
RELEASE_CFLAGS = $(SERVER_CFLAGS) -O2 -ffast-math
DEBUG_CFLAGS   = $(SERVER_CFLAGS) -g -O0
LDFLAGS        = -lm
# GL client: Makefile.Linux compiles every glclient object with
# $(CFLAGS) $(GLCFLAGS) (GLCFLAGS = -DGLQUAKE ...), so -DGLQUAKE applies to
# the whole client, not just the gl_*.c files. GL/gl.h + GL/glu.h come from
# the shared Phase-1 shim (reused, not duplicated).
CLIENT_CFLAGS         = $(BASE_CFLAGS) -DGLQUAKE -I../WinQuake/macosx-shim \
                        $(SDL_CFLAGS)
CLIENT_RELEASE_CFLAGS = $(CLIENT_CFLAGS) -O2 -ffast-math
CLIENT_LDFLAGS        = $(SDL_LIBS) $(GL_LIBS) -lm

# Server objects (Makefile.Linux QWSV_OBJS, verbatim): the first 15 compile
# from $(SERVER_DIR), the last 11 from $(CLIENT_DIR), all with SERVER_CFLAGS.
# Objects land in $(BUILDDIR)/server/ as in the Linux build.
# NOTE: the $(SERVER_DIR) pattern rule below must stay first — client/ and
# server/ share filenames (model.c historically, sys_unix.c today), and the
# server variant must own $(BUILDDIR)/server/*.o.
SERVER_OBJS = \
	$(BUILDDIR)/server/pr_cmds.o $(BUILDDIR)/server/pr_edict.o \
	$(BUILDDIR)/server/pr_exec.o $(BUILDDIR)/server/sv_init.o \
	$(BUILDDIR)/server/sv_main.o $(BUILDDIR)/server/sv_nchan.o \
	$(BUILDDIR)/server/sv_ents.o $(BUILDDIR)/server/sv_send.o \
	$(BUILDDIR)/server/sv_move.o $(BUILDDIR)/server/sv_phys.o \
	$(BUILDDIR)/server/sv_user.o $(BUILDDIR)/server/sv_ccmds.o \
	$(BUILDDIR)/server/world.o $(BUILDDIR)/server/sys_unix.o \
	$(BUILDDIR)/server/model.o \
	$(BUILDDIR)/server/cmd.o $(BUILDDIR)/server/common.o \
	$(BUILDDIR)/server/crc.o $(BUILDDIR)/server/cvar.o \
	$(BUILDDIR)/server/mathlib.o $(BUILDDIR)/server/md4.o \
	$(BUILDDIR)/server/zone.o $(BUILDDIR)/server/pmove.o \
	$(BUILDDIR)/server/pmovetst.o $(BUILDDIR)/server/net_chan.o \
	$(BUILDDIR)/server/net_udp.o

# GL client objects (Makefile.Linux GLQWCL_OBJS, all compiled with -DGLQUAKE
# via DO_GL_CC, plus the glqwcl.glx vid object). Swaps vs the Linux list:
#   cd_linux -> cd_null (Linux cdrom ioctls have no macOS equivalent; same
#               replacement as Phase 1), snd_linux -> snd_sdl,
#   gl_vidlinuxglx -> gl_vidsdl (both new files adapted copies, see their
#               headers), asm objects math/snd_mixa/sys_dosa omitted (arm64;
#               nonintel.o covers the !id386 paths as on Linux non-i386).
# Objects land in $(BUILDDIR)/client/ as in the Linux build ($(BUILDDIR)/
# glclient/ there).
GLCLIENT_OBJS = \
	$(BUILDDIR)/client/cl_demo.o $(BUILDDIR)/client/cl_ents.o \
	$(BUILDDIR)/client/cl_input.o $(BUILDDIR)/client/cl_main.o \
	$(BUILDDIR)/client/cl_parse.o $(BUILDDIR)/client/cl_pred.o \
	$(BUILDDIR)/client/cl_tent.o $(BUILDDIR)/client/cl_cam.o \
	$(BUILDDIR)/client/cmd.o $(BUILDDIR)/client/common.o \
	$(BUILDDIR)/client/console.o $(BUILDDIR)/client/crc.o \
	$(BUILDDIR)/client/cvar.o \
	$(BUILDDIR)/client/keys.o $(BUILDDIR)/client/mathlib.o \
	$(BUILDDIR)/client/md4.o $(BUILDDIR)/client/menu.o \
	$(BUILDDIR)/client/net_chan.o $(BUILDDIR)/client/net_udp.o \
	$(BUILDDIR)/client/nonintel.o \
	$(BUILDDIR)/client/pmove.o $(BUILDDIR)/client/pmovetst.o \
	$(BUILDDIR)/client/r_part.o \
	$(BUILDDIR)/client/sbar.o $(BUILDDIR)/client/skin.o \
	$(BUILDDIR)/client/snd_dma.o $(BUILDDIR)/client/snd_mem.o \
	$(BUILDDIR)/client/snd_mix.o \
	$(BUILDDIR)/client/view.o $(BUILDDIR)/client/wad.o \
	$(BUILDDIR)/client/zone.o \
	$(BUILDDIR)/client/cd_null.o $(BUILDDIR)/client/sys_unix.o \
	$(BUILDDIR)/client/snd_sdl.o \
	$(BUILDDIR)/client/gl_draw.o $(BUILDDIR)/client/gl_mesh.o \
	$(BUILDDIR)/client/gl_model.o $(BUILDDIR)/client/gl_ngraph.o \
	$(BUILDDIR)/client/gl_refrag.o $(BUILDDIR)/client/gl_rlight.o \
	$(BUILDDIR)/client/gl_rmain.o $(BUILDDIR)/client/gl_rmisc.o \
	$(BUILDDIR)/client/gl_rsurf.o $(BUILDDIR)/client/gl_screen.o \
	$(BUILDDIR)/client/gl_warp.o \
	$(BUILDDIR)/client/gl_vidsdl.o

.DEFAULT_GOAL := help

.PHONY: help build-server build-server-debug build-client check-data \
	run-server run-client clean

# ── Help ─────────────────────────────────────────────────────────────────────

help: ## Print this help message
	@printf '\033[01;32m${SERVICE}\033[00;37m\n\n'
	@printf "\033[33mUsage:\033[0m\n  make [target]\n\n\033[33mTargets:\033[0m\n"
	@grep -E '^[-a-zA-Z0-9_\.\/]+:.*?## .*$$' $(MAKEFILE_LIST) | \
		awk 'BEGIN {FS = ":.*?## "}; \
		{printf "  \033[36m%-16s\033[0m %s\n", $$1, $$2}'

# ── Build ────────────────────────────────────────────────────────────────────

$(BUILDDIR)/server:
	mkdir -p $(BUILDDIR)/server

$(BUILDDIR)/server/%.o: $(SERVER_DIR)/%.c | $(BUILDDIR)/server
	$(CC) $(CFLAGS) -o $@ -c $<

$(BUILDDIR)/server/%.o: $(CLIENT_DIR)/%.c | $(BUILDDIR)/server
	$(CC) $(CFLAGS) -o $@ -c $<

build-server: CFLAGS = $(RELEASE_CFLAGS)
build-server: $(BUILDDIR)/qwsv ## Build optimized qwsv

build-server-debug: CFLAGS = $(DEBUG_CFLAGS)
build-server-debug: $(BUILDDIR)/qwsv ## Build qwsv with -g -O0

$(BUILDDIR)/qwsv: $(SERVER_OBJS)
	$(CC) -o $@ $(SERVER_OBJS) $(LDFLAGS)

# ── GL client build ──────────────────────────────────────────────────────────

$(BUILDDIR)/client:
	mkdir -p $(BUILDDIR)/client

$(BUILDDIR)/client/%.o: $(CLIENT_DIR)/%.c | $(BUILDDIR)/client
	$(CC) $(CFLAGS) -o $@ -c $<

build-client: CFLAGS = $(CLIENT_RELEASE_CFLAGS)
build-client: $(BUILDDIR)/glqwcl ## Build optimized glqwcl

$(BUILDDIR)/glqwcl: $(GLCLIENT_OBJS)
	$(CC) -o $@ $(GLCLIENT_OBJS) $(CLIENT_LDFLAGS)

# ── Data gate & run ──────────────────────────────────────────────────────────

check-data: ## Verify game data (qw/qwprogs.dat, qw/pak0.pak) is present
	@if [ ! -f "$(GAMEDIR)/qw/qwprogs.dat" ] || [ ! -f "$(GAMEDIR)/qw/pak0.pak" ]; then \
		echo "ERROR: game data not found."; \
		echo "Expected: $(GAMEDIR)/qw/qwprogs.dat and $(GAMEDIR)/qw/pak0.pak"; \
		echo "Copy qwprogs.dat (ships in this repo: QW/progs/qwprogs.dat) and"; \
		echo "pak0.pak from your legally owned Quake into $(GAMEDIR)/qw/"; \
		echo "and re-run."; \
		exit 1; \
	fi
	@echo "Game data OK: $(GAMEDIR)/qw"

run-server: check-data build-server ## Launch qwsv against $(GAMEDIR)/qw
	$(BUILDDIR)/qwsv -basedir "$(GAMEDIR)" +gamedir qw

run-client: check-data build-client ## Launch glqwcl against $(GAMEDIR)/qw
	$(BUILDDIR)/glqwcl -basedir "$(GAMEDIR)" +gamedir qw

clean: ## Remove build output
	rm -rf $(BUILDDIR)
