import { describe, expect, it } from 'vitest'
import { dataUrlToBlob } from './image'

describe('dataUrlToBlob', () => {
  it('decodes base64 data URLs with their media type', async () => {
    const blob = dataUrlToBlob('data:image/jpeg;base64,/9j/4A==')

    expect(blob.type).toBe('image/jpeg')
    expect([...new Uint8Array(await blob.arrayBuffer())]).toEqual([0xff, 0xd8, 0xff, 0xe0])
  })
})
