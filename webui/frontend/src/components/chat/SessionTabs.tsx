import { useState, useRef, useEffect, useMemo } from 'react'
import {
  Plus, X, Star, Edit3, Trash2, Tag, Archive,
} from 'lucide-react'
import { useSessionStore, type Session } from '../../stores/session'
import { ConfirmDialog } from '../common/ConfirmDialog'

interface ContextMenuState {
  x: number
  y: number
  sessionId: string
}

export function SessionTabs() {
  const openSessionIds = useSessionStore((s) => s.openSessionIds)
  const currentSessionId = useSessionStore((s) => s.currentSessionId)
  const sessions = useSessionStore((s) => s.sessions)
  const switchSession = useSessionStore((s) => s.switchSession)
  const closeSession = useSessionStore((s) => s.closeSession)
  const createNewSession = useSessionStore((s) => s.createNewSession)
  const updateSessionMeta = useSessionStore((s) => s.updateSessionMeta)
  const deleteSession = useSessionStore((s) => s.deleteSession)

  // Build open sessions in order; if metadata missing, skip
  const openSessions = useMemo(
    () =>
      (openSessionIds ?? [])
        .map((id) => (sessions ?? []).find((sess) => sess.id === id))
        .filter((s): s is Session => Boolean(s)),
    [openSessionIds, sessions]
  )

  const [menu, setMenu] = useState<ContextMenuState | null>(null)
  const [renamingId, setRenamingId] = useState<string | null>(null)
  const [renameValue, setRenameValue] = useState('')
  const [taggingId, setTaggingId] = useState<string | null>(null)
  const [tagInput, setTagInput] = useState('')
  const [pendingDelete, setPendingDelete] = useState<{ id: string; title: string } | null>(null)
  const renameRef = useRef<HTMLInputElement>(null)
  const tagRef = useRef<HTMLInputElement>(null)

  // Focus rename input when entering rename mode
  useEffect(() => {
    if (renamingId && renameRef.current) {
      renameRef.current.focus()
      renameRef.current.select()
    }
  }, [renamingId])

  // Focus tag input when entering tag mode
  useEffect(() => {
    if (taggingId && tagRef.current) {
      tagRef.current.focus()
    }
  }, [taggingId])

  // Dismiss context menu on outside click / Escape
  useEffect(() => {
    if (!menu) return
    const onClick = () => setMenu(null)
    const onKey = (e: KeyboardEvent) => {
      if (e.key === 'Escape') {
        setMenu(null)
        setRenamingId(null)
        setTaggingId(null)
      }
    }
    window.addEventListener('click', onClick)
    window.addEventListener('keydown', onKey)
    return () => {
      window.removeEventListener('click', onClick)
      window.removeEventListener('keydown', onKey)
    }
  }, [menu])

  const handleContextMenu = (e: React.MouseEvent, sessionId: string) => {
    e.preventDefault()
    setMenu({ x: e.clientX, y: e.clientY, sessionId })
  }

  const handleRename = (id: string) => {
    const sess = sessions.find((s) => s.id === id)
    if (!sess) return
    setRenamingId(id)
    setRenameValue(sess.title)
    setMenu(null)
  }

  const commitRename = async () => {
    if (!renamingId) return
    const trimmed = renameValue.trim()
    if (trimmed && trimmed !== sessions.find((s) => s.id === renamingId)?.title) {
      try {
        await updateSessionMeta(renamingId, { title: trimmed })
      } catch (err) {
        console.error('rename failed', err)
      }
    }
    setRenamingId(null)
  }

  const handleToggleStar = async (id: string) => {
    const sess = sessions.find((s) => s.id === id)
    if (!sess) return
    try {
      await updateSessionMeta(id, { starred: !sess.starred })
    } catch (err) {
      console.error('star toggle failed', err)
    }
    setMenu(null)
  }

  const handleArchive = async (id: string) => {
    const sess = sessions.find((s) => s.id === id)
    if (!sess) return
    try {
      await updateSessionMeta(id, { archived: !sess.archived })
    } catch (err) {
      console.error('archive failed', err)
    }
    setMenu(null)
  }

  const handleTag = (id: string) => {
    const sess = sessions.find((s) => s.id === id)
    if (!sess) return
    setTaggingId(id)
    setTagInput(sess.tags.join(', '))
    setMenu(null)
  }

  const commitTags = async () => {
    if (!taggingId) return
    const tags = tagInput
      .split(',')
      .map((t) => t.trim().slice(0, 32))
      .filter(Boolean)
    try {
      await updateSessionMeta(taggingId, { tags })
    } catch (err) {
      console.error('tag failed', err)
    }
    setTaggingId(null)
  }

  const handleDelete = (id: string) => {
    const sess = sessions.find((s) => s.id === id)
    if (!sess) return
    setPendingDelete({ id, title: sess.title })
    setMenu(null)
  }

  const confirmDelete = async () => {
    if (!pendingDelete) return
    try {
      await deleteSession(pendingDelete.id)
    } catch (err) {
      console.error('delete failed', err)
    }
    setPendingDelete(null)
  }

  return (
    <>
      <div className="flex h-10 items-center gap-1 border-b border-slate-800 bg-slate-900/40 px-2 overflow-x-auto flex-shrink-0">
        {openSessions.map((sess) => {
          const isActive = sess.id === currentSessionId
          const isRenaming = renamingId === sess.id
          const isTagging = taggingId === sess.id
          return (
            <div
              key={sess.id}
              onContextMenu={(e) => handleContextMenu(e, sess.id)}
              className={`group relative flex h-7 items-center gap-1.5 rounded-t-md border-b-2 px-2.5 text-xs transition-colors cursor-pointer flex-shrink-0
                ${isActive
                  ? 'border-primary-500 bg-slate-800 text-slate-100'
                  : 'border-transparent text-slate-400 hover:bg-slate-800/50 hover:text-slate-200'
                }
              `}
              onClick={() => {
                if (!isRenaming && !isTagging) void switchSession(sess.id)
              }}
              title={sess.title}
            >
              {sess.starred && (
                <Star className="h-3 w-3 fill-amber-400 text-amber-400 flex-shrink-0" />
              )}
              {isRenaming ? (
                <input
                  ref={renameRef}
                  value={renameValue}
                  onChange={(e) => setRenameValue(e.target.value)}
                  onBlur={() => void commitRename()}
                  onKeyDown={(e) => {
                    if (e.key === 'Enter') {
                      e.preventDefault()
                      void commitRename()
                    } else if (e.key === 'Escape') {
                      setRenamingId(null)
                    }
                  }}
                  onClick={(e) => e.stopPropagation()}
                  className="w-32 rounded border border-primary-500 bg-slate-900 px-1.5 py-0.5 text-xs text-slate-100 outline-none"
                  maxLength={80}
                />
              ) : isTagging ? (
                <input
                  ref={tagRef}
                  value={tagInput}
                  onChange={(e) => setTagInput(e.target.value)}
                  onBlur={() => void commitTags()}
                  onKeyDown={(e) => {
                    if (e.key === 'Enter') {
                      e.preventDefault()
                      void commitTags()
                    } else if (e.key === 'Escape') {
                      setTaggingId(null)
                    }
                  }}
                  onClick={(e) => e.stopPropagation()}
                  placeholder="tag1, tag2"
                  className="w-32 rounded border border-primary-500 bg-slate-900 px-1.5 py-0.5 text-xs text-slate-100 outline-none"
                />
              ) : (
                <>
                  <span className="max-w-[100px] truncate" title={sess.id}>{sess.title}</span>
                  <span className="text-[10px] text-slate-500 ml-1 font-mono">{sess.id.slice(0, 12)}</span>
                  {sess.message_count > 0 && (
                    <span className="rounded-full bg-slate-700/60 px-1.5 py-px text-[9px] text-slate-400">
                      {sess.message_count}
                    </span>
                  )}
                </>
              )}
              <button
                onClick={(e) => {
                  e.stopPropagation()
                  closeSession(sess.id)
                }}
                className="flex h-4 w-4 items-center justify-center rounded text-slate-500 hover:bg-slate-700 hover:text-slate-200 opacity-0 group-hover:opacity-100 transition-opacity"
                title="关闭（保留历史）"
              >
                <X className="h-3 w-3" />
              </button>
              {sess.tags.length > 0 && !isTagging && (
                <div className="absolute -bottom-0.5 left-1.5 flex gap-0.5">
                  {sess.tags.slice(0, 3).map((t) => (
                    <span
                      key={t}
                      className="h-1 w-1 rounded-full bg-primary-500"
                      title={t}
                    />
                  ))}
                </div>
              )}
            </div>
          )
        })}

        <button
          onClick={() => void createNewSession('新会话')}
          className="flex h-7 w-7 items-center justify-center rounded-md text-slate-500 hover:bg-slate-800 hover:text-slate-200 flex-shrink-0"
          title="新建会话 (⌘T)"
        >
          <Plus className="h-3.5 w-3.5" />
        </button>

        {/* Spacer pushes search button right */}
        <div className="flex-1" />
      </div>

      {/* Context Menu */}
      {menu && (
        <div
          className="fixed z-50 min-w-[160px] rounded-lg border border-slate-700 bg-slate-800 py-1 shadow-xl"
          style={{ left: menu.x, top: menu.y }}
          onClick={(e) => e.stopPropagation()}
        >
          <MenuButton
            icon={<Star className="h-3.5 w-3.5" />}
            label={
              sessions.find((s) => s.id === menu.sessionId)?.starred
                ? '取消收藏'
                : '收藏'
            }
            onClick={() => void handleToggleStar(menu.sessionId)}
          />
          <MenuButton
            icon={<Edit3 className="h-3.5 w-3.5" />}
            label="重命名"
            onClick={() => handleRename(menu.sessionId)}
          />
          <MenuButton
            icon={<Tag className="h-3.5 w-3.5" />}
            label="编辑标签..."
            onClick={() => handleTag(menu.sessionId)}
          />
          <MenuButton
            icon={<Archive className="h-3.5 w-3.5" />}
            label={
              sessions.find((s) => s.id === menu.sessionId)?.archived
                ? '取消归档'
                : '归档'
            }
            onClick={() => void handleArchive(menu.sessionId)}
          />
          <div className="my-1 border-t border-slate-700" />
          <MenuButton
            icon={<Trash2 className="h-3.5 w-3.5" />}
            label="删除会话"
            danger
            onClick={() => handleDelete(menu.sessionId)}
          />
        </div>
      )}

      <ConfirmDialog
        open={Boolean(pendingDelete)}
        onOpenChange={(open) => !open && setPendingDelete(null)}
        title="删除会话"
        description={
          pendingDelete
            ? `确认删除「${pendingDelete.title}」？会话内所有消息将被永久清除，不可恢复。`
            : ''
        }
        confirmLabel="删除"
        cancelLabel="取消"
        variant="danger"
        onConfirm={() => void confirmDelete()}
      />
    </>
  )
}

function MenuButton({
  icon,
  label,
  onClick,
  danger,
}: {
  icon: React.ReactNode
  label: string
  onClick: () => void
  danger?: boolean
}) {
  return (
    <button
      onClick={onClick}
      className={`flex w-full items-center gap-2 px-3 py-1.5 text-xs
        ${danger
          ? 'text-red-400 hover:bg-red-500/10'
          : 'text-slate-300 hover:bg-slate-700'
        }
      `}
    >
      {icon}
      {label}
    </button>
  )
}