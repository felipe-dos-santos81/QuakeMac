/*
cl_access.h — mouse-only control (accessibility) module interface.

Every entry point degrades to vanilla behavior when access_mouseonly
is 0. Spec: docs/superpowers/2026-08-30-mouse-only-control.md

Engine convention: the including .c has already included quakedef.h
(this header deliberately includes nothing; quakedef.h has no guard).
*/

#ifndef CL_ACCESS_H
#define CL_ACCESS_H

void Access_Init (void);
void Access_Frame (float frametime);
void Access_ButtonEvent (int keynum, int down, unsigned int ms);
void Access_MouseMove (usercmd_t *cmd, int mx, int my);
void Access_Reset (void);
void Access_DrawHUD (void);

/* menu seams (bodies land in Task 8) */
void Access_MenuFrame (void);
int  Access_MenuItem (int index, int x, int y, int w, int h, int *cursor);
int  Access_MenuHovered (int index);
int  Access_ClickRect (int x, int y, int w, int h);
void Access_MenuDrawHover (void);

extern cvar_t access_mouseonly;

#endif /* CL_ACCESS_H */
