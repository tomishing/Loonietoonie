// PDF receipts (e-receipts, statements saved as PDF): each page becomes an image for the review
// screen, Drive, and Claude's second look, and its embedded text, when it has some, replaces OCR.
// pdf.js is loaded only when a PDF is chosen.

import { dataUrlToBlob } from '../lib/image'

/** The scan accepts at most this many parts (backend MAX_IMAGES). */
export const MAX_PDF_PAGES = 5
// Rendered page width: sharp enough for OCR and reading, small enough for upload.
const RENDER_WIDTH = 1600
const JPEG_QUALITY = 0.9

export interface PdfPages {
  images: Blob[]
  /** Text embedded in each page ("" for scanned pages, which need OCR). */
  texts: string[]
  /** Pages in the file (only the first MAX_PDF_PAGES are read). */
  pageCount: number
}

export class PdfError extends Error {}

export function isPdf(file: File | Blob): boolean {
  return file.type === 'application/pdf' || ('name' in file && /\.pdf$/i.test(file.name))
}

interface TextPiece {
  str: string
  transform: number[]
}

/** Rebuilds printed lines from pdf.js text pieces: same baseline → one line, left to right. */
export function piecesToText(pieces: TextPiece[]): string {
  const lines: { y: number; parts: { x: number; text: string }[] }[] = []
  for (const piece of pieces) {
    if (!piece.str.trim()) continue
    const x = piece.transform[4]
    const y = piece.transform[5]
    const line = lines.find((l) => Math.abs(l.y - y) < 2)
    if (line) line.parts.push({ x, text: piece.str })
    else lines.push({ y, parts: [{ x, text: piece.str }] })
  }
  return lines
    .sort((a, b) => b.y - a.y) // PDF y grows upwards
    .map((line) =>
      line.parts
        .sort((a, b) => a.x - b.x)
        .map((part) => part.text.trim())
        .join(' '),
    )
    .join('\n')
}

export async function readPdf(file: Blob): Promise<PdfPages> {
  const [pdfjs, { default: workerUrl }] = await Promise.all([
    import('pdfjs-dist'),
    import('pdfjs-dist/build/pdf.worker.min.mjs?url'),
  ])
  pdfjs.GlobalWorkerOptions.workerSrc = workerUrl

  // The loading task owns the worker; destroying it frees the document.
  const task = pdfjs.getDocument({ data: new Uint8Array(await file.arrayBuffer()) })
  let document
  try {
    document = await task.promise
  } catch (error) {
    void task.destroy()
    throw new PdfError(
      error instanceof Error && error.name === 'PasswordException'
        ? 'This PDF is password-protected. Save it without a password and try again.'
        : "Couldn't open that PDF.",
    )
  }

  const images: Blob[] = []
  const texts: string[] = []
  try {
    for (let number = 1; number <= Math.min(document.numPages, MAX_PDF_PAGES); number += 1) {
      const page = await document.getPage(number)
      const base = page.getViewport({ scale: 1 })
      const viewport = page.getViewport({ scale: RENDER_WIDTH / base.width })
      const canvas = window.document.createElement('canvas')
      canvas.width = Math.round(viewport.width)
      canvas.height = Math.round(viewport.height)
      const context = canvas.getContext('2d')
      if (context === null) throw new PdfError('Canvas is not available')
      context.fillStyle = '#fff' // transparent PDF backgrounds would turn black in a JPEG
      context.fillRect(0, 0, canvas.width, canvas.height)
      await page.render({ canvas, canvasContext: context, viewport }).promise
      // toDataURL, not toBlob: toBlob takes seconds in the Android WebView (see lib/image.ts).
      images.push(dataUrlToBlob(canvas.toDataURL('image/jpeg', JPEG_QUALITY)))
      const content = await page.getTextContent()
      texts.push(piecesToText(content.items.filter((item): item is TextPiece & typeof item => 'str' in item)))
      page.cleanup()
    }
    return { images, texts, pageCount: document.numPages }
  } finally {
    void task.destroy()
  }
}
