/*
in_sdl.c — SDL3 input module for the QuakeWorld client on macOS arm64.
Adapted copy of Quake/in_sdl.c, split out of QuakeWorld/client/
gl_vidsdl.c the same way; resolves against QuakeWorld/client headers,
not Quake's. Additions over Quake/in_sdl.c, mirroring the reference
gl_vidlinuxglx.c: the _windowed_mouse cvar (menu.c links it).

SDL3 notes:
- Event constants are SDL_EVENT_*; ev.key.key is an SDL_Keycode,
  ev.key.down a bool; ev.motion.xrel/yrel are floats.
- The reference's XF86DGA mouse and XGrabPointer machinery is replaced
  wholesale by SDL relative mouse mode (SDL_SetWindowRelativeMouseMode).
*/

#include <SDL3/SDL.h>

#include "quakedef.h"

extern SDL_Window *sdl_window;	/* owned by gl_vidsdl.c */

static qboolean        mouse_avail;
static qboolean        mouse_active;
static int   mx, my;
static int	old_mouse_x, old_mouse_y;

static cvar_t in_mouse = {"in_mouse", "1", false};
static cvar_t in_dgamouse = {"in_dgamouse", "1", false};
static cvar_t m_filter = {"m_filter", "0"};
/* QuakeWorld menu.c (M_AdjustSliders/M_Options_Draw) toggles this; defined
   by the reference gl_vidlinuxglx.c. Present here so the client links. */
cvar_t	_windowed_mouse = {"_windowed_mouse","0", true};

/*
===========
XLateSDLKey

Same coverage as XLateKey in gl_vidlinuxglx.c: letters, digits, F-keys,
arrows/navigation, keypad, modifiers, escape/tab/tilde, punctuation.
SDL3 keycodes in the ASCII range already carry the lowercase ASCII value.
===========
*/
static int XLateSDLKey(SDL_Keycode key)
{
	switch(key)
	{
		case SDLK_PAGEUP:	 return K_PGUP;
		case SDLK_PAGEDOWN:	 return K_PGDN;
		case SDLK_HOME:		 return K_HOME;
		case SDLK_END:		 return K_END;
		case SDLK_LEFT:		 return K_LEFTARROW;
		case SDLK_RIGHT:	 return K_RIGHTARROW;
		case SDLK_DOWN:		 return K_DOWNARROW;
		case SDLK_UP:		 return K_UPARROW;

		case SDLK_ESCAPE:	 return K_ESCAPE;
		case SDLK_KP_ENTER:
		case SDLK_RETURN:	 return K_ENTER;
		case SDLK_TAB:		 return K_TAB;

		case SDLK_F1:		 return K_F1;
		case SDLK_F2:		 return K_F2;
		case SDLK_F3:		 return K_F3;
		case SDLK_F4:		 return K_F4;
		case SDLK_F5:		 return K_F5;
		case SDLK_F6:		 return K_F6;
		case SDLK_F7:		 return K_F7;
		case SDLK_F8:		 return K_F8;
		case SDLK_F9:		 return K_F9;
		case SDLK_F10:		 return K_F10;
		case SDLK_F11:		 return K_F11;
		case SDLK_F12:		 return K_F12;

		case SDLK_BACKSPACE: return K_BACKSPACE;
		case SDLK_DELETE:	 return K_DEL;
		case SDLK_PAUSE:	 return K_PAUSE;

		case SDLK_LSHIFT:
		case SDLK_RSHIFT:	 return K_SHIFT;
		case SDLK_LCTRL:
		case SDLK_RCTRL:	 return K_CTRL;
		case SDLK_LALT:
		case SDLK_RALT:
		case SDLK_LGUI:	/* Command on Mac; XK_Meta_* mapped to K_ALT in the reference */
		case SDLK_RGUI:		 return K_ALT;

		case SDLK_INSERT:	 return K_INS;

		case SDLK_KP_5:		 return '5';
		case SDLK_KP_0:		 return '0';
		case SDLK_KP_MULTIPLY: return '*';
		case SDLK_KP_PLUS:	 return '+';
		case SDLK_KP_MINUS:	 return '-';
		case SDLK_KP_DIVIDE: return '/';
		case SDLK_KP_PERIOD: return '.';

		default:
			/* keypad digits 1-9 are contiguous in SDL3 */
			if (key >= SDLK_KP_1 && key <= SDLK_KP_9)
				return '1' + (key - SDLK_KP_1);
			/* ASCII-range keycodes: digits, lowercase letters, punctuation */
			if (key >= 32 && key < 127)
				return (int)key;
			break;
	}

	return 0;
}

/*
===========
install_grabs / uninstall_grabs

SDL relative mouse mode replaces the reference's null-cursor +
XGrabPointer + XF86DGA direct-video machinery: it hides the cursor and
reports motion as deltas.
===========
*/
static void install_grabs(void)
{
	/* Pointer capture is intentionally never engaged: the mouse must never
	   lock, so SDL relative mode stays off and the cursor remains free. */
	mouse_active = true;
}

static void uninstall_grabs(void)
{
	if (!sdl_window)
		return;

	SDL_SetWindowRelativeMouseMode(sdl_window, false);
	mouse_active = false;
}

/*
===========
HandleEvents

Pump the SDL event queue, translating key/mouse-button events to
Key_Event and accumulating relative mouse motion into mx/my, exactly
as the reference accumulates from MotionNotify (deltas scaled by 2,
as both its DGA and warp paths did).
===========
*/
static void HandleEvents(void)
{
	SDL_Event event;
	int b;

	if (!sdl_window)
		return;

	while (SDL_PollEvent(&event)) {

		switch (event.type) {
		case SDL_EVENT_KEY_DOWN:
		case SDL_EVENT_KEY_UP:
			Key_Event(XLateSDLKey(event.key.key), event.key.down);
			break;

		case SDL_EVENT_MOUSE_MOTION:
			if (mouse_active) {
				mx += (int)event.motion.xrel * 2;
				my += (int)event.motion.yrel * 2;
			}
			break;

		case SDL_EVENT_MOUSE_BUTTON_DOWN:
		case SDL_EVENT_MOUSE_BUTTON_UP:
			b = -1;
			if (event.button.button == SDL_BUTTON_LEFT)
				b = 0;
			else if (event.button.button == SDL_BUTTON_MIDDLE)
				b = 2;
			else if (event.button.button == SDL_BUTTON_RIGHT)
				b = 1;
			if (b >= 0)
				Key_Event(K_MOUSE1 + b, event.button.down);
			break;

		case SDL_EVENT_QUIT:
			Sys_Quit();
			break;
		}
	}
}

void IN_DeactivateMouse( void )
{
	if (!mouse_avail || !sdl_window)
		return;

	if (mouse_active) {
		uninstall_grabs();
		mouse_active = false;
	}
}

static void IN_ActivateMouse( void )
{
	if (!mouse_avail || !sdl_window)
		return;

	if (!mouse_active) {
		mx = my = 0; // don't spazz
		install_grabs();
		mouse_active = true;
	}
}

void Sys_SendKeyEvents(void)
{
	HandleEvents();
}

void IN_Init(void)
{
	Cvar_RegisterVariable (&_windowed_mouse);
	Cvar_RegisterVariable (&in_mouse);
	Cvar_RegisterVariable (&in_dgamouse);
	Cvar_RegisterVariable (&m_filter);

	/* Note: the reference driver never sets mouse_avail, leaving its mouse
	   path dead; this driver enables it so relative-mode motion reaches
	   IN_MouseMove. The mouse is never grabbed. */
	mouse_avail = true;
	mouse_active = true;
}

void IN_Shutdown(void)
{
}

/*
===========
IN_Commands
===========
*/
void IN_Commands (void)
{
	if (!sdl_window)
		return;

	if (key_dest == key_game)
		IN_ActivateMouse();
	else
		IN_DeactivateMouse ();
}

/*
===========
IN_Move
===========
*/
void IN_MouseMove (usercmd_t *cmd)
{
	if (!mouse_avail)
		return;

	if (m_filter.value)
	{
		mx = (mx + old_mouse_x) * 0.5;
		my = (my + old_mouse_y) * 0.5;
	}
	old_mouse_x = mx;
	old_mouse_y = my;

	mx *= sensitivity.value;
	my *= sensitivity.value;

// add mouse X/Y movement to cmd
	if ( (in_strafe.state & 1) || (lookstrafe.value && (in_mlook.state & 1) ))
		cmd->sidemove += m_side.value * mx;
	else
		cl.viewangles[YAW] -= m_yaw.value * mx;

	if (in_mlook.state & 1)
		V_StopPitchDrift ();

	if ( (in_mlook.state & 1) && !(in_strafe.state & 1))
	{
		cl.viewangles[PITCH] += m_pitch.value * my;
		if (cl.viewangles[PITCH] > 80)
			cl.viewangles[PITCH] = 80;
		if (cl.viewangles[PITCH] < -70)
			cl.viewangles[PITCH] = -70;
	}
	else
	{
		if ((in_strafe.state & 1) && noclip_anglehack)
			cmd->upmove -= m_forward.value * my;
		else
			cmd->forwardmove -= m_forward.value * my;
	}
	mx = my = 0;
}

void IN_Move (usercmd_t *cmd)
{
	IN_MouseMove(cmd);
}
