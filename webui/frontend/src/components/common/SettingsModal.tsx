import { useState, useEffect } from 'react'
import * as Dialog from '@radix-ui/react-dialog'
import { X, Cpu, User, FolderOpen, Palette, Eye, EyeOff } from 'lucide-react'
import { useLayoutStore } from '../../stores/layout'
import { useAuthStore } from '../../stores/auth'
import { api } from '../../api/client'

interface SystemInfo {
  workspace_path: string
  user_count: number
  llm: { configured: boolean; provider: string; model: string; api_key_source: string }
}

export function SettingsModal() {
  const open = useLayoutStore((s) => s.settingsOpen)
  const setOpen = useLayoutStore((s) => s.setSettingsOpen)
  const user = useAuthStore((s) => s.user)

  const [systemInfo, setSystemInfo] = useState<SystemInfo | null>(null)
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
      api.get<SystemInfo>('/system/info').then(setSystemInfo).catch(() => {})
      setOldPassword('')
      setNewPassword('')
      setConfirmPassword('')
      setPwMsg('')
      setPwError('')
    }
  }, [open])

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
              {systemInfo?.llm ? (
                <div className="space-y-2 text-sm">
                  <Row label="Provider" value={systemInfo.llm.provider} />
                  <Row label="Model" value={systemInfo.llm.model} />
                  <Row label="API Key" value={systemInfo.llm.api_key_source} />
                  <Row label="状态" value={systemInfo.llm.configured ? '✓ 已配置' : '✗ 未配置'} accent={systemInfo.llm.configured} />
                </div>
              ) : (
                <p className="text-sm text-slate-500">加载中...</p>
              )}
            </Section>

            {/* ── 用户管理 ── */}
            <Section icon={User} title="用户管理">
              <div className="space-y-3">
                <Row label="当前用户" value={user?.username || '—'} />
                <div className="space-y-2">
                  <PasswordInput
                    placeholder="当前密码"
                    value={oldPassword}
                    onChange={setOldPassword}
                    show={showOldPw}
                    onToggle={() => setShowOldPw(!showOldPw)}
                  />
                  <PasswordInput
                    placeholder="新密码"
                    value={newPassword}
                    onChange={setNewPassword}
                    show={showNewPw}
                    onToggle={() => setShowNewPw(!showNewPw)}
                  />
                  <input
                    type="password"
                    placeholder="确认新密码"
                    value={confirmPassword}
                    onChange={(e) => setConfirmPassword(e.target.value)}
                    className="w-full rounded-lg border border-slate-700 bg-slate-900 px-3 py-2 text-sm text-slate-100 outline-none focus:border-primary-500"
                  />
                  {pwError && <p className="text-xs text-red-400">{pwError}</p>}
                  {pwMsg && <p className="text-xs text-green-400">{pwMsg}</p>}
                  <button
                    onClick={handleChangePassword}
                    disabled={pwLoading}
                    className="rounded-lg bg-primary-600 px-4 py-2 text-sm font-medium text-white hover:bg-primary-700 disabled:opacity-50"
                  >
                    {pwLoading ? '修改中...' : '修改密码'}
                  </button>
                </div>
              </div>
            </Section>

            {/* ── 工作区设置 ── */}
            <Section icon={FolderOpen} title="工作区设置">
              {systemInfo ? (
                <div className="space-y-2 text-sm">
                  <Row label="路径" value={systemInfo.workspace_path} />
                  <Row label="用户数" value={String(systemInfo.user_count)} />
                </div>
              ) : (
                <p className="text-sm text-slate-500">加载中...</p>
              )}
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

function Row({ label, value, accent }: { label: string; value: string; accent?: boolean }) {
  return (
    <div className="flex items-center justify-between">
      <span className="text-slate-400">{label}</span>
      <span className={accent ? 'text-green-400' : 'text-slate-200'}>{value}</span>
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
    <button
      className={`rounded-lg px-4 py-1.5 text-sm transition-colors ${
        active ? 'bg-primary-600 text-white' : 'bg-slate-700 text-slate-300 hover:bg-slate-600'
      }`}
    >
      {label}
    </button>
  )
}

function SizeBtn({ label, active }: { label: string; active?: boolean }) {
  return (
    <button
      className={`rounded-lg px-4 py-1.5 text-sm transition-colors ${
        active ? 'bg-primary-600 text-white' : 'bg-slate-700 text-slate-300 hover:bg-slate-600'
      }`}
    >
      {label}
    </button>
  )
}
