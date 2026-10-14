import { useEffect, useRef, useState, type FormEvent, type KeyboardEvent } from 'react'
import { errorMessages, streamChat, type ChatTurn } from '../api/client'
import { ChatText } from '../components/ChatText'

const STORAGE_KEY = 'loonietoonie.chat'
const SUGGESTIONS = [
  'What did I spend the most on this month?',
  'How does this month compare with my usual month?',
  'Where do I buy groceries most often?',
  '今月の外食はいくら？',
]

interface Turn extends ChatTurn {
  /** Set on an assistant turn that failed; it is shown but not sent back to the assistant. */
  error?: boolean
}

/** The conversation lives only in this browser tab (sessionStorage); the server keeps nothing. */
function loadTurns(): Turn[] {
  try {
    const saved = sessionStorage.getItem(STORAGE_KEY)
    return saved ? (JSON.parse(saved) as Turn[]) : []
  } catch {
    return []
  }
}

export function ChatPage() {
  const [turns, setTurns] = useState<Turn[]>(loadTurns)
  const [input, setInput] = useState('')
  const [streaming, setStreaming] = useState(false)
  const [status, setStatus] = useState<string | null>(null)
  const abortRef = useRef<AbortController | null>(null)
  const endRef = useRef<HTMLDivElement>(null)

  useEffect(() => {
    try {
      sessionStorage.setItem(STORAGE_KEY, JSON.stringify(turns))
    } catch {
      // storage unavailable (private mode): the chat still works for this visit
    }
    endRef.current?.scrollIntoView({ behavior: 'smooth', block: 'end' })
  }, [turns, status])

  useEffect(() => () => abortRef.current?.abort(), [])

  const updateLast = (change: (turn: Turn) => Turn) =>
    setTurns((current) => [...current.slice(0, -1), change(current[current.length - 1])])

  const ask = async (question: string) => {
    const text = question.trim()
    if (!text || streaming) return
    // Failed answers are shown to the user but not sent back to the assistant.
    const earlier: ChatTurn[] = turns.filter((t) => !t.error && t.content).map(({ role, content }) => ({ role, content }))
    const history: ChatTurn[] = [...earlier, { role: 'user', content: text }]
    setTurns((current) => [...current, { role: 'user', content: text }, { role: 'assistant', content: '' }])
    setInput('')
    setStreaming(true)
    setStatus('Thinking…')

    const controller = new AbortController()
    abortRef.current = controller
    try {
      await streamChat(
        history,
        (event) => {
          if (event.type === 'text') {
            setStatus(null)
            updateLast((turn) => ({ ...turn, content: turn.content + event.text }))
          } else if (event.type === 'status') {
            setStatus(event.text)
          } else if (event.type === 'reset') {
            updateLast((turn) => ({ ...turn, content: '' }))
          } else if (event.type === 'error') {
            updateLast((turn) => ({ ...turn, content: event.text, error: true }))
          }
        },
        controller.signal,
      )
    } catch (error) {
      if (!controller.signal.aborted) {
        updateLast((turn) => ({ ...turn, content: errorMessages(error).join(' '), error: true }))
      }
    } finally {
      setStreaming(false)
      setStatus(null)
      abortRef.current = null
    }
  }

  const handleSubmit = (event: FormEvent) => {
    event.preventDefault()
    void ask(input)
  }

  const handleKeyDown = (event: KeyboardEvent<HTMLTextAreaElement>) => {
    if (event.key === 'Enter' && !event.shiftKey && !event.nativeEvent.isComposing) {
      event.preventDefault()
      void ask(input)
    }
  }

  const newChat = () => {
    abortRef.current?.abort()
    setTurns([])
  }

  return (
    <section className="page chat">
      <div className="page-title">
        <h1>Ask about your spending</h1>
        {turns.length > 0 && (
          <button type="button" className="button button-secondary button-small" onClick={newChat}>
            New chat
          </button>
        )}
      </div>

      <div className="chat-log" aria-live="polite">
        {turns.length === 0 && (
          <div className="chat-welcome">
            <img src="/favicon.svg" alt="" width={56} height={56} />
            <p>Hi! Ask me anything about your receipts. I look up the numbers in your spreadsheet.</p>
            <div className="suggestions">
              {SUGGESTIONS.map((suggestion) => (
                <button key={suggestion} type="button" className="chip suggestion" onClick={() => void ask(suggestion)}>
                  {suggestion}
                </button>
              ))}
            </div>
          </div>
        )}

        {turns.map((turn, index) =>
          turn.role === 'user' ? (
            <div key={index} className="bubble bubble-user">
              {turn.content}
            </div>
          ) : (
            <div key={index} className="assistant-row">
              <img className="assistant-avatar" src="/favicon.svg" alt="" width={32} height={32} />
              <div className={`bubble bubble-assistant${turn.error ? ' bubble-error' : ''}`}>
                {turn.content ? (
                  <ChatText text={turn.content} />
                ) : (
                  <p className="muted typing">{status ?? 'Thinking…'}</p>
                )}
              </div>
            </div>
          ),
        )}
        <div ref={endRef} />
      </div>

      <form className="chat-input" onSubmit={handleSubmit}>
        <label className="sr-only" htmlFor="chat-question">
          Your question
        </label>
        <textarea
          id="chat-question"
          value={input}
          onChange={(event) => setInput(event.target.value)}
          onKeyDown={handleKeyDown}
          placeholder="e.g. How much did I spend on dining out last month?"
          rows={1}
          maxLength={4000}
        />
        <button type="submit" className="button button-primary" disabled={streaming || !input.trim()}>
          {streaming ? '…' : 'Ask'}
        </button>
      </form>
      <p className="muted field-note">
        Answers come from your receipts. Your questions and spending data are sent to Claude (Anthropic) to answer them.
      </p>
    </section>
  )
}
