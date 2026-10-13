import type { Summary } from '../../api/client'
import { formatMoney } from '../../lib/format'

export function TopLists({ summary }: { summary: Summary }) {
  const { currency } = summary
  if (summary.top_stores.length === 0 && summary.top_items.length === 0) return null

  return (
    <div className="card-grid">
      <section className="card" aria-labelledby="stores-title">
        <h2 id="stores-title" className="card-title">
          Top stores
        </h2>
        <table className="data-table">
          <thead>
            <tr>
              <th scope="col">Store</th>
              <th scope="col">Visits</th>
              <th scope="col">Spent</th>
            </tr>
          </thead>
          <tbody>
            {summary.top_stores.map((store) => (
              <tr key={store.store}>
                <th scope="row">{store.store || '—'}</th>
                <td>{store.visits}</td>
                <td>{formatMoney(store.total, currency)}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </section>

      <section className="card" aria-labelledby="items-title">
        <h2 id="items-title" className="card-title">
          Bought most often
        </h2>
        <table className="data-table">
          <thead>
            <tr>
              <th scope="col">Item</th>
              <th scope="col">Times</th>
              <th scope="col">Spent</th>
            </tr>
          </thead>
          <tbody>
            {summary.top_items.map((item) => (
              <tr key={item.name}>
                <th scope="row">{item.name}</th>
                <td>{item.times}</td>
                <td>{formatMoney(item.total, currency)}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </section>
    </div>
  )
}
