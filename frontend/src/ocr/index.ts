// Single OCR entry point for every platform. The rest of the app only calls recognizeReceipt().
// Android app: Google ML Kit on the device (native.ts). Web/PWA: Tesseract.js in the browser.
// iOS later: Apple Vision. Platform differences stay in this folder.

import { isNativeApp } from '../platform'

export interface OcrResult {
  text: string
}

export interface OcrProgress {
  /** 0–1 */
  progress: number
  /** Short, human-readable stage, e.g. "Downloading language data". */
  stage: string
}

export async function recognizeReceipt(
  image: Blob,
  onProgress?: (progress: OcrProgress) => void,
): Promise<OcrResult> {
  if (isNativeApp) {
    const { recognizeWithMlKit } = await import('./native')
    return recognizeWithMlKit(image, onProgress)
  }
  const { recognizeWithTesseract } = await import('./tesseract')
  return recognizeWithTesseract(image, onProgress)
}

/**
 * Text of a long receipt photographed in parts (top to bottom), separated by
 * "--- part N of M ---" lines. The backend's prompt explains the markers and that
 * consecutive parts may overlap. A single part is returned as it is.
 */
export function joinParts(texts: string[]): string {
  if (texts.length === 1) return texts[0]
  return texts.map((text, index) => `--- part ${index + 1} of ${texts.length} ---\n${text.trim()}`).join('\n')
}

/** recognizeReceipt() for each part of a receipt, in order; progress covers all parts. */
export async function recognizeReceiptParts(
  images: Blob[],
  onProgress?: (progress: OcrProgress & { part: number }) => void,
): Promise<OcrResult> {
  const texts: string[] = []
  for (const [index, image] of images.entries()) {
    const { text } = await recognizeReceipt(image, ({ progress, stage }) =>
      onProgress?.({ progress: (index + progress) / images.length, stage, part: index + 1 }),
    )
    texts.push(text)
  }
  return { text: joinParts(texts) }
}
