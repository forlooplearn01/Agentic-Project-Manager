"use client";

import { useState } from "react";
import type { AgentTrace } from "@/lib/types";

const AGENT_LABEL: Record<AgentTrace["agent"], string> = {
  planning: "Planning",
  sync_reporting: "Sync / Reporting",
  meeting_coordinator: "Meeting Coordinator",
};

function resultSummary(raw: string): string {
  try {
    const parsed = JSON.parse(raw);
    if (Array.isArray(parsed)) return `${parsed.length} result${parsed.length === 1 ? "" : "s"}`;
    if (parsed && typeof parsed === "object") {
      const keys = Object.keys(parsed);
      return keys.length ? `{ ${keys.join(", ")} }` : "{}";
    }
    return String(parsed);
  } catch {
    return raw;
  }
}

function AgentSection({ agent }: { agent: AgentTrace }) {
  const [open, setOpen] = useState(agent.triggered);

  return (
    <div className="glass-panel overflow-hidden rounded-xl">
      <button
        type="button"
        onClick={() => setOpen((v) => !v)}
        className="hover-tint flex w-full items-center gap-2 px-3.5 py-2.5 text-left transition-colors"
      >
        <svg
          viewBox="0 0 16 16"
          className={`h-3 w-3 shrink-0 text-text-faint transition-transform duration-200 ${open ? "rotate-90" : ""}`}
          fill="currentColor"
        >
          <path d="M5 3l6 5-6 5V3z" />
        </svg>
        <span
          className={`h-1.5 w-1.5 shrink-0 rounded-full ${
            agent.triggered ? "bg-gradient-to-br from-accent-from to-accent-to" : "bg-text-faint"
          }`}
        />
        <span className="font-mono text-[11px] uppercase tracking-wider text-text-muted">
          {AGENT_LABEL[agent.agent]} - {agent.triggered ? `${agent.trace.length} tool call${agent.trace.length === 1 ? "" : "s"}` : "not triggered"}
        </span>
      </button>

      <div className="trace-collapse" data-open={open}>
        <div>
          {agent.trace.length === 0 ? (
            <p className="border-t border-line px-3.5 py-3 text-[12.5px] text-text-faint">
              {agent.triggered ? "Ran with no tool calls." : "Skipped this run - no trigger condition was met."}
            </p>
          ) : (
            <ol className="space-y-2.5 border-t border-line px-3.5 py-3">
              {agent.trace.map((entry, i) => (
                <li key={i} className="font-mono text-[12.5px] leading-relaxed">
                  <div className="flex items-start gap-2">
                    <span className="mt-1 h-1 w-1 shrink-0 rounded-full bg-gradient-to-br from-accent-from to-accent-to" />
                    <div className="min-w-0">
                      <span className="text-text-primary">{entry.tool}</span>
                      <span className="text-text-faint">
                        ({Object.entries(entry.args).map(([k, v]) => `${k}: ${JSON.stringify(v)}`).join(", ")})
                      </span>
                      <div className="mt-0.5 break-words text-text-muted">→ {resultSummary(entry.result)}</div>
                    </div>
                  </div>
                </li>
              ))}
            </ol>
          )}
        </div>
      </div>
    </div>
  );
}

export default function TracePanel({ agents }: { agents: AgentTrace[] }) {
  return (
    <div className="flex w-full flex-col gap-2">
      {agents.map((a) => (
        <AgentSection key={a.agent} agent={a} />
      ))}
    </div>
  );
}
