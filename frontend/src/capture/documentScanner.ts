import { registerPlugin } from '@capacitor/core'
import { isNativeApp } from '../platform'
import { dataUrlToBlob } from '../lib/image'

interface DocumentScannerPlugin {
  scan(): Promise<{ cancelled: boolean; image?: string }>
}

// Implemented in android/app/src/main/java/com/loonietoonie/app/DocumentScannerPlugin.java
// (Google ML Kit Document Scanner: finds the receipt's edges, crops, straightens, cleans up).
const DocumentScanner = registerPlugin<DocumentScannerPlugin>('DocumentScanner')

/** The scanner can't be used on this phone (e.g. no Google Play services): use the camera instead. */
export class ScannerUnavailableError extends Error {}

/** Whether "Take photo" uses the document scanner (the Android app) or the camera file input (web). */
export const hasDocumentScanner = isNativeApp

/** Opens the scanner. Resolves with the cropped receipt as a JPEG, or null if the user backed out. */
export async function scanDocument(): Promise<Blob | null> {
  let result: { cancelled: boolean; image?: string }
  try {
    result = await DocumentScanner.scan()
  } catch (error) {
    const code = (error as { code?: string }).code
    if (code === 'UNAVAILABLE' || code === 'UNIMPLEMENTED') throw new ScannerUnavailableError(String(error))
    throw error
  }
  if (result.cancelled || !result.image) return null
  return dataUrlToBlob(`data:image/jpeg;base64,${result.image}`)
}
