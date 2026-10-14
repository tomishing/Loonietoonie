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
