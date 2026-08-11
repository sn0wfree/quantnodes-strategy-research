import { useEffect, useRef } from 'react'
import { useChatStore } from '../stores/chat'
import { sanitizeSpec, type DagSpec } from '../components/workflow/dagSpec'

/** Subscribe to chatStore.messages and apply `submit_dag_step` tool
 *  completions to the canvas via `onApplyDag`. Idempotent (per
 *  tool_call_id) so replay and stale subscriptions can't double-apply.
 *
 *  Lives in chat session scope (the orchestrator session
 *  dag:{name} is the only one that emits submit_dag_step) — when the
 *  page unmounts the effect tears down and re-subscribes on remount. */
export function useDagStepApply(onApplyDag: (spec: DagSpec) => void) {
  const applied = useRef<Set<string>>(new Set())

  useEffect(() => {
    return useChatStore.subscribe((state) => {
      for (const msg of state.messages.values()) {
        if (!msg.session_id.startsWith('dag:')) continue
        for (const p of msg.parts) {
          if (p.type !== 'tool_call') continue
          if (p.name !== 'submit_dag_step') continue
          if (p.status !== 'done') continue
          if (applied.current.has(p.id)) continue
          if (typeof p.result !== 'string') continue
          try {
            const parsed = JSON.parse(p.result)
            if (parsed?.applied !== true) continue
            const rawArgs = typeof p.arguments === 'string' ? p.arguments : JSON.stringify(p.arguments ?? '{}')
            const argsObj = JSON.parse(rawArgs)
            const spec = sanitizeSpec(argsObj.dag as DagSpec)
            applied.current.add(p.id)
            onApplyDag(spec)
          } catch {
            // skip malformed tool results/args
          }
        }
      }
    })
  }, [onApplyDag])
}