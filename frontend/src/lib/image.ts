// Receipt photos are downsized before OCR and upload: plenty for text, and well under the 5 MB limit.
const MAX_EDGE = 2000
const JPEG_QUALITY = 0.85

export interface PreparedImage {
  blob: Blob
  mediaType: 'image/jpeg'
  previewUrl: string
}

/** Downsizes a photo (respecting EXIF rotation) and converts it to JPEG. */
export async function prepareImage(file: Blob): Promise<PreparedImage> {
  const bitmap = await createImageBitmap(file, { imageOrientation: 'from-image' })
  const scale = Math.min(1, MAX_EDGE / Math.max(bitmap.width, bitmap.height))
  const width = Math.round(bitmap.width * scale)
  const height = Math.round(bitmap.height * scale)

  const canvas = document.createElement('canvas')
  canvas.width = width
  canvas.height = height
  const context = canvas.getContext('2d')
  if (context === null) throw new Error('Canvas is not available')
  context.fillStyle = '#fff'
  context.fillRect(0, 0, width, height)
  context.drawImage(bitmap, 0, 0, width, height)
  bitmap.close()

  const blob = await new Promise<Blob>((resolve, reject) =>
    canvas.toBlob((result) => (result ? resolve(result) : reject(new Error('Could not encode the photo'))), 'image/jpeg', JPEG_QUALITY),
  )
  return { blob, mediaType: 'image/jpeg', previewUrl: URL.createObjectURL(blob) }
}

/** Base64 (no data: prefix) for JSON requests. */
export async function toBase64(blob: Blob): Promise<string> {
  const dataUrl = await new Promise<string>((resolve, reject) => {
    const reader = new FileReader()
    reader.onload = () => resolve(String(reader.result))
    reader.onerror = () => reject(reader.error ?? new Error('Could not read the photo'))
    reader.readAsDataURL(blob)
  })
  return dataUrl.slice(dataUrl.indexOf(',') + 1)
}
