#ifndef Q_MCP_H
#define Q_MCP_H

void MCP_Init (void);
void MCP_Poll (void);
void MCP_Shutdown (void);
unsigned MCP_FrameId (void);
void MCP_ConsoleTail (char *out, int outsize);

// Input merge (q_mcp_input.c) — never touches in_attack/in_jump/in_impulse.
int MCP_Move (usercmd_t *cmd);
int MCP_Buttons (void);
int MCP_Impulse (void);
void MCP_BeginInput (float fwd, float strafe, float vert, float yawdeg,
	float pitchdeg, qboolean attack, int jump, int impulse, qboolean run);
void MCP_EndInput (void);

// Tick notification (q_mcp.c): MCP_Move calls this once per merged tick.
void MCP_NoteTick (void);

#endif
