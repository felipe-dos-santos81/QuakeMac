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
#include <sys/select.h>
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
#define MCP_SLOTS	2

extern char	*con_text;
extern int	con_totallines;
extern int	con_current;
extern int	con_linewidth;

extern kbutton_t	in_attack, in_jump;

cvar_t	mcp_enabled = {"mcp_enabled", "0"};
cvar_t	mcp_port = {"mcp_port", "28900"};

typedef struct
{
	int	fd;
	char	line[MCP_LINE_MAX + 1];
	int	len;
} mcp_slot_t;

static int	mcp_listen_fd = -1;
static mcp_slot_t mcp_slots[MCP_SLOTS] = {
	{ -1, { 0 }, 0 },
	{ -1, { 0 }, 0 },
};
static int	mcp_reply_fd = -1;	// target of the next MCP_Reply
static int	mcp_act_fd = -1;	// connection owning a deferred act
static int	mcp_observe_fd = -1;	// connection owning a deferred observe
static char	mcp_token[2 * MCP_TOKEN_BYTES + 1];
static char	mcp_token_path[256];
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
static char	mcp_act_lease[64];	// lease the action was admitted on
static unsigned	mcp_act_hash;		// argument hash for the receipt ring
static int	mcp_act_epoch;

// Stepped mode (Task 9): when set, _Host_Frame skips the client and
// server simulation steps while no action runs, so an idle owned session
// consumes no game time. Polling, rendering and the watchdog keep
// running. Realtime until the controller opts in with control mode.
static int	mcp_step_mode;

// Simulation frame counter: incremented once per completed simulation
// step, so a frozen session reports a constant frame and an act of N
// ticks moves it by exactly N. This is the id used for observation
// attribution.
static unsigned	mcp_sim_frame;

// Receipt ring (Task 9), bounded so the module never grows. A repeated
// (lease, action_id, epoch) with the same argument hash returns its
// recorded receipt; a different hash is refused. With 64 slots, a client
// that echoes action ids across a long session can still outrun it: the
// miss then surfaces as RESULT_EXPIRED via the sequence high-water.
#define MCP_LEDGER_SIZE 64
typedef struct
{
	char		lease[64];
	char		action_id[128];
	int		epoch;
	unsigned	hash;
	char		result[MCP_REPLY_MAX];
} mcp_ledger_t;

static mcp_ledger_t mcp_ledger[MCP_LEDGER_SIZE];
static int	mcp_ledger_next;

// World generation (Task 6, exact since Task 10): MCP_NoteWorldSpawn
// is called from SV_SpawnServer.
static int	mcp_world_gen;

// Deferred observation reply (Task 7): observe arms MCAP_* and the reply
// is sent from MCP_Poll once the frame is captured.
static char	mcp_observe_id[128];

// the slot layer finishes a deferred act when its connection goes away
static void MCP_FinishAct (qboolean interrupted);


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

Bind loopback, non-blocking, room for both connection slots. Port from
-mcp_port CLI first, mcp_port cvar second. Idempotent: safe to call
every poll.
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
	if (listen (fd, MCP_SLOTS) < 0)
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
MCP_SlotForFd

Find the slot owning fd, or -1.
==================
*/
static int MCP_SlotForFd (int fd)
{
	int i;

	for (i = 0; i < MCP_SLOTS; i++)
		if (mcp_slots[i].fd == fd)
			return i;
	return -1;
}

/*
==================
MCP_FreeSlot

First slot without a connection, or -1 when both are occupied.
==================
*/
static int MCP_FreeSlot (void)
{
	int i;

	for (i = 0; i < MCP_SLOTS; i++)
		if (mcp_slots[i].fd < 0)
			return i;
	return -1;
}

/*
==================
MCP_CloseSlot

Close one connection and forget it. When the slot owned a deferred act
the act is finished (the reply to the dead fd is a no-op) and when it
owned a deferred observe the capture is dropped: neither may outlive
the connection that asked for it.
==================
*/
static void MCP_CloseSlot (int i)
{
	mcp_slot_t *s;
	int fd, ownsact, ownsobs;

	s = &mcp_slots[i];
	fd = s->fd;
	if (fd < 0)
		return;
	s->fd = -1;
	s->len = 0;
	ownsact = (mcp_act_fd == fd);
	ownsobs = (mcp_observe_fd == fd);
	if (mcp_reply_fd == fd)
		mcp_reply_fd = -1;
	close (fd);
	if (ownsact)
		MCP_FinishAct (true);
	if (ownsobs)
	{
		mcp_observe_fd = -1;
		MCAP_Cancel ();
	}
}

/*
==================
MCP_CloseAll

Drop every connection, for shutdown.
==================
*/
static void MCP_CloseAll (void)
{
	int i;

	for (i = 0; i < MCP_SLOTS; i++)
		MCP_CloseSlot (i);
	mcp_reply_fd = -1;
}

/*
==================
MCP_CloseFd

Close the slot that owns fd; other connections are untouched. The send
error paths use this so only the failed target is dropped.
==================
*/
static void MCP_CloseFd (int fd)
{
	int i;

	i = MCP_SlotForFd (fd);
	if (i >= 0)
		MCP_CloseSlot (i);
}

/*
==================
MCP_ReapEofSlot

Close a slot whose peer is at EOF (or in error) and return its index.
The MSG_PEEK probe leaves any pending bytes untouched. Returns -1 when
every occupied slot still has a live peer.
==================
*/
static int MCP_ReapEofSlot (void)
{
	int i;

	for (i = 0; i < MCP_SLOTS; i++)
	{
		char probe;
		int pn;

		if (mcp_slots[i].fd < 0)
			continue;
		pn = recv (mcp_slots[i].fd, &probe, 1, MSG_PEEK);
		if (pn == 0 || (pn < 0 && errno != EAGAIN
			&& errno != EWOULDBLOCK && errno != EINTR))
		{
			MCP_CloseSlot (i);
			return i;
		}
	}
	return -1;
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

	if (mcp_reply_fd < 0)
		return;
	left = strlen (msg);
	while (left > 0 && mcp_reply_fd >= 0)
	{
		n = send (mcp_reply_fd, msg + strlen (msg) - left, left, 0);
		if (n <= 0)
		{
			if (errno == EAGAIN || errno == EWOULDBLOCK || errno == EINTR)
				continue;
			MCP_CloseFd (mcp_reply_fd);
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
MCP_FormatState

Write the state snapshot JSON fragment (no outer braces) shared by the
state op and every observation. One pass over main-thread globals, so
callers never mix reads from different snapshots. Returns the fragment
length, or -1 if it did not fit.
==================
*/
static int MCP_FormatState (char *out, int outsize)
{
	char escmap[256];
	float *org, *ang;
	int viewent, health, ammo, n;

	// cl.viewentity can index out of range before a world is loaded
	viewent = cl.viewentity;
	if (viewent < 0 || viewent >= MAX_EDICTS)
		viewent = 0;
	org = cl_entities[viewent].origin;
	ang = cl_entities[viewent].angles;
	health = cl.stats[STAT_HEALTH];
	ammo = cl.stats[STAT_AMMO];
	MCP_Escape (escmap, sizeof (escmap), sv.name);
	n = snprintf (out, outsize,
		"\"epoch\":%d,\"world_gen\":%d,\"control_rev\":%d,"
		"\"frame\":%u,\"time\":%.3f,\"map\":\"%s\","
		"\"mode\":\"%s\","
		"\"pos\":[%.2f,%.2f,%.2f],"
		"\"angles\":[%.2f,%.2f,%.2f],"
		"\"health\":%d,\"ammo\":%d,\"ui\":%d,"
		"\"loading\":%s,\"dead\":%s,\"intermission\":%s,"
		"\"signon\":%d,\"movemessages\":%d",
		mcp_epoch, mcp_world_gen, mcp_control_rev,
		MCP_FrameId (), host_time, escmap,
		mcp_step_mode ? "stepped" : "realtime",
		org[0], org[1], org[2], ang[0], ang[1], ang[2],
		health, ammo, (int)key_dest,
		scr_disabled_for_loading ? "true" : "false",
		health <= 0 ? "true" : "false",
		cl.intermission ? "true" : "false",
		cls.signon, cl.movemessages);
	if (n < 0 || n >= outsize)
		return -1;
	return n;
}

/*
==================
MCP_SendBlob

Send raw framed bytes after a reply line. The client is waiting for
exactly this payload, so a healthy reader drains in a few sends; a
stalled reader is dropped at the deadline rather than freezing the
frame loop.
==================
*/
static qboolean MCP_SendBlob (byte *data, int len)
{
	double	stop;
	int	sent, n;
	fd_set	w;
	struct timeval tv;

	if (mcp_reply_fd < 0)
		return false;
	stop = Sys_DoubleTime () + 0.1;
	sent = 0;
	while (sent < len && mcp_reply_fd >= 0)
	{
		n = send (mcp_reply_fd, data + sent, len - sent, 0);
		if (n > 0)
		{
			sent += n;
			continue;
		}
		if (n < 0 && (errno == EAGAIN || errno == EWOULDBLOCK))
		{
			if (Sys_DoubleTime () > stop)
			{
				MCP_CloseFd (mcp_reply_fd);
				return false;
			}
			FD_ZERO (&w);
			FD_SET (mcp_reply_fd, &w);
			tv.tv_sec = 0;
			tv.tv_usec = 20000;
			select (mcp_reply_fd + 1, NULL, &w, NULL, &tv);
			continue;
		}
		if (n < 0 && errno == EINTR)
			continue;
		MCP_CloseFd (mcp_reply_fd);
		return false;
	}
	return mcp_reply_fd >= 0;
}

/*
==================
MCP_SendObservation

Reply to the deferred observe op: one JSON header line (state + image
metadata + blob_bytes) followed by the framed RGB blob.
==================
*/
static void MCP_SendObservation (mcap_snapshot_t *snap)
{
	char	result[MCP_REPLY_MAX];
	int	n, bytes, savedfd;

	// the deferred reply targets the connection that asked for the
	// capture; the caller's own reply target is restored
	savedfd = mcp_reply_fd;
	mcp_reply_fd = mcp_observe_fd;
	mcp_observe_fd = -1;
	n = MCP_FormatState (result, sizeof (result));
	if (n < 0)
	{
		MCP_Reply (mcp_observe_id, false, "INVALID_CONTEXT",
			"state snapshot overflow");
		MCAP_Release ();
		mcp_reply_fd = savedfd;
		return;
	}
	bytes = snap->w * snap->h * 3;
	snprintf (result + n, sizeof (result) - n,
		",\"src_w\":%d,\"src_h\":%d,"
		"\"viewport\":[%d,%d,%d,%d],\"hud_rect\":[%d,%d,%d,%d],"
		"\"captured_at\":%.3f,\"capture_age_ms\":%d,\"blob_bytes\":%d",
		snap->w, snap->h,
		snap->viewport[0], snap->viewport[1],
		snap->viewport[2], snap->viewport[3],
		snap->hud_rect[0], snap->hud_rect[1],
		snap->hud_rect[2], snap->hud_rect[3],
		snap->captured_at,
		(int)((Sys_DoubleTime () - snap->captured_at) * 1000.0),
		bytes);
	MCP_Reply (mcp_observe_id, true, NULL, result);
	MCP_SendBlob (snap->data, bytes);
	MCAP_Release ();
	mcp_reply_fd = savedfd;
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
	int elapsed, savedfd;

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

	// record the receipt before replying: a caller that retries after a
	// dropped connection gets this result instead of a second execution
	if (mcp_act_aid[0])
	{
		mcp_ledger_t *e = &mcp_ledger[mcp_ledger_next];

		mcp_ledger_next = (mcp_ledger_next + 1) % MCP_LEDGER_SIZE;
		strncpy (e->lease, mcp_act_lease, sizeof (e->lease) - 1);
		e->lease[sizeof (e->lease) - 1] = 0;
		strncpy (e->action_id, mcp_act_aid, sizeof (e->action_id) - 1);
		e->action_id[sizeof (e->action_id) - 1] = 0;
		e->epoch = mcp_act_epoch;
		e->hash = mcp_act_hash;
		strncpy (e->result, result, sizeof (e->result) - 1);
		e->result[sizeof (e->result) - 1] = 0;
	}

	// the deferred reply targets the connection that armed the act; the
	// caller's own reply target (an enclosing handler) is restored
	savedfd = mcp_reply_fd;
	mcp_reply_fd = mcp_act_fd;
	mcp_act_fd = -1;
	MCP_Reply (mcp_act_id, true, NULL, result);
	mcp_reply_fd = savedfd;
	MCP_EndInput ();
	mcp_act_active = 0;
	// the deferred reply is how a long action proved it was alive
	mcp_lease_lastbeat = Sys_DoubleTime ();
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

Called by the host loop once per completed simulation step (local
server frame, or the remote send). Advances the simulation frame id and
counts completed ticks toward the running action's budget; frozen steps
never reach it.
==================
*/
void MCP_NoteTick (void)
{
	mcp_sim_frame++;
	if (mcp_act_active)
		mcp_act_completed++;
}

/*
==================
MCP_HashAct

FNV-1a over the canonical argument values of an act request (never the
wire id, sequence or lease, which vary between a call and its retry).
==================
*/
static unsigned MCP_HashAct (float fwd, float strafe, float vert, float yaw,
	float pitch, int run, int attack, int jump, int impulse,
	int has_ticks, int ticks, int dur)
{
	unsigned h = 2166136261u;
	char buf[256];
	char *p;

	snprintf (buf, sizeof (buf),
		"%.3f|%.3f|%.3f|%.3f|%.3f|%d|%d|%d|%d|%d|%d|%d",
		fwd, strafe, vert, yaw, pitch, run, attack, jump, impulse,
		has_ticks, ticks, dur);
	for (p = buf; *p; p++)
	{
		h ^= (unsigned char)*p;
		h *= 16777619u;
	}
	return h;
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
MCP_NoteWorldSpawn

Called from SV_SpawnServer (the server owns the world lifecycle), so the
generation is exact for map changes, restarts and savegame loads alike.
A same-map same-time reload that a name/time poll cannot see still bumps.
==================
*/
void MCP_NoteWorldSpawn (void)
{
	mcp_world_gen++;
}

/*
==================
MCP_CheckLease

Expire the controller lease after 2 s without a heartbeat. Expiry
clears MCP input so no synthetic button survives a dead controller,
even mid-action: a deferred act is interrupted at expiry instead of
running to its wall cap on a silent lease.
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

	if (!strcmp (op, "key"))
	{
		int	key, down;

		key = MCP_FieldInt (line, "key");
		down = MCP_FieldInt (line, "down");
		if (key < 0 || key > 255)
		{
			MCP_Reply (id, false, "INVALID_CONTEXT", "key out of range");
			return;
		}
		// routes through the normal UI path (key_dest decides who sees it)
		Key_Event (key, down ? true : false);
		MCP_Reply (id, true, NULL, "\"ok\":true");
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
		if (!strcmp (sub, "mode"))
		{
			char mode[32];

			if (!MCP_Field (line, "mode", mode, sizeof (mode)))
			{
				MCP_Reply (id, false, "INVALID_CONTEXT",
					"mode needs a value");
				return;
			}
			if (!strcmp (mode, "stepped"))
				mcp_step_mode = 1;
			else if (!strcmp (mode, "realtime"))
				mcp_step_mode = 0;
			else
			{
				MCP_Reply (id, false, "UNSUPPORTED_CAPABILITY", mode);
				return;
			}
			snprintf (result, sizeof (result), "\"mode\":\"%s\"",
				mcp_step_mode ? "stepped" : "realtime");
			MCP_Reply (id, true, NULL, result);
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
		char hleas[64], hepocs[32];

		// a beat must name the live lease: it may not resurrect a
		// revoked one or keep a foreign controller alive
		if (!mcp_lease_active || mcp_lease_id[0] == 0)
		{
			MCP_Reply (id, false, "STALE_STATE", "no lease");
			return;
		}
		if (!MCP_Field (line, "lease", hleas, sizeof (hleas))
			|| strcmp (hleas, mcp_lease_id) != 0)
		{
			MCP_Reply (id, false, "STALE_STATE", "lease mismatch");
			return;
		}
		if (MCP_Field (line, "epoch", hepocs, sizeof (hepocs))
			&& atoi (hepocs) != mcp_epoch)
		{
			MCP_Reply (id, false, "STALE_STATE", "epoch mismatch");
			return;
		}
		mcp_lease_lastbeat = Sys_DoubleTime ();
		MCP_Reply (id, true, NULL, "\"ok\":true");
		return;
	}

	if (!strcmp (op, "state"))
	{
		if (MCP_FormatState (result, sizeof (result)) < 0)
		{
			MCP_Reply (id, false, "INVALID_CONTEXT", "state overflow");
			return;
		}
		MCP_Reply (id, true, NULL, result);
		return;
	}

	if (!strcmp (op, "observe"))
	{
		int after, tmo;

		if (MCAP_Busy ())
		{
			MCP_Reply (id, false, "CONTROL_BUSY",
				"observe already pending");
			return;
		}
		mcp_observe_fd = mcp_reply_fd;
		strcpy (mcp_observe_id, id);
		after = MCP_FieldInt (line, "after_frame");
		tmo = MCP_FieldInt (line, "timeout_ms");
		if (tmo <= 0)
			tmo = 1000;
		if (tmo > 10000)
			tmo = 10000;
		MCAP_Request ((unsigned)after, tmo / 1000.0);
		// reply deferred: MCP_Poll captures and sends header + blob
		return;
	}

	if (!strcmp (op, "act"))
	{
		char leas[64], aida[128], seqs[32], epocs[32];
		char tickss[32], durs[32], worlds[32], ctrls[32];
		char jumps[16];
		int epoch, seq, ticks, dur;
		int has_ticks, has_dur, jump, impulse, run, attack;
		unsigned hash;
		int i;
		float fwd, strafe, vert, yaw, pitch;

		// parse every argument before deciding anything: duplicate
		// detection needs the full argument hash, and it runs before
		// the state preconditions so a legitimate retry still gets the
		// receipt after its lease or world moved on
		MCP_Field (line, "lease", leas, sizeof (leas));
		if (!MCP_Field (line, "epoch", epocs, sizeof (epocs)))
		{
			MCP_Reply (id, false, "INVALID_CONTEXT", "act needs epoch");
			return;
		}
		epoch = atoi (epocs);
		if (!MCP_Field (line, "seq", seqs, sizeof (seqs)))
		{
			MCP_Reply (id, false, "INVALID_CONTEXT", "act needs seq");
			return;
		}
		seq = atoi (seqs);
		has_ticks = MCP_Field (line, "ticks", tickss, sizeof (tickss)) > 0;
		has_dur = MCP_Field (line, "duration_ms", durs,
			sizeof (durs)) > 0;
		if (has_ticks == has_dur)
		{
			MCP_Reply (id, false, "INVALID_CONTEXT",
				"exactly one of ticks / duration_ms");
			return;
		}
		ticks = has_ticks ? atoi (tickss) : 0;
		dur = has_dur ? atoi (durs) : 0;

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

		hash = MCP_HashAct (fwd, strafe, vert, yaw, pitch, run, attack,
			jump, impulse, has_ticks, ticks, dur);

		// known duplicate: the same lease + action_id + arguments
		// returns its recorded receipt; different arguments under the
		// same id are a conflict, never a second execution. Acts with
		// no action_id carry no identity and are never deduplicated.
		if (aida[0])
		{
			for (i = 0; i < MCP_LEDGER_SIZE; i++)
			{
				mcp_ledger_t *e = &mcp_ledger[i];

				if (e->action_id[0] == 0 || e->epoch != epoch
					|| strcmp (e->action_id, aida) != 0
					|| strcmp (e->lease, leas) != 0)
					continue;
				if (e->hash == hash)
				{
					MCP_Reply (id, true, NULL, e->result);
					return;
				}
				MCP_Reply (id, false, "POLICY_DENIED",
					"action_id reused with different arguments");
				return;
			}
		}

		if (!mcp_lease_active || mcp_lease_id[0] == 0)
		{
			MCP_Reply (id, false, "STALE_STATE", "no lease");
			return;
		}
		if (strcmp (leas, mcp_lease_id) != 0)
		{
			MCP_Reply (id, false, "STALE_STATE", "lease mismatch");
			return;
		}
		if (epoch != mcp_epoch)
		{
			MCP_Reply (id, false, "STALE_STATE", "epoch mismatch");
			return;
		}
		if (MCP_Field (line, "world_generation", worlds, sizeof (worlds))
			&& atoi (worlds) != mcp_world_gen)
		{
			MCP_Reply (id, false, "STALE_STATE",
				"world generation mismatch");
			return;
		}
		if (MCP_Field (line, "control_revision", ctrls, sizeof (ctrls))
			&& atoi (ctrls) != mcp_control_rev)
		{
			MCP_Reply (id, false, "STALE_STATE",
				"control revision mismatch");
			return;
		}
		if (mcp_act_active)
		{
			MCP_Reply (id, false, "CONTROL_BUSY",
				"action already running");
			return;
		}
		if (seq <= mcp_lease_seq)
		{
			// the sequence is spent and no receipt survived in the
			// ring: the original result can no longer be returned
			MCP_Reply (id, false, "RESULT_EXPIRED",
				"sequence already consumed");
			return;
		}

		if (has_ticks)
		{
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
			// stepped mode counts fixed simulation steps; a wall-clock
			// budget would silently degrade into real-time stepping
			if (mcp_step_mode)
			{
				MCP_Reply (id, false, "UNSUPPORTED_CAPABILITY",
					"duration_ms in stepped mode; use ticks");
				return;
			}
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
		mcp_act_fd = mcp_reply_fd;
		strcpy (mcp_act_id, id);
		strncpy (mcp_act_aid, aida, sizeof (mcp_act_aid) - 1);
		mcp_act_aid[sizeof (mcp_act_aid) - 1] = 0;
		strncpy (mcp_act_lease, leas, sizeof (mcp_act_lease) - 1);
		mcp_act_lease[sizeof (mcp_act_lease) - 1] = 0;
		mcp_act_hash = hash;
		mcp_act_epoch = epoch;
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
MCP_PollSlot

Drain complete lines from one connection within the poll budget.
Oversize lines are dropped and counted, never grown.
==================
*/
static void MCP_PollSlot (int i, double stop)
{
	mcp_slot_t *s;
	char *nl;
	int n;

	s = &mcp_slots[i];
	for (;;)
	{
		if (Sys_DoubleTime () >= stop)
			return;
		if (s->fd < 0)
			return;
		if (s->len >= MCP_LINE_MAX)
		{
			s->len = 0;
			mcp_oversize_drops++;
			continue;
		}
		n = recv (s->fd, s->line + s->len, MCP_LINE_MAX - s->len, 0);
		if (n > 0)
		{
			s->len += n;
			s->line[s->len] = 0;
			while ((nl = strchr (s->line, '\n')) != NULL)
			{
				*nl = 0;
				mcp_reply_fd = s->fd;
				MCP_HandleLine (s->line);
				if (s->fd < 0)
					return;
				n = strlen (nl + 1);
				memmove (s->line, nl + 1, n + 1);
				s->len = n;
				if (Sys_DoubleTime () >= stop)
					return;
			}
			continue;
		}
		if (n == 0)
		{
			MCP_CloseSlot (i);
			return;
		}
		if (errno == EAGAIN || errno == EWOULDBLOCK || errno == EINTR)
			return;
		MCP_CloseSlot (i);
		return;
	}
}

/*
==================
MCP_PollClient

Drain every occupied slot within the poll budget, so one quiet or
stalled peer never starves the other connection.
==================
*/
static void MCP_PollClient (double stop)
{
	int i;

	for (i = 0; i < MCP_SLOTS; i++)
	{
		if (Sys_DoubleTime () >= stop)
			return;
		MCP_PollSlot (i, stop);
	}
}

/*
==================
MCP_Init
==================
*/
void MCP_Init (void)
{
	int i;

	Cvar_RegisterVariable (&mcp_enabled);
	Cvar_RegisterVariable (&mcp_port);

	mcp_listen_fd = -1;
	for (i = 0; i < MCP_SLOTS; i++)
	{
		mcp_slots[i].fd = -1;
		mcp_slots[i].len = 0;
	}
	mcp_reply_fd = -1;
	mcp_act_fd = -1;
	mcp_observe_fd = -1;
	mcp_token[0] = 0;
	mcp_token_path[0] = 0;
	mcp_oversize_drops = 0;
	mcp_retry_at = 0;

	mcp_epoch = (int)getpid ();
	mcp_control_rev = 0;
	mcp_lease_active = 0;
	mcp_lease_id[0] = 0;
	mcp_lease_seq = 0;
	mcp_lease_lastbeat = 0;
	mcp_act_active = 0;
	mcp_world_gen = 0;
	mcp_observe_id[0] = 0;
	MCAP_Shutdown ();	// normalize capture ring/statics
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

	if (MCAP_Busy ())
	{
		mcap_snapshot_t	snap;
		int		rc;

		rc = MCAP_Poll (&snap);
		if (rc == 1)
			MCP_SendObservation (&snap);
		else if (rc == -1)
		{
			mcp_reply_fd = mcp_observe_fd;
			mcp_observe_fd = -1;
			MCP_Reply (mcp_observe_id, false, "FRAME_TIMEOUT",
				"no rendered frame within the window");
		}
		else if (rc == -2)
		{
			mcp_reply_fd = mcp_observe_fd;
			mcp_observe_fd = -1;
			MCP_Reply (mcp_observe_id, false, "RENDER_UNAVAILABLE",
				"frame readback unavailable");
		}
	}

	if (mcp_listen_fd < 0)
		MCP_Setup ();
	if (mcp_listen_fd < 0)
		return;

	stop = Sys_DoubleTime () + MCP_POLL_BUDGET;

	for (;;)
	{
		int slot;

		fd = accept (mcp_listen_fd, NULL, NULL);
		if (fd < 0)
			break;
		// The control layer opens one connection per call. Take a free
		// slot; when both are busy, reap a slot whose peer already said
		// goodbye; a live pair keeps the endpoint and the new fd drops.
		slot = MCP_FreeSlot ();
		if (slot < 0)
			slot = MCP_ReapEofSlot ();
		if (slot < 0)
		{
			close (fd);
			continue;
		}
		MCP_SetNonblock (fd);
		mcp_slots[slot].fd = fd;
		mcp_slots[slot].len = 0;
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
	MCP_CloseAll ();
	if (mcp_listen_fd >= 0)
		close (mcp_listen_fd);
	mcp_listen_fd = -1;
	if (mcp_token_path[0])
		unlink (mcp_token_path);
	mcp_token_path[0] = 0;
	mcp_token[0] = 0;
	MCAP_Shutdown ();
}

/*
==================
MCP_FrameId

Simulation frame id: see mcp_sim_frame. A frozen stepped session keeps
returning the same id; each merged tick advances it by one.
==================
*/
unsigned MCP_FrameId (void)
{
	return mcp_sim_frame;
}

/*
==================
MCP_FreezeSim

True while the host loop must skip this frame's client/server
simulation. Polling, rendering, sound and the watchdog are unaffected,
so an idle owned session stays observable without consuming game time.
==================
*/
qboolean MCP_FreezeSim (void)
{
	return mcp_step_mode && !mcp_act_active;
}
