import { useState, useEffect } from 'react'
import * as Dialog from '@radix-ui/react-dialog'
import { X, Cpu, User, FolderOpen, Palette, Eye, EyeOff, Save, Check, AlertCircle, MessageSquare } from 'lucide-react'
import { useLayoutStore } from '../../stores/layout'
import { useAuthStore } from '../../stores/auth'
import { api } from '../../api/client'

interface SystemInfo {
  workspace_path: string
  user_count: number
  llm: { configured: boolean; provider: string; model: string; api_key_source: string }
}

interface LLMConfig {
  provider: string
  model: string
  api_key: string
  base_url: string
}

const PROVIDERS = [
  { value: 'minimax',  label: 'MiniMax',              models: ['minimax-M3'] },
  { value: 'openai',   label: 'OpenAI',               models: ['gpt-4o', 'gpt-4o-mini', 'gpt-4.1-nano'] },
  { value: 'anthropic', label: 'Anthropic',           models: ['claude-sonnet-4-20250514', 'claude-3-haiku-20240307'] },
  { value: 'deepseek', label: 'DeepSeek',             models: ['deepseek-chat', 'deepseek-reasoner'] },
  { value: 'kimi',     label: 'Kimi (Moonshot)',      models: ['moonshot-v1-8k', 'moonshot-v1-32k', 'moonshot-v1-128k'] },
  { value: 'qwen',     label: '通义千问',              models: ['qwen-plus', 'qwen-turbo', 'qwen-max'] },
  { value: 'custom',   label: '自定义 (OpenAI 兼容)',  models: [] },
]

export function SettingsModal() {
  const open = useLayoutStore((s) => s.settingsOpen)
  const setOpen = useLayoutStore((s) => s.setSettingsOpen)
  const chatLayout = useLayoutStore((s) => s.chatLayout)
  const setChatLayout = useLayoutStore((s) => s.setChatLayout)
  const user = useAuthStore((s) => s.user)

  const [systemInfo, setSystemInfo] = useState<SystemInfo | null>(null)
  const [llmConfig, setLlmConfig] = useState<LLMConfig>({ provider: '', model: '', api_key: '', base_url: '' })
  const [llmLoading, setLlmLoading] = useState(false)
  const [llmMsg, setLlmMsg] = useState('')
  const [llmError, setLlmError] = useState('')
  const [showApiKey, setShowApiKey] = useState(false)

  const [oldPassword, setOldPassword] = useState('')
  const [newPassword, setNewPassword] = useState('')
  const [confirmPassword, setConfirmPassword] = useState('')
  const [pwLoading, setPwLoading] = useState(false)
  const [pwMsg, setPwMsg] = useState('')
  const [pwError, setPwError] = useState('')
  const [showOldPw, setShowOldPw] = useState(false)
  const [showNewPw, setShowNewPw] = useState(false)

  useEffect(() => {
    if (open) {
      // Load system info
      api.get<SystemInfo>('/system/info').then(setSystemInfo).catch(() => {})
      // Load LLM config
      api.get<LLMConfig>('/system/llm').then(setLlmConfig).catch(() => {})
      // Reset password fields
      setOldPassword('')
      setNewPassword('')
      setConfirmPassword('')
      setPwMsg('')
      setPwError('')
      setLlmMsg('')
      setLlmError('')
    }
  }, [open])

  const handleSaveLLM = async () => {
    setLlmMsg('')
    setLlmError('')
    if (!llmConfig.provider) {
      setLlmError('请选择 Provider')
      return
    }
    if (!llmConfig.model) {
      setLlmError('请输入 Model')
      return
    }
    if (llmConfig.provider === 'custom' && !llmConfig.base_url) {
      setLlmError('自定义模式下 Base URL 不能为空')
      return
    }
    setLlmLoading(true)
    try {
      await api.put('/system/llm', llmConfig)
      setLlmMsg('配置已保存')
      // Reload system info
      const info = await api.get<SystemInfo>('/system/info')
      setSystemInfo(info)
    } catch (err: any) {
      setLlmError(err.message || '保存失败')
    } finally {
      setLlmLoading(false)
    }
  }

  const handleChangePassword = async () => {
    setPwMsg('')
    setPwError('')
    if (!oldPassword || !newPassword) {
      setPwError('请填写所有字段')
      return
    }
    if (newPassword !== confirmPassword) {
      setPwError('两次密码不一致')
      return
    }
    if (newPassword.length < 4) {
      setPwError('密码至少 4 位')
      return
    }
    setPwLoading(true)
    try {
      await api.post('/auth/change-password', { old_password: oldPassword, new_password: newPassword })
      setPwMsg('密码修改成功')
      setOldPassword('')
      setNewPassword('')
      setConfirmPassword('')
    } catch (err: any) {
      setPwError(err.message || '修改失败')
    } finally {
      setPwLoading(false)
    }
  }

  const currentModels = PROVIDERS.find((p) => p.value === llmConfig.provider)?.models || []

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

            {/* ── LLM 配置 ── */}
            <Section icon={Cpu} title="LLM 配置">
              <div className="space-y-3">
                {/* Provider */}
                <div>
                  <label className="mb-1 block text-xs text-slate-400">Provider</label>
                  <select
                    value={llmConfig.provider}
                    onChange={(e) => {
                      const provider = e.target.value
                      const models = PROVIDERS.find((p) => p.value === provider)?.models || []
                      setLlmConfig({ ...llmConfig, provider, model: models[0] || '' })
                    }}
                    className="w-full rounded-lg border border-slate-700 bg-slate-900 px-3 py-2 text-sm text-slate-100 outline-none focus:border-primary-500"
                  >
                    <option value="">选择 Provider</option>
                    {PROVIDERS.map((p) => (
                      <option key={p.value} value={p.value}>{p.label}</option>
                    ))}
                  </select>
                </div>

                {/* Model */}
                <div>
                  <label className="mb-1 block text-xs text-slate-400">Model</label>
                  {currentModels.length > 0 ? (
                    <select
                      value={llmConfig.model}
                      onChange={(e) => setLlmConfig({ ...llmConfig, model: e.target.value })}
                      className="w-full rounded-lg border border-slate-700 bg-slate-900 px-3 py-2 text-sm text-slate-100 outline-none focus:border-primary-500"
                    >
                      <option value="">选择 Model</option>
                      {currentModels.map((m) => (
                        <option key={m} value={m}>{m}</option>
                      ))}
                    </select>
                  ) : (
                    <input
                      type="text"
                      placeholder="输入模型名称，如 qwen2.5、llama3、mistral"
                      value={llmConfig.model}
                      onChange={(e) => setLlmConfig({ ...llmConfig, model: e.target.value })}
                      className="w-full rounded-lg border border-slate-700 bg-slate-900 px-3 py-2 text-sm text-slate-100 outline-none focus:border-primary-500"
                    />
                  )}
                </div>

                {/* API Key */}
                <div>
                  <label className="mb-1 block text-xs text-slate-400">API Key</label>
                  <div className="relative">
                    <input
                      type={showApiKey ? 'text' : 'password'}
                      placeholder="输入 API Key"
                      value={llmConfig.api_key}
                      onChange={(e) => setLlmConfig({ ...llmConfig, api_key: e.target.value })}
                      className="w-full rounded-lg border border-slate-700 bg-slate-900 px-3 py-2 pr-10 text-sm text-slate-100 outline-none focus:border-primary-500"
                    />
                    <button
                      type="button"
                      onClick={() => setShowApiKey(!showApiKey)}
                      className="absolute right-2 top-1/2 -translate-y-1/2 text-slate-400 hover:text-slate-200"
                    >
                      {showApiKey ? <EyeOff className="h-4 w-4" /> : <Eye className="h-4 w-4" />}
                    </button>
                  </div>
                </div>

                {/* Base URL */}
                <div>
                  <label className="mb-1 block text-xs text-slate-400">
                    Base URL {llmConfig.provider === 'custom' ? '(必填)' : '(可选，留空使用默认)'}
                  </label>
                  <input
                    type="text"
                    placeholder={llmConfig.provider === 'custom' ? '如 http://localhost:11434/v1' : '留空使用默认'}
                    value={llmConfig.base_url}
                    onChange={(e) => setLlmConfig({ ...llmConfig, base_url: e.target.value })}
                    className="w-full rounded-lg border border-slate-700 bg-slate-900 px-3 py-2 text-sm text-slate-100 outline-none focus:border-primary-500"
                  />
                </div>

                {/* Status */}
                {systemInfo?.llm && (
                  <div className="flex items-center gap-2 text-xs">
                    {systemInfo.llm.configured ? (
                      <><Check className="h-3 w-3 text-green-400" /><span className="text-green-400">当前: {systemInfo.llm.provider} / {systemInfo.llm.model}</span></>
                    ) : (
                      <><AlertCircle className="h-3 w-3 text-amber-400" /><span className="text-amber-400">未配置</span></>
                    )}
                  </div>
                )}

                {llmError && <p className="text-xs text-red-400">{llmError}</p>}
                {llmMsg && <p className="text-xs text-green-400">{llmMsg}</p>}

                <button
                  onClick={handleSaveLLM}
                  disabled={llmLoading}
                  className="flex items-center gap-2 rounded-lg bg-primary-600 px-4 py-2 text-sm font-medium text-white hover:bg-primary-700 disabled:opacity-50"
                >
                  <Save className="h-4 w-4" />
                  {llmLoading ? '保存中...' : '保存配置'}
                </button>
              </div>
            </Section>

            {/* ── 用户管理 ── */}
            <Section icon={User} title="用户管理">
              <div className="space-y-3">
                <div className="flex items-center justify-between text-sm">
                  <span className="text-slate-400">当前用户</span>
                  <span className="text-slate-200">{user?.username || '—'}</span>
                </div>
                <div className="space-y-2">
                  <PasswordInput placeholder="当前密码" value={oldPassword} onChange={setOldPassword} show={showOldPw} onToggle={() => setShowOldPw(!showOldPw)} />
                  <PasswordInput placeholder="新密码" value={newPassword} onChange={setNewPassword} show={showNewPw} onToggle={() => setShowNewPw(!showNewPw)} />
                  <input type="password" placeholder="确认新密码" value={confirmPassword} onChange={(e) => setConfirmPassword(e.target.value)}
                    className="w-full rounded-lg border border-slate-700 bg-slate-900 px-3 py-2 text-sm text-slate-100 outline-none focus:border-primary-500" />
                  {pwError && <p className="text-xs text-red-400">{pwError}</p>}
                  {pwMsg && <p className="text-xs text-green-400">{pwMsg}</p>}
                  <button onClick={handleChangePassword} disabled={pwLoading}
                    className="rounded-lg bg-primary-600 px-4 py-2 text-sm font-medium text-white hover:bg-primary-700 disabled:opacity-50">
                    {pwLoading ? '修改中...' : '修改密码'}
                  </button>
                </div>
              </div>
            </Section>

            {/* ── 工作区设置 ── */}
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

            {/* ── 聊天布局 ── */}
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

            {/* ── 外观设置 ── */}
            <Section icon={Palette} title="外观设置">
              <div className="space-y-3 text-sm">
                <div>
                  <p className="mb-2 text-slate-400">主题</p>
                  <div className="flex gap-2">
                    <ThemeBtn label="暗色" active />
                    <ThemeBtn label="亮色" />
                  </div>
                </div>
                <div>
                  <p className="mb-2 text-slate-400">字体大小</p>
                  <div className="flex gap-2">
                    <SizeBtn label="小" />
                    <SizeBtn label="中" active />
                    <SizeBtn label="大" />
                  </div>
                </div>
              </div>
            </Section>
          </div>
        </Dialog.Content>
      </Dialog.Portal>
    </Dialog.Root>
  )
}

// ── Sub-components ──────────────────────────────────────────

function Section({ icon: Icon, title, children }: { icon: any; title: string; children: React.ReactNode }) {
  return (
    <div>
      <div className="mb-3 flex items-center gap-2">
        <Icon className="h-4 w-4 text-primary-400" />
        <h3 className="text-sm font-medium text-slate-200">{title}</h3>
      </div>
      {children}
    </div>
  )
}

function PasswordInput({ placeholder, value, onChange, show, onToggle }: {
  placeholder: string; value: string; onChange: (v: string) => void; show: boolean; onToggle: () => void
}) {
  return (
    <div className="relative">
      <input
        type={show ? 'text' : 'password'}
        placeholder={placeholder}
        value={value}
        onChange={(e) => onChange(e.target.value)}
        className="w-full rounded-lg border border-slate-700 bg-slate-900 px-3 py-2 pr-10 text-sm text-slate-100 outline-none focus:border-primary-500"
      />
      <button type="button" onClick={onToggle} className="absolute right-2 top-1/2 -translate-y-1/2 text-slate-400 hover:text-slate-200">
        {show ? <EyeOff className="h-4 w-4" /> : <Eye className="h-4 w-4" />}
      </button>
    </div>
  )
}

function ThemeBtn({ label, active }: { label: string; active?: boolean }) {
  return (
    <button className={`rounded-lg px-4 py-1.5 text-sm transition-colors ${active ? 'bg-primary-600 text-white' : 'bg-slate-700 text-slate-300 hover:bg-slate-600'}`}>
      {label}
    </button>
  )
}

function SizeBtn({ label, active }: { label: string; active?: boolean }) {
  return (
    <button className={`rounded-lg px-4 py-1.5 text-sm transition-colors ${active ? 'bg-primary-600 text-white' : 'bg-slate-700 text-slate-300 hover:bg-slate-600'}`}>
      {label}
    </button>
  )
}

function LayoutOption({
  label,
  desc,
  active,
  onClick,
}: {
  label: string
  desc: string
  active: boolean
  onClick: () => void
}) {
  return (
    <button
      onClick={onClick}
      className={`rounded-lg border p-3 text-left transition-colors ${
        active
          ? 'border-primary-500 bg-primary-600/10'
          : 'border-slate-700 bg-slate-900/50 hover:bg-slate-800/50'
      }`}
    >
      <div className="flex items-center justify-between">
        <span className="text-sm font-medium text-slate-200">{label}</span>
        {active && <Check className="h-3.5 w-3.5 text-primary-400" />}
      </div>
      <div className="mt-1 text-xs text-slate-500">{desc}</div>
    </button>
  )
}
