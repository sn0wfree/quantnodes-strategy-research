import { MessageList } from '../chat/MessageList'
import { Composer } from '../chat/Composer'
import { SessionTabs } from '../chat/SessionTabs'
import { TodoDrawer } from '../chat/TodoDrawer'

export function MainSplit() {
  return (
    <div className="relative flex flex-1 flex-col overflow-hidden bg-slate-900">
      <SessionTabs />
      {/* Messages */}
      <div className="flex-1 overflow-hidden">
        <MessageList />
      </div>
      {/* Composer */}
      <Composer />
      {/* Todo drawer: slides over the chat column when the agent
          starts tracking a multi-step task (todo_updated SSE). */}
      <TodoDrawer />
    </div>
  )
}
