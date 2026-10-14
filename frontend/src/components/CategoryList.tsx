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
      <p className="muted categories-note">
        Edit them in the <strong>categories</strong> and <strong>subcategories</strong> tabs of your spreadsheet.
      </p>
      <ul>
        {state.categories.map((category) => (
          <li key={category.name} className="category-entry">
            <span className="category-entry-name">
              <span aria-hidden="true">{category.icon}</span> {category.name}
            </span>
            {category.subcategories.length > 0 && (
              <span className="muted category-entry-subs">{category.subcategories.join(' · ')}</span>
            )}
          </li>
        ))}
      </ul>
    </section>
  )
}
