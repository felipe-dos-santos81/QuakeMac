/*
Copyright (C) 2026 Id Software-derived QuakeMCP contributors

This program is free software; you can redistribute it and/or
modify it under the terms of the GNU General Public License
as published by the Free Software Foundation; either version 2
of the License, or (at your option) any later version.
*/

// q_mcp.c -- QuakeMCP bridge: token TCP, poll pump, console/cvar ops
//
// Out-of-process Python server talks line-JSON over loopback TCP.
// This file owns the socket, the per-instance token and request
// dispatch. Called on the main thread only (MCP_Poll from _Host_Frame
// and the SCR_ModalMessage loop). exec/cvar mutate through the same
// Cbuf_/Cvar_ paths the in-game console uses.

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
#define MCP_EXEC_MAX	4096
#define MCP_TAIL_LINES	8
#define MCP_REPLY_MAX	16384
#define MCP_EXEC_MAX	4096
#define MCP_TAIL_LINES	8

extern char	*con_text;
extern int	con_totallines;
extern int	con_current;
extern int	con_linewidth;

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

Copy the top-level string value of "name" from a flat JSON line.
Returns 1 if the full value fit, -1 if truncated to outsize (still
NUL-terminated), 0 if absent. Nested objects are skipped, so a key
inside a nested value never matches. Handles \" \\ \/ \b \f \n
\r \t escapes; \uXXXX decodes to '?' (protocol carries ASCII).
==================
*/
static int MCP_Field (char *line, char *name, char *out, int outsize)
{
	char *p, *q;
	int depth, n, full, i;

	p = line;
	if (*p == '{')
		p++;
	depth = 0;
	while (*p)
	{
		while (*p == ' ' || *p == '\t')
			p++;
		if (*p != '"')
		{
		// skip value noise between members
			if (*p == '{' || *p == '[')
				depth++;
			else if (*p == '}' || *p == ']')
			{
				if (depth == 0)
					break;
				depth--;
			}
			p++;
			continue;
		}
	// quoted key at top level?
		q = p + 1;
		n = strlen (name);
		if (depth == 0 && !strncmp (q, name, n) && q[n] == '"')
		{
			p = strchr (q + n + 1, ':');
			if (!p)
				return 0;
			p++;
			while (*p == ' ' || *p == '\t')
				p++;
			if (*p != '"')
				return 0;
			p++;
			n = 0;
			full = 0;
			while (*p && *p != '"')
			{
				if (*p == '\\' && p[1])
				{
					p++;
					full++;
					if (*p == 'u')
					{ // \uXXXX -> '?', skip hex digits
						if (n + 1 < outsize)
							out[n++] = '?';
						for (i = 0; i < 4 && p[1]; i++)
							p++;
						p++;
						continue;
					}
					if (n + 1 >= outsize)
					{
						p++;
						continue;
					}
					switch (*p)
					{
					case 'n': out[n++] = '\n'; break;
					case 'r': out[n++] = '\r'; break;
					case 't': out[n++] = '\t'; break;
					case 'b': out[n++] = '\b'; break;
					case 'f': out[n++] = '\f'; break;
					default: out[n++] = *p; break;
					}
					p++;
					continue;
				}
				full++;
				if (n + 1 < outsize)
					out[n++] = *p;
				p++;
			}
			out[n] = 0;
			return (full <= outsize - 1) ? 1 : -1;
		}
	// skip this quoted string, then track depth past it
		p = q;
		while (*p && *p != '"')
		{
			if (*p == '\\' && p[1])
				p++;
			p++;
		}
		if (*p == '"')
			p++;
	}
	out[0] = 0;
	return 0;
}

/*
==================
MCP_Escape

JSON-escape src into dst (quotes, backslash, control chars).
==================
*/
static void MCP_Escape (char *dst, int dstsize, char *src)
{
	int n;

	n = 0;
	while (*src && n + 6 < dstsize)
	{
		switch (*src)
		{
		case '"': dst[n++] = '\\'; dst[n++] = '"'; break;
		case '\\': dst[n++] = '\\'; dst[n++] = '\\'; break;
		case '\n': dst[n++] = '\\'; dst[n++] = 'n'; break;
		case '\r': dst[n++] = '\\'; dst[n++] = 'r'; break;
		case '\t': dst[n++] = '\\'; dst[n++] = 't'; break;
		default:
			if ((unsigned char)*src < 0x20)
			{
				n += snprintf (dst + n, dstsize - n, "\\u%04x",
					(unsigned char)*src);
			}
			else
				dst[n++] = *src;
			break;
		}
		src++;
	}
	dst[n] = 0;
}

/*
==================
MCP_ConsoleTail

Copy the last MCP_TAIL_LINES console lines into out, oldest first.
Reads the con_text ring only; no console changes. Row bytes carry a
color bit in the high bit; trailing spaces are trimmed. Guards: NULL
ring (pre-Con_Init), degenerate width, con_current < 0 at startup.
==================
*/
void MCP_ConsoleTail (char *out, int outsize)
{
	char row[256];
	int width, total, cur, first, i, j, n;

	n = 0;
	out[0] = 0;
	if (!con_text || con_linewidth <= 0 || con_totallines <= 0)
		return;
	width = con_linewidth;
	if (width > (int)sizeof (row) - 1)
		width = sizeof (row) - 1;
	total = con_totallines;
	cur = con_current;
	if (cur < 0)
		return;
	first = cur - (MCP_TAIL_LINES - 1);
	if (first < 0)
		first = 0;
	for (i = first; i <= cur; i++)
	{
		Q_memcpy (row, con_text + (i % total) * con_linewidth, width);
		row[width] = 0;
		for (j = width - 1; j >= 0; j--)
		{
			if (row[j] != ' ')
				break;
			row[j] = 0;
		}
		for (j = 0; row[j]; j++)
			row[j] &= 0x7f;
		if (n > 0 && n + 1 < outsize)
			out[n++] = '\n';
		for (j = 0; row[j] && n + 1 < outsize; j++)
			out[n++] = row[j];
	}
	out[n] = 0;
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
	char idbuf[128], *s, *d;
	static char out[MCP_REPLY_MAX];
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

exec: Cbuf_AddText (newline appended if missing); oversize 4 KiB
rejected with POLICY_DENIED. Reply carries the last 8 console lines.
Note: the tail reflects lines printed before this poll; the exec
text itself runs later in _Host_Frame via Cbuf_Execute, so callers
poll a second time for command output.
cvar get/set via Cvar_FindVar/Cvar_Set; miss -> INVALID_CONTEXT.
==================
*/
static void MCP_HandleLine (char *line)
{
	char auth[128], id[128], op[64];
	char text[MCP_EXEC_MAX + 1];
	char name[128], value[1024];
	char tail[8192], esctail[8192 * 2], result[MCP_REPLY_MAX];
	char escval[2048];
	cvar_t *var;
	int r;

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

	if (!strcmp (op, "exec"))
	{
		r = MCP_Field (line, "text", text, sizeof (text));
		if (r == 0)
		{
			MCP_Reply (id, false, "INVALID_CONTEXT", "exec needs text");
			return;
		}
		if (r < 0)
		{
			MCP_Reply (id, false, "POLICY_DENIED", "exec text over 4 KiB");
			return;
		}
		Cbuf_AddText (text);
		if (text[0] && text[strlen (text) - 1] != '\n')
			Cbuf_AddText ("\n");
		MCP_ConsoleTail (tail, sizeof (tail));
		MCP_Escape (esctail, sizeof (esctail), tail);
		snprintf (result, sizeof (result), "\"output\":\"%s\"", esctail);
		MCP_Reply (id, true, NULL, result);
		return;
	}

	if (!strcmp (op, "cvar"))
	{
		if (!MCP_Field (line, "name", name, sizeof (name)))
		{
			MCP_Reply (id, false, "INVALID_CONTEXT", "cvar needs name");
			return;
		}
		var = Cvar_FindVar (name);
		if (!var)
		{
			MCP_Reply (id, false, "INVALID_CONTEXT", name);
			return;
		}
		if (MCP_Field (line, "value", value, sizeof (value)))
			Cvar_Set (name, value);
		MCP_Escape (escval, sizeof (escval), var->string);
		snprintf (result, sizeof (result), "\"value\":\"%s\"", escval);
		MCP_Reply (id, true, NULL, result);
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
