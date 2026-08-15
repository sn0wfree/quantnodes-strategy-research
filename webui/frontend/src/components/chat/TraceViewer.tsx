/** TraceViewer — timeline view of agent trace events (DSH Trajectory View).

Displays LLM requests, responses, tool calls, and compaction events
as a scrollable timeline.  Fetches data from GET /api/chat/session/{id}/trace.
*/

import { useCallback, useEffect, useState } from "react";

interface TraceEvent {
  ts?: number;
  type: string;
  iteration?: number;
  [key: string]: unknown;
}

interface TraceViewerProps {
  sessionId: string;
  onClose?: () => void;
}

const EVENT_COLORS: Record<string, string> = {
  llm_request: "border-blue-500/60 bg-blue-950/30",
  llm_response: "border-green-500/60 bg-green-950/30",
  tool_result: "border-amber-500/60 bg-amber-950/30",
  tool_error: "border-red-500/60 bg-red-950/30",
  loop_start: "border-purple-500/60 bg-purple-950/30",
  loop_end: "border-purple-500/60 bg-purple-950/30",
  iter_start: "border-slate-500/60 bg-slate-950/30",
  compression: "border-cyan-500/60 bg-cyan-950/30",
  error: "border-red-500/60 bg-red-950/30",
  heartbeat: "border-gray-500/40 bg-gray-950/20",
};

const EVENT_ICONS: Record<string, string> = {
  llm_request: "▶",
  llm_response: "◀",
  tool_result: "⚙",
  tool_error: "✗",
  loop_start: "▶▶",
  loop_end: "■■",
  iter_start: "→",
  compression: "◆",
  error: "⚠",
  heartbeat: "♡",
};

function formatTimestamp(ts?: number): string {
  if (!ts) return "";
  const d = new Date(ts * 1000);
  return d.toLocaleTimeString("zh-CN", { hour12: false });
}

function EventCard({ event }: { event: TraceEvent }) {
  const [expanded, setExpanded] = useState(false);
  const color = EVENT_COLORS[event.type] ?? "border-gray-500/40 bg-gray-950/20";
  const icon = EVENT_ICONS[event.type] ?? "•";

  const summary = (() => {
    switch (event.type) {
      case "llm_request":
        return `LLM Request — ${event.tools_count ?? 0} tools, ${event.history_count ?? 0} messages, ${event.system_prompt_len ?? 0} chars prompt`;
      case "llm_response":
        return `LLM Response — ${event.finish_reason ?? "?"}, ${event.tool_call_count ?? 0} tool calls`;
      case "tool_result":
        return `${event.tool ?? "?"} — ${event.status ?? "?"} (${event.elapsed_ms ?? 0}ms)`;
      case "tool_error":
        return `Tool Error — ${event.tool ?? "?"}`;
      case "loop_start":
        return `Loop Start — max ${event.max_iterations ?? "?"} iterations`;
      case "loop_end":
        return `Loop End — ${event.reason ?? "?"}, ${event.iterations ?? "?"} iters`;
      case "iter_start":
        return `Iteration ${event.iteration ?? "?"} — ~${event.tokens ?? "?"} tokens`;
      case "compression":
        return `Compression — ${event.applied ?? "?"}`;
      case "error":
        return `Error — ${event.error ?? "?"}`;
      case "heartbeat":
        return `Heartbeat — iteration ${event.iteration ?? "?"}`;
      default:
        return event.type;
    }
  })();

  return (
    <div className={`border-l-2 ${color} rounded-r-md px-3 py-1.5`}>
      <button
        type="button"
        onClick={() => setExpanded(!expanded)}
        className="flex w-full items-center gap-2 text-left text-xs"
      >
        <span className="flex-shrink-0 text-[10px] opacity-60">
          {icon}
        </span>
        <span className="flex-shrink-0 font-mono text-[9px] text-gray-500">
          {formatTimestamp(event.ts)}
        </span>
        {event.iteration != null && (
          <span className="flex-shrink-0 rounded bg-gray-800 px-1 py-0.5 font-mono text-[9px] text-gray-400">
            i{event.iteration}
          </span>
        )}
        <span className="truncate text-gray-300">{summary}</span>
        <span className="ml-auto flex-shrink-0 text-[10px] text-gray-600">
          {expanded ? "▾" : "▸"}
        </span>
      </button>
      {expanded && (
        <pre className="mt-1 max-h-60 overflow-auto whitespace-pre-wrap break-all font-mono text-[10px] leading-relaxed text-gray-400">
          {JSON.stringify(event, null, 2)}
        </pre>
      )}
    </div>
  );
}

export function TraceViewer({ sessionId, onClose }: TraceViewerProps) {
  const [events, setEvents] = useState<TraceEvent[]>([]);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [filter, setFilter] = useState<string>("");

  const fetchTrace = useCallback(async () => {
    if (!sessionId) return;
    setLoading(true);
    setError(null);
    try {
      const params = new URLSearchParams({ limit: "200" });
      if (filter) params.set("type", filter);
      const res = await fetch(
        `/api/chat/session/${encodeURIComponent(sessionId)}/trace?${params}`
      );
      if (!res.ok) throw new Error(`HTTP ${res.status}`);
      const data = await res.json();
      setEvents(data.events ?? []);
    } catch (e: unknown) {
      setError(e instanceof Error ? e.message : "Failed to load trace");
    } finally {
      setLoading(false);
    }
  }, [sessionId, filter]);

  useEffect(() => {
    fetchTrace();
  }, [fetchTrace]);

  return (
    <div className="flex h-full flex-col">
      {/* Header */}
      <div className="flex items-center gap-2 border-b border-gray-800 px-3 py-2">
        <span className="text-xs font-medium text-gray-300">Trace Timeline</span>
        <select
          value={filter}
          onChange={(e) => setFilter(e.target.value)}
          className="ml-auto rounded border border-gray-700 bg-gray-800 px-1.5 py-0.5 text-[10px] text-gray-300"
        >
          <option value="">All events</option>
          <option value="llm_request,llm_response">LLM only</option>
          <option value="tool_result,tool_error">Tools only</option>
          <option value="loop_start,loop_end,iter_start">Loop lifecycle</option>
          <option value="error">Errors only</option>
        </select>
        <button
          type="button"
          onClick={fetchTrace}
          className="rounded border border-gray-700 px-1.5 py-0.5 text-[10px] text-gray-400 hover:bg-gray-800"
        >
          ↻
        </button>
        {onClose && (
          <button
            type="button"
            onClick={onClose}
            className="rounded border border-gray-700 px-1.5 py-0.5 text-[10px] text-gray-400 hover:bg-gray-800"
          >
            ✕
          </button>
        )}
      </div>

      {/* Content */}
      <div className="flex-1 overflow-auto px-3 py-2">
        {loading && (
          <div className="py-4 text-center text-xs text-gray-500">Loading...</div>
        )}
        {error && (
          <div className="py-4 text-center text-xs text-red-400">{error}</div>
        )}
        {!loading && !error && events.length === 0 && (
          <div className="py-4 text-center text-xs text-gray-500">
            No trace events found
          </div>
        )}
        <div className="space-y-1">
          {events.map((event, i) => (
            <EventCard key={`${event.ts}-${i}`} event={event} />
          ))}
        </div>
      </div>

      {/* Footer */}
      <div className="border-t border-gray-800 px-3 py-1.5 text-center text-[9px] text-gray-600">
        {events.length} events · session {sessionId.slice(0, 8)}
      </div>
    </div>
  );
}
