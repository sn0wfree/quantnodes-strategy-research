/**
 * useStudyChatMode — manages directive/chat mode and chat session
 * association for a study's embedded chat widget.
 *
 * Mode is persisted to localStorage per study.
 * Chat session ID is created on-demand and mapped in localStorage.
 */
import { useState, useCallback, useEffect } from 'react'
import { useSessionStore } from '../../../../stores/session'

export type ChatMode = 'directive' | 'chat'

function modeKey(studyId: string): string {
  return `sr-study-chat-mode-${studyId}`
}

function sessionKey(studyId: string): string {
  return `sr-study-chat-session-${studyId}`
}

function readMode(studyId: string): ChatMode {
  if (typeof window === 'undefined') return 'directive'
  try {
    return (localStorage.getItem(modeKey(studyId)) as ChatMode) || 'directive'
  } catch {
    return 'directive'
  }
}

function readSessionId(studyId: string): string | null {
  if (typeof window === 'undefined') return null
  try {
    return localStorage.getItem(sessionKey(studyId))
  } catch {
    return null
  }
}

function writeMode(studyId: string, mode: ChatMode): void {
  try {
    localStorage.setItem(modeKey(studyId), mode)
  } catch { /* ignore */ }
}

function writeSessionId(studyId: string, sessionId: string): void {
  try {
    localStorage.setItem(sessionKey(studyId), sessionId)
  } catch { /* ignore */ }
}

export function useStudyChatMode(studyId: string) {
  const [mode, setModeState] = useState<ChatMode>(() => readMode(studyId))
  const [chatSessionId, setChatSessionId] = useState<string | null>(
    () => readSessionId(studyId),
  )
  const [creating, setCreating] = useState(false)

  const setMode = useCallback((m: ChatMode) => {
    setModeState(m)
    writeMode(studyId, m)
  }, [studyId])

  /**
   * Ensure a chat session exists for this study.
   * Creates one on-demand if needed (lazy — only when switching to chat mode).
   */
  const ensureChatSession = useCallback(async (): Promise<string> => {
    // Return existing if valid
    if (chatSessionId) return chatSessionId

    setCreating(true)
    try {
      // Use session store's createNewSession
      const session = await useSessionStore.getState().createNewSession(
        `Study: ${studyId.slice(0, 12)}...`,
      )
      const id = session.id
      setChatSessionId(id)
      writeSessionId(studyId, id)
      return id
    } finally {
      setCreating(false)
    }
  }, [studyId, chatSessionId])

  // Sync mode on studyId change
  useEffect(() => {
    setModeState(readMode(studyId))
    setChatSessionId(readSessionId(studyId))
  }, [studyId])

  return {
    mode,
    setMode,
    chatSessionId,
    ensureChatSession,
    creating,
  }
}
