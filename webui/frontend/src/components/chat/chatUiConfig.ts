/**
 * Chat UI Config — 从配置文件加载聊天界面视觉样式
 *
 * 配置文件: public/chat-ui-config.json
 * 修改该文件即可改变聊天界面的外观，无需改代码。
 */
import chatUiConfigRaw from '../../../public/chat-ui-config.json'

// ── 类型定义 ───────────────────────────────────────────────────

export interface AssistantConfig {
  avatar: {
    icon: string
    size: number
    gradient: string[]
  }
  labels: {
    modelPrefix: string
    modelSeparator: string
    queuedLabel: string
    toolCountFormat: string
    tokenCountFormat: string
  }
  colors: {
    streamingDot: string
    flatLabel: string
    bubbleLabel: string
    timestamp: string
  }
  visibility: {
    showVerifiabilityBadge: boolean
    showStreamingStatus: boolean
  }
}

export interface UserBubbleConfig {
  labels: {
    userLabel: string
    editLabel: string
    resendLabel: string
    cancelLabel: string
  }
  colors: {
    label: string
    timestamp: string
    gradient: string[]
  }
  sizing: {
    maxWidth: string
    borderRadius: string
    padding: { x: number; y: number }
  }
  visibility: {
    allowEdit: boolean
  }
}

export interface ToolCallConfig {
  icons: {
    default: string
    status: Record<string, string>
  }
  colors: {
    running: { border: string; bg: string }
    error: { border: string; bg: string }
    done: { border: string; bg: string }
    pending: { border: string; bg: string }
  }
  labels: {
    dangerousBadge: string
    dangerousTooltip: string
  }
  dangerousTools: string[]
  visibility: {
    showArgsPreview: boolean
    showResultSummary: boolean
    showDuration: boolean
    showCopy: boolean
    showRetry: boolean
    showDangerousBadge: boolean
  }
}

export interface ThinkingConfig {
  icons: {
    header: string
  }
  colors: {
    border: string
    background: string
    headerText: string
    contentText: string
    accent: string
  }
  labels: {
    streamingFormat: string
    doneFormat: string
    charCountSuffix: string
  }
  sizing: {
    maxHeight: number
    charThreshold: number
  }
  visibility: {
    showCopy: boolean
    showCharCount: boolean
  }
}

export interface MessageListConfig {
  labels: {
    roundSeparator: string
    emptyTitle: string
    emptyDescription: string
    loadMore: string
    errorLabel: string
    errorDetail: string
    compactionLabel: string
    scrollToBottom: string
  }
  colors: {
    separatorLine: string
    separatorPill: string
    separatorText: string
  }
  visibility: {
    showDaySeparators: boolean
    showQuickStartChips: boolean
    showContextBar: boolean
    showCompactBanner: boolean
  }
}

export interface PageShellConfig {
  layout: {
    headerHeight: number
    contentMaxWidth: number
    contentPadding: { x: number; y: number }
  }
  labels: {
    themeLightLabel: string
    themeDarkLabel: string
  }
  visibility: {
    showThemeToggle: boolean
  }
}

export interface ChatUiConfig {
  assistant: AssistantConfig
  userBubble: UserBubbleConfig
  toolCall: ToolCallConfig
  thinking: ThinkingConfig
  messageList: MessageListConfig
  pageShell: PageShellConfig
}

// ── 导出配置 ───────────────────────────────────────────────────

export const chatUiConfig: ChatUiConfig = chatUiConfigRaw as ChatUiConfig

// ── 公共 API ───────────────────────────────────────────────────

/**
 * 获取完整的聊天界面配置
 */
export function getChatConfig(): ChatUiConfig {
  return chatUiConfig
}

/**
 * 获取特定部分的配置
 */
export function getAssistantConfig(): AssistantConfig {
  return chatUiConfig.assistant
}

export function getToolCallConfig(): ToolCallConfig {
  return chatUiConfig.toolCall
}

export function getThinkingConfig(): ThinkingConfig {
  return chatUiConfig.thinking
}

export function getMessageListConfig(): MessageListConfig {
  return chatUiConfig.messageList
}

export function getPageShellConfig(): PageShellConfig {
  return chatUiConfig.pageShell
}
