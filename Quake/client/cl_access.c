/*
cl_access.c — mouse-only control: Look/Walk modes, throttle & velocity
movement profiles, gesture engine, cruise control, safety resets,
point-and-click menus, HUD feedback.

Spec: docs/superpowers/specs/2026-08-30-mouse-only-control-design.md
Plan: docs/superpowers/plans/2026-08-30-mouse-only-control.md

Design invariant: when access_mouseonly is 0, this module is inert and
every input path is byte-identical to vanilla.
*/

#include <math.h>
#include <SDL3/SDL.h>

#include "quakedef.h"
#include "cl_access.h"

extern SDL_Window *sdl_window;	/* owned by gl_vidsdl.c */

#define ACCESS_LOOK 0
#define ACCESS_WALK 1

/* ---------------------------------------------------------------- cvars */

cvar_t	access_mouseonly = {"access_mouseonly", "1", true};
cvar_t	access_move_profile = {"access_move_profile", "0", true};
cvar_t	access_throttle_gain = {"access_throttle_gain", "0.002", true};
cvar_t	access_velocity_gain = {"access_velocity_gain", "1", true};
cvar_t	access_deadzone = {"access_deadzone", "0", true};
cvar_t	access_curve = {"access_curve", "1", true};
cvar_t	access_walkspeed = {"access_walkspeed", "190", true};
cvar_t	access_tremor = {"access_tremor", "0", true};
cvar_t	access_turnrate = {"access_turnrate", "240", true};
cvar_t	access_throttle_decay = {"access_throttle_decay", "0", true};
cvar_t	access_toggle_button = {"access_toggle_button", "202", true}; /* K_MOUSE3 */
cvar_t	access_longpress_button = {"access_longpress_button", "0", true};
cvar_t	access_longpress_ms = {"access_longpress_ms", "400", true};
cvar_t	access_layer_timeout = {"access_layer_timeout", "3", true};
cvar_t	access_layer_cmd1 = {"access_layer_cmd1", "save quick"};
cvar_t	access_layer_cmd2 = {"access_layer_cmd2", "togglemenu"};
cvar_t	access_doubleclick_button = {"access_doubleclick_button", "0", true};
cvar_t	access_doubleclick_ms = {"access_doubleclick_ms", "350", true};
cvar_t	access_doubleclick_command = {"access_doubleclick_command", ""};
cvar_t	access_cruise_speed = {"access_cruise_speed", "0", true};
cvar_t	access_idle_timeout = {"access_idle_timeout", "0", true};
cvar_t	access_hud = {"access_hud", "1", true};
cvar_t	access_sounds = {"access_sounds", "1", true};
cvar_t	access_log = {"access_log", "1", true};

/* ---------------------------------------------------------------- state */

static int		access_mode = ACCESS_LOOK;
static float		access_throttle;
static qboolean		access_cruise;

static void Access_Log (char *msg)
{
	if (access_log.value)
		Con_Printf ("access: %s\n", msg);
}

/* ------------------------------------------------------------- commands */

static void Access_ToggleMode_f (void)
{
	if (!access_mouseonly.value)
		return;
	access_mode = (access_mode == ACCESS_LOOK) ? ACCESS_WALK : ACCESS_LOOK;
	access_throttle = 0;
	if (access_mode == ACCESS_WALK)
		access_cruise = false;
	Access_Log (access_mode == ACCESS_WALK ? "walk mode" : "look mode");
}

static void Access_ToggleCruise_f (void)
{
	if (!access_mouseonly.value)
		return;
	if (access_mode == ACCESS_WALK)
	{
		Access_Log ("cruise refused (walk mode)");
		return;
	}
	access_cruise = !access_cruise;
	Access_Log (access_cruise ? "cruise on" : "cruise off");
}

/* ------------------------------------------------------------- lifecycle */

void Access_Init (void)
{
	Cvar_RegisterVariable (&access_mouseonly);
	Cvar_RegisterVariable (&access_move_profile);
	Cvar_RegisterVariable (&access_throttle_gain);
	Cvar_RegisterVariable (&access_velocity_gain);
	Cvar_RegisterVariable (&access_deadzone);
	Cvar_RegisterVariable (&access_curve);
	Cvar_RegisterVariable (&access_walkspeed);
	Cvar_RegisterVariable (&access_tremor);
	Cvar_RegisterVariable (&access_turnrate);
	Cvar_RegisterVariable (&access_throttle_decay);
	Cvar_RegisterVariable (&access_toggle_button);
	Cvar_RegisterVariable (&access_longpress_button);
	Cvar_RegisterVariable (&access_longpress_ms);
	Cvar_RegisterVariable (&access_layer_timeout);
	Cvar_RegisterVariable (&access_layer_cmd1);
	Cvar_RegisterVariable (&access_layer_cmd2);
	Cvar_RegisterVariable (&access_doubleclick_button);
	Cvar_RegisterVariable (&access_doubleclick_ms);
	Cvar_RegisterVariable (&access_doubleclick_command);
	Cvar_RegisterVariable (&access_cruise_speed);
	Cvar_RegisterVariable (&access_idle_timeout);
	Cvar_RegisterVariable (&access_hud);
	Cvar_RegisterVariable (&access_sounds);
	Cvar_RegisterVariable (&access_log);

	Cmd_AddCommand ("access_toggle_mode", Access_ToggleMode_f);
	Cmd_AddCommand ("access_toggle_cruise", Access_ToggleCruise_f);
}

void Access_Reset (void)
{
	if (!access_mouseonly.value)
		return;
	if (access_mode != ACCESS_LOOK || access_cruise)
	{
		access_mode = ACCESS_LOOK;
		access_throttle = 0;
		access_cruise = false;
		Access_Log ("reset to look mode");
	}
}

void Access_Frame (float frametime)
{
	if (!access_mouseonly.value)
		return;
	/* Task 4: pitch easing. Task 5: safety resets. Task 6: gestures. */
}

/* ---------------------------------------------------------------- input */

void Access_ButtonEvent (int keynum, int down, unsigned int ms)
{
	if (!access_mouseonly.value)
	{
		Key_Event (keynum, down);
		return;
	}
	/* Task 5 consumes the toggle button; Task 6 adds gestures;
	   Task 8 intercepts menu-mode clicks. */
	Key_Event (keynum, down);
}

void Access_MouseMove (usercmd_t *cmd, int mx, int my)
{
	float	fx, fy;

	if (!access_mouseonly.value)
		return;

	/* Look mode (and Walk, until Task 4): vanilla mlook semantics */
	fx = mx * sensitivity.value;
	fy = my * sensitivity.value;

	if (in_strafe.state & 1)
		cmd->sidemove += m_side.value * fx;
	else
		cl.viewangles[YAW] -= m_yaw.value * fx;

	V_StopPitchDrift ();

	cl.viewangles[PITCH] += m_pitch.value * fy;
	if (cl.viewangles[PITCH] > 80)
		cl.viewangles[PITCH] = 80;
	if (cl.viewangles[PITCH] < -70)
		cl.viewangles[PITCH] = -70;
}

/* ----------------------------------------------------------------- HUD */

void Access_DrawHUD (void)
{
	/* Task 7 */
}

/* ---------------------------------------------------------- menu seams */

void Access_MenuFrame (void)
{
	/* Task 8 */
}

int Access_MenuItem (int index, int x, int y, int w, int h, int *cursor)
{
	(void)index; (void)x; (void)y; (void)w; (void)h; (void)cursor;
	return 0;
}

int Access_MenuHovered (int index)
{
	(void)index;
	return 0;
}

int Access_ClickRect (int x, int y, int w, int h)
{
	(void)x; (void)y; (void)w; (void)h;
	return 0;
}
