/*
Copyright (C) 2026 Id Software-derived QuakeMCP contributors

This program is free software; you can redistribute it and/or
modify it under the terms of the GNU General Public License
as published by the Free Software Foundation; either version 2
of the License, or (at your option) any later version.
*/

// q_mcp.c -- QuakeMCP bridge skeleton: token TCP, poll pump, ping op
//
// Out-of-process Python server talks line-JSON over loopback TCP.
// This file owns the socket, the per-instance token and request
// dispatch. Called on the main thread only (MCP_Poll from _Host_Frame
// and the SCR_ModalMessage loop). No game-state writes in this task.

#include "quakedef.h"

#include <sys/socket.h>
#include <netinet/in.h>
#include <arpa/inet.h>
#include <unistd.h>
#include <fcntl.h>
#include <errno.h>
#include <sys/stat.h>

#define MCP_LINE_MAX	65536
#define MCP_TOKEN_BYTES	32
#define MCP_POLL_BUDGET	0.05
#define MCP_RETRY_SECS	5.0

cvar_t	mcp_enabled = {"mcp_enabled", "0"};
cvar_t	mcp_port = {"mcp_port", "28900"};

static int	mcp_listen_fd = -1;
static int	mcp_client_fd = -1;
static char	mcp_token[2 * MCP_TOKEN_BYTES + 1];
static char	mcp_token_path[256];
static char	mcp_line[MCP_LINE_MAX + 1];
static int	mcp_line_len = 0;
static int	mcp_oversize_drops = 0;
static double	mcp_retry_at = 0;


/*
==================
MCP_SetNonblock
==================
*/
static void MCP_SetNonblock (int fd)
{
	int flags;

	flags = fcntl (fd, F_GETFL, 0);
	if (flags >= 0)
		fcntl (fd, F_SETFL, flags | O_NONBLOCK);
}

/*
==================
MCP_WriteToken

32 bytes from /dev/urandom as hex. Fail closed: no token, no endpoint.
Token file is created mode 0600. Never logged, never on the CLI.
==================
*/
static qboolean MCP_WriteToken (void)
{
	static const char hex[] = "0123456789abcdef";
	FILE *f;
	unsigned char raw[MCP_TOKEN_BYTES];
	char *tmpdir;
	int i;

	f = fopen ("/dev/urandom", "r");
	if (!f)
		return false;
	if (fread (raw, 1, sizeof (raw), f) != sizeof (raw))
	{
		fclose (f);
		return false;
	}
	fclose (f);

	for (i = 0; i < MCP_TOKEN_BYTES; i++)
	{
		mcp_token[2 * i] = hex[raw[i] >> 4];
		mcp_token[2 * i + 1] = hex[raw[i] & 15];
	}
	mcp_token[2 * MCP_TOKEN_BYTES] = 0;

	tmpdir = getenv ("TMPDIR");
	if (!tmpdir || !tmpdir[0])
		tmpdir = "/tmp";
	snprintf (mcp_token_path, sizeof (mcp_token_path),
		"%s/quakemcp-%d.token", tmpdir, (int)getpid ());

	f = fopen (mcp_token_path, "w");
	if (!f)
		return false;
	chmod (mcp_token_path, 0600);
	fputs (mcp_token, f);
	fclose (f);
	chmod (mcp_token_path, 0600);

	return true;
}

/*
==================
MCP_Setup

Bind loopback, single client, non-blocking. Port from -mcp_port CLI
first, mcp_port cvar second. Idempotent: safe to call every poll.
==================
*/
static void MCP_Setup (void)
{
	struct sockaddr_in addr;
	int fd, port, p, one;

	if (mcp_listen_fd >= 0)
		return;
	if (Sys_DoubleTime () < mcp_retry_at)
		return;

	port = (int)mcp_port.value;
	if (!port)
		port = 28900;
	p = COM_CheckParm ("-mcp_port");
	if (p && p + 1 < com_argc)
		port = atoi (com_argv[p + 1]);
	if (port <= 0 || port > 65535)
	{
		Con_Printf ("MCP: bad port %d\n", port);
		mcp_retry_at = Sys_DoubleTime () + MCP_RETRY_SECS;
		return;
	}

	if (!MCP_WriteToken ())
	{
		Con_Printf ("MCP: no token, endpoint disabled\n");
		mcp_retry_at = Sys_DoubleTime () + MCP_RETRY_SECS;
		return;
	}

	fd = socket (AF_INET, SOCK_STREAM, 0);
	if (fd < 0)
		goto fail;
	MCP_SetNonblock (fd);

	memset (&addr, 0, sizeof (addr));
	addr.sin_family = AF_INET;
	addr.sin_addr.s_addr = htonl (INADDR_LOOPBACK);
	addr.sin_port = htons ((unsigned short)port);
	one = 1;
	setsockopt (fd, SOL_SOCKET, SO_REUSEADDR, &one, sizeof (one));
	if (bind (fd, (struct sockaddr *)&addr, sizeof (addr)) < 0)
		goto fail;
	if (listen (fd, 1) < 0)
		goto fail;

	mcp_listen_fd = fd;
	Con_Printf ("MCP: listening on 127.0.0.1:%d\n", port);
	return;

fail:
	Con_Printf ("MCP: bind failed: %s\n", strerror (errno));
	if (fd >= 0)
		close (fd);
	unlink (mcp_token_path);
	mcp_token_path[0] = 0;
	mcp_retry_at = Sys_DoubleTime () + MCP_RETRY_SECS;
}

/*
==================
MCP_CloseClient
==================
*/
static void MCP_CloseClient (void)
{
	if (mcp_client_fd >= 0)
		close (mcp_client_fd);
	mcp_client_fd = -1;
	mcp_line_len = 0;
}

/*
==================
MCP_Field

Copy the string value of "name" from a flat JSON line. False if absent.
==================
*/
static qboolean MCP_Field (char *line, char *name, char *out, int outsize)
{
	char key[64];
	char *p, *q;
	int n;

	snprintf (key, sizeof (key), "\"%s\"", name);
	p = strstr (line, key);
	if (!p)
		return false;
	p = strchr (p + strlen (key), ':');
	if (!p)
		return false;
	p = strchr (p + 1, '"');
	if (!p)
		return false;
	p++;
	n = 0;
	for (q = p; *q && *q != '"' && n + 1 < outsize; q++)
	{
		if (*q == '\\' && q[1])
			q++;
		out[n++] = *q;
	}
	out[n] = 0;
	return true;
}

/*
==================
MCP_Send
==================
*/
static void MCP_Send (char *msg)
{
	int left, n;

	left = strlen (msg);
	while (left > 0 && mcp_client_fd >= 0)
	{
		n = send (mcp_client_fd, msg + strlen (msg) - left, left, 0);
		if (n <= 0)
		{
			if (errno == EAGAIN || errno == EWOULDBLOCK || errno == EINTR)
				continue;
			MCP_CloseClient ();
			return;
		}
		left -= n;
	}
}

/*
==================
MCP_Reply

Always JSON on one \n-terminated line. ok:false carries a frozen
error code (auth failures use POLICY_DENIED until Task 8 refines
policy; malformed lines use INVALID_CONTEXT).
==================
*/
static void MCP_Reply (char *id, qboolean ok, char *error, char *result)
{
	char idbuf[128], out[1024], *s, *d;
	int n;

	n = 0;
	for (s = id, d = idbuf; *s && n + 2 < (int)sizeof (idbuf); s++)
	{
		if (*s == '"' || *s == '\\')
			idbuf[n++] = '\\';
		idbuf[n++] = *s;
	}
	idbuf[n] = 0;

	if (ok)
		snprintf (out, sizeof (out),
			"{\"v\":1,\"id\":\"%s\",\"ok\":true,\"result\":{%s}}\n",
			idbuf, result);
	else
		snprintf (out, sizeof (out),
			"{\"v\":1,\"id\":\"%s\",\"ok\":false,\"error\":\"%s\",\"detail\":\"%s\"}\n",
			idbuf, error, result);
	MCP_Send (out);
}

/*
==================
MCP_HandleLine
==================
*/
static void MCP_HandleLine (char *line)
{
	char auth[128], id[128], op[64];

	if (!MCP_Field (line, "id", id, sizeof (id)))
		strcpy (id, "");
	if (!MCP_Field (line, "auth", auth, sizeof (auth))
		|| strcmp (auth, mcp_token) != 0)
	{
		MCP_Reply (id, false, "POLICY_DENIED", "bad auth");
		return;
	}
	if (!MCP_Field (line, "op", op, sizeof (op)))
	{
		MCP_Reply (id, false, "INVALID_CONTEXT", "malformed request");
		return;
	}

	if (!strcmp (op, "ping"))
	{
		MCP_Reply (id, true, NULL, "\"ready\":true");
		return;
	}

	MCP_Reply (id, false, "UNSUPPORTED_CAPABILITY", op);
}

/*
==================
MCP_PollClient

Drain complete lines within the poll budget. Oversize lines are
dropped and counted, never grown.
==================
*/
static void MCP_PollClient (double stop)
{
	char *nl;
	int n;

	for (;;)
	{
		if (Sys_DoubleTime () >= stop)
			return;
		if (mcp_client_fd < 0)
			return;
		if (mcp_line_len >= MCP_LINE_MAX)
		{
			mcp_line_len = 0;
			mcp_oversize_drops++;
			continue;
		}
		n = recv (mcp_client_fd, mcp_line + mcp_line_len,
			MCP_LINE_MAX - mcp_line_len, 0);
		if (n > 0)
		{
			mcp_line_len += n;
			mcp_line[mcp_line_len] = 0;
			while ((nl = strchr (mcp_line, '\n')) != NULL)
			{
				*nl = 0;
				MCP_HandleLine (mcp_line);
				if (mcp_client_fd < 0)
					return;
				n = strlen (nl + 1);
				memmove (mcp_line, nl + 1, n + 1);
				mcp_line_len = n;
				if (Sys_DoubleTime () >= stop)
					return;
			}
			continue;
		}
		if (n == 0)
		{
			MCP_CloseClient ();
			return;
		}
		if (errno == EAGAIN || errno == EWOULDBLOCK || errno == EINTR)
			return;
		MCP_CloseClient ();
		return;
	}
}

/*
==================
MCP_Init
==================
*/
void MCP_Init (void)
{
	Cvar_RegisterVariable (&mcp_enabled);
	Cvar_RegisterVariable (&mcp_port);

	mcp_listen_fd = -1;
	mcp_client_fd = -1;
	mcp_token[0] = 0;
	mcp_token_path[0] = 0;
	mcp_line_len = 0;
	mcp_oversize_drops = 0;
	mcp_retry_at = 0;
}

/*
==================
MCP_Poll

Main-thread pump. Lazy setup: +mcp_enabled 1 on the CLI lands via
stuffcmds after Host_Init, so the listener comes up on the first
poll that sees the cvar set. Disabled means silent and inert.
==================
*/
void MCP_Poll (void)
{
	double stop;
	int fd;

	if (!mcp_enabled.value)
		return;

	if (mcp_listen_fd < 0)
		MCP_Setup ();
	if (mcp_listen_fd < 0)
		return;

	stop = Sys_DoubleTime () + MCP_POLL_BUDGET;

	for (;;)
	{
		fd = accept (mcp_listen_fd, NULL, NULL);
		if (fd < 0)
			break;
		if (mcp_client_fd >= 0)
		{
			close (fd);	// single client: keep the first
			continue;
		}
		MCP_SetNonblock (fd);
		mcp_client_fd = fd;
		mcp_line_len = 0;
		if (Sys_DoubleTime () >= stop)
			return;
	}

	MCP_PollClient (stop);
}

/*
==================
MCP_Shutdown
==================
*/
void MCP_Shutdown (void)
{
	MCP_CloseClient ();
	if (mcp_listen_fd >= 0)
		close (mcp_listen_fd);
	mcp_listen_fd = -1;
	if (mcp_token_path[0])
		unlink (mcp_token_path);
	mcp_token_path[0] = 0;
	mcp_token[0] = 0;
}

/*
==================
MCP_FrameId
==================
*/
unsigned MCP_FrameId (void)
{
	return (unsigned)host_framecount;
}
