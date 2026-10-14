import type { MouseEvent } from 'react'

// Desktop browsers only open the month picker from its small calendar icon; open it on any click.
export const openPicker = (event: MouseEvent<HTMLInputElement>) => {
  try {
    event.currentTarget.showPicker()
  } catch {
    // Not supported (or not allowed here): the input still works when typed into.
  }
}
