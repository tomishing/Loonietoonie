import { useCategories } from '../hooks/useCategories'
import { Icon } from './Icon'

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
        Tap a category to see its subcategories. Edit them in the <strong>categories</strong> and{' '}
        <strong>subcategories</strong> tabs of your spreadsheet; scanning follows the descriptions.
      </p>
      <ul>
        {state.categories.map((category) => (
          <li key={category.name} className="category-entry">
            {category.subcategories.length === 0 ? (
              <span className="category-entry-name">
                <span aria-hidden="true">{category.icon}</span> {category.name}
              </span>
            ) : (
              <details>
                <summary className="category-entry-name">
                  <span>
                    <span aria-hidden="true">{category.icon}</span> {category.name}
                    <span className="muted category-entry-count"> · {category.subcategories.length}</span>
                  </span>
                  <Icon name="down" size={18} className="category-entry-chevron" />
                </summary>
                <ul className="subcategory-list">
                  {category.subcategories.map((name) => (
                    <li key={name}>
                      <span className="subcategory-name">{name}</span>
                      {category.descriptions[name] && (
                        <span className="muted subcategory-description">{category.descriptions[name]}</span>
                      )}
                    </li>
                  ))}
                </ul>
              </details>
            )}
          </li>
        ))}
      </ul>
    </section>
  )
}
