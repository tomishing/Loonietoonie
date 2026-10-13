import { useEffect, useState } from 'react'
import { getCategories, type Category } from '../api/client'

type LoadState =
  | { status: 'loading' }
  | { status: 'loaded'; categories: Category[] }
  | { status: 'error' }

export function CategoryList() {
  const [state, setState] = useState<LoadState>({ status: 'loading' })

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

  if (state.status === 'loading') {
    return <p className="muted">Loading categories…</p>
  }
  if (state.status === 'error') {
    return (
      <p className="notice notice-error" role="alert">
        Couldn't load your categories. Please try again.
      </p>
    )
  }

  return (
    <section className="categories" aria-label="Categories">
      <h2>Categories</h2>
      <ul>
        {state.categories.map((category) => (
          <li key={category.name} className="chip">
            <span aria-hidden="true">{category.icon}</span> {category.name}
          </li>
        ))}
      </ul>
    </section>
  )
}
