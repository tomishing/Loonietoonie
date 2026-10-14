import { registerPlugin } from '@capacitor/core'
import { toBase64 } from '../lib/image'
import type { OcrProgress, OcrResult } from './index'
import { chooseLines, linesToText, type OcrLine } from './rows'

interface ReceiptOcrPlugin {
  recognize(options: { image: string }): Promise<{ width: number; height: number; latin: OcrLine[]; japanese: OcrLine[] }>
}

// Implemented in android/app/src/main/java/com/loonietoonie/app/ReceiptOcrPlugin.java (Google ML Kit).
const ReceiptOcr = registerPlugin<ReceiptOcrPlugin>('ReceiptOcr')

export async function recognizeWithMlKit(
  image: Blob,
  onProgress?: (progress: OcrProgress) => void,
): Promise<OcrResult> {
  onProgress?.({ progress: 0.2, stage: 'Reading the receipt' })
  const result = await ReceiptOcr.recognize({ image: await toBase64(image) })
  onProgress?.({ progress: 1, stage: 'Reading the receipt' })
  return { text: linesToText(chooseLines(result.latin, result.japanese)) }
}
