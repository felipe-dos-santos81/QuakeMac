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
static float		tremor_x, tremor_y;	/* low-pass filter state */
static double		access_lastinput;	/* for the idle timeout (Task 5) */
static int		access_lasthealth = 100;
static keydest_t	access_lastdest = key_game;

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

static void Access_ForceLook (char *reason)
{
	if (access_mode == ACCESS_LOOK && !access_cruise)
		return;
	access_mode = ACCESS_LOOK;
	access_throttle = 0;
	access_cruise = false;
	Access_Log (va ("forced look mode (%s)", reason));
}

void Access_Frame (float frametime)
{
	float	step;

	if (!access_mouseonly.value)
		return;

	/* Walk mode: pitch stays level — ease toward the horizon */
	if (access_mode == ACCESS_WALK)
	{
		step = 120.0f * frametime;
		if (cl.viewangles[PITCH] > step)
			cl.viewangles[PITCH] -= step;
		else if (cl.viewangles[PITCH] < -step)
			cl.viewangles[PITCH] += step;
		else
			cl.viewangles[PITCH] = 0;
	}

	/* safety: death */
	if (cls.state == ca_connected)
	{
		if (cl.stats[STAT_HEALTH] <= 0 && access_lasthealth > 0)
			Access_ForceLook ("death");
		access_lasthealth = cl.stats[STAT_HEALTH];
	}
	else
		access_lasthealth = 100;

	/* safety: menu or console opened */
	if (key_dest != key_game && access_lastdest == key_game)
		Access_ForceLook ("menu/console");
	access_lastdest = key_dest;

	/* optional inactivity timeout (default off) */
	if (access_idle_timeout.value > 0 && access_mode == ACCESS_WALK
	    && realtime - access_lastinput > access_idle_timeout.value)
		Access_ForceLook ("idle timeout");
}

/* ---------------------------------------------------------------- input */

void Access_ButtonEvent (int keynum, int down, unsigned int ms)
{
	if (!access_mouseonly.value)
	{
		Key_Event (keynum, down);
		return;
	}

	access_lastinput = realtime;

	/* dedicated toggle button: engine-consumed, no binding honored */
	if (down && keynum == (int)access_toggle_button.value)
	{
		Access_ToggleMode_f ();
		return;
	}

	Key_Event (keynum, down);
}

static float Access_Curve (float v)
{
	float	e = access_curve.value;

	if (e == 1 || v == 0)
		return v;
	if (v > 0)
		return powf (v, e);
	return -powf (-v, e);
}

static float Access_WalkSpeedCap (void)
{
	float	cap = access_walkspeed.value;

	if (cap > cl_forwardspeed.value - 10)
		cap = cl_forwardspeed.value - 10;
	if (cap < 0)
		cap = 0;
	return cap;
}

void Access_MouseMove (usercmd_t *cmd, int mx, int my)
{
	float	fx, fy, v, cap, a;

	if (!access_mouseonly.value)
		return;

	fx = mx * sensitivity.value;
	fy = my * sensitivity.value;

	/* tremor low-pass */
	a = access_tremor.value;
	if (a > 0)
	{
		if (a >= 1)
			a = 0.99f;
		fx = a * fx + (1 - a) * tremor_x;
		fy = a * fy + (1 - a) * tremor_y;
	}
	tremor_x = fx;
	tremor_y = fy;

	/* dead zone */
	if (fabs (fx) < access_deadzone.value)
		fx = 0;
	if (fabs (fy) < access_deadzone.value)
		fy = 0;

	access_lastinput = realtime;

	if (access_mode == ACCESS_WALK)
	{
		V_StopPitchDrift ();

	/* X: turn, or sidestep while +strafe is held */
		if (in_strafe.state & 1)
		{
			v = Access_Curve (fx) * m_side.value;
			if (v > cl_sidespeed.value)
				v = cl_sidespeed.value;
			else if (v < -cl_sidespeed.value)
				v = -cl_sidespeed.value;
			cmd->sidemove += v;
		}
		else
		{
			v = fx * m_yaw.value;
			if (access_turnrate.value > 0)
			{
				cap = access_turnrate.value * (float)host_frametime;
				if (v > cap)
					v = cap;
				else if (v < -cap)
					v = -cap;
			}
			cl.viewangles[YAW] -= v;
		}

	/* Y: movement */
		cap = Access_WalkSpeedCap ();
		if (access_move_profile.value == 0)
		{
		/* throttle profile: deltas accumulate into a held throttle */
			access_throttle += fy * access_throttle_gain.value;
			if (access_throttle > 1)
				access_throttle = 1;
			else if (access_throttle < -1)
				access_throttle = -1;
			if (access_throttle_decay.value > 0)
			{
				access_throttle *= 1.0f - access_throttle_decay.value * (float)host_frametime;
				if (fabs (access_throttle) < 0.001f)
					access_throttle = 0;
			}
			cmd->forwardmove -= Access_Curve (access_throttle) * cap;
		}
		else
		{
		/* velocity profile: native m_forward semantics, per-frame */
			v = Access_Curve (fy * access_velocity_gain.value);
			if (v > cap)
				v = cap;
			else if (v < -cap)
				v = -cap;
			cmd->forwardmove -= v;
		}
		return;
	}

	/* Look mode: vanilla mlook semantics */
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

	/* cruise: constant forward in Look mode */
	if (access_cruise)
	{
		v = (access_cruise_speed.value > 0) ? access_cruise_speed.value
		                                    : cl_forwardspeed.value;
		cmd->forwardmove += v;
		if (cmd->forwardmove < 0)
		{
			access_cruise = false;
			Access_Log ("cruise cancelled (backward input)");
		}
	}
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
