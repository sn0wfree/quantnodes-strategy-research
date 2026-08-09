import type { ReactNode } from 'react'
import {
  Target,
  BookOpen,
  Minimize2,
  HelpCircle,
  Trash2,
  Bot,
} from 'lucide-react'

export interface SlashCommand {
  command: string
  label: string
  description: string
  icon: ReactNode
  /**
   * When true the Composer fires the command immediately on selection
   * (Enter / click). When false (default), the command token + a
   * trailing space is inserted into the textarea so the user can
   * supply the required argument(s).
   */
  autoSend?: boolean
}

export const SLASH_COMMANDS: SlashCommand[] = [
  {
    command: '/goal',
    label: '目标',
    description: '创建并跟踪一个复合目标',
    icon: <Target className="h-4 w-4" />,
  },
  {
    command: '/study',
    label: '研究',
    description: '启动一个研究任务（多轮迭代）',
    icon: <BookOpen className="h-4 w-4" />,
  },
  {
    command: '/compact',
    label: '压缩上下文',
    description: '总结并压缩当前对话历史',
    icon: <Minimize2 className="h-4 w-4" />,
    autoSend: true,
  },
  {
    command: '/agent',
    label: '切换智能体',
    description: '在输入框左侧选择对话 persona',
    icon: <Bot className="h-4 w-4" />,
    autoSend: true,
  },
  {
    command: '/clear',
    label: '清空会话',
    description: '清空当前会话的 LLM 上下文（保留历史）',
    icon: <Trash2 className="h-4 w-4" />,
    autoSend: true,
  },
  {
    command: '/help',
    label: '帮助',
    description: '查看可用命令与快捷键',
    icon: <HelpCircle className="h-4 w-4" />,
    autoSend: true,
  },
]