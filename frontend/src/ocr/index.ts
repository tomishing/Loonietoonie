// Single OCR entry point for every platform (implemented in phase 5).
// Android: ML Kit, iOS: Apple Vision, web: Cloud Vision / Tesseract.js.
// Platform differences must stay inside this folder.

export interface OcrResult {
  text: string
}

export async function recognizeReceipt(_image: Blob): Promise<OcrResult> {
  throw new Error('OCR is not implemented yet (phase 5).')
}
