/** Placeholder cards while the dashboard code or numbers load. */
export function DashboardSkeleton({ withTitle = true }: { withTitle?: boolean }) {
  const cards = (
    <div className="dashboard-body" aria-hidden="true">
      <div className="skeleton skeleton-hero" />
      <div className="skeleton skeleton-card" />
      <div className="skeleton skeleton-card" />
    </div>
  )
  if (!withTitle) return cards
  return (
    <section className="page dashboard" aria-busy="true">
      <div className="page-title">
        <h1>Spending patterns</h1>
      </div>
      <p className="sr-only" role="status">
        Loading your spending…
      </p>
      {cards}
    </section>
  )
}
