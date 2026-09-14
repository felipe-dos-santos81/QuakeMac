/*
Copyright (C) 2026 Id Software-derived QuakeMCP contributors

This program is free software; you can redistribute it and/or
modify it under the terms of the GNU General Public License
as published by the Free Software Foundation; either version 2
of the License, or (at your option) any later version.
*/

// q_mcp_capture.c -- MCP vision capture (Task 7)
//
// A deferred observe request arms a capture; the render path
// (MCAP_Frame, called at the end of SCR_UpdateScreen before the buffer
// swap) reads the composed frame into a 2-slot ring. The next MCP_Poll
// labels the slot with the completed frame id and hands it to the
// dispatch layer, which sends the JSON header and the framed RGB blob.
// Reading at that point is what makes "state and pixels are one
// snapshot" true: no simulation runs between the render and the label
// poll. Vertical flip/scale happen in Python (vision.py); the ring
// carries raw bottom-up RGB, the glReadPixels order.

#include "quakedef.h"
#include "q_mcp.h"

#include <stdlib.h>

#define MCAP_SRC_MAX_BYTES	(32 * 1024 * 1024)

typedef struct
{
	byte	*data;			// RGB, bottom-up rows
	int	bufsize;
	int	w, h;
	int	viewport[4];		// glx, gly, glwidth, glheight
	int	hud_rect[4];		// hud strip in top-down image coords
	double	captured_at;
	unsigned frame;
} mcap_slot_t;

static mcap_slot_t	mcap_ring[2];
static int	mcap_next_slot;
static int	mcap_pinned;		// slot handed to the sender, -1 none

static qboolean	mcap_req_active;
static unsigned	mcap_req_after;
static double	mcap_req_deadline;

// -1 idle, >=0 slot captured at render, -2 capture refused (too large)
static int	mcap_pending;
static double	mcap_pending_at;

/*
==================
MCAP_Request

Record a deferred observe request. The caller must not have one
outstanding (MCAP_Busy is the gate).
==================
*/
void MCAP_Request (unsigned after_frame, double timeout_secs)
{
	mcap_req_active = true;
	mcap_req_after = after_frame;
	mcap_req_deadline = Sys_DoubleTime () + timeout_secs;
}

/*
==================
MCAP_Busy

True while a request is waiting for its frame, a capture awaits its
label, or a refused capture awaits its error reply.
==================
*/
qboolean MCAP_Busy (void)
{
	return (mcap_req_active || mcap_pending != -1) ? true : false;
}

/*
==================
MCAP_Frame

Render-path hook. Captures only when the frame about to complete has an
id past the request's lower bound; host_framecount is incremented right
after the render, so that id is MCP_FrameId () + 1. Skipped renders
(block_drawing, loading, not initialized) leave the request armed.
==================
*/
void MCAP_Frame (void)
{
	mcap_slot_t	*s;
	long	bytes;
	int	slot, x, y, w, h, hudh;

	if (!mcap_req_active || mcap_pending != -1)
		return;
	if (!(MCP_FrameId () + 1 > mcap_req_after))
		return;

	x = glx;
	y = gly;
	w = glwidth;
	h = glheight;
	if (w <= 0 || h <= 0)
		return;

	bytes = (long)w * h * 3;
	if (bytes > MCAP_SRC_MAX_BYTES)
	{
		mcap_pending = -2;
		mcap_pending_at = Sys_DoubleTime ();
		return;
	}

	slot = mcap_next_slot;
	if (slot == mcap_pinned)
		slot = slot ^ 1;
	mcap_next_slot = slot ^ 1;
	s = &mcap_ring[slot];

	if (s->bufsize < (int)bytes)
	{
		free (s->data);
		s->data = (byte *)malloc ((size_t)bytes);
		s->bufsize = (int)bytes;
	}
	if (!s->data)
	{
		mcap_pending = -2;
		mcap_pending_at = Sys_DoubleTime ();
		return;
	}

	glPixelStorei (GL_PACK_ALIGNMENT, 1);
	glReadPixels (x, y, w, h, GL_RGB, GL_UNSIGNED_BYTE, s->data);

	s->w = w;
	s->h = h;
	s->viewport[0] = x;
	s->viewport[1] = y;
	s->viewport[2] = w;
	s->viewport[3] = h;
	hudh = sb_lines;
	if (hudh < 0)
		hudh = 0;
	if (hudh > h)
		hudh = h;
	s->hud_rect[0] = 0;
	s->hud_rect[1] = h - hudh;
	s->hud_rect[2] = w;
	s->hud_rect[3] = hudh;

	mcap_pending = slot;
	mcap_pending_at = Sys_DoubleTime ();
}

/*
==================
MCAP_Poll

Advance the outstanding request. Returns 1 when out is filled (the slot
stays pinned until MCAP_Release), 0 while waiting, -1 on the timeout,
-2 when the frame could not be read.
==================
*/
int MCAP_Poll (mcap_snapshot_t *out)
{
	mcap_slot_t	*s;
	double		now;

	now = Sys_DoubleTime ();

	if (mcap_pending == -2)
	{
		mcap_pending = -1;
		mcap_req_active = false;
		return -2;
	}

	if (mcap_pending >= 0)
	{
		s = &mcap_ring[mcap_pending];
		s->frame = MCP_FrameId ();
		s->captured_at = mcap_pending_at;
		mcap_pinned = mcap_pending;
		mcap_pending = -1;
		mcap_req_active = false;

		out->data = s->data;
		out->w = s->w;
		out->h = s->h;
		out->viewport[0] = s->viewport[0];
		out->viewport[1] = s->viewport[1];
		out->viewport[2] = s->viewport[2];
		out->viewport[3] = s->viewport[3];
		out->hud_rect[0] = s->hud_rect[0];
		out->hud_rect[1] = s->hud_rect[1];
		out->hud_rect[2] = s->hud_rect[2];
		out->hud_rect[3] = s->hud_rect[3];
		out->captured_at = s->captured_at;
		out->frame = s->frame;
		return 1;
	}

	if (mcap_req_active && now > mcap_req_deadline)
	{
		mcap_req_active = false;
		return -1;
	}

	return 0;
}

/*
==================
MCAP_Release

Unpin the delivered slot; its buffer stays allocated for reuse.
==================
*/
void MCAP_Release (void)
{
	mcap_pinned = -1;
}

/*
==================
MCAP_Shutdown
==================
*/
void MCAP_Shutdown (void)
{
	free (mcap_ring[0].data);
	free (mcap_ring[1].data);
	Q_memset (mcap_ring, 0, sizeof (mcap_ring));
	mcap_next_slot = 0;
	mcap_pinned = -1;
	mcap_req_active = false;
	mcap_pending = -1;
}
