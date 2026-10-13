import { NOT_ITEMIZED } from '../../api/client'
import { categoryIcon, type CategoriesState } from '../../hooks/useCategories'

/** Trend fold for smaller categories (matches the backend's OTHER constant). */
export const OTHER_CATEGORIES = '(other categories)'

export function categoryLabel(name: string): string {
  if (name === NOT_ITEMIZED) return 'Not itemized'
  if (name === OTHER_CATEGORIES) return 'Other categories'
  return name
}

export function categoryEmoji(categories: CategoriesState, name: string): string {
  if (name === NOT_ITEMIZED) return '❔'
  if (name === OTHER_CATEGORIES) return '…'
  return categoryIcon(categories, name)
}
