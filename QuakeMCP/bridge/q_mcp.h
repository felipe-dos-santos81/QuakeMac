#ifndef Q_MCP_H
#define Q_MCP_H

void MCP_Init (void);
void MCP_Poll (void);
void MCP_Shutdown (void);
unsigned MCP_FrameId (void);
void MCP_ConsoleTail (char *out, int outsize);

#endif
