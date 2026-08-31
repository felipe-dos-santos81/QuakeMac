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

/* gesture engine */
static qboolean	gest_down;	/* physical button held */
static qboolean	gest_pending;	/* a press is held pending */
static unsigned	gest_down_time;	/* ms timestamp of the pending press */
static qboolean	gest_tap;	/* released tap waiting out the dbl window */
static int	gest_tap_key;
static unsigned	gest_tap_time;

/* sticky layer */
static qboolean	access_layer;
static double	access_layer_until;

/* transient HUD label */
static char	access_label[32];
static double	access_label_until;

static void Access_Log (char *msg)
{
	if (access_log.value)
		Con_Printf ("access: %s\n", msg);
}

static void Access_Label (char *text)
{
	Q_strcpy (access_label, text);
	access_label_until = realtime + 1.5;
}

static void Access_Sound (char *name);	/* defined in the input section */

/* ------------------------------------------------------------- commands */

static void Access_ToggleMode_f (void)
{
	if (!access_mouseonly.value)
		return;
	access_mode = (access_mode == ACCESS_LOOK) ? ACCESS_WALK : ACCESS_LOOK;
	access_throttle = 0;
	if (access_mode == ACCESS_WALK)
	{
		access_cruise = false;
		access_lastinput = realtime;
	}
	if (access_mode == ACCESS_WALK)
	{
		Access_Log ("walk mode");
		Access_Label ("WALK MODE");
		Access_Sound ("misc/menu1.wav");
	}
	else
	{
		Access_Log ("look mode");
		Access_Label ("LOOK MODE");
		Access_Sound ("misc/menu2.wav");
	}
}

static void Access_ToggleCruise_f (void)
{
	if (!access_mouseonly.value)
		return;
	if (access_mode == ACCESS_WALK)
	{
		Access_Log ("cruise refused (walk mode)");
		Access_Sound ("misc/menu3.wav");
		return;
	}
	access_cruise = !access_cruise;
	if (access_cruise)
	{
		Access_Log ("cruise on");
		Access_Label ("CRUISE ON");
		Access_Sound ("misc/menu3.wav");
	}
	else
	{
		Access_Log ("cruise off");
		Access_Label ("CRUISE OFF");
		Access_Sound ("misc/menu3.wav");
	}
}

/* ------------------------------------------------------------- lifecycle */

static void Access_OpenLayer (void);	/* defined in the input section */
static void Access_CloseLayer (void);

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
	gest_pending = false;
	gest_tap = false;
	Access_CloseLayer ();
}

static void Access_ForceLook (char *reason)
{
	if (access_mode == ACCESS_LOOK && !access_cruise)
		return;
	access_mode = ACCESS_LOOK;
	access_throttle = 0;
	access_cruise = false;
	Access_Log (va ("forced look mode (%s)", reason));
	Access_Label ("LOOK MODE");
	Access_Sound ("misc/menu2.wav");
}

void Access_Frame (float frametime)
{
	static float	last_mouseonly = -1;
	float		step;

	/* kill-switch edge: clear transient gesture/layer/mode state whenever
	   access_mouseonly is toggled, so nothing stale survives an off->on cycle */
	if (last_mouseonly != access_mouseonly.value)
	{
		last_mouseonly = access_mouseonly.value;
		gest_pending = false;
		gest_tap = false;
		access_layer = false;
		access_mode = ACCESS_LOOK;
		access_throttle = 0;
		access_cruise = false;
	}

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

	/* gesture timers */
	{
		unsigned	now = (unsigned)SDL_GetTicks ();
		unsigned	dbl = (unsigned)access_doubleclick_ms.value;

		/* long-press threshold crossed: discard the press, open the layer */
		if (gest_pending && gest_down
		    && access_longpress_button.value >= K_MOUSE1
		    && now - gest_down_time >= (unsigned)access_longpress_ms.value)
		{
			gest_pending = false;
			Access_Log (va ("long-press on %s",
			                Key_KeynumToString ((int)access_longpress_button.value)));
			Access_OpenLayer ();
		}

		/* a tap that survived the double-click window is delivered */
		if (gest_tap && now - gest_tap_time > dbl)
		{
			gest_tap = false;
			Key_Event (gest_tap_key, true);
			Key_Event (gest_tap_key, false);
		}

		if (access_layer && realtime > access_layer_until)
			Access_CloseLayer ();
	}
}

/* ---------------------------------------------------------------- input */

static void Access_Sound (char *name)
{
	if (access_sounds.value)
		S_LocalSound (name);
}

static void Access_OpenLayer (void)
{
	access_layer = true;
	access_layer_until = realtime + access_layer_timeout.value;
	Access_Log ("layer open");
	Access_Label ("LAYER");
	Access_Sound ("buttons/switch02.wav");
}

static void Access_CloseLayer (void)
{
	if (!access_layer)
		return;
	access_layer = false;
	Access_Log ("layer closed");
	Access_Sound ("buttons/switch02.wav");
}

static qboolean Access_GestureButton (int keynum)
{
	static qboolean	warned_fire;

	/* never-on-fire rule: double-click on MOUSE1 is rejected */
	if (keynum == K_MOUSE1
	    && keynum == (int)access_doubleclick_button.value)
	{
		if (!warned_fire)
		{
			Con_Printf ("access: double-click on MOUSE1 (fire) is not allowed; ignoring\n");
			warned_fire = true;
		}
		return false;
	}

	if (keynum == (int)access_longpress_button.value
	    && access_longpress_button.value >= K_MOUSE1)
		return true;
	if (keynum == (int)access_doubleclick_button.value
	    && access_doubleclick_button.value >= K_MOUSE1)
		return true;
	return false;
}

static void Access_GestureEvent (int keynum, int down, unsigned int ms)
{
	unsigned	dbl = (unsigned)access_doubleclick_ms.value;

	if (down)
	{
		gest_down = true;
		if (gest_tap && keynum == gest_tap_key
		    && keynum == (int)access_doubleclick_button.value
		    && ms - gest_tap_time <= dbl)
		{
		/* second press inside the window: run the command, eat both */
			gest_tap = false;
			gest_pending = false;
			Access_Log (va ("double-click on %s", Key_KeynumToString (keynum)));
			if (access_doubleclick_command.string[0])
				Cbuf_AddText (va ("%s\n", access_doubleclick_command.string));
			return;
		}
		gest_pending = true;
		gest_down_time = ms;
		return;
	}

	/* release */
	gest_down = false;
	if (!gest_pending)
	{
		Key_Event (keynum, down);	/* release of a consumed press */
		return;
	}
	gest_pending = false;
	if (keynum == (int)access_doubleclick_button.value
	    && access_doubleclick_button.value >= K_MOUSE1)
	{
		/* hold the tap: a second press may still claim it */
		gest_tap = true;
		gest_tap_key = keynum;
		gest_tap_time = ms;
		return;
	}
	/* plain long-press button, released early: deliver the press */
	Key_Event (keynum, true);
	Key_Event (keynum, false);
}

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

	/* sticky layer: next M1/M2 click runs the layer commands */
	if (access_layer)
	{
		if (down)
		{
			if (keynum == K_MOUSE1)
			{
				Access_CloseLayer ();
				Cbuf_AddText (va ("%s\n", access_layer_cmd1.string));
				return;
			}
			if (keynum == K_MOUSE2)
			{
				Access_CloseLayer ();
				Cbuf_AddText (va ("%s\n", access_layer_cmd2.string));
				return;
			}
		}
		Key_Event (keynum, down);
		return;
	}

	if (Access_GestureButton (keynum))
	{
		Access_GestureEvent (keynum, down, ms);
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

	if (mx || my)
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
	char	line[40];
	int	x, y, w, mid, fill;

	if (!access_mouseonly.value || !access_hud.value)
		return;
	if (key_dest != key_game)
		return;

	x = 8;
	y = 8;

	Q_strcpy (line, access_mode == ACCESS_WALK ? "WALK" : "LOOK");
	if (access_cruise)
		Q_strcpy (line + Q_strlen (line), " CRUISE");
	Draw_String (x, y, line);

	/* throttle bar */
	w = 64;
	y += 10;
	Draw_Fill (x, y, w, 4, 0);		/* background (palette index 0) */
	mid = x + w / 2;
	Draw_Fill (mid, y, 1, 4, 15);		/* center notch */
	if (access_mode == ACCESS_WALK)
	{
		fill = (int)(access_throttle * (w / 2));
		if (fill > 0)
			Draw_Fill (mid, y, fill, 4, 12);
		else if (fill < 0)
			Draw_Fill (mid + fill, y, -fill, 4, 12);
	}
	else if (access_cruise)
		Draw_Fill (x, y, w, 4, 12);

	/* transient mode label, centered in the 2D space (vid.width may be
	   640 — never assume 320) */
	if (access_label[0] && realtime < access_label_until)
		Draw_String (vid.width / 2 - 4 * Q_strlen (access_label),
		             vid.height / 2 - 4, access_label);
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
