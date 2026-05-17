export function PickCardSkeleton() {
  return (
    <div className="skeleton" style={{ minHeight: 120 }}>
      <div className="skeleton__line tall short" />
      <div className="skeleton__line mid" />
      <div className="skeleton__line full" style={{ height: 52, borderRadius: 10 }} />
      <div className="skeleton__line short" />
    </div>
  )
}

export function StatCardSkeleton() {
  return (
    <div className="skeleton de-stat-card">
      <div className="skeleton__line short" />
      <div className="skeleton__line tall mid" />
    </div>
  )
}

export function ChartSkeleton() {
  return (
    <div className="skeleton de-chart-wrap" style={{ height: 220 }}>
      <div className="skeleton__line short" />
    </div>
  )
}

export function TableRowSkeleton({ rows = 5 }) {
  return Array.from({ length: rows }, (_, i) => (
    <tr key={i}>
      <td colSpan={99}>
        <div
          className="skeleton__line"
          style={{ width: `${60 + Math.random() * 30}%`, margin: '8px 0' }}
        />
      </td>
    </tr>
  ))
}
