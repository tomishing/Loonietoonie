import type { ReactNode } from 'react'

/** Renders the assistant's simple formatting: paragraphs, "- " bullet lists, and **bold**. */
function inline(text: string): ReactNode[] {
  return text.split(/(\*\*[^*]+\*\*)/g).map((part, index) =>
    part.startsWith('**') && part.endsWith('**') && part.length > 4 ? (
      <strong key={index}>{part.slice(2, -2)}</strong>
    ) : (
      part
    ),
  )
}

export function ChatText({ text }: { text: string }) {
  const blocks: ReactNode[] = []
  let bullets: string[] = []
  const flush = () => {
    if (bullets.length) {
      blocks.push(
        <ul key={`list-${blocks.length}`}>
          {bullets.map((item, index) => (
            <li key={index}>{inline(item)}</li>
          ))}
        </ul>,
      )
      bullets = []
    }
  }
  for (const line of text.split('\n')) {
    const bullet = /^\s*[-•*]\s+(.*)$/.exec(line)
    if (bullet) {
      bullets.push(bullet[1])
    } else {
      flush()
      if (line.trim()) blocks.push(<p key={`p-${blocks.length}`}>{inline(line)}</p>)
    }
  }
  flush()
  return <>{blocks}</>
}
