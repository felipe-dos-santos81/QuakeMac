#ifndef Q_MCP_H
#define Q_MCP_H

void MCP_Init (void);
void MCP_Poll (void);
void MCP_Shutdown (void);
unsigned MCP_FrameId (void);
void MCP_ConsoleTail (char *out, int outsize);

// Stepped mode gate for the host loop: true while the simulation must
// stay frozen. Compile the call sites out when the bridge is absent.
qboolean MCP_FreezeSim (void);

// Input merge (q_mcp_input.c) — never touches in_attack/in_jump/in_impulse.
int MCP_Move (usercmd_t *cmd);
int MCP_Buttons (void);
int MCP_Impulse (void);
void MCP_BeginInput (float fwd, float strafe, float vert, float yawdeg,
	float pitchdeg, qboolean attack, int jump, int impulse, qboolean run);
void MCP_EndInput (void);

// Tick notification (q_mcp.c): the host loop calls this once per
// completed simulation step, frozen steps excluded.
void MCP_NoteTick (void);

// World generation (q_mcp.c): SV_SpawnServer calls this for every new
// world (map, restart, savegame load).
void MCP_NoteWorldSpawn (void);

// Vision capture (q_mcp_capture.c) — render-path glReadPixels into a
// 2-slot ring, served to the deferred observe op.
typedef struct
{
	byte	*data;			// RGB, bottom-up (glReadPixels order)
	int	w, h;
	int	viewport[4];		// glx, gly, glwidth, glheight
	int	hud_rect[4];		// hud strip in top-down image coords
	double	captured_at;		// Sys_DoubleTime at capture
	unsigned frame;			// MCP_FrameId of the captured frame
} mcap_snapshot_t;

void MCAP_Request (unsigned after_frame, double timeout_secs);
void MCAP_Cancel (void);
void MCAP_Frame (void);
qboolean MCAP_Busy (void);
// 1 = out filled (call MCAP_Release once the blob is sent), 0 = waiting,
// -1 = FRAME_TIMEOUT, -2 = RENDER_UNAVAILABLE
int MCAP_Poll (mcap_snapshot_t *out);
void MCAP_Release (void);
void MCAP_Shutdown (void);

#endif
