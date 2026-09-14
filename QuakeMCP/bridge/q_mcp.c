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
#include "q_mcp.h"

#include <sys/socket.h>
#include <netinet/in.h>
#include <arpa/inet.h>
#include <unistd.h>
#include <fcntl.h>
#include <errno.h>
#include <sys/stat.h>
#include <stdlib.h>

#define MCP_LINE_MAX	65536
#define MCP_TOKEN_BYTES	32
#define MCP_POLL_BUDGET	0.05
#define MCP_RETRY_SECS	5.0
#define MCP_EXEC_MAX	4096
#define MCP_TAIL_LINES	8
#define MCP_REPLY_MAX	16384
#define MCP_LEASE_TIMEOUT	2.0
#define MCP_ACT_CAP	5.0

extern char	*con_text;
extern int	con_totallines;
extern int	con_current;
extern int	con_linewidth;

extern kbutton_t	in_attack, in_jump;

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

// Controller lease + bounded action state (Task 5). Lease is acquired via
// the control op, kept alive by the hb op, and expires on 2 s wall silence.
// An act runs over subsequent simulation ticks and replies once finished.
#define MCP_ACT_TICKS	0
#define MCP_ACT_DURATION	1

static int	mcp_epoch;
static int	mcp_control_rev;
static int	mcp_lease_active;
static char	mcp_lease_id[64];	// "l<epoch>-<control_rev>"
static int	mcp_lease_seq;
static double	mcp_lease_lastbeat;

static int	mcp_act_active;
static int	mcp_act_mode;
static int	mcp_act_ticks;		// requested ticks (MCP_ACT_TICKS)
static int	mcp_act_completed;	// ticks merged so far
static double	mcp_act_start;		// wall clock at act start
static double	mcp_act_duration;	// seconds requested (MCP_ACT_DURATION)
static char	mcp_act_id[128];
static char	mcp_act_aid[128];	// action_id echo


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
MCP_FieldFloat / MCP_FieldInt

Read a top-level string field and convert. Absent or non-string fields
yield 0 (the control layer stringifies all numeric values, so a raw
JSON number never reaches MCP_Field).
==================
*/
static float MCP_FieldFloat (char *line, char *name)
{
	char buf[64];

	if (MCP_Field (line, name, buf, sizeof (buf)))
		return (float)atof (buf);
	return 0.0f;
}

static int MCP_FieldInt (char *line, char *name)
{
	char buf[64];

	if (MCP_Field (line, name, buf, sizeof (buf)))
		return atoi (buf);
	return 0;
}

/*
==================
MCP_FinishAct

Send the running action's completion reply and neutralize MCP input.
interrupted is true when the action ended short of its target (wall
cap, release, lease expiry). Idempotent: no-op when no action runs.
==================
*/
static void MCP_FinishAct (qboolean interrupted)
{
	char result[MCP_REPLY_MAX];
	char aid[256];
	int elapsed;

	if (!mcp_act_active)
		return;
	MCP_Escape (aid, sizeof (aid), mcp_act_aid);
	elapsed = (int)((Sys_DoubleTime () - mcp_act_start) * 1000.0);
	if (elapsed < 0)
		elapsed = 0;
	snprintf (result, sizeof (result),
		"\"completed_ticks\":%d,\"elapsed_ms\":%d,\"interrupted\":%s,"
		"\"action_id\":\"%s\"",
		mcp_act_completed, elapsed, interrupted ? "true" : "false", aid);
	MCP_Reply (mcp_act_id, true, NULL, result);
	MCP_EndInput ();
	mcp_act_active = 0;
}

/*
==================
MCP_ClearControl

Release the controller lease and any running action. Used by control
release/detach and the queue-jumping release op. Idempotent.
==================
*/
static void MCP_ClearControl (void)
{
	mcp_control_rev++;
	mcp_lease_active = 0;
	mcp_lease_id[0] = 0;
	MCP_FinishAct (true);
	MCP_EndInput ();
}

/*
==================
MCP_NoteTick

Called by MCP_Move once per merged simulation tick. Counts completed
ticks toward the running action's budget.
==================
*/
void MCP_NoteTick (void)
{
	if (mcp_act_active)
		mcp_act_completed++;
}

/*
==================
MCP_CheckAction

Advance the running action's completion. Runs every poll, independent
of incoming traffic, so an action finishes on schedule even with a
silent client. Hard 5 s wall cap interrupts a stuck action.
==================
*/
static void MCP_CheckAction (void)
{
	double now;

	if (!mcp_act_active)
		return;
	now = Sys_DoubleTime ();
	if (mcp_act_mode == MCP_ACT_TICKS)
	{
		if (mcp_act_completed >= mcp_act_ticks)
			MCP_FinishAct (false);
		else if (now - mcp_act_start >= MCP_ACT_CAP)
			MCP_FinishAct (true);
	}
	else
	{
		if (now - mcp_act_start >= mcp_act_duration)
			MCP_FinishAct (false);
		else if (now - mcp_act_start >= MCP_ACT_CAP)
			MCP_FinishAct (true);
	}
}

/*
==================
MCP_CheckLease

Expire the controller lease after 2 s without a heartbeat. Expiry
clears MCP input so no synthetic button survives a dead controller.
==================
*/
static void MCP_CheckLease (void)
{
	if (!mcp_lease_active)
		return;
	if (Sys_DoubleTime () - mcp_lease_lastbeat > MCP_LEASE_TIMEOUT)
		MCP_ClearControl ();
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

	if (!strcmp (op, "control"))
	{
		char sub[64];

		if (!MCP_Field (line, "sub", sub, sizeof (sub)))
		{
			MCP_Reply (id, false, "INVALID_CONTEXT", "control needs sub");
			return;
		}
		if (!strcmp (sub, "acquire"))
		{
			// neutral physical controls required; a held attack/jump
			// button means the human is mid-action -> CONTROL_BUSY
			if ((in_attack.state & 3) || (in_jump.state & 3))
			{
				MCP_Reply (id, false, "CONTROL_BUSY",
					"physical buttons held");
				return;
			}
			if (!mcp_lease_active)
			{
				mcp_control_rev++;
				mcp_lease_active = 1;
				mcp_lease_seq = 0;
				snprintf (mcp_lease_id, sizeof (mcp_lease_id),
					"l%d-%d", mcp_epoch, mcp_control_rev);
			}
			mcp_lease_lastbeat = Sys_DoubleTime ();
			snprintf (result, sizeof (result),
				"\"lease\":\"%s\",\"epoch\":%d,\"control_rev\":%d",
				mcp_lease_id, mcp_epoch, mcp_control_rev);
			MCP_Reply (id, true, NULL, result);
			return;
		}
		if (!strcmp (sub, "release") || !strcmp (sub, "detach"))
		{
			MCP_ClearControl ();
			MCP_Reply (id, true, NULL, "\"released\":true");
			return;
		}
		MCP_Reply (id, false, "UNSUPPORTED_CAPABILITY", sub);
		return;
	}

	if (!strcmp (op, "release"))
	{
		MCP_ClearControl ();
		MCP_Reply (id, true, NULL, "\"released\":true");
		return;
	}

	if (!strcmp (op, "hb"))
	{
		mcp_lease_lastbeat = Sys_DoubleTime ();
		MCP_Reply (id, true, NULL, "\"ok\":true");
		return;
	}

	if (!strcmp (op, "act"))
	{
		char leas[64], aida[128], seqs[32], epocs[32];
		char tickss[32], durs[32];
		char jumps[16];
		int epoch, seq, ticks, dur;
		int has_ticks, has_dur, jump, impulse, run, attack;
		float fwd, strafe, vert, yaw, pitch;

		if (!mcp_lease_active || mcp_lease_id[0] == 0)
		{
			MCP_Reply (id, false, "STALE_STATE", "no lease");
			return;
		}
		if (!MCP_Field (line, "lease", leas, sizeof (leas))
			|| strcmp (leas, mcp_lease_id) != 0)
		{
			MCP_Reply (id, false, "STALE_STATE", "lease mismatch");
			return;
		}
		if (!MCP_Field (line, "epoch", epocs, sizeof (epocs)))
		{
			MCP_Reply (id, false, "INVALID_CONTEXT", "act needs epoch");
			return;
		}
		epoch = atoi (epocs);
		if (epoch != mcp_epoch)
		{
			MCP_Reply (id, false, "STALE_STATE", "epoch mismatch");
			return;
		}
		if (!MCP_Field (line, "seq", seqs, sizeof (seqs)))
		{
			MCP_Reply (id, false, "INVALID_CONTEXT", "act needs seq");
			return;
		}
		seq = atoi (seqs);
		if (seq <= mcp_lease_seq)
		{
			MCP_Reply (id, false, "STALE_STATE", "stale seq");
			return;
		}
		if (mcp_act_active)
		{
			MCP_Reply (id, false, "CONTROL_BUSY",
				"action already running");
			return;
		}

		has_ticks = MCP_Field (line, "ticks", tickss, sizeof (tickss)) > 0;
		has_dur = MCP_Field (line, "duration_ms", durs,
			sizeof (durs)) > 0;
		if (has_ticks == has_dur)
		{
			MCP_Reply (id, false, "INVALID_CONTEXT",
				"exactly one of ticks / duration_ms");
			return;
		}

		fwd = MCP_FieldFloat (line, "forward");
		strafe = MCP_FieldFloat (line, "strafe");
		vert = MCP_FieldFloat (line, "vertical");
		yaw = MCP_FieldFloat (line, "yaw");
		pitch = MCP_FieldFloat (line, "pitch");
		run = MCP_FieldInt (line, "run") != 0;
		attack = MCP_FieldInt (line, "attack") != 0;
		impulse = MCP_FieldInt (line, "impulse");
		jump = 0;
		if (MCP_Field (line, "jump", jumps, sizeof (jumps)))
		{
			if (!strcmp (jumps, "tap"))
				jump = 1;
			else if (!strcmp (jumps, "hold"))
				jump = 2;
		}
		if (!MCP_Field (line, "action_id", aida, sizeof (aida)))
			aida[0] = 0;

		if (has_ticks)
		{
			ticks = atoi (tickss);
			if (ticks < 1 || ticks > 72)
			{
				MCP_Reply (id, false, "INVALID_CONTEXT",
					"ticks out of range 1..72");
				return;
			}
			mcp_act_mode = MCP_ACT_TICKS;
			mcp_act_ticks = ticks;
		}
		else
		{
			dur = atoi (durs);
			if (dur < 1 || dur > 1000)
			{
				MCP_Reply (id, false, "INVALID_CONTEXT",
					"duration_ms out of range 1..1000");
				return;
			}
			mcp_act_mode = MCP_ACT_DURATION;
			mcp_act_duration = dur / 1000.0;
		}

		MCP_BeginInput (fwd, strafe, vert, yaw, pitch, attack,
			jump, impulse, run ? true : false);
		strcpy (mcp_act_id, id);
		strncpy (mcp_act_aid, aida, sizeof (mcp_act_aid) - 1);
		mcp_act_aid[sizeof (mcp_act_aid) - 1] = 0;
		mcp_act_start = Sys_DoubleTime ();
		mcp_act_completed = 0;
		mcp_lease_seq = seq;
		mcp_act_active = 1;
		// reply deferred: MCP_CheckAction sends it once the tick
		// budget (or duration) is consumed or the wall cap fires
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

	mcp_epoch = (int)getpid ();
	mcp_control_rev = 0;
	mcp_lease_active = 0;
	mcp_lease_id[0] = 0;
	mcp_lease_seq = 0;
	mcp_lease_lastbeat = 0;
	mcp_act_active = 0;
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

	MCP_CheckAction ();
	MCP_CheckLease ();

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
