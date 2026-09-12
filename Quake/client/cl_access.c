/*
cl_access.c — mouse-only control: Look/Walk modes, throttle & velocity
movement profiles, gesture engine, safety resets, point-and-click
menus, HUD feedback.

Control scheme (see configs/autoexec-mouseonly.cfg): MOUSE1 fires, a
MOUSE1 double-click jumps, MOUSE2 toggles Look/Walk (the boxed HUD
label is a click fallback). Walk mode moves on Y and sidesteps on X,
and levels the view on entry. During demo playback any click opens
the main menu.

Spec + plan: docs/superpowers/2026-08-30-mouse-only-control.md

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

/* HUD mode-button: centered, 50% opaque while the mouse moves, fully
   opaque once the mouse rests.  Geometry is derived in
   Access_ButtonRect from a 4-character label ("WALK"/"LOOK") plus
   padding, in vid.width x vid.height screen space. */
#define ACCESS_BTN_ALPHA     0.5f
#define ACCESS_BTN_IDLE_MS   500
#define ACCESS_BTN_PAD_X     6
#define ACCESS_BTN_PAD_Y     2

/* ---------------------------------------------------------------- cvars */

cvar_t	access_mouseonly = {"access_mouseonly", "1", true};
cvar_t	access_move_profile = {"access_move_profile", "0", true};
cvar_t	access_throttle_gain = {"access_throttle_gain", "0.002", true};
cvar_t	access_velocity_gain = {"access_velocity_gain", "1", true};
cvar_t	access_deadzone = {"access_deadzone", "0", true};
cvar_t	access_curve = {"access_curve", "1", true};
cvar_t	access_walkspeed = {"access_walkspeed", "190", true};
cvar_t	access_tremor = {"access_tremor", "0", true};
cvar_t	access_throttle_decay = {"access_throttle_decay", "0", true};
cvar_t	access_toggle_button = {"access_toggle_button", "201", true}; /* K_MOUSE2 */
cvar_t	access_longpress_button = {"access_longpress_button", "0", true};
cvar_t	access_longpress_ms = {"access_longpress_ms", "400", true};
cvar_t	access_layer_timeout = {"access_layer_timeout", "3", true};
cvar_t	access_layer_cmd1 = {"access_layer_cmd1", "save quick"};
cvar_t	access_layer_cmd2 = {"access_layer_cmd2", "togglemenu"};
cvar_t	access_doubleclick_button = {"access_doubleclick_button", "0", true};
cvar_t	access_doubleclick_ms = {"access_doubleclick_ms", "350", true};
cvar_t	access_doubleclick_command = {"access_doubleclick_command", ""};
cvar_t	access_idle_timeout = {"access_idle_timeout", "0", true};
cvar_t	access_hud = {"access_hud", "1", true};
cvar_t	access_sounds = {"access_sounds", "1", true};
cvar_t	access_log = {"access_log", "1", true};

/* ---------------------------------------------------------------- state */

static int		access_mode = ACCESS_LOOK;
static float		access_throttle;
static float		tremor_x, tremor_y;	/* low-pass filter state */
static double		access_lastinput;	/* for the idle timeout (Task 5) */
static int		access_lasthealth = 100;
static keydest_t	access_lastdest = key_game;

/* gesture engine */
static qboolean	gest_down;	/* physical button held */
static qboolean	gest_pending;	/* a long-press candidate is held pending */
static unsigned	gest_down_time;	/* ms timestamp of the pending press */
static unsigned	gest_dbltime;	/* ms timestamp of last double-click press */

/* sticky layer */
static qboolean	access_layer;
static double	access_layer_until;

/* transient HUD label */
static char	access_label[32];
static double	access_label_until;

/* -------------------------------------------------- menu pointer state */

#define ACCESS_MAX_MENU_ITEMS 64

typedef struct {
	int	index;
	int	x, y, w, h;
	int	*cursor;
} access_menuitem_t;

static access_menuitem_t	menu_items[ACCESS_MAX_MENU_ITEMS];
static int	menu_numitems;
static int	menu_hover = -1;
static float	menu_cx, menu_cy;	/* cursor in vid.width x vid.height space,
	                                   with the 320-based menu x-offset applied */

/* synthesized key, dispatched at the next frame boundary */
static int	menu_queued_key;
static int	*menu_queued_cursor;
static int	menu_queued_index;

/* one click lives for exactly one draw pass */
static qboolean	menu_click_pending;	/* set by the event pump */
static qboolean	menu_click_live;	/* set by Access_MenuFrame */
static float	menu_click_x, menu_click_y;

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
	/* entering walk mode levels the view: pitch snaps to the horizon,
	   yaw keeps whichever way the player was facing */
		cl.viewangles[PITCH] = 0;
		access_lastinput = realtime;
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
	Cvar_RegisterVariable (&access_idle_timeout);
	Cvar_RegisterVariable (&access_hud);
	Cvar_RegisterVariable (&access_sounds);
	Cvar_RegisterVariable (&access_log);

	Cmd_AddCommand ("access_toggle_mode", Access_ToggleMode_f);
}

void Access_Reset (void)
{
	if (!access_mouseonly.value)
		return;
	if (access_mode != ACCESS_LOOK)
	{
		access_mode = ACCESS_LOOK;
		access_throttle = 0;
		Access_Log ("reset to look mode");
	}
	gest_pending = false;
	gest_dbltime = 0;
	Access_CloseLayer ();
}

static void Access_ForceLook (char *reason)
{
	if (access_mode == ACCESS_LOOK)
		return;
	access_mode = ACCESS_LOOK;
	access_throttle = 0;
	Access_Log (va ("forced look mode (%s)", reason));
	Access_Label ("LOOK MODE");
	Access_Sound ("misc/menu2.wav");
}

void Access_Frame (float frametime)
{
	static float	last_mouseonly = -1;

	/* kill-switch edge: clear transient gesture/layer/mode state whenever
	   access_mouseonly is toggled, so nothing stale survives an off->on cycle */
	if (last_mouseonly != access_mouseonly.value)
	{
		last_mouseonly = access_mouseonly.value;
		gest_pending = false;
		gest_dbltime = 0;
		access_layer = false;
		access_mode = ACCESS_LOOK;
		access_throttle = 0;
		menu_click_pending = false;
		menu_click_live = false;
		menu_queued_key = 0;
		menu_queued_cursor = NULL;
	}

	if (!access_mouseonly.value)
		return;

	/* a closed menu cannot leave a live click or queued key behind */
	if (key_dest != key_menu)
	{
		menu_click_pending = false;
		menu_click_live = false;
		menu_queued_key = 0;
		menu_queued_cursor = NULL;
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

	/* long-press button: hold the press until the threshold */
		if (keynum == (int)access_longpress_button.value
		    && access_longpress_button.value >= K_MOUSE1)
		{
			gest_pending = true;
			gest_down_time = ms;
			return;
		}

	/* double-click button: presses are always delivered immediately so
	   fire is never delayed by the double-click wait; a second press
	   inside the window additionally runs the double-click command */
		if (keynum == (int)access_doubleclick_button.value
		    && access_doubleclick_button.value >= K_MOUSE1)
		{
			if (gest_dbltime && ms - gest_dbltime <= dbl)
			{
			/* a clean pair is consumed; reset so the next press
			   starts a new window instead of pairing with this one */
				gest_dbltime = 0;
				Access_Log (va ("double-click on %s",
				                Key_KeynumToString (keynum)));
				if (access_doubleclick_command.string[0])
					Cbuf_AddText (va ("%s\n",
					                  access_doubleclick_command.string));
			}
			else
				gest_dbltime = ms;
			Key_Event (keynum, true);
			return;
		}

		Key_Event (keynum, true);
		return;
	}

	/* release */
	gest_down = false;
	if (!gest_pending)
	{
		Key_Event (keynum, down);	/* release of a consumed or plain press */
		return;
	}
	gest_pending = false;
	/* long-press button released before the threshold: deliver the press */
	Key_Event (keynum, true);
	Key_Event (keynum, false);
}

static void Access_MenuButton (int keynum, int down)
{
	if (!down)
		return;
	if (keynum == K_MOUSE1)
	{
		menu_click_pending = true;
		menu_click_x = menu_cx;
		menu_click_y = menu_cy;
		if (menu_hover >= 0)
		{
			menu_queued_key = K_ENTER;
			menu_queued_cursor = menu_items[menu_hover].cursor;
			menu_queued_index = menu_items[menu_hover].index;
		}
	}
	else if (keynum == K_MOUSE2)
	{
		menu_queued_key = K_ESCAPE;
		menu_queued_cursor = NULL;
	}
}

/*
The HUD mode button lives in vid.width x vid.height screen space. The
OS cursor is never grabbed in this port, so its window coordinates map
to screen space the same way Access_MenuFrame maps the menu cursor —
minus the 320-based offset, because the HUD draws in raw vid coords.
*/
/* Centered WALK/LOOK button rect, derived every call from the current
   vid size so video-mode changes need no stored state.  Label is always
   4 characters. */
static void Access_ButtonRect (int *x, int *y, int *w, int *h)
{
	*w = 4 * 8 + 2 * ACCESS_BTN_PAD_X;
	*h = 8 + 2 * ACCESS_BTN_PAD_Y;
	*x = (vid.width  - *w) / 2;
	*y = (vid.height - *h) / 2;
}

static qboolean Access_CursorInButton (void)
{
	float	cx, cy;
	int	ww, wh;
	int	x, y, w, h;

	SDL_GetMouseState (&cx, &cy);
	SDL_GetWindowSize (sdl_window, &ww, &wh);
	if (ww <= 0 || wh <= 0)
		return false;
	cx = cx * (float)vid.width / ww;
	cy = cy * (float)vid.height / wh;

	Access_ButtonRect (&x, &y, &w, &h);
	return cx >= x - 2 && cx < x + w + 2
	    && cy >= y - 2 && cy < y + h + 2;
}

void Access_ButtonEvent (int keynum, int down, unsigned int ms)
{
	if (!access_mouseonly.value)
	{
		Key_Event (keynum, down);
		return;
	}

	access_lastinput = realtime;

	/* menus: point-and-click; buttons never fire gameplay bindings here */
	if (key_dest == key_menu)
	{
		if (M_BindGrabActive ())
			Key_Event (keynum, down);	/* rebinding flow needs raw keys */
		else if (!down)
			Key_Event (keynum, false);	/* releases must clear keydown[] */
		else
			Access_MenuButton (keynum, down);
		return;
	}

	/* demo playback: a click opens the main menu, exactly like the
	   keyboard keys do in Key_Event */
	if (cls.demoplayback)
	{
		if (down)
			M_ToggleMenu_f ();
		return;
	}

	/* HUD mode button: clicking the boxed LOOK/WALK label toggles —
	   the fallback for devices whose right button cannot be used */
	if (down && key_dest == key_game && Access_CursorInButton ())
	{
		Access_ToggleMode_f ();
		return;
	}

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

	/* demos drive the view themselves; local input must not fight them */
	if (cls.demoplayback)
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

	/* X: sidestep */
		v = Access_Curve (fx) * m_side.value;
		if (v > cl_sidespeed.value)
			v = cl_sidespeed.value;
		else if (v < -cl_sidespeed.value)
			v = -cl_sidespeed.value;
		cmd->sidemove += v;

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
}

/* ----------------------------------------------------------------- HUD */

void Access_DrawHUD (void)
{
	float	alpha;
	float	cx, cy;
	int	x, y, w, h;
	int	now;
	static float	last_x = -1, last_y = -1;
	static int	last_move_ms;

	if (!access_mouseonly.value || !access_hud.value)
		return;
	if (key_dest != key_game || cls.demoplayback)
		return;

	now = (int)SDL_GetTicks ();
	SDL_GetMouseState (&cx, &cy);
	if (cx != last_x || cy != last_y)
	{
		last_x = cx;
		last_y = cy;
		last_move_ms = now;
	}
	alpha = (now - last_move_ms > ACCESS_BTN_IDLE_MS)
	    ? 1.0f : ACCESS_BTN_ALPHA;

	Access_ButtonRect (&x, &y, &w, &h);
	Draw_FillAlpha (x, y, w, h, 12, alpha);
	Draw_FillAlpha (x + 1, y + 1, w - 2, h - 2, 0, alpha);
	Draw_StringAlpha (x + ACCESS_BTN_PAD_X, y + ACCESS_BTN_PAD_Y,
	                  access_mode == ACCESS_WALK ? "WALK" : "LOOK", alpha);
}

/* ---------------------------------------------------------- menu seams */

void Access_MenuFrame (void)
{
	float	mx, my;
	int	ww, wh;

	if (!access_mouseonly.value)
		return;

	menu_numitems = 0;
	menu_hover = -1;
	menu_click_live = menu_click_pending;
	menu_click_pending = false;

	if (key_dest != key_menu)
		return;

	SDL_GetMouseState (&mx, &my);
	SDL_GetWindowSize (sdl_window, &ww, &wh);
	if (ww <= 0 || wh <= 0)
		return;
	/* GL_Set2D (gl_draw.c:875) makes the 2D ortho space vid.width x
	   vid.height (640x480 by default, gl_vidsdl.c:391). menu.c M_Draw*
	   helpers render at x + ((vid.width-320)>>1) (menu.c:110); bring
	   the cursor into that same 320-based menu coordinate space so the
	   registered rects (raw M_Print coordinates) line up with drawn
	   items */
	menu_cx = mx * (float)vid.width / ww - ((vid.width - 320) >> 1);
	menu_cy = my * (float)vid.height / wh;

	/* dispatch last frame's queued action before this frame draws */
	if (menu_queued_key)
	{
		if (menu_queued_cursor)
			*menu_queued_cursor = menu_queued_index;
		Key_Event (menu_queued_key, true);
		Key_Event (menu_queued_key, false);
		menu_queued_key = 0;
		menu_queued_cursor = NULL;
	}
}

int Access_MenuItem (int index, int x, int y, int w, int h, int *cursor)
{
	access_menuitem_t	*it;

	if (!access_mouseonly.value || key_dest != key_menu)
		return 0;
	if (menu_numitems >= ACCESS_MAX_MENU_ITEMS)
		return 0;

	it = &menu_items[menu_numitems++];
	it->index = index;
	it->x = x - 2;			/* 2 px hit padding */
	it->y = y - 2;
	it->w = w + 4;
	it->h = h + 4;
	it->cursor = cursor;

	if (menu_cx >= it->x && menu_cx < it->x + it->w
	    && menu_cy >= it->y && menu_cy < it->y + it->h)
		menu_hover = menu_numitems - 1;

	return 0;
}

int Access_MenuHovered (int index)
{
	return menu_hover >= 0 && menu_items[menu_hover].index == index;
}

int Access_ClickRect (int x, int y, int w, int h)
{
	if (!access_mouseonly.value || !menu_click_live)
		return 0;
	if (menu_click_x >= x && menu_click_x < x + w
	    && menu_click_y >= y && menu_click_y < y + h)
	{
		menu_click_live = false;	/* one click, one consumer */
		return 1;
	}
	return 0;
}

void Access_MenuDrawHover (void)
{
	access_menuitem_t	*it;

	if (!access_mouseonly.value || key_dest != key_menu || menu_hover < 0)
		return;
	it = &menu_items[menu_hover];
	/* blink the same marker character the pages use for their cursors,
	   at the left edge of the hovered item's rect (menu coordinate space,
	   so apply the same centering offset the M_Draw* helpers use) */
	Draw_Character (it->x - 8 + ((vid.width - 320) >> 1), it->y,
	                12 + ((int)(realtime * 4) & 1));
}
