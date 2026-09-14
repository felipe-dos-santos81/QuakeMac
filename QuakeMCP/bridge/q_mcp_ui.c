/*
Copyright (C) 2026 Id Software-derived QuakeMCP contributors

This program is free software; you can redistribute it and/or
modify it under the terms of the GNU General Public License
as published by the Free Software Foundation; either version 2
of the License, or (at your option) any later version.
*/

// q_mcp_ui.c -- MCP takeover control: visible banner + left click.
//
// While a controller lease is held, glquake draws a top-center banner
// and the first physical left-click revokes the lease (human takeover).
// There is no cursor or hit test: any left-click is the stop control,
// which makes recovery work in game, console and menu contexts alike.
// Without a lease nothing is drawn and no event is touched.

#include "quakedef.h"
#include "q_mcp.h"

#define MCP_STOP_TEXT	"MCP CONTROL - LEFT-CLICK TO STOP"
#define MCP_STOP_PAD	4
#define MCP_STOP_MARGIN	8

static qboolean	mcp_stop_swallow;	// matching left-up after takeover

/*
==================
MCP_UiRect

Screen-space rect of the stop banner (top-center, 8x8 font metrics).
False while there is nothing to draw.
==================
*/
static qboolean MCP_UiRect (int *x, int *y, int *w, int *h)
{
	int	textw;

	if (!MCP_LeaseHeld () || vid.width <= 0 || vid.height <= 0)
		return false;
	textw = strlen (MCP_STOP_TEXT) * 8;
	*w = textw + 2 * MCP_STOP_PAD;
	*h = 8 + 2 * MCP_STOP_PAD;
	*x = (vid.width - *w) / 2;
	*y = MCP_STOP_MARGIN;
	return true;
}

/*
==================
MCP_UiDraw

Called at the end of SCR_UpdateScreen, so the banner is part of the
frame the bridge captures.
==================
*/
void MCP_UiDraw (void)
{
	int	x, y, w, h;

	if (!MCP_UiRect (&x, &y, &w, &h))
		return;
	Draw_FillAlpha (x, y, w, h, 12, 0.65f);
	Draw_FillAlpha (x + 1, y + 1, w - 2, h - 2, 0, 0.65f);
	Draw_StringAlpha (x + MCP_STOP_PAD, y + MCP_STOP_PAD,
		MCP_STOP_TEXT, 0.9f);
}

/*
==================
MCP_UiMouseClick

One physical mouse button event, before the access/game funnel. While a
lease is held the left button takes over and the click (and its
matching release) is swallowed; otherwise the event passes through
untouched.
==================
*/
int MCP_UiMouseClick (int button, qboolean down)
{
	if (button != 0)
		return 0;
	if (down)
	{
		if (!MCP_LeaseHeld ())
			return 0;
		mcp_stop_swallow = true;
		MCP_HumanTakeover ();
		return 1;
	}
	if (mcp_stop_swallow)
	{
		mcp_stop_swallow = false;
		return 1;
	}
	return 0;
}
