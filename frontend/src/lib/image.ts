// Receipt photos are downsized before OCR and upload: plenty for text, and well under the 5 MB limit.
const MAX_EDGE = 2000
const JPEG_QUALITY = 0.85

export interface PreparedImage {
  blob: Blob
  mediaType: 'image/jpeg'
  previewUrl: string
}

/** Converts a data: URL to a Blob without a network round trip. */
export function dataUrlToBlob(dataUrl: string): Blob {
  const [header, data] = dataUrl.split(',', 2)
  const mediaType = /^data:([^;,]+)/.exec(header)?.[1] ?? 'application/octet-stream'
  const binary = atob(data)
  const bytes = new Uint8Array(binary.length)
  for (let i = 0; i < binary.length; i += 1) bytes[i] = binary.charCodeAt(i)
  return new Blob([bytes], { type: mediaType })
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

  // toDataURL, not toBlob: in the Android WebView toBlob takes ~4 s for the same JPEG (measured on a
  // Pixel 8), while toDataURL takes well under 0.1 s.
  const blob = dataUrlToBlob(canvas.toDataURL('image/jpeg', JPEG_QUALITY))
  return { blob, mediaType: 'image/jpeg', previewUrl: URL.createObjectURL(blob) }
}

// Parts of a long receipt are stitched top to bottom into one image for the review screen and Drive.
const STITCH_MAX_WIDTH = 1400
const STITCH_MAX_HEIGHT = 14000 // stays within Android WebView canvas limits and under the 5 MB upload
const STITCH_JPEG_QUALITY = 0.8

/** Size of the stitched image and the height of each part in it (all parts scaled to one width). */
export function stitchLayout(sizes: { width: number; height: number }[]): {
  width: number
  heights: number[]
} {
  let width = Math.min(STITCH_MAX_WIDTH, Math.max(...sizes.map((size) => size.width)))
  let heights = sizes.map((size) => (size.height * width) / size.width)
  const total = heights.reduce((sum, height) => sum + height, 0)
  if (total > STITCH_MAX_HEIGHT) {
    const scale = STITCH_MAX_HEIGHT / total
    width *= scale
    heights = heights.map((height) => height * scale)
  }
  return { width: Math.round(width), heights: heights.map((height) => Math.round(height)) }
}

/** Stitches prepared receipt parts (in order, top to bottom) into one JPEG. */
export async function stitchImages(parts: PreparedImage[]): Promise<PreparedImage> {
  const bitmaps = await Promise.all(parts.map((part) => createImageBitmap(part.blob)))
  const { width, heights } = stitchLayout(bitmaps)
  const canvas = document.createElement('canvas')
  canvas.width = width
  canvas.height = heights.reduce((sum, height) => sum + height, 0)
  const context = canvas.getContext('2d')
  if (context === null) throw new Error('Canvas is not available')
  context.fillStyle = '#fff'
  context.fillRect(0, 0, canvas.width, canvas.height)
  let top = 0
  bitmaps.forEach((bitmap, index) => {
    context.drawImage(bitmap, 0, top, width, heights[index])
    top += heights[index]
    bitmap.close()
  })
  const blob = dataUrlToBlob(canvas.toDataURL('image/jpeg', STITCH_JPEG_QUALITY))
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
