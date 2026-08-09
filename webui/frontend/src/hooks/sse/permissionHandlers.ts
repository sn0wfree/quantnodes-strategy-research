import type { SSEHandler } from './types'

/**
 * Tier 1 A1: payload for a backend `permission_request` SSE event.
 *
 * The fields mirror `core.permission.schema.PermissionDecision`:
 * - `tool_call_id`   — opaque key used by the gateway to pair
 *                       request/response and to scope audit history.
 * - `tool_name`      — the BaseTool being gated.
 * - `args`           — the kwargs the agent would invoke with
 *                       (after internal markers are filtered).
 * - `pattern`        — the rule pattern the evaluator matched (for
 *                       display in the dialog).
 * - `target`         — the path / command target the pattern was
 *                       applied to (for the always-persist breadcrumb).
 */
export interface PermissionRequest {
  tool_call_id: string
  tool_name: string
  args: Record<string, unknown>
  pattern: string
  target: string
}

/**
 * Payload for the `permission_result` event — emitted by the gateway
 * after the user (or default) makes a decision, so the front-end can
 * close the dialog optimistically. The persistent rule is appended
 * server-side if permanent=true.
 */
export interface PermissionResult {
  tool_call_id: string
  action: 'allow' | 'deny'
  permanent: boolean
  reason?: string
}

export const permissionRequest: SSEHandler = (data, ctx) => {
  const payload = data as unknown as Partial<PermissionRequest>
  if (!payload || !payload.tool_call_id || !payload.tool_name) return
  ctx.setPendingPermission({
    tool_call_id: payload.tool_call_id,
    tool_name: payload.tool_name,
    args: payload.args ?? {},
    pattern: payload.pattern ?? '',
    target: payload.target ?? '',
  })
}

export const permissionResult: SSEHandler = (data, ctx) => {
  const payload = data as unknown as Partial<PermissionResult>
  if (!payload || !payload.tool_call_id) return
  ctx.clearPendingPermission(payload.tool_call_id)
}