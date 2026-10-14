import { describe, expect, it } from 'vitest'
import { joinParts } from './index'

describe('joinParts', () => {
  it('returns a single part unchanged', () => {
    expect(joinParts(['MILK 3.49\nTOTAL 3.49'])).toBe('MILK 3.49\nTOTAL 3.49')
  })

  it('marks each part of a long receipt in order', () => {
    expect(joinParts(['FRESHCO\nMILK 3.49\n', 'MILK 3.49\nTOTAL 3.49'])).toBe(
      '--- part 1 of 2 ---\nFRESHCO\nMILK 3.49\n--- part 2 of 2 ---\nMILK 3.49\nTOTAL 3.49',
    )
  })
})
