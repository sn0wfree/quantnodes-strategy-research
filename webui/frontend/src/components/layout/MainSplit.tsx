import { MessageList } from '../chat/MessageList'
import { Composer } from '../chat/Composer'
import { SessionTabs } from '../chat/SessionTabs'

export function MainSplit() {
  return (
    <div className="flex flex-1 flex-col overflow-hidden bg-slate-900">
      <SessionTabs />
      {/* Messages */}
      <div className="flex-1 overflow-hidden">
        <MessageList />
      </div>
      {/* Composer */}
      <Composer />
    </div>
  )
}