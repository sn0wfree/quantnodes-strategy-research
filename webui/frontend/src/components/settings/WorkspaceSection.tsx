import { FolderOpen } from 'lucide-react'
import type { SystemInfo } from './types'
import { Section } from './shared'

export function WorkspaceSection({ systemInfo }: { systemInfo: SystemInfo | null }) {
  return (
    <Section icon={FolderOpen} title="工作区设置">
      {systemInfo ? (
        <div className="space-y-2 text-sm">
          <div className="flex items-center justify-between">
            <span className="text-slate-400">路径</span>
            <span className="text-slate-200 text-xs">{systemInfo.workspace_path}</span>
          </div>
          <div className="flex items-center justify-between">
            <span className="text-slate-400">用户数</span>
            <span className="text-slate-200">{systemInfo.user_count}</span>
          </div>
        </div>
      ) : (
        <p className="text-sm text-slate-500">加载中...</p>
      )}
    </Section>
  )
}