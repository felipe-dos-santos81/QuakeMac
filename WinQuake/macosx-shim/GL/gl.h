#include <OpenGL/gl.h>

/* Apple's <OpenGL/gl.h> has no APIENTRY (a Windows calling-convention
   decoration from <windows.h>). The 1998 GL-client headers (e.g.
   QW/client/glquake.h) use it in function-pointer typedefs; the original
   Linux build got it from Mesa. Empty is correct off Windows. */
#ifndef APIENTRY
#define APIENTRY
#endif
