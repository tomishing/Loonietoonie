// Single OCR entry point for every platform. The rest of the app only calls recognizeReceipt().
// Web/PWA: Tesseract.js in the browser (the photo never leaves the device).
// Android (phase 9): Google ML Kit; iOS later: Apple Vision. Platform differences stay in this folder.

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
  const { recognizeWithTesseract } = await import('./tesseract')
  return recognizeWithTesseract(image, onProgress)
}
