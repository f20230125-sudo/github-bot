export type AgentMeta = { id: string; name: string; role: string; color: string };

const AGENTS: Record<string, AgentMeta> = {
  patch: { id: "patch", name: "Patch", role: "GitHub maintainer", color: "var(--agent-patch)" },
  pitch: { id: "pitch", name: "Pitch", role: "LinkedIn writer", color: "var(--agent-pitch)" },
  desk: { id: "desk", name: "Desk", role: "System", color: "var(--agent-desk)" },
};

export function agentMeta(id: string): AgentMeta {
  return AGENTS[id] ?? { id, name: id, role: "Agent", color: "var(--agent-desk)" };
}
