/*
gl_vidsdl.c — SDL3 video/GL/input driver for GLQuake on macOS arm64.
Replaces gl_vidlinuxglx.c (X11/GLX/DGA). Structure mirrors that file
section by section; only the windowing calls change.

SDL3 notes:
- Event constants are SDL_EVENT_*; ev.key.key is an SDL_Keycode,
  ev.key.down a bool; ev.motion.xrel/yrel are floats.
- The reference's XF86DGA mouse and XGrabPointer machinery is replaced
  wholesale by SDL relative mouse mode (SDL_SetWindowRelativeMouseMode).
- GL context is a compatibility-profile context on SDL_WINDOW_OPENGL,
  swapped with SDL_GL_SwapWindow.
*/

#include <dlfcn.h>
#include <signal.h>

#include <SDL3/SDL.h>

#include "quakedef.h"

/* Apple's <OpenGL/gl.h> does not pull in the EXT shared-palette token */
#ifndef GL_SHARED_TEXTURE_PALETTE_EXT
#define GL_SHARED_TEXTURE_PALETTE_EXT 0x81FB
#endif

#define WARP_WIDTH              320
#define WARP_HEIGHT             200

static SDL_Window *sdl_window = NULL;
static SDL_GLContext sdl_glctx = NULL;

static int scr_width, scr_height;

unsigned short	d_8to16table[256];
unsigned		d_8to24table[256];
unsigned char	d_15to8table[65536];

cvar_t	vid_mode = {"vid_mode","0",false};

static qboolean        mouse_avail;
static qboolean        mouse_active;
static int   mx, my;
static int	old_mouse_x, old_mouse_y;

static cvar_t in_mouse = {"in_mouse", "1", false};
static cvar_t in_dgamouse = {"in_dgamouse", "1", false};
static cvar_t m_filter = {"m_filter", "0"};

/*-----------------------------------------------------------------------*/

//int		texture_mode = GL_NEAREST;
//int		texture_mode = GL_NEAREST_MIPMAP_NEAREST;
//int		texture_mode = GL_NEAREST_MIPMAP_LINEAR;
int		texture_mode = GL_LINEAR;
//int		texture_mode = GL_LINEAR_MIPMAP_NEAREST;
//int		texture_mode = GL_LINEAR_MIPMAP_LINEAR;

int		texture_extension_number = 1;

float		gldepthmin, gldepthmax;

cvar_t	gl_ztrick = {"gl_ztrick","1"};

const char *gl_vendor;
const char *gl_renderer;
const char *gl_version;
const char *gl_extensions;

void (*qglColorTableEXT) (int, int, int, int, int, const void*);
void (*qgl3DfxSetPaletteEXT) (GLuint *);

static float vid_gamma = 1.0;

qboolean is8bit = false;
qboolean isPermedia = false;
qboolean gl_mtexable = false;

/*-----------------------------------------------------------------------*/
void D_BeginDirectRect (int x, int y, byte *pbitmap, int width, int height)
{
}

void D_EndDirectRect (int x, int y, int width, int height)
{
}

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

static void IN_DeactivateMouse( void )
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

void VID_Shutdown(void)
{
	if (!sdl_glctx || !sdl_window)
		return;
	IN_DeactivateMouse();
	if (sdl_window) {
		if (sdl_glctx)
			SDL_GL_DestroyContext(sdl_glctx);
		SDL_DestroyWindow(sdl_window);
	}
	SDL_QuitSubSystem(SDL_INIT_VIDEO);
	sdl_window = NULL;
	sdl_glctx = NULL;
}

void signal_handler(int sig)
{
	printf("Received signal %d, exiting...\n", sig);
	Sys_Quit();
	exit(0);
}

void InitSig(void)
{
	signal(SIGHUP, signal_handler);
	signal(SIGINT, signal_handler);
	signal(SIGQUIT, signal_handler);
	signal(SIGILL, signal_handler);
	signal(SIGTRAP, signal_handler);
	signal(SIGIOT, signal_handler);
	signal(SIGBUS, signal_handler);
	signal(SIGFPE, signal_handler);
	signal(SIGSEGV, signal_handler);
	signal(SIGTERM, signal_handler);
}

void VID_ShiftPalette(unsigned char *p)
{
//	VID_SetPalette(p);
}

void	VID_SetPalette (unsigned char *palette)
{
	byte	*pal;
	unsigned r,g,b;
	unsigned v;
	int     r1,g1,b1;
	int		j,k,l,m;
	unsigned short i;
	unsigned	*table;
	FILE *f;
	char s[255];
	int dist, bestdist;

//
// 8 8 8 encoding
//
	pal = palette;
	table = d_8to24table;
	for (i=0 ; i<256 ; i++)
	{
		r = pal[0];
		g = pal[1];
		b = pal[2];
		pal += 3;

		v = (255<<24) + (r<<0) + (g<<8) + (b<<16);
		*table++ = v;
	}
	d_8to24table[255] &= 0xffffff;	// 255 is transparent

	for (i=0; i < (1<<15); i++) {
		/* Maps
		000000000000000
		000000000011111 = Red  = 0x1F
		000001111100000 = Blue = 0x03E0
		111110000000000 = Grn  = 0x7C00
		*/
		r = ((i & 0x1F) << 3)+4;
		g = ((i & 0x03E0) >> 2)+4;
		b = ((i & 0x7C00) >> 7)+4;
		pal = (unsigned char *)d_8to24table;
		for (v=0,k=0,bestdist=10000*10000; v<256; v++,pal+=4) {
			r1 = (int)r - (int)pal[0];
			g1 = (int)g - (int)pal[1];
			b1 = (int)b - (int)pal[2];
			dist = (r1*r1)+(g1*g1)+(b1*b1);
			if (dist < bestdist) {
				k=v;
				bestdist = dist;
			}
		}
		d_15to8table[i]=k;
	}
}

void CheckMultiTextureExtensions(void)
{
	void *prjobj;

	if (strstr(gl_extensions, "GL_SGIS_multitexture ") && !COM_CheckParm("-nomtex")) {
		Con_Printf("Found GL_SGIS_multitexture...\n");

		if ((prjobj = dlopen(NULL, RTLD_LAZY)) == NULL) {
			Con_Printf("Unable to open symbol list for main program.\n");
			return;
		}

		qglMTexCoord2fSGIS = (void *) dlsym(prjobj, "glMTexCoord2fSGIS");
		qglSelectTextureSGIS = (void *) dlsym(prjobj, "glSelectTextureSGIS");

		if (qglMTexCoord2fSGIS && qglSelectTextureSGIS) {
			Con_Printf("Multitexture extensions found.\n");
			gl_mtexable = true;
		} else
			Con_Printf("Symbol not found, disabled.\n");

		dlclose(prjobj);
	}
}

/*
===============
GL_Init
===============
*/
void GL_Init (void)
{
	gl_vendor = glGetString (GL_VENDOR);
	Con_Printf ("GL_VENDOR: %s\n", gl_vendor);
	gl_renderer = glGetString (GL_RENDERER);
	Con_Printf ("GL_RENDERER: %s\n", gl_renderer);

	gl_version = glGetString (GL_VERSION);
	Con_Printf ("GL_VERSION: %s\n", gl_version);
	gl_extensions = glGetString (GL_EXTENSIONS);
	Con_Printf ("GL_EXTENSIONS: %s\n", gl_extensions);

//	Con_Printf ("%s %s\n", gl_renderer, gl_version);

	CheckMultiTextureExtensions ();

	glClearColor (1,0,0,0);
	glCullFace(GL_FRONT);
	glEnable(GL_TEXTURE_2D);

	glEnable(GL_ALPHA_TEST);
	glAlphaFunc(GL_GREATER, 0.666);

	glPolygonMode (GL_FRONT_AND_BACK, GL_FILL);
	glShadeModel (GL_FLAT);

	glTexParameterf(GL_TEXTURE_2D, GL_TEXTURE_MIN_FILTER, GL_NEAREST);
	glTexParameterf(GL_TEXTURE_2D, GL_TEXTURE_MAG_FILTER, GL_NEAREST);
	glTexParameterf(GL_TEXTURE_2D, GL_TEXTURE_WRAP_S, GL_REPEAT);
	glTexParameterf(GL_TEXTURE_2D, GL_TEXTURE_WRAP_T, GL_REPEAT);

	glBlendFunc (GL_SRC_ALPHA, GL_ONE_MINUS_SRC_ALPHA);

//	glTexEnvf(GL_TEXTURE_ENV, GL_TEXTURE_ENV_MODE, GL_MODULATE);
	glTexEnvf(GL_TEXTURE_ENV, GL_TEXTURE_ENV_MODE, GL_REPLACE);
}

/*
=================
GL_BeginRendering

=================
*/
void GL_BeginRendering (int *x, int *y, int *width, int *height)
{
	extern cvar_t gl_clear;

	*x = *y = 0;
	*width = scr_width;
	*height = scr_height;

//    if (!wglMakeCurrent( maindc, baseRC ))
//		Sys_Error ("wglMakeCurrent failed");

//	glViewport (*x, *y, *width, *height);
}


void GL_EndRendering (void)
{
	glFlush();
	SDL_GL_SwapWindow(sdl_window);
}

qboolean VID_Is8bit(void)
{
	return is8bit;
}

void VID_Init8bitPalette(void)
{
	// Check for 8bit Extensions and initialize them.
	int i;
	void *prjobj;

	if ((prjobj = dlopen(NULL, RTLD_LAZY)) == NULL) {
		Con_Printf("Unable to open symbol list for main program.\n");
		return;
	}

	if (strstr(gl_extensions, "3DFX_set_global_palette") &&
		(qgl3DfxSetPaletteEXT = dlsym(prjobj, "gl3DfxSetPaletteEXT")) != NULL) {
		GLubyte table[256][4];
		char *oldpal;

		Con_SafePrintf("8-bit GL extensions enabled.\n");
		glEnable( GL_SHARED_TEXTURE_PALETTE_EXT );
		oldpal = (char *) d_8to24table; //d_8to24table3dfx;
		for (i=0;i<256;i++) {
			table[i][2] = *oldpal++;
			table[i][1] = *oldpal++;
			table[i][0] = *oldpal++;
			table[i][3] = 255;
			oldpal++;
		}
		qgl3DfxSetPaletteEXT((GLuint *)table);
		is8bit = true;

	} else if (strstr(gl_extensions, "GL_EXT_shared_texture_palette") &&
		(qglColorTableEXT = dlsym(prjobj, "glColorTableEXT")) != NULL) {
		char thePalette[256*3];
		char *oldPalette, *newPalette;

		Con_SafePrintf("8-bit GL extensions enabled.\n");
		glEnable( GL_SHARED_TEXTURE_PALETTE_EXT );
		oldPalette = (char *) d_8to24table; //d_8to24table3dfx;
		newPalette = thePalette;
		for (i=0;i<256;i++) {
			*newPalette++ = *oldPalette++;
			*newPalette++ = *oldPalette++;
			*newPalette++ = *oldPalette++;
			oldPalette++;
		}
		qglColorTableEXT(GL_SHARED_TEXTURE_PALETTE_EXT, GL_RGB, 256, GL_RGB, GL_UNSIGNED_BYTE, (void *) thePalette);
		is8bit = true;
	}

	dlclose(prjobj);
}

static void Check_Gamma (unsigned char *pal)
{
	float	f, inf;
	unsigned char	palette[768];
	int		i;

	if ((i = COM_CheckParm("-gamma")) == 0) {
		if ((gl_renderer && strstr(gl_renderer, "Voodoo")) ||
			(gl_vendor && strstr(gl_vendor, "3Dfx")))
			vid_gamma = 1;
		else
			vid_gamma = 0.7; // default to 0.7 on non-3dfx hardware
	} else
		vid_gamma = Q_atof(com_argv[i+1]);

	for (i=0 ; i<768 ; i++)
	{
		f = pow ( (pal[i]+1)/256.0 , vid_gamma );
		inf = f*255 + 0.5;
		if (inf < 0)
			inf = 0;
		if (inf > 255)
			inf = 255;
		palette[i] = inf;
	}

	memcpy (pal, palette, sizeof(palette));
}

void VID_Init(unsigned char *palette)
{
	int i;
	char	gldir[MAX_OSPATH];
	int width = 1024, height = 768;

	Cvar_RegisterVariable (&vid_mode);
	Cvar_RegisterVariable (&in_mouse);
	Cvar_RegisterVariable (&in_dgamouse);
	Cvar_RegisterVariable (&m_filter);
	Cvar_RegisterVariable (&gl_ztrick);

	vid.maxwarpwidth = WARP_WIDTH;
	vid.maxwarpheight = WARP_HEIGHT;
	vid.colormap = host_colormap;
	vid.fullbright = 256 - LittleLong (*((int *)vid.colormap + 2048));

// interpret command-line params

// set vid parameters
	if ((i = COM_CheckParm("-window")) != 0)
		;	// always windowed on this driver; accepted for compatibility

	if ((i = COM_CheckParm("-width")) != 0)
		width = atoi(com_argv[i+1]);

	if ((i = COM_CheckParm("-height")) != 0)
		height = atoi(com_argv[i+1]);

	if ((i = COM_CheckParm("-conwidth")) != 0)
		vid.conwidth = Q_atoi(com_argv[i+1]);
	else
		vid.conwidth = 640;

	vid.conwidth &= 0xfff8; // make it a multiple of eight

	if (vid.conwidth < 320)
		vid.conwidth = 320;

	// pick a conheight that matches with correct aspect
	vid.conheight = vid.conwidth*3 / 4;

	if ((i = COM_CheckParm("-conheight")) != 0)
		vid.conheight = Q_atoi(com_argv[i+1]);
	if (vid.conheight < 200)
		vid.conheight = 200;

	if (!SDL_InitSubSystem(SDL_INIT_VIDEO))
		Sys_Error ("SDL_InitSubSystem(SDL_INIT_VIDEO) failed: %s", SDL_GetError());

	SDL_GL_SetAttribute(SDL_GL_RED_SIZE, 1);
	SDL_GL_SetAttribute(SDL_GL_GREEN_SIZE, 1);
	SDL_GL_SetAttribute(SDL_GL_BLUE_SIZE, 1);
	SDL_GL_SetAttribute(SDL_GL_DOUBLEBUFFER, 1);
	SDL_GL_SetAttribute(SDL_GL_DEPTH_SIZE, 1);
	SDL_GL_SetAttribute(SDL_GL_CONTEXT_PROFILE_MASK,
	                    SDL_GL_CONTEXT_PROFILE_COMPATIBILITY);

	sdl_window = SDL_CreateWindow("GLQuake", width, height, SDL_WINDOW_OPENGL);
	if (!sdl_window)
		Sys_Error ("SDL_CreateWindow failed: %s", SDL_GetError());

	sdl_glctx = SDL_GL_CreateContext(sdl_window);
	if (!sdl_glctx)
		Sys_Error ("SDL_GL_CreateContext failed: %s", SDL_GetError());

	SDL_GL_MakeCurrent(sdl_window, sdl_glctx);

	scr_width = width;
	scr_height = height;

	if (vid.conheight > height)
		vid.conheight = height;
	if (vid.conwidth > width)
		vid.conwidth = width;
	vid.width = vid.conwidth;
	vid.height = vid.conheight;

	vid.aspect = ((float)vid.height / (float)vid.width) * (320.0 / 240.0);
	vid.numpages = 2;

	InitSig(); // trap evil signals

	GL_Init();

	sprintf (gldir, "%s/glquake", com_gamedir);
	Sys_mkdir (gldir);

	VID_SetPalette(palette);

	// Check for 3DFX Extensions and initialize them.
	VID_Init8bitPalette();

	// the SDL window is ready for play; the mouse is never grabbed
	mouse_avail = true;
	mouse_active = true;

	Con_SafePrintf ("Video mode %dx%d initialized.\n", width, height);

	vid.recalc_refdef = 1;				// force a surface cache flush
}

void Sys_SendKeyEvents(void)
{
	HandleEvents();
}

void Force_CenterView_f (void)
{
	cl.viewangles[PITCH] = 0;
}

void IN_Init(void)
{
	/* Note: the reference driver never sets mouse_avail, leaving its mouse
	   path dead; this driver enables it so relative-mode motion reaches
	   IN_MouseMove. */
	mouse_avail = true;
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
