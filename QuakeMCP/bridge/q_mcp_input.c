/*
Copyright (C) 2026 Id Software-derived QuakeMCP contributors

This program is free software; you can redistribute it and/or
modify it under the terms of the GNU General Public License
as published by the Free Software Foundation; either version 2
of the License, or (at your option) any later version.
*/

// q_mcp_input.c -- MCP gameplay input merge (movement, view, buttons, impulse)
//
// Keeps MCP input in a state fully separate from the human input globals
// (in_attack/in_jump/in_impulse are never written here). Movement and view
// deltas merge into the usercmd in MCP_Move (called from CL_SendCmd).
// Attack/jump/impulse are exposed to the CL_SendMove serialization through
// MCP_Buttons()/MCP_Impulse(), which the caller ORs into the wire bits.

#include "quakedef.h"
#include "q_mcp.h"

// Axis signs per design: forward/right/up positive; yaw/pitch deltas
// positive = turn right / look up. Raw degrees applied once per action.

static qboolean	mcp_input_active;
static qboolean	mcp_apply_view;
static float	mcp_forward;		// -1..1
static float	mcp_strafe;		// -1..1
static float	mcp_vertical;		// -1..1
static qboolean	mcp_run;		// +speed hold
static float	mcp_yaw_delta;		// degrees, +right
static float	mcp_pitch_delta;	// degrees, +up
static qboolean	mcp_attack;		// hold
static int	mcp_jump;		// 0 none, 1 tap (one frame), 2 hold
static int	mcp_impulse;		// one-shot, cleared on read


/*
==================
MCP_BeginInput

Snapshot the inputs for a newly scheduled action. Neutral fields are 0.
View deltas are applied on the first MCP_Move of the action, then cleared.
==================
*/
void MCP_BeginInput (float fwd, float strafe, float vert,
	float yawdeg, float pitchdeg, qboolean attack, int jump, int impulse,
	qboolean run)
{
	// clamp axes to [-1,1]
	if (fwd < -1) fwd = -1; else if (fwd > 1) fwd = 1;
	if (strafe < -1) strafe = -1; else if (strafe > 1) strafe = 1;
	if (vert < -1) vert = -1; else if (vert > 1) vert = 1;

	mcp_input_active = true;
	mcp_apply_view = true;
	mcp_forward = fwd;
	mcp_strafe = strafe;
	mcp_vertical = vert;
	mcp_run = run;
	mcp_yaw_delta = yawdeg;
	mcp_pitch_delta = pitchdeg;
	mcp_attack = attack;
	mcp_jump = jump;
	mcp_impulse = impulse;
}

/*
==================
MCP_EndInput

Neutralize all MCP input. Called on action completion, expiry, release,
and takeover. Always safe to call (idempotent).
==================
*/
void MCP_EndInput (void)
{
	mcp_input_active = false;
	mcp_apply_view = false;
	mcp_forward = 0;
	mcp_strafe = 0;
	mcp_vertical = 0;
	mcp_run = false;
	mcp_yaw_delta = 0;
	mcp_pitch_delta = 0;
	mcp_attack = false;
	mcp_jump = 0;
	mcp_impulse = 0;
}

/*
==================
MCP_Move

Merge MCP input into the usercmd. Returns 1 if a tick was merged, else 0.
Applies the yaw/pitch delta once, then scales -1..1 axes through the
engine speed cvars (cl_forwardspeed/cl_sidespeed/cl_upspeed). Never touches
in_attack/in_jump/in_impulse. Calls MCP_NoteTick on each merged tick so the
dispatch layer counts completed simulation steps.
==================
*/
int MCP_Move (usercmd_t *cmd)
{
	if (!mcp_input_active)
		return 0;

	if (mcp_apply_view)
	{
		cl.viewangles[YAW] -= mcp_yaw_delta;
		// engine pitch is positive-down: +up input subtracts
		cl.viewangles[PITCH] -= mcp_pitch_delta;
		// engine view limits: pitch +80/-70, roll cleared
		if (cl.viewangles[PITCH] > 80)
			cl.viewangles[PITCH] = 80;
		if (cl.viewangles[PITCH] < -70)
			cl.viewangles[PITCH] = -70;
		cl.viewangles[ROLL] = 0;
		mcp_apply_view = false;
	}

	cmd->forwardmove += mcp_forward * cl_forwardspeed.value;
	cmd->sidemove += mcp_strafe * cl_sidespeed.value;
	cmd->upmove += mcp_vertical * cl_upspeed.value;
	if (mcp_run)
	{
		cmd->forwardmove *= cl_movespeedkey.value;
		cmd->sidemove *= cl_movespeedkey.value;
		cmd->upmove *= cl_movespeedkey.value;
	}

	MCP_NoteTick ();

	return 1;
}

/*
==================
MCP_Buttons

Attack + jump bits for the move packet (bit 0 = attack, bit 1 = jump).
Jump tap lasts exactly one serialization then auto-clears; hold stays set
while input is active. Returns 0 when input is inactive, so stale held
buttons never leak into the packet.
==================
*/
int MCP_Buttons (void)
{
	int bits;

	if (!mcp_input_active)
		return 0;
	bits = 0;
	if (mcp_attack)
		bits |= 1;
	if (mcp_jump == 2)
		bits |= 2;
	else if (mcp_jump == 1)
	{
		bits |= 2;
		mcp_jump = 0;	// tap: one frame, then release
	}
	return bits;
}

/*
==================
MCP_Impulse

One-shot impulse byte. Returns the pending impulse and clears it.
==================
*/
int MCP_Impulse (void)
{
	int i;

	if (!mcp_input_active)
		return 0;
	i = mcp_impulse;
	mcp_impulse = 0;
	return i;
}