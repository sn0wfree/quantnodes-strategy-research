import { Palette } from 'lucide-react'
import { Section, ThemeBtn, SizeBtn } from './shared'

// TODO(feature): appearance settings (theme/font-size) are placeholders —
// buttons below have no onClick. Wire to a theme store + font-size context
// when the appearance feature lands.
export function AppearanceSection() {
  return (
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
  )
}