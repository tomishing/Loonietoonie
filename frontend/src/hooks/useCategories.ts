import { useEffect, useState } from 'react'
import { getCategories, type Category } from '../api/client'

export type CategoriesState =
  | { status: 'loading' }
  | { status: 'loaded'; categories: Category[] }
  | { status: 'error' }

export function useCategories(): CategoriesState {
  const [state, setState] = useState<CategoriesState>({ status: 'loading' })

  useEffect(() => {
    let active = true
    getCategories()
      .then((categories) => {
        if (active) setState({ status: 'loaded', categories })
      })
      .catch(() => {
        if (active) setState({ status: 'error' })
      })
    return () => {
      active = false
    }
  }, [])

  return state
}

/** Icon for a category name, or a neutral tag if the category is unknown. */
export function categoryIcon(state: CategoriesState, name: string): string {
  if (state.status !== 'loaded') return '🏷️'
  return state.categories.find((c) => c.name === name)?.icon || '🏷️'
}
