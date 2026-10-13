import { useCategories } from '../hooks/useCategories'

export function CategoryList() {
  const state = useCategories()

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
