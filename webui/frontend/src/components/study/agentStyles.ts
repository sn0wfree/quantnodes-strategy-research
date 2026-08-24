/**
 * Agent Styles — 从配置文件加载 agent 视觉样式
 *
 * 配置文件: public/agent-styles.json
 * 修改该文件即可改变 agent 的颜色、图标、标签，无需改代码。
 */
import agentStylesConfig from '../../../public/agent-styles.json'

// ── 类型定义 ───────────────────────────────────────────────────

export interface AgentStyleConfig {
  name: string
  icon: string
  color: string
  category: string
  description?: string
}

export interface CategoryConfig {
  label: string
  color: string
}

export interface AgentStylesConfig {
  agents: Record<string, AgentStyleConfig>
  categories: Record<string, CategoryConfig>
  default: AgentStyleConfig
}

export interface AgentStyle extends AgentStyleConfig {
  text: string      // Tailwind text color class
  bg: string        // Tailwind background color class
  border: string    // Tailwind border color class
}

// ── 颜色映射 ───────────────────────────────────────────────────

const COLOR_MAP: Record<string, { text: string; bg: string; border: string }> = {
  blue:    { text: 'text-blue-400',    bg: 'bg-blue-500/10',    border: 'border-blue-500/30' },
  violet:  { text: 'text-violet-400',  bg: 'bg-violet-500/10',  border: 'border-violet-500/30' },
  emerald: { text: 'text-emerald-400', bg: 'bg-emerald-500/10', border: 'border-emerald-500/30' },
  cyan:    { text: 'text-cyan-400',    bg: 'bg-cyan-500/10',    border: 'border-cyan-500/30' },
  amber:   { text: 'text-amber-400',   bg: 'bg-amber-500/10',   border: 'border-amber-500/30' },
  red:     { text: 'text-red-400',     bg: 'bg-red-500/10',     border: 'border-red-500/30' },
  yellow:  { text: 'text-yellow-400',  bg: 'bg-yellow-500/10',  border: 'border-yellow-500/30' },
  pink:    { text: 'text-pink-400',    bg: 'bg-pink-500/10',    border: 'border-pink-500/30' },
  orange:  { text: 'text-orange-400',  bg: 'bg-orange-500/10',  border: 'border-orange-500/30' },
  slate:   { text: 'text-slate-400',   bg: 'bg-slate-500/10',   border: 'border-slate-500/30' },
}

// ── 导出配置 ───────────────────────────────────────────────────

export const agentStyles: AgentStylesConfig = agentStylesConfig as AgentStylesConfig

// ── 公共 API ───────────────────────────────────────────────────

/**
 * 获取 agent 的完整样式配置
 * @param agentId - agent ID（如 "researcher", "strategist"）
 * @returns AgentStyle 对象，包含颜色、图标、标签等
 */
export function getAgentStyle(agentId: string): AgentStyle {
  const config = agentStyles.agents[agentId] || agentStyles.default
  const colors = COLOR_MAP[config.color] || COLOR_MAP.slate
  return {
    ...config,
    ...colors,
  }
}

/**
 * 获取 category 的样式配置
 * @param category - 分类名（如 "research", "execution"）
 */
export function getCategoryStyle(category: string) {
  return agentStyles.categories[category] || { label: category, color: 'slate' }
}
