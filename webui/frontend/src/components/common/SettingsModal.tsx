import { useState, useEffect } from 'react'
import * as Dialog from '@radix-ui/react-dialog'
import { X } from 'lucide-react'
import { useLayoutStore } from '../../stores/layout'
import { useAuthStore } from '../../stores/auth'
import { api } from '../../api/client'
import type { SystemInfo, LLMConfig } from '../settings/types'
import { LLMSection } from '../settings/LLMSection'
import { AccountSection } from '../settings/AccountSection'
import { WorkspaceSection } from '../settings/WorkspaceSection'
import { ChatLayoutSection } from '../settings/ChatLayoutSection'
import { AppearanceSection } from '../settings/AppearanceSection'

const EMPTY_LLM_CONFIG: LLMConfig = {
  provider: '',
  model: '',
  api_key: '',
  api_key_masked: false,
  base_url: '',
  active_profile: '',
  profiles: [],
  providers: [],
}

export function SettingsModal() {
  const open = useLayoutStore((s) => s.settingsOpen)
  const setOpen = useLayoutStore((s) => s.setSettingsOpen)
  const chatLayout = useLayoutStore((s) => s.chatLayout)
  const setChatLayout = useLayoutStore((s) => s.setChatLayout)
  const user = useAuthStore((s) => s.user)

  const [systemInfo, setSystemInfo] = useState<SystemInfo | null>(null)
  const [llmConfig, setLlmConfig] = useState<LLMConfig>(EMPTY_LLM_CONFIG)
  const [apiKeyInput, setApiKeyInput] = useState('')
  const [sysError, setSysError] = useState('')

  useEffect(() => {
    if (!open) return
    // Load system info. Failures surface as an error banner instead
    // of silently rendering empty selects (B5) — otherwise the user
    // could save an LLM config over an unknown current state.
    api
      .get<SystemInfo>('/system/info')
      .then((info) => {
        setSystemInfo(info)
        setSysError('')
      })
      .catch((err: any) => setSysError(err.message || '系统信息加载失败'))
    api
      .get<LLMConfig>('/system/llm')
      .then((cfg) => setLlmConfig(cfg))
      .catch(() => {
        // LLMSection surfaces its own load-failure banner; keep
        // behavior parity with the original (no global toast here).
      })
    // Reset transient fields for a fresh open.
    setApiKeyInput('')
  }, [open])

  const reloadSystemInfo = async () => {
    const info = await api.get<SystemInfo>('/system/info')
    setSystemInfo(info)
    setSysError('')
  }

  return (
    <Dialog.Root open={open} onOpenChange={setOpen}>
      <Dialog.Portal>
        <Dialog.Overlay className="fixed inset-0 z-50 bg-black/60 backdrop-blur-sm" />
        <Dialog.Content className="fixed left-1/2 top-1/2 z-50 flex max-h-[85vh] w-full max-w-lg -translate-x-1/2 -translate-y-1/2 flex-col rounded-xl bg-slate-800 shadow-2xl">
          {/* Header */}
          <div className="flex items-center justify-between border-b border-slate-700 px-6 py-4">
            <Dialog.Title className="text-lg font-semibold text-slate-100">设置</Dialog.Title>
            <Dialog.Close asChild>
              <button className="text-slate-400 hover:text-slate-200">
                <X className="h-5 w-5" />
              </button>
            </Dialog.Close>
          </div>

          {/* Body */}
          <div className="flex-1 overflow-y-auto px-6 py-5 space-y-6">
            <LLMSection
              systemInfo={systemInfo}
              llmConfig={llmConfig}
              setLlmConfig={setLlmConfig}
              apiKeyInput={apiKeyInput}
              setApiKeyInput={setApiKeyInput}
              sysError={sysError}
              onSystemInfoReloaded={reloadSystemInfo}
            />
            <AccountSection username={user?.username || ''} />
            <WorkspaceSection systemInfo={systemInfo} />
            <ChatLayoutSection chatLayout={chatLayout} setChatLayout={setChatLayout} />
            <AppearanceSection />
          </div>
        </Dialog.Content>
      </Dialog.Portal>
    </Dialog.Root>
  )
}