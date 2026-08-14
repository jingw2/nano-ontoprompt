import { useState } from 'react'
import { useTranslation } from 'react-i18next'
import type { AgentMessage } from '@/api/agentSessions'
import type { StreamState } from '@/api/agentStream'

interface Props {
  messages: AgentMessage[]
  stream: StreamState
  clarification: { id: string; question: string; baseRevision: number } | null
  onSend: (text: string) => void
  onAnswerClarification: (answer: string) => void
  onRetry: () => void
}

export default function ConversationPanel({
  messages, stream, clarification, onSend, onAnswerClarification, onRetry,
}: Props) {
  const { t } = useTranslation()
  const [draft, setDraft] = useState('')
  const [clarificationAnswer, setClarificationAnswer] = useState('')

  const submit = () => {
    if (!draft.trim()) return
    onSend(draft.trim())
    setDraft('')
  }

  return (
    <div className="flex-1 flex flex-col min-w-0" data-testid="conversation-panel">
      <div className="flex-1 overflow-auto p-4 space-y-3">
        {messages.map(m => (
          <div key={m.id} className={`flex ${m.role === 'user' ? 'justify-end' : 'justify-start'}`}>
            <div className={`max-w-[75%] rounded-lg px-3 py-2 text-sm ${m.role === 'user' ? 'bg-black text-white' : 'bg-gray-100'}`}>
              {m.content ?? '…'}
            </div>
          </div>
        ))}
        {stream.phase === 'streaming' && (
          <div className="text-xs text-gray-400" data-testid="stream-indicator">
            {t('agent.app.streaming', 'streaming…')}
          </div>
        )}
        {stream.events.filter(e => e.event === 'message' || e.event === 'final_response').map((e, i) => (
          <div key={`evt-${i}`} className="flex justify-start">
            <div className="bg-gray-100 rounded-lg px-3 py-2 text-sm">
              {String((e.data as { message?: string }).message ?? '')}
            </div>
          </div>
        ))}
        {stream.phase === 'error' && stream.error && (
          <div className="bg-red-50 border border-red-200 rounded-lg p-3 text-sm text-red-600" role="alert">
            <p>{stream.error}</p>
            <button type="button" onClick={onRetry}
              className="mt-2 px-3 py-1 text-xs border border-red-300 rounded hover:bg-red-100">
              {t('agent.list.retry', 'Retry')}
            </button>
          </div>
        )}
      </div>

      {clarification && (
        <div className="border-t p-3 bg-amber-50" data-testid="clarification-box">
          <p className="text-sm mb-2">{clarification.question}</p>
          <div className="flex gap-2">
            <input value={clarificationAnswer}
              onChange={e => setClarificationAnswer(e.target.value)}
              className="flex-1 border rounded-lg px-3 py-2 text-sm"
              placeholder={t('agent.app.clarify_answer', '回答…')} />
            <button type="button" onClick={() => { onAnswerClarification(clarificationAnswer.trim()); setClarificationAnswer('') }}
              className="px-3 py-2 text-sm bg-black text-white rounded-lg disabled:opacity-40"
              disabled={!clarificationAnswer.trim()}>
              {t('agent.app.answer', '回答')}
            </button>
          </div>
        </div>
      )}

      <div className="border-t p-3 flex gap-2">
        <input value={draft} onChange={e => setDraft(e.target.value)}
          onKeyDown={e => { if (e.key === 'Enter') submit() }}
          className="flex-1 border rounded-lg px-3 py-2 text-sm"
          placeholder={t('agent.app.message', '输入消息…')} />
        <button type="button" onClick={submit}
          className="px-4 py-2 text-sm bg-black text-white rounded-lg disabled:opacity-40"
          disabled={!draft.trim() || stream.phase === 'streaming'}>
          {t('agent.app.send', 'Send')}
        </button>
      </div>
    </div>
  )
}
