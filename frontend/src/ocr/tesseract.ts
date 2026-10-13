import { createWorker, type Worker } from 'tesseract.js'
import type { OcrProgress, OcrResult } from './index'

// English, French (Québec receipts), and Japanese. Language data downloads once, then is cached.
const LANGUAGES = ['eng', 'fra', 'jpn']

const STAGES: Record<string, string> = {
  'loading tesseract core': 'Starting the text reader',
  'initializing tesseract': 'Starting the text reader',
  'loading language traineddata': 'Downloading language data (first time only)',
  'initializing api': 'Getting ready',
  'recognizing text': 'Reading the receipt',
}

let workerPromise: Promise<Worker> | null = null
let progressListener: ((progress: OcrProgress) => void) | undefined

function getWorker(): Promise<Worker> {
  workerPromise ??= createWorker(LANGUAGES, undefined, {
    logger: (message) => {
      progressListener?.({ progress: message.progress, stage: STAGES[message.status] ?? 'Reading the receipt' })
    },
  }).catch((error: unknown) => {
    workerPromise = null
    throw error
  })
  return workerPromise
}

export async function recognizeWithTesseract(
  image: Blob,
  onProgress?: (progress: OcrProgress) => void,
): Promise<OcrResult> {
  progressListener = onProgress
  try {
    const worker = await getWorker()
    const { data } = await worker.recognize(image)
    return { text: data.text }
  } finally {
    progressListener = undefined
  }
}
