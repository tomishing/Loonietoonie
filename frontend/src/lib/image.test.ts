import { describe, expect, it } from 'vitest'
import { dataUrlToBlob, stitchLayout } from './image'

describe('dataUrlToBlob', () => {
  it('decodes base64 data URLs with their media type', async () => {
    const blob = dataUrlToBlob('data:image/jpeg;base64,/9j/4A==')

    expect(blob.type).toBe('image/jpeg')
    expect([...new Uint8Array(await blob.arrayBuffer())]).toEqual([0xff, 0xd8, 0xff, 0xe0])
  })
})

describe('stitchLayout', () => {
  it('scales all parts to one width, capped at 1400 px', () => {
    expect(stitchLayout([{ width: 2000, height: 1000 }, { width: 1000, height: 1000 }])).toEqual({
      width: 1400,
      heights: [700, 1400],
    })
  })

  it('keeps narrow parts at their width', () => {
    expect(stitchLayout([{ width: 800, height: 1600 }, { width: 800, height: 400 }])).toEqual({
      width: 800,
      heights: [1600, 400],
    })
  })

  it('shrinks everything when the stitched image would be too tall', () => {
    const { width, heights } = stitchLayout(Array.from({ length: 5 }, () => ({ width: 1000, height: 4000 })))
    expect(heights.reduce((sum, height) => sum + height, 0)).toBeLessThanOrEqual(14000)
    expect(width).toBe(700)
  })
})
