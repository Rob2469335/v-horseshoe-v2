type StatusBadgeProps = {
  label: string
  tone?: 'neutral' | 'success' | 'warning' | 'danger'
}

export default function StatusBadge({ label, tone = 'neutral' }: StatusBadgeProps) {
  return <span className={`status-badge status-badge--${tone}`}>{label}</span>
}
