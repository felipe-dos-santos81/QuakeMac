# Makefile — GLQuake for Apple Silicon (macOS arm64)
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
	$(BUILDDIR)/cd_null.o $(BUILDDIR)/sys_unix.o \
	$(BUILDDIR)/snd_dma.o $(BUILDDIR)/snd_mem.o $(BUILDDIR)/snd_mix.o

PLATFORM_OBJS = $(BUILDDIR)/gl_vidsdl.o $(BUILDDIR)/snd_sdl.o

OBJS = $(CORE_OBJS) $(PLATFORM_OBJS)

.DEFAULT_GOAL := help

.PHONY: help objects build-release build-debug check-data run clean

# ── Help ─────────────────────────────────────────────────────────────────────

help: ## Print this help message
	@printf '\033[01;32m${SERVICE}\033[00;37m\n\n'
	@printf "\033[33mUsage:\033[0m\n  make [target]\n\n\033[33mTargets:\033[0m\n"
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
build-release: $(BUILDDIR)/glquake ## Build optimized glquake

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
