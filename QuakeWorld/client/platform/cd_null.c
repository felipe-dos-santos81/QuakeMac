#include "quakedef.h"

void CDAudio_Play(byte track, qboolean looping)
{
}


void CDAudio_Stop(void)
{
}


/* referenced by cl_parse.c (server-triggered cd pause); present in
   Quake/cd_null.c, added here so the QuakeWorld client links */
void CDAudio_Pause(void)
{
}


void CDAudio_Resume(void)
{
}


void CDAudio_Update(void)
{
}


int CDAudio_Init(void)
{
	return 0;
}


void CDAudio_Shutdown(void)
{
}