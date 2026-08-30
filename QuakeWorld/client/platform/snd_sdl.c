/*
snd_sdl.c — SDL3 audio driver for the QuakeWorld GL client (glqwcl) on
macOS arm64.

Adapted copy of Quake/snd_sdl.c (Task 3), verbatim except for this
header. Copied here so #include "quakedef.h" resolves against
QuakeWorld/client headers, not Quake's (Task 7 decision rule; see
gl_vidsdl.c).

Replaces snd_linux.c (/dev/dsp + mmap) with an SDL3 callback device.
Format/parm handling mirrors snd_linux.c:37-140.

SDL3 note: SDL_AudioSpec has no callback member. The device is opened with
SDL_OpenAudioDeviceStream(), which opens the device, creates and binds a
stream, and installs the get-callback in one call (the documented SDL2
callback-device migration path). The device starts paused and is resumed
with SDL_ResumeAudioStreamDevice(); destroying the stream closes the device.
*/

#include <stdlib.h>
#include <string.h>
#include <SDL3/SDL.h>
#include "quakedef.h"

static SDL_AudioStream *audio_stream;
static int snd_inited;
static int read_bytes;	/* bytes the callback has consumed, wrapped to [0, bufsize) */

static void SDLCALL snd_callback(void *userdata, SDL_AudioStream *stream,
                                 int additional_amount, int total_amount)
{
	int bufsize = shm->samples * (shm->samplebits / 8);
	int chunk = additional_amount;

	while (chunk > 0) {
		int pos = read_bytes % bufsize;
		int n = bufsize - pos;
		if (n > chunk)
			n = chunk;
		SDL_PutAudioStreamData(stream, shm->buffer + pos, n);
		read_bytes += n;
		if (read_bytes >= bufsize)
			read_bytes -= bufsize;	/* keep the counter bounded (one sub suffices) */
		chunk -= n;
	}
}

qboolean SNDDMA_Init(void)
{
	SDL_AudioSpec spec;
	int i;
	char *s;

	snd_inited = 0;

	if (!SDL_InitSubSystem(SDL_INIT_AUDIO)) {
		Con_Printf("Could not initialize SDL audio subsystem: %s\n", SDL_GetError());
		return 0;
	}

	shm = &sn;
	shm->splitbuffer = 0;

	s = getenv("QUAKE_SOUND_SAMPLEBITS");
	if (s) shm->samplebits = atoi(s);
	else if ((i = COM_CheckParm("-sndbits")) != 0)
		shm->samplebits = atoi(com_argv[i+1]);
	if (shm->samplebits != 16 && shm->samplebits != 8)
		shm->samplebits = 16;

	s = getenv("QUAKE_SOUND_SPEED");
	if (s) shm->speed = atoi(s);
	else if ((i = COM_CheckParm("-sndspeed")) != 0)
		shm->speed = atoi(com_argv[i+1]);
	else
		shm->speed = 44100;

	s = getenv("QUAKE_SOUND_CHANNELS");
	if (s) shm->channels = atoi(s);
	else if ((i = COM_CheckParm("-sndmono")) != 0)
		shm->channels = 1;
	else if ((i = COM_CheckParm("-sndstereo")) != 0)
		shm->channels = 2;
	else shm->channels = 2;

	/* round the ~1s ring up to a power of two: snd_mix.c masks ring indices, not modulos */
	shm->samples = 1;
	while (shm->samples < shm->speed * shm->channels)
		shm->samples <<= 1;
	shm->submission_chunk = 1;
	shm->buffer = (unsigned char *) malloc(shm->samples * (shm->samplebits / 8));
	if (!shm->buffer) {
		Con_Printf("Could not allocate sound ring\n");
		return 0;
	}
	memset(shm->buffer, 0, shm->samples * (shm->samplebits / 8));

	memset(&spec, 0, sizeof(spec));
	spec.freq = shm->speed;
	spec.channels = shm->channels;
	spec.format = (shm->samplebits == 16) ? SDL_AUDIO_S16LE : SDL_AUDIO_U8;

	/* reset the read cursor before the stream exists so the callback can never run on stale state */
	read_bytes = 0;
	shm->samplepos = 0;

	audio_stream = SDL_OpenAudioDeviceStream(SDL_AUDIO_DEVICE_DEFAULT_PLAYBACK,
	                                         &spec, snd_callback, NULL);
	if (!audio_stream) {
		Con_Printf("Could not open SDL audio device: %s\n", SDL_GetError());
		free(shm->buffer);
		shm->buffer = NULL;
		return 0;
	}
	SDL_ResumeAudioStreamDevice(audio_stream);

	snd_inited = 1;
	return 1;
}

int SNDDMA_GetDMAPos(void)
{
	if (!snd_inited)
		return 0;
	shm->samplepos = (read_bytes / (shm->samplebits / 8)) % shm->samples;
	return shm->samplepos;
}

void SNDDMA_Submit(void)
{
	/* callback-driven: nothing to push */
}

void SNDDMA_Shutdown(void)
{
	if (snd_inited) {
		SDL_DestroyAudioStream(audio_stream);
		audio_stream = NULL;
		free(shm->buffer);
		shm->buffer = NULL;
		snd_inited = 0;
	}
	SDL_QuitSubSystem(SDL_INIT_AUDIO);
}
