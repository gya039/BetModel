export default function StatChip({ label, value, color }) {
  return (
    <div className="stat-chip">
      <span className="stat-chip__label">{label}</span>
      <span className="stat-chip__value" style={color ? { color } : undefined}>
        {value}
      </span>
    </div>
  )
}
