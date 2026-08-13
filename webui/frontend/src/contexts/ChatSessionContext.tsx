import { createContext, useContext, type ReactNode } from 'react'
import { useSessionStore } from '../stores/session'

/** A small React Context that lets chat components render under an
 *  arbitrary sessionId without disturbing the global currentSessionId
 *  used by the main /chat page.
 *
 *  When no Provider is present, useChatSessionId() falls back to the
 *  global session-store value, so the behaviour of the existing 8 chat
 *  components is unchanged when used in /chat. The Orchestrator panel
 *  wraps its subtree with <ChatSessionProvider sessionId="dag:xxx"> so
 *  Composer / MessageList / AssistantMessage / MessageBubble /
 *  ContextUsageBar / QueuePauseBanner / QuickStartChips / MessageActions
 *  all read the orchestrator session, not the last chat one. */

const ChatSessionContext = createContext<string | null>(null)

export function ChatSessionProvider({
  sessionId,
  children,
}: {
  sessionId: string | null
  children: ReactNode
}) {
  return (
    <ChatSessionContext.Provider value={sessionId}>
      {children}
    </ChatSessionContext.Provider>
  )
}

export function useChatSessionId(): string | null {
  const ctx = useContext(ChatSessionContext)
  const currentSessionId = useSessionStore((s) => s.currentSessionId)
  return ctx !== null ? ctx : currentSessionId
}