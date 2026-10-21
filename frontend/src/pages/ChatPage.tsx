import { useEffect, useRef, useState, type FormEvent, type KeyboardEvent } from 'react'
import {
  deleteConversation,
  errorMessages,
  getConversation,
  listConversations,
  streamChat,
  type ChatTurn,
  type ConversationSummary,
} from '../api/client'
import { Icon } from '../components/Icon'
import { ChatText } from '../components/ChatText'
import { formatDateTime } from '../lib/format'

// The server accepts this many messages per question; longer chats send their latest part.
const MAX_SENT = 40
const SUGGESTIONS = [
  'What did I spend the most on this month?',
  'How does this month compare with my usual month?',
  'Where do I buy groceries most often?',
]

interface Turn extends ChatTurn {
  /** Set on an assistant turn that failed; it is shown but not sent back to the assistant. */
  error?: boolean
}

/** The latest messages of a conversation, starting with a question (the assistant needs that). */
function recent(turns: ChatTurn[]): ChatTurn[] {
  const tail = turns.slice(-MAX_SENT)
  const firstQuestion = tail.findIndex((t) => t.role === 'user')
  return firstQuestion > 0 ? tail.slice(firstQuestion) : tail
}

export function ChatPage() {
  const [turns, setTurns] = useState<Turn[]>([])
  // Conversations are saved in the user's spreadsheet; Ask opens the latest one.
  const [conversationId, setConversationId] = useState<string | null>(null)
  const [conversations, setConversations] = useState<ConversationSummary[] | null>(null)
  const [showHistory, setShowHistory] = useState(false)
  const [loading, setLoading] = useState(true)
  const [loadError, setLoadError] = useState<string | null>(null)
  const [input, setInput] = useState('')
  const [streaming, setStreaming] = useState(false)
  const [status, setStatus] = useState<string | null>(null)
  const abortRef = useRef<AbortController | null>(null)
  const endRef = useRef<HTMLDivElement>(null)

  useEffect(() => {
    endRef.current?.scrollIntoView({ behavior: 'smooth', block: 'end' })
  }, [turns, status])

  const refreshList = () =>
    listConversations()
      .then(setConversations)
      .catch(() => undefined) // the list is a convenience; the chat works without it

  const open = async (id: string) => {
    abortRef.current?.abort()
    setShowHistory(false)
    setLoading(true)
    setLoadError(null)
    try {
      setTurns(await getConversation(id))
      setConversationId(id)
    } catch (error) {
      setLoadError(errorMessages(error).join(' '))
    } finally {
      setLoading(false)
    }
  }

  useEffect(() => {
    let active = true
    listConversations()
      .then(async (list) => {
        if (!active) return
        setConversations(list)
        if (list.length > 0) {
          const latest = await getConversation(list[0].conversation_id)
          if (!active) return
          setTurns(latest)
          setConversationId(list[0].conversation_id)
        }
      })
      .catch((error: unknown) => active && setLoadError(errorMessages(error).join(' ')))
      .finally(() => active && setLoading(false))
    return () => {
      active = false
    }
  }, [])

  useEffect(() => () => abortRef.current?.abort(), [])

  const updateLast = (change: (turn: Turn) => Turn) =>
    setTurns((current) => [...current.slice(0, -1), change(current[current.length - 1])])

  const ask = async (question: string) => {
    const text = question.trim()
    if (!text || streaming) return
    // Failed answers are shown to the user but not sent back to the assistant.
    const earlier: ChatTurn[] = turns.filter((t) => !t.error && t.content).map(({ role, content }) => ({ role, content }))
    const history: ChatTurn[] = recent([...earlier, { role: 'user', content: text }])
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
          if (event.type === 'conversation') {
            setConversationId(event.text)
          } else if (event.type === 'text') {
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
        conversationId,
      )
      void refreshList()
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
    setShowHistory(false)
    setLoadError(null)
    setTurns([])
    setConversationId(null)
  }

  const remove = async (conversation: ConversationSummary) => {
    if (!window.confirm(`Delete "${conversation.title}"?`)) return
    try {
      await deleteConversation(conversation.conversation_id)
      setConversations((list) => list?.filter((c) => c.conversation_id !== conversation.conversation_id) ?? null)
      if (conversation.conversation_id === conversationId) newChat()
    } catch (error) {
      setLoadError(errorMessages(error).join(' '))
    }
  }

  return (
    <section className="page chat">
      <div className="page-title">
        <h1>Ask about your spending</h1>
        <div className="chat-actions">
          {conversations !== null && conversations.length > 0 && (
            <button
              type="button"
              className={`button button-ghost button-small${showHistory ? ' button-on' : ''}`}
              onClick={() => setShowHistory((shown) => !shown)}
              aria-expanded={showHistory}
            >
              <Icon name="clock" size={18} />
              History
            </button>
          )}
          {turns.length > 0 && (
            <button type="button" className="button button-secondary button-small" onClick={newChat}>
              <Icon name="plus" size={18} />
              New chat
            </button>
          )}
        </div>
      </div>

      {showHistory && conversations !== null && (
        <ul className="card chat-history" aria-label="Saved conversations">
          {conversations.map((conversation) => (
            <li key={conversation.conversation_id} className={conversation.conversation_id === conversationId ? 'chat-history-current' : ''}>
              <button type="button" className="chat-history-open" onClick={() => void open(conversation.conversation_id)}>
                <span className="chat-history-title">{conversation.title || 'Conversation'}</span>
                <span className="muted">{formatDateTime(conversation.updated_at)}</span>
              </button>
              <button
                type="button"
                className="icon-button"
                onClick={() => void remove(conversation)}
                aria-label={`Delete "${conversation.title}"`}
              >
                <Icon name="trash" size={18} />
              </button>
            </li>
          ))}
        </ul>
      )}

      {loadError && (
        <p className="notice notice-error" role="alert">
          {loadError}
        </p>
      )}

      <div className="chat-log" aria-live="polite">
        {loading && <p className="muted typing">Loading your conversations…</p>}
        {!loading && turns.length === 0 && (
          <div className="chat-welcome">
            <img src="/loon-logo.png" alt="" width={44} height={56} />
            <p>Hi! Ask me anything about your spending. I look up the numbers from your receipts and bank and card transactions.</p>
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
              <img className="assistant-avatar" src="/loon-logo.png" alt="" width={25} height={32} />
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
        <button
          type="submit"
          className="send-button"
          disabled={streaming || !input.trim()}
          aria-label={streaming ? 'Answering…' : 'Ask'}
        >
          <Icon name="send" size={20} />
        </button>
      </form>
      <p className="muted field-note">
        Answers come from your receipts and bank and card transactions. Your questions and spending data are sent to Claude
        (Anthropic) to answer them. Conversations are saved in your spreadsheet.
      </p>
    </section>
  )
}
