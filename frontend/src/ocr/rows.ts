// Turns OCR lines with bounding boxes into receipt text, one printed row per line.
// ML Kit often returns the item names and the prices of a receipt as separate blocks;
// grouping by vertical position puts "MILK 2% 4L    6.49" back on one line for Claude.

export interface OcrLine {
  text: string
  left: number
  top: number
  right: number
  bottom: number
}

const CJK = /[぀-ヿ㐀-鿿ｦ-ﾟ]/g

/** Prefer the Japanese reading when it actually found Japanese text; otherwise the Latin one. */
export function chooseLines(latin: OcrLine[], japanese: OcrLine[]): OcrLine[] {
  const cjkCount = japanese.reduce((count, line) => count + (line.text.match(CJK)?.length ?? 0), 0)
  if (cjkCount >= 3) return japanese
  return latin.length ? latin : japanese
}

function median(values: number[]): number {
  const sorted = [...values].sort((a, b) => a - b)
  return sorted.length ? sorted[Math.floor(sorted.length / 2)] : 0
}

/** Groups lines whose vertical centres are within half a line height, left to right. */
export function linesToText(lines: OcrLine[]): string {
  const usable = lines.filter((line) => line.text.trim() && line.bottom > line.top)
  if (!usable.length) return ''
  const tolerance = median(usable.map((line) => line.bottom - line.top)) / 2
  const centre = (line: OcrLine) => (line.top + line.bottom) / 2

  const rows: OcrLine[][] = []
  for (const line of [...usable].sort((a, b) => centre(a) - centre(b))) {
    const row = rows[rows.length - 1]
    const rowCentre = row ? row.reduce((sum, l) => sum + centre(l), 0) / row.length : null
    if (row && rowCentre !== null && Math.abs(centre(line) - rowCentre) <= tolerance) row.push(line)
    else rows.push([line])
  }
  return rows
    .map((row) =>
      row
        .sort((a, b) => a.left - b.left)
        .map((line) => line.text.trim())
        .join('    '),
    )
    .join('\n')
}
