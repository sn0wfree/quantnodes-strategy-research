import { MessageSquare } from 'lucide-react'
import type { ChatLayout } from '../../stores/layout'
import { Section, LayoutOption } from './shared'

interface ChatLayoutSectionProps {
  chatLayout: ChatLayout
  setChatLayout: (l: ChatLayout) => void
}

export function ChatLayoutSection({ chatLayout, setChatLayout }: ChatLayoutSectionProps) {
  return (
    <Section icon={MessageSquare} title="聊天布局">
      <div className="grid grid-cols-2 gap-3">
        <LayoutOption
          label="气泡式"
          desc="用户消息右对齐气泡，Agent 带头像"
          active={chatLayout === 'bubble'}
          onClick={() => setChatLayout('bubble')}
        />
        <LayoutOption
          label="扁平式"
          desc="所有消息左对齐（Codex 风格）"
          active={chatLayout === 'flat'}
          onClick={() => setChatLayout('flat')}
        />
      </div>
    </Section>
  )
}